#!/usr/bin/env python3
"""
Walk-forward test of the key levels (module H): do price reactions happen at the zones, or would a
zone of the same width a little further away have done as well?

  python scripts/levels_backtest.py BARS.csv [BARS.csv ...] [--horizon swing|position]
                                    [--step 5] [--forward 20]

Each CSV holds daily bars with Date, Open, High, Low, Close and Volume columns (any case; extra
columns such as Adj Close are ignored), split-adjusted like the rest of the skill.

Every `step` bars, levels are built as of that bar from bars <= it only. For each S1-S3 and R1-R3
zone:
  - touched: a bar within the next `forward` bars trades into the zone (low <= zone_high for a
    support, high >= zone_low for a resistance);
  - from the first touching bar on, the outcome is read over `forward` more bars: the zone is held
    once price trades 1 ATR beyond its near edge (zone_high + ATR for a support) before any close
    beyond its far edge (below zone_low), and broken once such a close comes first; outcomes that
    resolve neither way are left out. The outcome can therefore end up to 2 x `forward` - 1 bars
    after the build bar.
The placebo keeps each zone's shape, its width and distance from the close in ATRs, but moves it
to other as-of bars of the same series (PLACEBO_OFFSETS steps later, wrapping around), where it
marks no level; it is scored the same way. Hold rates rise with the distance from the close
(price that has travelled further is more stretched), and the placebo has the same distances, so
edge = hold rate - placebo hold rate is what the zone's location adds.

In-sample and without costs; it measures where price reacts, not a trading rule.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
import indicators as I
import levels as L

REACTION_ATR = 1.0          # held = price trades this many ATRs beyond the zone's near edge
PLACEBO_OFFSETS = (17, 41)  # placebo as-of bars: this many test steps later (cyclic)


def load_csv(path: str | Path) -> pd.DataFrame:
    """Daily OHLCV from a CSV, indexed by date, oldest first, rows without prices dropped."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()[["open", "high", "low", "close", "volume"]]
    df = df.apply(pd.to_numeric, errors="coerce").dropna(subset=["open", "high", "low", "close"])
    df = df[(df["high"] >= df["low"]) & (df["low"] > 0)]
    df.index.name = "date"
    return df[~df.index.duplicated(keep="last")]


def outcome(bars: pd.DataFrame, t: int, lo: float, hi: float, side: str, atr: float, forward: int) -> str | None:
    """'held', 'broken', 'open' (touched, unresolved) or None (not touched) for zone [lo, hi]
    over bars t+1..t+forward, scanning on from the first touch up to `forward` bars."""
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    end = min(len(bars), t + 1 + forward)
    touched = None
    for i in range(t + 1, end):
        if (low[i] <= hi) if side == "support" else (high[i] >= lo):
            touched = i
            break
    if touched is None:
        return None
    for i in range(touched, min(len(bars), touched + forward)):
        if side == "support":
            if close[i] < lo:
                return "broken"
            if high[i] >= hi + REACTION_ATR * atr:
                return "held"
        else:
            if close[i] > hi:
                return "broken"
            if low[i] <= lo - REACTION_ATR * atr:
                return "held"
    return "open"


def _context(bars: pd.DataFrame, ind: pd.DataFrame, t: int, horizon: str) -> dict:
    hz = C.HORIZONS[horizon]
    keep = max(int(hz["levels_lookback"]), C.HIGH_52W_BARS) + 80   # enough for every lookback used
    b = bars.iloc[max(0, t + 1 - keep):t + 1]
    return {"ticker": "BT", "as_of": b.index[-1].strftime("%Y-%m-%d"), "horizon": horizon, "hz": hz,
            "bars": b, "ind": ind.loc[:b.index[-1]], "is_etf": True, "earnings": None,
            "skipped": {}, "notes": []}


def walk(bars: pd.DataFrame, horizon: str = "swing", step: int = 5, forward: int = 20,
         build=L.build_levels) -> pd.DataFrame:
    """One row per (as-of bar, level): rank, side, width and distance in ATRs, outcome and the
    placebo outcomes."""
    ind = I.compute_all(bars)
    start = max(int(C.HORIZONS[horizon]["levels_lookback"]), C.HIGH_52W_BARS, 220)
    asof, rows = [], []
    for t in range(start, len(bars) - forward, step):
        lv = build(_context(bars, ind, t, horizon))
        if not lv.get("available"):
            continue
        close, atr = lv["last_close"], lv["atr"]
        asof.append((t, close, atr))
        for side, key in (("support", "supports"), ("resistance", "resistances")):
            for rank, z in enumerate(lv[key], 1):
                lo, hi = float(z["zone_low"]), float(z["zone_high"])
                rows.append({"date": bars.index[t], "t": t, "side": side, "rank": rank,
                             "lo_atr": (lo - close) / atr, "hi_atr": (hi - close) / atr,
                             "width_atr": (hi - lo) / atr, "dist_atr": abs(z["price"] - close) / atr,
                             "outcome": outcome(bars, t, lo, hi, side, atr, forward), "n_asof": len(asof) - 1})
    for r in rows:
        r["placebo"] = []
        for k in PLACEBO_OFFSETS:
            t2, close2, atr2 = asof[(r["n_asof"] + k) % len(asof)]
            r["placebo"].append(outcome(bars, t2, close2 + r["lo_atr"] * atr2, close2 + r["hi_atr"] * atr2,
                                        r["side"], atr2, forward))
    return pd.DataFrame(rows)


def summarize(rows: pd.DataFrame) -> pd.DataFrame:
    """Per rank (S1..S3 and R1..R3, then all): observations, touch rate, hold rate, placebo hold
    rate, edge, median width and distance in ATRs."""
    def stats(g: pd.DataFrame) -> dict:
        held = (g["outcome"] == "held").sum()
        broken = (g["outcome"] == "broken").sum()
        p = [o for ps in g["placebo"] for o in ps]
        p_held, p_broken = p.count("held"), p.count("broken")
        hold = held / (held + broken) if held + broken else np.nan
        p_hold = p_held / (p_held + p_broken) if p_held + p_broken else np.nan
        return {"n": len(g), "touch_rate": g["outcome"].notna().mean(), "resolved": int(held + broken),
                "hold_rate": hold, "placebo_hold": p_hold, "edge": hold - p_hold,
                "width_atr": g["width_atr"].median(), "dist_atr": g["dist_atr"].median()}

    if rows.empty:
        return pd.DataFrame()
    out = {f"{'S' if s == 'support' else 'R'}{r}": stats(g)
           for (s, r), g in rows.groupby(["side", "rank"], sort=True)}
    out["all"] = stats(rows)
    return pd.DataFrame(out).T


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--horizon", default=C.DEFAULT_HORIZON, choices=sorted(C.HORIZONS))
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--forward", type=int, default=20)
    a = ap.parse_args(argv)
    all_rows = []
    for path in a.csv:
        rows = walk(load_csv(path), a.horizon, a.step, a.forward)
        print(f"\n{Path(path).stem}\n{summarize(rows).round(3).to_string()}")
        all_rows.append(rows)
    if len(all_rows) > 1:
        print(f"\nall files\n{summarize(pd.concat(all_rows, ignore_index=True)).round(3).to_string()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
