#!/usr/bin/env python3
"""
Walk-forward test of the key levels (module H): do price reactions happen at the zones, or would a
zone of the same width a little further away have done as well?

  python scripts/levels_backtest.py BARS.csv [BARS.csv ...] [--horizon swing|position]
                                    [--step 5] [--forward 20] [--split 2017-01-01 2022-01-01]
                                    [--jobs 4] [--out DIR]

Each CSV holds daily bars with Date, Open, High, Low, Close and Volume columns (any case; extra
columns such as Adj Close are ignored), split-adjusted like the rest of the skill. The file name
(without extension) is the symbol in the per-symbol table.

Every `step` bars, levels are built as of that bar from bars <= it only. For each S1-S3 and R1-R3
zone [zone_low, zone_high], over the next `forward` bars:
  - touched: the first bar whose range overlaps the zone (low <= zone_high and high >= zone_low);
  - gap: price got past the zone without trading in it (a whole bar beyond its far edge before any
    overlap). Counted on its own, neither held nor broken;
  - from the touching bar on, the outcome is read over `forward` more bars: held once price trades
    1 ATR beyond the near edge (zone_high + ATR for a support), broken once a bar closes beyond the
    far edge (below zone_low) without having done so. A bar's close is its last print, so a bar that
    reaches the reaction and closes beyond the far edge is held. On the touching bar itself the
    reaction is known to come after the touch only if the bar opened in the zone or beyond it;
    otherwise the outcome is `ambiguous` and left out, like `open` (touched, unresolved);
  - the outcome can therefore end up to 2 x `forward` - 1 bars after the build bar.
The placebo keeps each zone's shape, its width and distance from the close in ATRs, but moves it
to other as-of bars of the same series (PLACEBO_OFFSETS steps later, wrapping around), where it
marks no level; it is scored the same way. Hold rates rise with the distance from the close
(price that has travelled further is more stretched), and the placebo has the same distances, so
edge = hold rate - placebo hold rate is what the zone's location adds.

The 90% interval for the edge is a cluster bootstrap: (symbol, calendar year) blocks are resampled
whole, because zones built 5 bars apart share most of their bars and are not independent draws.
Tables are given per rank, per symbol, per daily direction at the build bar (Uptrend / Downtrend /
Mixed, as in the regime module) and per period (--split dates).

In-sample and without costs; it measures where price reacts, not a trading rule.
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
import regime as R

REACTION_ATR = 1.0          # held = price trades this many ATRs beyond the zone's near edge
PLACEBO_OFFSETS = (17, 41)  # placebo as-of bars: this many test steps later (cyclic)
BOOT_REPS = 2000            # cluster-bootstrap resamples for the edge interval
BOOT_CI = 0.90              # two-sided coverage of that interval
BOOT_SEED = 20261026        # fixed, so a report is reproducible
RESOLVED = ("held", "broken")


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
    """'held', 'broken', 'ambiguous', 'open' (touched, unresolved), 'gap' (passed without trading in
    the zone) or None (not reached) for zone [lo, hi] over bars t+1..t+forward, scanning on from the
    first touch up to `forward` bars."""
    o = bars["open"].to_numpy(dtype=float)
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    sup = side == "support"
    end = min(len(bars), t + 1 + forward)
    touched = None
    for i in range(t + 1, end):
        if low[i] <= hi and high[i] >= lo:
            touched = i
            break
        if (high[i] < lo) if sup else (low[i] > hi):
            return "gap"
    if touched is None:
        return None
    for i in range(touched, min(len(bars), touched + forward)):
        reached = high[i] >= hi + REACTION_ATR * atr if sup else low[i] <= lo - REACTION_ATR * atr
        if reached and i == touched and not (o[i] <= hi if sup else o[i] >= lo):
            return "ambiguous"
        if reached:
            return "held"
        if (close[i] < lo) if sup else (close[i] > hi):
            return "broken"
    return "open"


def _context(bars: pd.DataFrame, ind: pd.DataFrame, t: int, horizon: str) -> dict:
    hz = C.HORIZONS[horizon]
    keep = max(int(hz["levels_lookback"]), C.HIGH_52W_BARS) + 80   # enough for every lookback used
    b = bars.iloc[max(0, t + 1 - keep):t + 1]
    return {"ticker": "BT", "as_of": b.index[-1].strftime("%Y-%m-%d"), "horizon": horizon, "hz": hz,
            "bars": b, "ind": ind.loc[:b.index[-1]], "is_etf": True, "earnings": None,
            "skipped": {}, "notes": []}


def _direction(close: float, row: pd.Series) -> str:
    """Daily direction at the build bar, as regime.daily_direction labels it ('n/a' without inputs)."""
    def num(k):
        v = row.get(k)
        return float(v) if v is not None and np.isfinite(v) else None
    label, _ = R.direction_from_votes(R.direction_votes(close, num("sma50"), num("sma200"), num("sma200_slope")))
    return label or "n/a"


def walk(bars: pd.DataFrame, horizon: str = "swing", step: int = 5, forward: int = 20,
         build=L.build_levels, symbol: str = "") -> pd.DataFrame:
    """One row per (as-of bar, level): symbol, rank, side, daily direction, width and distance in
    ATRs, outcome and the placebo outcomes."""
    ind = I.compute_all(bars)
    start = max(int(C.HORIZONS[horizon]["levels_lookback"]), C.HIGH_52W_BARS, 220)
    asof, rows = [], []
    for t in range(start, len(bars) - forward, step):
        lv = build(_context(bars, ind, t, horizon))
        if not lv.get("available"):
            continue
        close, atr = lv["last_close"], lv["atr"]
        asof.append((t, close, atr))
        direction = _direction(close, ind.iloc[t])
        for side, key in (("support", "supports"), ("resistance", "resistances")):
            for rank, z in enumerate(lv[key], 1):
                lo, hi = float(z["zone_low"]), float(z["zone_high"])
                rows.append({"symbol": symbol, "date": bars.index[t], "t": t, "side": side, "rank": rank,
                             "direction": direction,
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


def _counts(g: pd.DataFrame) -> np.ndarray:
    """[held, broken, placebo held, placebo broken] for the rows in g."""
    p = pd.Series([o for ps in g["placebo"] for o in ps], dtype=object)
    return np.array([(g["outcome"] == "held").sum(), (g["outcome"] == "broken").sum(),
                     (p == "held").sum(), (p == "broken").sum()], dtype=float)


def _edge(c: np.ndarray) -> np.ndarray:
    """Edge (hold rate - placebo hold rate) from count rows [held, broken, p_held, p_broken]."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return c[..., 0] / (c[..., 0] + c[..., 1]) - c[..., 2] / (c[..., 2] + c[..., 3])


def edge_interval(g: pd.DataFrame, reps: int = BOOT_REPS, ci: float = BOOT_CI,
                  seed: int = BOOT_SEED) -> tuple[float, float]:
    """Cluster-bootstrap interval for the edge, resampling (symbol, year) blocks whole;
    (nan, nan) with fewer than two blocks."""
    keys = list(zip(g["symbol"] if "symbol" in g else [""] * len(g), pd.DatetimeIndex(g["date"]).year))
    blocks = np.array([_counts(b) for _, b in g.groupby(pd.Series(keys, index=g.index).astype(str), sort=True)])
    if len(blocks) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    sums = blocks[rng.integers(0, len(blocks), size=(reps, len(blocks)))].sum(axis=1)
    e = _edge(sums)
    e = e[np.isfinite(e)]
    if not len(e):
        return np.nan, np.nan
    a = (1 - ci) / 2
    return float(np.quantile(e, a)), float(np.quantile(e, 1 - a))


def _stats(g: pd.DataFrame, interval: bool = True) -> dict:
    held, broken, p_held, p_broken = _counts(g)
    hold = held / (held + broken) if held + broken else np.nan
    p_hold = p_held / (p_held + p_broken) if p_held + p_broken else np.nan
    lo, hi = edge_interval(g) if interval else (np.nan, np.nan)
    return {"n": len(g), "touch_rate": g["outcome"].isin(["held", "broken", "open", "ambiguous"]).mean(),
            "gap": int((g["outcome"] == "gap").sum()), "ambiguous": int((g["outcome"] == "ambiguous").sum()),
            "resolved": int(held + broken), "hold_rate": hold, "placebo_hold": p_hold, "edge": hold - p_hold,
            "edge_lo": lo, "edge_hi": hi,
            "width_atr": g["width_atr"].median(), "dist_atr": g["dist_atr"].median()}


def summarize(rows: pd.DataFrame, by: str | None = None, interval: bool = True) -> pd.DataFrame:
    """Observations, touch rate, gap and ambiguous counts, hold rate, placebo hold rate, edge with
    its bootstrap interval, median width and distance in ATRs: per rank (S1..S3 and R1..R3) by
    default, else per value of column `by`; then all rows."""
    if rows.empty:
        return pd.DataFrame()
    if by is None:
        out = {f"{'S' if s == 'support' else 'R'}{r}": _stats(g, interval)
               for (s, r), g in rows.groupby(["side", "rank"], sort=True)}
    else:
        out = {str(k): _stats(g, interval) for k, g in rows.groupby(by, sort=True)}
    out["all"] = _stats(rows, interval)
    return pd.DataFrame(out).T


def periods(dates: pd.Series, split: list[str]) -> pd.Series:
    """'train' / 'validation' / 'test' (or 'p1', 'p2', ...) for each date, cut at the split dates."""
    cuts = [pd.Timestamp(x) for x in split]
    names = ["train", "validation", "test"] if len(cuts) == 2 else [f"p{i + 1}" for i in range(len(cuts) + 1)]
    idx = np.searchsorted(np.array(cuts, dtype="datetime64[ns]"), pd.DatetimeIndex(dates).values, side="right")
    return pd.Series([names[i] for i in idx], index=dates.index)


def _run(args: tuple) -> pd.DataFrame:
    path, horizon, step, forward = args
    return walk(load_csv(path), horizon, step, forward, symbol=Path(path).stem.upper())


def report(rows: pd.DataFrame, meta: dict) -> str:
    """Markdown report: settings, then the tables per rank, direction, period and symbol."""
    def table(df: pd.DataFrame) -> str:
        cols = ["n", "touch_rate", "gap", "ambiguous", "resolved", "hold_rate", "placebo_hold",
                "edge", "edge_lo", "edge_hi", "width_atr", "dist_atr"]
        df = df[cols].astype(float)
        head = "| | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"
        fmt = lambda c, v: "" if not np.isfinite(v) else (f"{v:.0f}" if c in ("n", "gap", "ambiguous", "resolved")
                                                          else f"{v:.3f}")
        return head + "".join(f"| {k} | " + " | ".join(fmt(c, r[c]) for c in cols) + " |\n" for k, r in df.iterrows())

    out = ["# Levels walk-forward baseline", ""]
    out += [f"- {k}: {v}" for k, v in meta.items()]
    out += ["", f"Edge interval: {BOOT_CI:.0%} cluster bootstrap over (symbol, year) blocks, {BOOT_REPS} resamples.", ""]
    sections = [("By rank", None), ("By daily direction at the build bar", "direction")]
    if "period" in rows:
        sections.append(("By period", "period"))
    sections.append(("By symbol", "symbol"))
    for title, by in sections:
        out += [f"## {title}", "", table(summarize(rows, by)), ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--horizon", default=C.DEFAULT_HORIZON, choices=sorted(C.HORIZONS))
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--forward", type=int, default=20)
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE",
                    help="period boundaries by build date; two dates give train / validation / test")
    ap.add_argument("--jobs", type=int, default=1, help="files run in parallel")
    ap.add_argument("--out", help="directory for rows.csv and report.md")
    a = ap.parse_args(argv)
    tasks = [(p, a.horizon, a.step, a.forward) for p in a.csv]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            results = list(ex.map(_run, tasks))
    else:
        results = [_run(t) for t in tasks]
    for path, rows in zip(a.csv, results):
        print(f"\n{Path(path).stem}\n{summarize(rows, interval=False).round(3).to_string()}")
    rows = pd.concat(results, ignore_index=True)
    if rows.empty:
        print("no levels built", file=sys.stderr)
        return 1
    if a.split:
        rows["period"] = periods(rows["date"], a.split)
    if len(results) > 1:
        print(f"\nall files\n{summarize(rows).round(3).to_string()}")
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        rows.assign(placebo=rows["placebo"].map(lambda ps: ";".join(str(x) for x in ps))) \
            .drop(columns=["n_asof"]).to_csv(out / "rows.csv", index=False)
        meta = {"horizon": a.horizon, "step": a.step, "forward": a.forward, "files": len(a.csv),
                "first build": rows["date"].min().strftime("%Y-%m-%d"),
                "last build": rows["date"].max().strftime("%Y-%m-%d"),
                "split": ", ".join(a.split) or "none"}
        (out / "report.md").write_text(report(rows, meta), encoding="utf-8")
        print(f"\nwrote {out / 'rows.csv'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
