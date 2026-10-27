#!/usr/bin/env python3
"""
Do levels where several independent methods agree hold better? Confluence count vs hold rate.

  python scripts/confluence_test.py BARS.csv [...] [--hourly --daily DIR] [--step 5] [--forward 20]
                                    [--split 2017-01-01 2022-01-01] [--exclude START:END ...]
                                    [--jobs 4] [--out DIR]

Families (independent ways of finding a level): the levels.source_type families of build_levels'
candidates (pivot, volume_profile, range_52w, supply_demand, gap, moving_average, anchored_vwap),
plus weekly_pivot (sr_screen.weekly_pivots, a higher timeframe) and round_number (multiples of
round_step(close)). With --hourly the bars are hourly (intraday_levels.load_hourly, builds once per
session at its 2nd bar) and two more families join: prior_session (prior session high, low, close,
POC and value area) and session (first-hour range, session VWAP).

Two tests per build bar, from bars <= it only:
  - kind `fixed`: every candidate price c gets a zone c +- sr_screen.ZONE_HALF_ATR ATRs, and its
    confluence = the number of distinct families with a candidate inside that zone. Per side and
    per confluence class (1, 2, 3+), the nearest such zone lying wholly on its side of the close
    within sr_screen.REACH_ATR ATRs is scored. Width is the same for every class, so only the
    agreement differs;
  - kind `level`: the key levels S1-S3 / R1-R3, by the number of families among the candidates
    inside each zone (build_levels' source_types, plus weekly_pivot and round_number when one lies
    inside); the zones' own widths.
Outcome and moved-zone placebo as in levels_backtest; --exclude as in regime_split.

Pre-registered reading: confluence helps if the edge rises from class 1 to 2 to 3+ and the 3+ edge
interval lies above 0 on the main sample, with a positive 3+ edge in each period.
"""
from __future__ import annotations

import argparse
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import indicators as I
import levels as L
import levels_backtest as B
import regime_split as RS
import sr_screen as SS

CLASSES = ("1", "2", "3+")
ROUND_STEPS = ((25, 1.0), (100, 5.0), (250, 10.0), (1000, 25.0), (math.inf, 50.0))  # close below -> step
INTRADAY_FAMILY = {"prev_high": "prior_session", "prev_low": "prior_session", "prev_close": "prior_session",
                   "prev_poc": "prior_session", "prev_value_area": "prior_session",
                   "opening_range": "session", "session_vwap": "session"}


def round_step(close: float) -> float:
    return next(step for top, step in ROUND_STEPS if close < top)


def round_numbers(close: float, atr: float) -> list[float]:
    """Multiples of round_step(close) within reach of the close."""
    s = round_step(close)
    lo, hi = close - SS.REACH_ATR * atr, close + SS.REACH_ATR * atr
    return [k * s for k in range(int(math.ceil(lo / s)), int(math.floor(hi / s)) + 1) if k > 0]


def cls(n: int) -> str:
    return "3+" if n >= 3 else str(n)


def family_points(lv: dict, ctx_bars: pd.DataFrame, extra: dict[str, list[float]]) -> list[tuple[float, str]]:
    """(price, family) for every candidate."""
    pts = [(float(c["price"]), L.source_type(c["source"])) for c in lv["candidates"]
           if c.get("price") is not None and np.isfinite(c["price"])]
    pts += [(p, "weekly_pivot") for p in SS.weekly_pivots(ctx_bars)]
    pts += [(p, "round_number") for p in round_numbers(lv["last_close"], lv["atr"])]
    for src, prices in extra.items():
        pts += [(float(p), INTRADAY_FAMILY.get(src, src)) for p in prices]
    return pts


def confluence(pts: list[tuple[float, str]], lo: float, hi: float) -> int:
    return len({f for p, f in pts if lo <= p <= hi})


def build_rows(lv: dict, pts: list[tuple[float, str]], base: dict, bars: pd.DataFrame, t: int,
               forward: int) -> list[dict]:
    close, atr = lv["last_close"], lv["atr"]
    h = SS.ZONE_HALF_ATR * atr
    rows = []
    prices = np.array([p for p, _ in pts])
    fams = [f for _, f in pts]
    for side in ("support", "resistance"):
        best: dict[str, tuple[float, int, list[str]]] = {}
        for p in sorted(set(prices.tolist()), key=lambda x: abs(x - close)):
            if not (p + h < close if side == "support" else p - h > close) or abs(p - close) > SS.REACH_ATR * atr:
                continue
            inside = sorted({fams[i] for i in np.flatnonzero((prices >= p - h) & (prices <= p + h))})
            c = cls(len(inside))
            if c not in best:
                best[c] = (p, len(inside), inside)
            if len(best) == len(CLASSES):
                break
        for c, (p, n, inside) in best.items():
            lo, hi = p - h, p + h
            rows.append({**base, "kind": "fixed", "conf": c, "n_families": n, "families": "+".join(inside),
                         "side": side, "rank": 1, "lo_atr": (lo - close) / atr, "hi_atr": (hi - close) / atr,
                         "width_atr": 2 * SS.ZONE_HALF_ATR, "dist_atr": abs(p - close) / atr,
                         "outcome": B.outcome(bars, t, lo, hi, side, atr, forward)})
    for side, key in (("support", "supports"), ("resistance", "resistances")):
        for rank, z in enumerate(lv[key], 1):
            lo, hi = float(z["zone_low"]), float(z["zone_high"])
            fam = set(z.get("source_types") or [])
            fam |= {f for p, f in pts if f in ("weekly_pivot", "round_number", "prior_session", "session")
                    and lo <= p <= hi}
            rows.append({**base, "kind": "level", "conf": cls(len(fam)), "n_families": len(fam),
                         "families": "+".join(sorted(fam)), "side": side, "rank": rank,
                         "lo_atr": (lo - close) / atr, "hi_atr": (hi - close) / atr, "width_atr": (hi - lo) / atr,
                         "dist_atr": abs(z["price"] - close) / atr,
                         "outcome": B.outcome(bars, t, lo, hi, side, atr, forward)})
    return rows


def walk(bars: pd.DataFrame, step: int = 5, forward: int = 20, symbol: str = "", windows=(),
         hourly: bool = False) -> pd.DataFrame:
    ind = I.compute_all(bars)
    excl = RS.excluded_bars(bars.index, list(windows), forward)
    if hourly:
        import intraday_levels as IL
        si = IL.session_info(bars)
        builds = [int(t) for t in np.flatnonzero(si["pos"].to_numpy() == 2) if 300 <= t < len(bars) - forward]
    else:
        start = max(int(B.C.HORIZONS["swing"]["levels_lookback"]), B.C.HIGH_52W_BARS, 220)
        builds = list(range(start, len(bars) - forward, step))
    asof, rows = [], []
    for t in builds:
        ctx = B._context(bars, ind, t, "swing")
        lv = L.build_levels(ctx)
        if not lv.get("available"):
            continue
        asof.append((t, lv["last_close"], lv["atr"], bool(excl[t])))
        extra = IL.intraday_candidates(bars, si, t) if hourly else {}
        base = {"symbol": symbol, "date": bars.index[t], "t": t, "direction": "n/a",
                "excluded": bool(excl[t]), "n_asof": len(asof) - 1}
        rows += build_rows(lv, family_points(lv, ctx["bars"], extra), base, bars, t, forward)
    pool = [i for i, a in enumerate(asof) if not a[3]]
    pos = {n: i for i, n in enumerate(pool)}
    for r in rows:
        r["placebo"] = []
        p = pos.get(r["n_asof"])
        if p is None or len(pool) < 2:
            continue
        for k in B.PLACEBO_OFFSETS:
            t2, c2, a2, _ = asof[pool[(p + k) % len(pool)]]
            r["placebo"].append(B.outcome(bars, t2, c2 + r["lo_atr"] * a2, c2 + r["hi_atr"] * a2,
                                          r["side"], a2, forward))
    return pd.DataFrame(rows)


def verdict(rows: pd.DataFrame) -> dict:
    """Edge per class and the pre-registered pass."""
    fx = rows[rows["kind"] == "fixed"]
    s = {c: B._stats(fx[fx["conf"] == c]) for c in CLASSES}
    top = fx[fx["conf"] == "3+"]
    per = {p: B._stats(g, interval=False)["edge"] for p, g in top.groupby("period")} if "period" in top else {}
    e = [s[c]["edge"] for c in CLASSES]
    ok = (all(np.isfinite(e)) and e[0] < e[1] < e[2] and np.isfinite(s["3+"]["edge_lo"])
          and s["3+"]["edge_lo"] > 0 and all(x > 0 for x in per.values()))
    return {"edges": dict(zip(CLASSES, e)), "per_period_3plus": per, "pass": bool(ok)}


def _table(df: pd.DataFrame) -> str:
    cols = ["n", "resolved", "hold_rate", "placebo_hold", "edge", "edge_lo", "edge_hi", "width_atr", "dist_atr"]
    df = df[cols].astype(float)
    head = "| | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"

    def fmt(c, v):
        return "" if not np.isfinite(v) else f"{v:.0f}" if c in ("n", "resolved") else f"{v:.3f}"
    return head + "".join(f"| {k} | " + " | ".join(fmt(c, r[c]) for c in cols) + " |\n" for k, r in df.iterrows())


def report(rows: pd.DataFrame, meta: dict) -> str:
    main = rows[~rows["excluded"]]
    v = verdict(main)
    out = ["# Confluence: do levels where several methods agree hold better?", ""]
    out += [f"- {k}: {x}" for k, x in meta.items()]
    out += ["", f"Edge interval: {B.BOOT_CI:.0%} cluster bootstrap over {B.BOOT_BLOCKS}.", "",
            f"Pre-registered verdict (main sample): {'PASS' if v['pass'] else 'no pass'}; fixed-width edges "
            + ", ".join(f"{c}: {e:.3f}" for c, e in v["edges"].items())
            + "; 3+ edge per period " + ", ".join(f"{p} {e:.3f}" for p, e in v["per_period_3plus"].items()), ""]
    fx, lvl = main[main["kind"] == "fixed"], main[main["kind"] == "level"]
    out += ["## Fixed-width zones by confluence class, main sample", "", _table(B.summarize(fx, by="conf")), ""]
    out += ["## Fixed-width zones by number of families, main sample", "",
            _table(B.summarize(fx.assign(nf=fx["n_families"].astype(int).astype(str).str.zfill(2)), by="nf")), ""]
    if "period" in fx:
        for p, g in fx.groupby("period"):
            out += [f"### Fixed-width by class, {p}", "", _table(B.summarize(g, by="conf")), ""]
    out += ["## Fixed-width by class and side, main sample", "",
            _table(B.summarize(fx.assign(k=fx["conf"] + " " + fx["side"]), by="k")), ""]
    out += ["## Key levels S1-S3 / R1-R3 by confluence class, main sample", "", _table(B.summarize(lvl, by="conf")), ""]
    full = rows[rows["kind"] == "fixed"]
    out += ["## Fixed-width zones by class, full sample", "", _table(B.summarize(full, by="conf")), ""]
    top = fx[fx["conf"] == "3+"]
    if not top.empty:
        common = top["families"].value_counts().head(12).index
        out += ["## Most common 3+ combinations, main sample", "",
                _table(B.summarize(top[top["families"].isin(common)], by="families")), ""]
    return "\n".join(out)


def _run(args) -> pd.DataFrame:
    path, step, forward, windows, hourly, daily = args
    sym = Path(path).stem.upper()
    if hourly:
        import intraday_levels as IL
        bars = IL.load_hourly(path)
        if daily and (Path(daily) / f"{sym}.csv").exists():
            bars, _ = IL.align_to_daily(bars, B.load_csv(Path(daily) / f"{sym}.csv"))
    else:
        bars = B.load_csv(path)
    return walk(bars, step, forward, symbol=sym, windows=windows, hourly=hourly)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--hourly", action="store_true")
    ap.add_argument("--daily")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--forward", type=int, default=20)
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="START:END")
    ap.add_argument("--exclusions", help="exclusions.csv from clean_data.py")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    wins = B.file_windows(a.csv, a.exclude, a.exclusions, hourly=a.hourly)
    tasks = [(p, a.step, a.forward, w, a.hourly, a.daily) for p, w in zip(a.csv, wins)]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            results = list(ex.map(_run, tasks))
    else:
        results = [_run(t) for t in tasks]
    results = [r for r in results if not r.empty]
    if not results:
        print("no rows", file=sys.stderr)
        return 1
    rows = pd.concat(results, ignore_index=True)
    if a.split:
        rows["period"] = B.periods(rows["date"], a.split)
    main_rows = rows[~rows["excluded"]]
    print(B.summarize(main_rows[main_rows["kind"] == "fixed"], by="conf").round(3).to_string())
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        rows.assign(placebo=rows["placebo"].map(lambda ps: ";".join(str(x) for x in ps))) \
            .drop(columns=["n_asof"]).to_csv(out / "rows.csv.gz", index=False)
        meta = {"bars": "hourly" if a.hourly else "daily", "step": "once per session" if a.hourly else a.step,
                "forward": a.forward, "files": len(a.csv), "split": ", ".join(a.split) or "none",
                "excluded": B.describe_windows(a.exclude, a.exclusions),
                "rows": f"{len(rows)} ({int(rows['excluded'].sum())} excluded)",
                "zone (fixed)": f"candidate +- {SS.ZONE_HALF_ATR} ATR, within {SS.REACH_ATR} ATR"}
        (out / "report.md").write_text(report(rows, meta), encoding="utf-8")
        print(f"wrote {out / 'rows.csv.gz'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
