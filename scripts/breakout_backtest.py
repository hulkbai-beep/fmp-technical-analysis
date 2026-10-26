#!/usr/bin/env python3
"""
Walk-forward test of trading-range breakouts: after a box (trading_ranges.py) breaks, does price
keep going more often than it would from an ordinary day?

  python scripts/breakout_backtest.py BARS.csv [BARS.csv ...] [--forward 20]
                                      [--split 2017-01-01 2022-01-01] [--jobs 4] [--out DIR]

CSV format as for levels_backtest.py. Events are taken on the bar that confirms a breakout (the
second breaching close), which is known on that bar; the reference price is its close. Over the
next `forward` bars the first barrier hit decides the outcome:
  - box_2atr: +-BARRIER_ATR ATRs from the close (ATR at the event bar); continued = the barrier in
    the breakout's direction first;
  - box_measured: the measured-move target (the broken edge + the box height, in the breakout's
    direction) against the box's midline behind it;
  - donchian_2atr: the control. A close beyond the highest high (lowest low) of the previous
    DONCHIAN_BARS bars, at most one per DONCHIAN_BARS bars and direction, with the box_2atr
    barriers: does the box add anything over a plain breakout?
A bar that reaches both barriers is `ambiguous` (order unknown on daily bars) and left out, like
`open` (neither hit). The placebo keeps the event's direction and its barrier distances in ATRs
but moves it to other bars of the same series (PLACEBO_OFFSET_BARS later, wrapping around); edge =
continuation rate - placebo rate, with the cluster-bootstrap interval of levels_backtest.py.
In the tables `hold_rate` is the continuation rate.

In-sample and without costs; it measures follow-through, not a trading rule.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import indicators as I
import levels_backtest as B
import trading_ranges as T

BARRIER_ATR = 2.0                     # box_2atr / donchian_2atr barriers, in ATRs from the event close
DONCHIAN_BARS = 20                    # control: close beyond this many bars' extreme
PLACEBO_OFFSET_BARS = (85, 205)       # placebo bars: this many bars later (cyclic over the testable span)
WARMUP_BARS = 60                      # no events before this bar (ATR and the first boxes need history)


def first_passage(bars_hl: tuple[np.ndarray, np.ndarray], t: int, up: float, dn: float, forward: int) -> str:
    """'up', 'down', 'ambiguous' (both on one bar) or 'open' for barriers up > dn over bars t+1..t+forward."""
    high, low = bars_hl
    for i in range(t + 1, min(len(high), t + 1 + forward)):
        hit_up, hit_dn = high[i] >= up, low[i] <= dn
        if hit_up and hit_dn:
            return "ambiguous"
        if hit_up:
            return "up"
        if hit_dn:
            return "down"
    return "open"


def _label(passage: str, d: int) -> str:
    if passage in ("ambiguous", "open"):
        return passage
    return "held" if (passage == "up") == (d > 0) else "broken"


def box_events(bars: pd.DataFrame, atr: pd.Series) -> list[dict]:
    """Confirmed breakouts of every box in the series: bar, direction, box geometry."""
    a = atr.reindex(bars.index).to_numpy(dtype=float, na_value=np.nan)
    boxes = T.scan_boxes(bars, a, T.pivot_rows(bars, atr), len(bars) - 1)
    pos = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(bars.index)}
    out = []
    for b in boxes:
        if b["breakout"] is None:
            continue
        d = 1 if b["breakout"]["direction"] == "up" else -1
        out.append({"t": pos[b["breakout"]["confirmed"]], "d": d, "upper": b["upper"], "lower": b["lower"],
                    "height_atr": b["height_atr"]})
    return out


def donchian_events(bars: pd.DataFrame) -> list[dict]:
    """Closes beyond the previous DONCHIAN_BARS bars' highest high / lowest low, de-clustered."""
    hi = bars["high"].rolling(DONCHIAN_BARS).max().shift(1).to_numpy()
    lo = bars["low"].rolling(DONCHIAN_BARS).min().shift(1).to_numpy()
    close = bars["close"].to_numpy(dtype=float)
    out, last = [], {1: -10 ** 9, -1: -10 ** 9}
    for t in range(len(bars)):
        for d, hit in ((1, close[t] > hi[t]), (-1, close[t] < lo[t])):
            if hit and t - last[d] > DONCHIAN_BARS:
                out.append({"t": t, "d": d})
                last[d] = t
    return out


def walk(bars: pd.DataFrame, forward: int = 20, symbol: str = "") -> pd.DataFrame:
    """One row per (event, variant) with the outcome and the placebo outcomes."""
    ind = I.compute_all(bars)
    atr = I.atr(bars)
    a = atr.to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    hl = (bars["high"].to_numpy(dtype=float), bars["low"].to_numpy(dtype=float))
    lo_t, hi_t = WARMUP_BARS, len(bars) - forward          # events and placebo bars in [lo_t, hi_t)
    if hi_t - lo_t < 2:
        return pd.DataFrame()

    def ok(t):
        return lo_t <= t < hi_t and np.isfinite(a[t]) and a[t] > 0

    def row(kind, t, d, up_atr, dn_atr, width_atr=np.nan):
        """up_atr / dn_atr: barrier distances above / below the close, in ATRs at t."""
        outcome = _label(first_passage(hl, t, close[t] + up_atr * a[t], close[t] - dn_atr * a[t], forward), d)
        placebo = []
        for k in PLACEBO_OFFSET_BARS:
            t2 = lo_t + (t - lo_t + k) % (hi_t - lo_t)
            if not ok(t2):
                placebo.append(None)
                continue
            placebo.append(_label(first_passage(hl, t2, close[t2] + up_atr * a[t2], close[t2] - dn_atr * a[t2],
                                                forward), d))
        return {"symbol": symbol, "date": bars.index[t], "kind": kind, "side": "up" if d > 0 else "down",
                "rank": 1, "direction": B._direction(close[t], ind.iloc[t]), "width_atr": width_atr,
                "dist_atr": up_atr if d > 0 else dn_atr, "outcome": outcome, "placebo": placebo}

    rows = []
    for e in box_events(bars, atr):
        t, d = e["t"], e["d"]
        if not ok(t):
            continue
        rows.append(row("box_2atr", t, d, BARRIER_ATR, BARRIER_ATR, e["height_atr"]))
        h = e["upper"] - e["lower"]
        mid = (e["upper"] + e["lower"]) / 2
        target = e["upper"] + h if d > 0 else e["lower"] - h
        up, dn = (target, mid) if d > 0 else (mid, target)
        if up > close[t] > dn:
            rows.append(row("box_measured", t, d, (up - close[t]) / a[t], (close[t] - dn) / a[t], e["height_atr"]))
    for e in donchian_events(bars):
        if ok(e["t"]):
            rows.append(row("donchian_2atr", e["t"], e["d"], BARRIER_ATR, BARRIER_ATR))
    return pd.DataFrame(rows)


def _run(args: tuple) -> pd.DataFrame:
    path, forward = args
    return walk(B.load_csv(path), forward, symbol=Path(path).stem.upper())


def _table(df: pd.DataFrame) -> str:
    cols = ["n", "ambiguous", "resolved", "hold_rate", "placebo_hold", "edge", "edge_lo", "edge_hi", "dist_atr"]
    df = df[cols].astype(float)
    head = "| | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"

    def fmt(c, v):
        return "" if not np.isfinite(v) else f"{v:.0f}" if c in ("n", "ambiguous", "resolved") else f"{v:.3f}"
    return head + "".join(f"| {k} | " + " | ".join(fmt(c, r[c]) for c in cols) + " |\n" for k, r in df.iterrows())


def report(rows: pd.DataFrame, meta: dict) -> str:
    out = ["# Breakout follow-through", ""] + [f"- {k}: {v}" for k, v in meta.items()]
    out += ["", "`hold_rate` = continuation rate (the barrier in the breakout's direction first). Edge interval: "
            f"{B.BOOT_CI:.0%} cluster bootstrap over (symbol, year) blocks.", "", "## By variant", "",
            _table(B.summarize(rows, by="kind")), ""]
    for kind in sorted(rows["kind"].unique()):
        g = rows[rows["kind"] == kind]
        for title, by in (("direction of the breakout", "side"), ("daily direction at the event", "direction"),
                          ("period", "period"), ("symbol", "symbol")):
            if by in g:
                out += [f"## {kind}: by {title}", "", _table(B.summarize(g, by=by)), ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--forward", type=int, default=20)
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    tasks = [(p, a.forward) for p in a.csv]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            results = list(ex.map(_run, tasks))
    else:
        results = [_run(t) for t in tasks]
    rows = pd.concat([r for r in results if not r.empty], ignore_index=True) if any(
        not r.empty for r in results) else pd.DataFrame()
    if rows.empty:
        print("no events", file=sys.stderr)
        return 1
    if a.split:
        rows["period"] = B.periods(rows["date"], a.split)
    print(B.summarize(rows, by="kind").round(3).to_string())
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        rows.assign(placebo=rows["placebo"].map(lambda ps: ";".join(str(x) for x in ps))) \
            .to_csv(out / "rows.csv", index=False)
        meta = {"forward": a.forward, "barrier_atr": BARRIER_ATR, "files": len(a.csv),
                "first event": rows["date"].min().strftime("%Y-%m-%d"),
                "last event": rows["date"].max().strftime("%Y-%m-%d"), "split": ", ".join(a.split) or "none"}
        (out / "report.md").write_text(report(rows, meta), encoding="utf-8")
        print(f"wrote {out / 'rows.csv'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
