#!/usr/bin/env python3
"""
Screen each support/resistance source on its own, and test the structural stop.

  python scripts/sr_screen.py BARS.csv [BARS.csv ...] [--step 5] [--forward 20]
                               [--split 2017-01-01 2022-01-01] [--jobs 4] [--out DIR]

CSV format as for levels_backtest.py; swing horizon. Every `step` bars, as of that bar only:

Source screen. levels.build_levels' candidates are grouped by source (pivot, poc, value_area, hvn,
range_52w, supply_demand, gap, sma50, sma200, anchored_vwap), plus two sources it does not use:
weekly_pivot (ATR ZigZag on weekly bars, last WEEKLY_LOOKBACK weeks) and big_gap_edge (the close
before a gap of at least BIG_GAP_ATR ATRs, last BIG_GAP_BARS bars). Per source and side, the
candidate nearest the close becomes a zone of fixed width (price +- ZONE_HALF_ATR ATRs, the same for
every source so they compare), if it lies wholly on its side of the close within REACH_ATR ATRs.
It is scored with levels_backtest.outcome and its moved-zone placebo, as the key levels are.

Stop test. The scorecard's structural stop: S1's zone_low - STRUCTURE_STOP_BUFFER_ATR ATRs for a
long, R1's zone_high + that for a short. `held` = not hit within `forward` bars (low above the stop
for a long), `broken` = hit (a gap through counts). The placebo puts a stop at the same ATR distance
from the close on other as-of bars. A structural stop that is hit less often than the placebo
means the level protects the stop.

--exclude START:END and --exclusions FILE drop rows as in levels_backtest.py (outcome span touches a
window; such as-of bars are never placebos); the report covers the rows left.

Pre-registered reading: a source passes when its edge interval lies above 0 in the train period
and its edge is positive in validation; the test period is read once, afterwards.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
import indicators as I
import levels as L
import levels_backtest as B

ZONE_HALF_ATR = 0.25       # screened zone = candidate price +- this many ATRs
REACH_ATR = 4.0            # nearest candidate within this many ATRs of the close
WEEKLY_LOOKBACK = 104      # weeks of weekly bars for weekly pivots
WEEKLY_SWING_ATR = 1.5     # weekly ZigZag reversal, in weekly ATRs
BIG_GAP_ATR = 1.5          # big gap: |open - previous close| at least this many ATRs (ATR of the previous bar)
BIG_GAP_BARS = 252         # big gaps within this many bars

GROUPS = {"pivot_high": "pivot", "pivot_low": "pivot", "poc": "poc", "value_area_low": "value_area",
          "value_area_high": "value_area", "hvn": "hvn", "high_52w": "range_52w", "low_52w": "range_52w",
          "demand": "supply_demand", "supply": "supply_demand", "sma50": "sma50", "sma200": "sma200"}


def group(source: str) -> str:
    if source.startswith("gap_"):
        return "gap"
    if source.startswith("avwap_"):
        return "anchored_vwap"
    return GROUPS.get(source, source)


def weekly_pivots(bars: pd.DataFrame) -> list[float]:
    """Prices of ATR ZigZag pivots on W-FRI bars of the last WEEKLY_LOOKBACK weeks."""
    w = bars.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    w = w.iloc[-WEEKLY_LOOKBACK:]
    if len(w) < 2 * C.ATR_PERIOD:
        return []
    piv = L.swing_pivots(w, I.atr(w), WEEKLY_SWING_ATR)
    return [p["price"] for p in piv["highs"] + piv["lows"]]


def big_gap_edges(bars: pd.DataFrame, atr: pd.Series) -> list[float]:
    """Previous closes before gaps of at least BIG_GAP_ATR ATRs within the last BIG_GAP_BARS bars."""
    b = bars.iloc[-(BIG_GAP_BARS + 1):]
    prev = b["close"].shift(1)
    a = atr.reindex(b.index).shift(1)
    big = (b["open"] - prev).abs() >= BIG_GAP_ATR * a
    return prev[big].dropna().tolist()


def nearest(prices: list[float], close: float, atr: float, side: str) -> float | None:
    """Nearest price whose fixed-width zone lies wholly on `side` of the close, within reach."""
    h = ZONE_HALF_ATR * atr
    if side == "support":
        ok = [p for p in prices if p + h < close and close - p <= REACH_ATR * atr]
        return max(ok) if ok else None
    ok = [p for p in prices if p - h > close and p - close <= REACH_ATR * atr]
    return min(ok) if ok else None


def walk(bars: pd.DataFrame, step: int = 5, forward: int = 20, symbol: str = "",
         windows: list[tuple[str, str]] = ()) -> pd.DataFrame:
    """Rows of kind 'source' (one per source and side) and 'stop' (structural stops), with the
    `excluded` flag; placebos come from non-excluded as-of bars."""
    ind = I.compute_all(bars)
    atr_s = ind["atr14"]
    start = max(int(C.HORIZONS["swing"]["levels_lookback"]), C.HIGH_52W_BARS, 220)
    asof, rows = [], []
    for t in range(start, len(bars) - forward, step):
        ctx = B._context(bars, ind, t, "swing")
        lv = L.build_levels(ctx)
        if not lv.get("available"):
            continue
        close, atr = lv["last_close"], lv["atr"]
        asof.append((t, close, atr))
        direction = B._direction(close, ind.iloc[t])
        by_group: dict[str, list[float]] = {}
        for c in lv["candidates"]:
            if c.get("price") is not None and np.isfinite(c["price"]):
                by_group.setdefault(group(c["source"]), []).append(float(c["price"]))
        by_group["weekly_pivot"] = weekly_pivots(ctx["bars"])
        by_group["big_gap_edge"] = big_gap_edges(ctx["bars"], atr_s)
        base = {"symbol": symbol, "date": bars.index[t], "t": t, "rank": 1, "direction": direction,
                "n_asof": len(asof) - 1}
        for g, prices in sorted(by_group.items()):
            for side in ("support", "resistance"):
                p = nearest(prices, close, atr, side)
                if p is None:
                    continue
                lo, hi = p - ZONE_HALF_ATR * atr, p + ZONE_HALF_ATR * atr
                rows.append({**base, "kind": "source", "source": g, "side": side,
                             "lo_atr": (lo - close) / atr, "hi_atr": (hi - close) / atr,
                             "width_atr": (hi - lo) / atr, "dist_atr": abs(p - close) / atr,
                             "outcome": B.outcome(bars, t, lo, hi, side, atr, forward)})
        for side, key in (("long", "supports"), ("short", "resistances")):
            if not lv[key]:
                continue
            z = lv[key][0]
            buf = C.STRUCTURE_STOP_BUFFER_ATR * atr
            stop = z["zone_low"] - buf if side == "long" else z["zone_high"] + buf
            rows.append({**base, "kind": "stop", "source": "structural_stop", "side": side,
                         "stop_atr": (stop - close) / atr, "width_atr": np.nan, "dist_atr": abs(stop - close) / atr,
                         "outcome": stop_outcome(bars, t, stop, side, forward)})
    excl = B.excluded_bars(bars.index, list(windows), forward)
    pool = B.placebo_pool(asof, excl)
    for r in rows:
        r["excluded"] = bool(excl[r["t"]])
        r["placebo"] = []
        for n2 in B.pool_offsets(pool, r["n_asof"]):
            t2, close2, atr2 = asof[n2]
            if r["kind"] == "source":
                r["placebo"].append(B.outcome(bars, t2, close2 + r["lo_atr"] * atr2, close2 + r["hi_atr"] * atr2,
                                              r["side"], atr2, forward))
            else:
                r["placebo"].append(stop_outcome(bars, t2, close2 + r["stop_atr"] * atr2, r["side"], forward))
    return pd.DataFrame(rows)


def stop_outcome(bars: pd.DataFrame, t: int, stop: float, side: str, forward: int) -> str:
    """'held' (stop not hit over bars t+1..t+forward) or 'broken' (hit; a gap through counts)."""
    seg = bars.iloc[t + 1:t + 1 + forward]
    hit = (seg["low"] <= stop).any() if side == "long" else (seg["high"] >= stop).any()
    return "broken" if hit else "held"


def _run(args: tuple) -> pd.DataFrame:
    path, step, forward, windows = args
    return walk(B.load_csv(path), step, forward, symbol=Path(path).stem.upper(), windows=windows)


def _table(df: pd.DataFrame) -> str:
    cols = ["n", "resolved", "hold_rate", "placebo_hold", "edge", "edge_lo", "edge_hi", "dist_atr"]
    df = df[cols].astype(float)
    head = "| | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"

    def fmt(c, v):
        return "" if not np.isfinite(v) else f"{v:.0f}" if c in ("n", "resolved") else f"{v:.3f}"
    return head + "".join(f"| {k} | " + " | ".join(fmt(c, r[c]) for c in cols) + " |\n" for k, r in df.iterrows())


def report(rows: pd.DataFrame, meta: dict) -> str:
    src, stop = rows[rows["kind"] == "source"], rows[rows["kind"] == "stop"]
    out = ["# Support/resistance source screen and stop test", ""] + [f"- {k}: {v}" for k, v in meta.items()]
    out += ["", f"Edge interval: {B.BOOT_CI:.0%} cluster bootstrap over {B.BOOT_BLOCKS}.", ""]
    periods = sorted(rows["period"].unique()) if "period" in rows else [None]
    for p in periods:
        g = src if p is None else src[src["period"] == p]
        out += [f"## Sources{'' if p is None else f' ({p})'}", "", _table(B.summarize(g, by="source")), ""]
    out += ["## Sources by side (all periods)", "",
            _table(B.summarize(src.assign(key=src["source"] + " " + src["side"]), by="key")), ""]
    if not stop.empty:
        out += ["## Structural stop (hold_rate = not hit)", "", _table(B.summarize(stop, by="side")), ""]
        if "period" in stop:
            out += ["## Structural stop by period", "", _table(B.summarize(stop, by="period")), ""]
        out += ["## Structural stop by daily direction", "", _table(B.summarize(stop, by="direction")), ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--forward", type=int, default=20)
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="START:END")
    ap.add_argument("--exclusions", help="exclusions.csv from clean_data.py")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    wins = B.file_windows(a.csv, a.exclude, a.exclusions)
    tasks = [(p, a.step, a.forward, w) for p, w in zip(a.csv, wins)]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            results = list(ex.map(_run, tasks))
    else:
        results = [_run(t) for t in tasks]
    rows = pd.concat([r for r in results if not r.empty], ignore_index=True) if any(
        not r.empty for r in results) else pd.DataFrame()
    if rows.empty:
        print("no rows", file=sys.stderr)
        return 1
    if a.split:
        rows["period"] = B.periods(rows["date"], a.split)
    main_rows = rows[~rows["excluded"]]
    print(B.summarize(main_rows[main_rows["kind"] == "source"], by="source").round(3).to_string())
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        rows.assign(placebo=rows["placebo"].map(lambda ps: ";".join(str(x) for x in ps))) \
            .drop(columns=["n_asof"]).to_csv(out / "rows.csv", index=False)
        meta = {"step": a.step, "forward": a.forward, "files": len(a.csv), "zone_half_atr": ZONE_HALF_ATR,
                "reach_atr": REACH_ATR, "split": ", ".join(a.split) or "none",
                "excluded": B.describe_windows(a.exclude, a.exclusions),
                "rows": f"{len(rows)} ({int(rows['excluded'].sum())} excluded)"}
        (out / "report.md").write_text(report(main_rows, meta), encoding="utf-8")
        print(f"wrote {out / 'rows.csv'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
