#!/usr/bin/env python3
"""
Optional cross-check of the skill's own SMA(50) and RSI(14) against FMP's
technical-indicator endpoints (never used by the analysis itself).

  python scripts/crosscheck.py TICKER [--json PATH]

Both series are aligned by date and compared over the last CROSSCHECK_WINDOW dates they share.
Tolerances: SMA within 0.01% of the close; RSI within 0.5 points, after a 250-bar warm-up on
both series. If the plan does not include the endpoints, the script says so and exits 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
import fetch
import fmp_client
import indicators

WINDOW = getattr(C, "CROSSCHECK_WINDOW", 250)
RSI_WARMUP = getattr(C, "CROSSCHECK_RSI_WARMUP", 250)
SMA_TOL = getattr(C, "CROSSCHECK_SMA_TOL", 0.0001)   # share of the close
RSI_TOL = getattr(C, "CROSSCHECK_RSI_TOL", 0.5)      # RSI points

# key, endpoint, request params, FMP value field, compute_all column, label
CHECKS = (
    ("sma50", "technical-indicators/sma", {"periodLength": 50, "timeframe": "1day"}, "sma", "sma50", "SMA(50)"),
    ("rsi14", "technical-indicators/rsi", {"periodLength": 14, "timeframe": "1day"}, "rsi", "rsi14", "RSI(14)"),
)

NOTES = [
    "RSI(14) uses Wilder smoothing, which is path-dependent: the value on any date depends on the "
    "bar the averages were seeded on and on how much history precedes it (the seed's weight decays "
    "like (13/14)^k). FMP does not document its seeding or history depth, so small RSI differences "
    "can come from that alone; both series are compared only after a 250-bar warm-up.",
    "SMA(50) is a plain rolling mean of split-adjusted closes; a difference beyond rounding means "
    "the two sides used different input prices (e.g. a missing or revised bar).",
]


def _fmp_series(data, field) -> pd.Series:
    """FMP indicator rows ("date": "YYYY-MM-DD 00:00:00", <field>) -> ascending float Series."""
    rows = [r for r in (data if isinstance(data, list) else []) if isinstance(r, dict) and r.get("date")]
    if not rows:
        return pd.Series(dtype="float64")
    s = pd.Series([r.get(field) for r in rows],
                  index=pd.to_datetime([str(r["date"])[:10] for r in rows], format="%Y-%m-%d"))
    s = pd.to_numeric(s, errors="coerce").sort_index(kind="mergesort")
    return s[~s.index.duplicated(keep="last")].dropna()


def compare(ours: pd.Series, theirs: pd.Series, tol, skip_ours=0, skip_theirs=0,
            window=WINDOW, rel_to: pd.Series | None = None) -> dict:
    """Summarise |ours - theirs| over the last `window` dates both series have, after dropping
    the first skip_ours / skip_theirs bars of each (warm-up). `tol` is an absolute tolerance,
    scalar or Series by date; with `rel_to`, the largest difference is also given as % of it."""
    a = ours.iloc[skip_ours:].dropna()
    b = theirs.iloc[skip_theirs:].dropna()
    common = a.index.intersection(b.index)[-window:]
    if not len(common):
        return {"n": 0, "max_abs_diff": None, "mean_abs_diff": None, "share_within_tol": None,
                "first_date": None, "last_date": None, "worst_date": None}
    diff = (a[common] - b[common]).abs()
    limit = tol.reindex(common) if isinstance(tol, pd.Series) else pd.Series(float(tol), index=common)
    out = {
        "n": int(len(common)),
        "max_abs_diff": float(diff.max()),
        "mean_abs_diff": float(diff.mean()),
        "share_within_tol": float((diff <= limit).mean()),
        "first_date": common[0].strftime("%Y-%m-%d"),
        "last_date": common[-1].strftime("%Y-%m-%d"),
        "worst_date": diff.idxmax().strftime("%Y-%m-%d"),
    }
    if rel_to is not None:
        out["max_rel_diff_pct"] = float((diff / rel_to.reindex(common)).max() * 100)
    return out


def crosscheck(client, ticker, now=None) -> dict:
    """Compare FMP's SMA(50)/RSI(14) with indicators.compute_all on the skill's own /full bars.
    status: "ok" (at least one indicator compared), "restricted" (plan lacks both endpoints)
    or "unavailable" (other failures)."""
    now = fetch.to_ny(now)
    symbol = ticker.strip().upper()
    calls_before = client.stats["network_calls"]
    result = {"ticker": symbol, "status": "unavailable", "as_of": None, "window": WINDOW,
              "indicators": {}, "notes": list(NOTES), "calls": 0}

    fmp, reasons = {}, []
    for key, endpoint, params, field, _, label in CHECKS:
        data, problem = fmp_client.get_with_reason(client, endpoint, symbol=symbol, **params)
        series = _fmp_series(data, field)
        if series.empty:
            problem = problem or "no data returned"
            reasons.append(problem)
            result["indicators"][key] = {"label": label, "available": False, "reason": problem}
        else:
            fmp[key] = series

    if not fmp:
        if all(r.startswith("restricted") for r in reasons):
            result["status"] = "restricted"
        result["calls"] = client.stats["network_calls"] - calls_before
        return result

    profile, _ = fmp_client.get_with_reason(client, "profile", symbol=symbol)
    profile = profile[0] if isinstance(profile, list) and profile else None
    rows, problem = fetch.fetch_history(client, fetch.FULL, symbol, fetch.history_start(profile),
                                        pd.Timestamp(now.date()))
    if not rows:
        raise ValueError(f"no price data for {symbol}" + (f" ({problem})" if problem else ""))
    bars, dq = fetch.validate(fetch.stock_frame(rows, []), now, symbol)
    ours = indicators.compute_all(bars)
    result["as_of"] = dq["last_date"]
    if dq["partial_bar_excluded"]:
        result["notes"].append("Today's partial bar is excluded from the skill's series, so today is not compared.")

    for key, _, _, _, column, label in CHECKS:
        if key not in fmp:
            continue
        if key == "sma50":
            stats = compare(ours[column], fmp[key], SMA_TOL * bars["close"], rel_to=bars["close"])
            tolerance = f"{SMA_TOL:.2%} of close"
        else:
            stats = compare(ours[column], fmp[key], RSI_TOL, skip_ours=RSI_WARMUP, skip_theirs=RSI_WARMUP)
            tolerance = f"{RSI_TOL:g} points after a {RSI_WARMUP}-bar warm-up"
        result["indicators"][key] = {
            "label": label, "available": stats["n"] > 0, "tolerance": tolerance, **stats,
            "fmp_rows": int(len(fmp[key])), "fmp_first_date": fmp[key].index[0].strftime("%Y-%m-%d"),
            "own_bars": int(len(bars)),
        }
        if not stats["n"]:
            result["indicators"][key]["reason"] = "no overlapping dates after warm-up"
    result["status"] = "ok" if any(v.get("available") for v in result["indicators"].values()) else "unavailable"
    result["calls"] = client.stats["network_calls"] - calls_before
    return result


def format_report(res: dict) -> str:
    lines = [f"Cross-check vs FMP technical-indicators: {res['ticker']} "
             f"(last {res['window']} overlapping dates, own bars to {res['as_of'] or 'n/a'})", ""]
    if res["status"] == "restricted":
        lines.append("FMP's technical-indicator endpoints are not included in this plan; nothing to compare.")
    lines.append(f"{'indicator':<10} {'n':>5} {'max |diff|':>11} {'mean |diff|':>12} {'within tol':>11}  tolerance / reason")
    for v in res["indicators"].values():
        if v.get("n"):
            lines.append(f"{v['label']:<10} {v['n']:>5} {v['max_abs_diff']:>11.4f} {v['mean_abs_diff']:>12.4f} "
                         f"{v['share_within_tol']:>10.1%}  {v['tolerance']} (worst {v['worst_date']})")
        else:
            lines.append(f"{v['label']:<10} {'-':>5} {'-':>11} {'-':>12} {'-':>11}  {v.get('reason', '')}")
    lines += [""] + [f"Note: {n}" for n in res["notes"]] + [f"Network calls used: {res['calls']}"]
    return "\n".join(lines)


def _json_safe(x):
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_json_safe(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return float(x) if np.isfinite(x) else None
    return x


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Cross-check SMA(50)/RSI(14) against FMP's technical-indicator endpoints.")
    parser.add_argument("ticker")
    parser.add_argument("--json", metavar="PATH", help="also write the result as JSON")
    args = parser.parse_args(argv)
    try:
        res = crosscheck(fmp_client.FMPClient(), args.ticker)
    except (fmp_client.FMPError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(format_report(res))
    if args.json:
        Path(args.json).write_text(json.dumps(_json_safe(res), indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")
    return 0 if res["status"] in ("ok", "restricted") else 1


if __name__ == "__main__":
    sys.exit(main())
