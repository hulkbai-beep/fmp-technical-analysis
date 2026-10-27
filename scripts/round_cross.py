#!/usr/bin/env python3
"""
After price crosses a round number, does it keep going faster than after crossing an ordinary
price? (The stop-loss half of Osler 2003: stops cluster just beyond round numbers, so a cross
triggers them.)

  python scripts/round_cross.py BARS.csv [BARS.csv ...] [--forward 14]
                                [--split 2014-01-01 2018-01-01] [--exclude START:END ...]
                                [--exclusions FILE] [--hourly] [--jobs 4] [--out DIR]

Round numbers are the multiples of confluence_test.round_step(previous close) (1 below $25, 5
below $100, 10 below $250, 25 below $1000, else 50). An event is a bar whose close crosses such a
level R from the previous close (prev < R <= close, or prev > R >= close); the control events
cross the non-round levels R + OFFSETS x step in the same way. At most one event per bar and kind
(the level nearest the close) and one per level and session (hourly) or level and week (daily).
From the event bar's close, the first of +-BARRIER_ATR ATRs (ATR at the event bar) reached within
`forward` bars decides: continued = the barrier in the crossing direction first. A bar that
reaches both is `ambiguous` and, like `open` (neither), left out. ret_session = the move in the
crossing direction over the next SESSION_BARS bars, in ATRs.

edge = continuation rate after round crosses - after the control crosses; the interval is the
cluster bootstrap over calendar-month blocks. --exclude / --exclusions drop events whose span
touches a window (hourly-only rows apply with --hourly). Pre-registered reading: passes when the
edge is at least 0.02, its 90% interval lies above 0 and it is positive in each period.
"""
from __future__ import annotations

import argparse
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import breakout_backtest as BB
import confluence_test as CT
import indicators as I
import levels_backtest as B
import sr_methods as M

OFFSETS = (0.3, 0.7)
BARRIER_ATR = 1.0
SESSION_BARS = 7
WARMUP_BARS = 60
MIN_EDGE = 0.02


def crossed(prev: float, close: float, step: float, offset: float) -> float | None:
    """The level k x step + offset x step crossed from prev to close nearest the close, or None."""
    lo, hi = min(prev, close), max(prev, close)
    k_lo = math.ceil((lo - offset * step) / step)
    k_hi = math.floor((hi - offset * step) / step)
    levels = [(k + offset) * step for k in range(k_lo, k_hi + 1)]
    up = close > prev
    levels = [x for x in levels if x > 0 and ((prev < x <= close) if up else (close <= x < prev))]
    if not levels:
        return None
    return max(levels) if up else min(levels)


def walk(bars: pd.DataFrame, forward: int = 14, symbol: str = "", windows=(), hourly: bool = True) -> pd.DataFrame:
    a = I.atr(bars).to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    hl = (bars["high"].to_numpy(dtype=float), bars["low"].to_numpy(dtype=float))
    excl = B.excluded_bars(bars.index, list(windows), forward)
    group = bars.index.normalize() if hourly else bars.index.to_period("W-FRI").start_time
    seen: set = set()
    rows = []
    for t in range(max(1, WARMUP_BARS), len(bars) - forward):
        if not (np.isfinite(a[t]) and a[t] > 0) or close[t] == close[t - 1]:
            continue
        step = CT.round_step(close[t - 1])
        d = 1 if close[t] > close[t - 1] else -1
        for kind, offsets in (("round", (0.0,)), ("control", OFFSETS)):
            hits = [x for x in (crossed(close[t - 1], close[t], step, o) for o in offsets) if x is not None]
            if not hits:
                continue
            level = min(hits, key=lambda x: abs(x - close[t]))
            key = (group[t], kind, round(level, 6))
            if key in seen:
                continue
            seen.add(key)
            up, dn = close[t] + BARRIER_ATR * a[t], close[t] - BARRIER_ATR * a[t]
            outcome = BB._label(BB.first_passage(hl, t, up, dn, forward), d)
            end = min(len(bars) - 1, t + SESSION_BARS)
            rows.append({"symbol": symbol, "date": bars.index[t], "t": t, "kind": kind,
                         "side": "up" if d > 0 else "down", "level": level, "step": step,
                         "dist_atr": abs(close[t] - level) / a[t], "outcome": outcome,
                         "ret_session": d * (close[end] - close[t]) / a[t], "excluded": bool(excl[t])})
    return pd.DataFrame(rows)


def verdict(rows: pd.DataFrame, ci: float = 0.90) -> dict:
    r, c = rows[rows["kind"] == "round"], rows[rows["kind"] == "control"]
    s = M.hold_edge(r, c, ci)
    per = {p: M.hold_edge(r[r["period"] == p], c[c["period"] == p], None)["edge"]
           for p in sorted(rows["period"].unique())} if "period" in rows else {}
    out = {"events": len(r), "control_events": len(c), "resolved": s["resolved"],
           "continue_round": s["hold_rate"], "continue_control": s["compare_hold"], "edge": s["edge"],
           "edge_lo": s["edge_lo"], "edge_hi": s["edge_hi"],
           "ret_session_round": r["ret_session"].mean(), "ret_session_control": c["ret_session"].mean(),
           **{f"edge_{p}": e for p, e in per.items()}}
    out["pass"] = bool(np.isfinite(s["edge_lo"]) and s["edge_lo"] > 0 and s["edge"] >= MIN_EDGE
                       and all(np.isfinite(e) and e > 0 for e in per.values()))
    return out


def report(rows: pd.DataFrame, meta: dict) -> str:
    main = rows[~rows["excluded"]]
    out = ["# Round-number crosses: follow-through", ""] + [f"- {k}: {v}" for k, v in meta.items()]
    out += ["", "continue = the +-1 ATR barrier in the crossing direction reached first; edge = round - control.", ""]
    tab = {"main sample": verdict(main), "full sample": verdict(rows)}
    for side in ("up", "down"):
        tab[f"main, {side} crosses"] = verdict(main[main["side"] == side])
    for name, g in main.groupby(main["step"].map(lambda s: f"step {s:g}")):
        tab[f"main, {name}"] = verdict(g)
    out += [M._fmt_table(pd.DataFrame(tab).T, ints=("events", "control_events", "resolved")), ""]
    return "\n".join(out)


def _run(args) -> pd.DataFrame:
    path, forward, windows, hourly = args
    if hourly:
        import intraday_levels as IL
        bars = IL.load_hourly(path)
    else:
        bars = B.load_csv(path)
    return walk(bars, forward, symbol=Path(path).stem.upper(), windows=windows, hourly=hourly)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--hourly", action="store_true")
    ap.add_argument("--forward", type=int, default=14)
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="START:END")
    ap.add_argument("--exclusions", help="exclusions.csv from clean_data.py")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    wins = B.file_windows(a.csv, a.exclude, a.exclusions, hourly=a.hourly)
    tasks = [(p, a.forward, w, a.hourly) for p, w in zip(a.csv, wins)]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            results = list(ex.map(_run, tasks))
    else:
        results = [_run(t) for t in tasks]
    results = [r for r in results if not r.empty]
    if not results:
        print("no events", file=sys.stderr)
        return 1
    rows = pd.concat(results, ignore_index=True)
    if a.split:
        rows["period"] = B.periods(rows["date"], a.split)
    print(pd.Series(verdict(rows[~rows["excluded"]])).to_string())
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        rows.to_csv(out / "rows.csv.gz", index=False)
        meta = {"bars": "hourly" if a.hourly else "daily", "forward": a.forward, "files": len(a.csv),
                "split": ", ".join(a.split) or "none", "excluded": B.describe_windows(a.exclude, a.exclusions),
                "events": f"{len(rows)} ({int(rows['excluded'].sum())} excluded)",
                "control offsets": ", ".join(f"{o:g} step" for o in OFFSETS)}
        (out / "report.md").write_text(report(rows, meta), encoding="utf-8")
        print(f"wrote {out / 'rows.csv.gz'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
