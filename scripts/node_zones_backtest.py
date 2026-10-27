#!/usr/bin/env python3
"""
Walk-forward test of the long-window node zones (node_zones.py) for position holders: buy at a
support zone and hold one to three months, or trim at a resistance zone.

  python scripts/node_zones_backtest.py BARS.csv [BARS.csv ...] [--step 5] [--entry 21]
                                        [--holds 21 63] [--split 2017-01-01 2022-01-01]
                                        [--exclude START:END ...] [--exclusions FILE]
                                        [--jobs 4] [--out DIR]

CSV format as for levels_backtest.py (daily bars). Every `step` bars, from the last
node_zones.LOOKBACK bars <= the build bar only, the zones are built; those lying wholly below the
close are supports S1, S2 (nearest first), those wholly above it resistances R1, R2. A zone that
contains the close is not used. Per zone:

  held: levels_backtest.outcome over `entry` bars (1 ATR beyond the near edge before a close
      beyond the far edge), the measure of the earlier level screens;
  trade, per hold h in `holds`: a limit order at the near edge on the first touch within `entry`
      bars (filled at the open when the bar opens beyond the near edge; no trade when a whole bar
      passes beyond the far edge first, or when the zone is not reached), out at the close h bars
      after the fill bar. ret = return in ATRs at the build bar after COST per side (a short at a
      resistance: the price fall); mae = the deepest move against the position from the fill to
      the exit, in ATRs (a positive number). No stop: a position holder sits through the move.

The placebo keeps each zone's shape (its edges' offsets from the close in ATRs) and moves it to
other as-of bars of the same series (levels_backtest.PLACEBO_OFFSETS steps on, never excluded
ones), and is scored the same way. Nodes cover most of the window's range, so the moved zone usually
lies on another node: the test asks whether the nearest node's exact edges matter. Edge = zone -
placebo: hold rate, mean ret, and for mae placebo depth - zone depth (positive when the zone limits
the drawdown). Its 90% interval is a cluster bootstrap over (symbol, year) blocks; zone and placebo
come from different dates, so market-wide moves stay in the edge, and clustering by calendar month
widens the intervals 1.5-2 times (methodology.md H.2).

Pre-registered hypotheses (written before the first run):
  s1_ret21, s1_ret63: S1 trades, ret edge, holds 21 and 63;
  s1_strong_ret63: S1 trades whose zone is graded strong or very_strong, ret edge, hold 63;
  s1_mae63: S1 trades, mae edge, hold 63;
  r1_ret21, r1_ret63: R1 shorts, ret edge, holds 21 and 63;
  s1_held, r1_held: hold-rate edge of S1 and R1.
A hypothesis passes the screen when its edge is at least MIN_EDGE (MIN_EDGE_ATR for ret and mae),
its 90% interval lies above 0 and its edge is positive in each period (--split); it passes the
multiple-testing gate when its (1 - 0.10 / N_HYPOTHESES) interval lies above 0. Grades, S2 / R2
and the daily direction are context only.

In-sample, on split-adjusted daily bars; it measures what the zones add over a zone of the same
shape elsewhere, not a complete trading rule.
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
import levels_backtest as B
import node_zones as NZ

COST = 0.0005               # per side, as a share of the price
PER_SIDE = 2                # S1, S2 and R1, R2
MIN_EDGE = 0.02             # hold-rate edge
MIN_EDGE_ATR = 0.10         # ret and mae edges, in ATRs
SCREEN_CI = 0.90
HYPOTHESES = ("s1_ret21", "s1_ret63", "s1_strong_ret63", "s1_mae63", "r1_ret21", "r1_ret63",
              "s1_held", "r1_held")
N_HYPOTHESES = len(HYPOTHESES)
STRONG = ("strong", "very_strong")


# ----------------------------------------------------------------------------- trade
def trade(arr: tuple, t: int, lo: float, hi: float, side: str, atr: float, entry: int,
          hold: int) -> tuple[float, float]:
    """(ret, mae) in ATRs of a limit order at the near edge on the first touch in t+1..t+entry,
    held `hold` bars after the fill bar; (nan, nan) when not filled or the exit lies past the data."""
    o, high, low, close = arr
    sup = side == "support"
    fill_i = None
    for i in range(t + 1, min(len(high), t + 1 + entry)):
        if (high[i] < lo) if sup else (low[i] > hi):
            return math.nan, math.nan
        if low[i] <= hi and high[i] >= lo:
            fill_i = i
            break
    if fill_i is None or fill_i + hold >= len(close):
        return math.nan, math.nan
    px = min(o[fill_i], hi) if sup else max(o[fill_i], lo)
    out = close[fill_i + hold]
    cost = COST * (px + out)
    if sup:
        ret = out - px - cost
        mae = px - low[fill_i:fill_i + hold + 1].min()
    else:
        ret = px - out - cost
        mae = high[fill_i:fill_i + hold + 1].max() - px
    return ret / atr, max(mae, 0.0) / atr


# ----------------------------------------------------------------------------- walk
def _measure(bars, arr, t, lo, hi, side, atr, entry, holds) -> dict:
    m = {"outcome": B.outcome(bars, t, lo, hi, side, atr, entry)}
    for h in holds:
        m[f"ret{h}"], m[f"mae{h}"] = trade(arr, t, lo, hi, side, atr, entry, h)
    return m


def walk(bars: pd.DataFrame, step: int = 5, entry: int = 21, holds: tuple = (21, 63), symbol: str = "",
         windows: list[tuple[str, str]] = ()) -> pd.DataFrame:
    """One row per (as-of bar, zone) with its measures, the placebo measures and `excluded`."""
    atr_s = I.atr(bars).to_numpy(dtype=float)
    arr = tuple(bars[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
    close_a = arr[3]
    sma50 = bars["close"].rolling(50).mean().to_numpy()
    sma200 = bars["close"].rolling(200).mean().to_numpy()
    last = len(bars) - entry - max(holds) - 1
    asof, rows = [], []
    for t in range(NZ.LOOKBACK - 1, last + 1, step):
        atr, close = atr_s[t], close_a[t]
        if not (np.isfinite(atr) and atr > 0):
            continue
        zones = NZ.node_zones(bars.iloc[t + 1 - NZ.LOOKBACK:t + 1])
        asof.append((t, close, atr))
        if not (np.isfinite(sma50[t]) and np.isfinite(sma200[t])):
            direction = "n/a"
        elif close > sma50[t] > sma200[t]:
            direction = "Uptrend"
        elif close < sma50[t] < sma200[t]:
            direction = "Downtrend"
        else:
            direction = "Mixed"
        sup = sorted([z for z in zones if z["zone_high"] < close], key=lambda z: -z["zone_high"])[:PER_SIDE]
        res = sorted([z for z in zones if z["zone_low"] > close], key=lambda z: z["zone_low"])[:PER_SIDE]
        for side, picked in (("support", sup), ("resistance", res)):
            for rank, z in enumerate(picked, 1):
                lo, hi = z["zone_low"], z["zone_high"]
                near = hi if side == "support" else lo
                rows.append({"symbol": symbol, "date": bars.index[t], "t": t, "side": side, "rank": rank,
                             "grade": z["grade"], "density": z["density"], "direction": direction,
                             "lo_atr": (lo - close) / atr, "hi_atr": (hi - close) / atr,
                             "width_atr": (hi - lo) / atr, "dist_atr": abs(near - close) / atr,
                             "width_pct": (hi - lo) / close, "n_asof": len(asof) - 1,
                             **_measure(bars, arr, t, lo, hi, side, atr, entry, holds)})
    forward = math.ceil((entry + max(holds) + 1) / 2)
    excl = B.excluded_bars(bars.index, list(windows), forward)
    pool = B.placebo_pool(asof, excl)
    keys = ["outcome"] + [f"{k}{h}" for h in holds for k in ("ret", "mae")]
    for r in rows:
        r["excluded"] = bool(excl[r["t"]])
        ps = []
        for n2 in B.pool_offsets(pool, r["n_asof"]):
            t2, close2, atr2 = asof[n2]
            ps.append(_measure(bars, arr, t2, close2 + r["lo_atr"] * atr2, close2 + r["hi_atr"] * atr2,
                               r["side"], atr2, entry, holds))
        r["placebo"] = [p["outcome"] for p in ps]
        for k in keys[1:]:
            r[f"p_{k}"] = [p[k] for p in ps]
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- statistics
def _sums(g: pd.DataFrame, col: str, sign: float) -> np.ndarray:
    """[sum, count] of the zone values and of the placebo values of `col` (times sign)."""
    x = sign * g[col].to_numpy(dtype=float)
    p = sign * np.array([v for ps in g[f"p_{col}"] for v in ps], dtype=float)
    return np.array([np.nansum(x), np.isfinite(x).sum(), np.nansum(p), np.isfinite(p).sum()])


def _mean_edge(c: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return c[..., 0] / c[..., 1] - c[..., 2] / c[..., 3]


def mean_interval(g: pd.DataFrame, col: str, sign: float = 1.0, ci: float = B.BOOT_CI,
                  reps: int = B.BOOT_REPS, seed: int = B.BOOT_SEED) -> tuple[float, float]:
    """Cluster-bootstrap interval of mean(zone) - mean(placebo) over (symbol, year) blocks."""
    keys = g["symbol"].astype(str) + ":" + pd.DatetimeIndex(g["date"]).year.astype(str)
    blocks = np.array([_sums(b, col, sign) for _, b in g.groupby(keys.to_numpy(), sort=True)])
    if len(blocks) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    e = _mean_edge(blocks[rng.integers(0, len(blocks), size=(reps, len(blocks)))].sum(axis=1))
    e = e[np.isfinite(e)]
    if not len(e):
        return np.nan, np.nan
    a = (1 - ci) / 2
    return float(np.quantile(e, a)), float(np.quantile(e, 1 - a))


def trade_stats(g: pd.DataFrame, h: int, ci: float = B.BOOT_CI) -> dict:
    """Trades, mean ret and mae with their placebo values and edges (mae edge = placebo depth -
    zone depth), and the bootstrap intervals."""
    r, m = f"ret{h}", f"mae{h}"
    s, sm = _sums(g, r, 1.0), _sums(g, m, -1.0)
    lo, hi = mean_interval(g, r, 1.0, ci)
    mlo, mhi = mean_interval(g, m, -1.0, ci)
    return {"trades": int(s[1]), "ret": s[0] / s[1] if s[1] else np.nan,
            "placebo_ret": s[2] / s[3] if s[3] else np.nan, "ret_edge": float(_mean_edge(s)),
            "ret_lo": lo, "ret_hi": hi, "win": float((g[r] > 0).sum() / s[1]) if s[1] else np.nan,
            "mae": -sm[0] / sm[1] if sm[1] else np.nan, "placebo_mae": -sm[2] / sm[3] if sm[3] else np.nan,
            "mae_edge": float(_mean_edge(sm)), "mae_lo": mlo, "mae_hi": mhi,
            "width_atr": g["width_atr"].median(), "dist_atr": g["dist_atr"].median()}


def hypothesis_rows(rows: pd.DataFrame) -> dict[str, tuple[pd.DataFrame, str, str]]:
    """name -> (rows, measure, column): measure 'held', 'ret' or 'mae'."""
    s1 = rows[(rows["side"] == "support") & (rows["rank"] == 1)]
    r1 = rows[(rows["side"] == "resistance") & (rows["rank"] == 1)]
    return {"s1_ret21": (s1, "ret", "ret21"), "s1_ret63": (s1, "ret", "ret63"),
            "s1_strong_ret63": (s1[s1["grade"].isin(STRONG)], "ret", "ret63"),
            "s1_mae63": (s1, "mae", "mae63"), "r1_ret21": (r1, "ret", "ret21"),
            "r1_ret63": (r1, "ret", "ret63"), "s1_held": (s1, "held", "outcome"),
            "r1_held": (r1, "held", "outcome")}


def _edge_of(g: pd.DataFrame, measure: str, col: str, ci: float) -> tuple[float, float, float, int]:
    """(edge, lo, hi, n measured) of one hypothesis."""
    if measure == "held":
        st = B._stats(g, interval=False)
        lo, hi = B.edge_interval(g, ci=ci)
        return st["edge"], lo, hi, st["resolved"]
    sign = 1.0 if measure == "ret" else -1.0
    s = _sums(g, col, sign)
    lo, hi = mean_interval(g, col, sign, ci)
    return float(_mean_edge(s)), lo, hi, int(s[1])


def verdict(rows: pd.DataFrame) -> pd.DataFrame:
    bonf = 1 - 0.10 / N_HYPOTHESES
    out = {}
    for name, (g, measure, col) in hypothesis_rows(rows).items():
        edge, lo, hi, n = _edge_of(g, measure, col, SCREEN_CI)
        _, blo, _, _ = _edge_of(g, measure, col, bonf)
        per = {}
        if "period" in g:
            for p, gp in g.groupby("period"):
                per[f"edge_{p}"] = _edge_of(gp, measure, col, SCREEN_CI)[0] if len(gp) else np.nan
        need = MIN_EDGE if measure == "held" else MIN_EDGE_ATR
        screen = bool(edge >= need and lo > 0 and all(v > 0 for v in per.values()))
        out[name] = {"measured": n, "edge": edge, "edge_lo": lo, "edge_hi": hi, "bonf_lo": blo, **per,
                     "screen": "yes" if screen else "no", "bonferroni": "yes" if screen and blo > 0 else "no"}
    return pd.DataFrame(out).T


# ----------------------------------------------------------------------------- report
def _fmt_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    head = "| | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"

    def f(v):
        if isinstance(v, str):
            return v
        if v is None or not np.isfinite(v):
            return ""
        return f"{v:.0f}" if float(v).is_integer() and abs(v) >= 10 else f"{v:.3f}"
    return head + "".join(f"| {k} | " + " | ".join(f(r[c]) for c in cols) + " |\n" for k, r in df.iterrows())


def trade_table(rows: pd.DataFrame, by: str, holds: tuple) -> str:
    out = []
    for h in holds:
        t = {str(k): trade_stats(g, h) for k, g in rows.groupby(by, sort=True)}
        t["all"] = trade_stats(rows, h)
        out += [f"Hold {h} bars:", "", _fmt_table(pd.DataFrame(t).T), ""]
    return "\n".join(out)


def report(rows: pd.DataFrame, meta: dict, holds: tuple) -> str:
    out = ["# Long-window node zones: position-holder test", ""] + [f"- {k}: {v}" for k, v in meta.items()]
    out += ["", f"Edges: zone - moved-zone placebo. ret in ATRs per trade after costs (shorts at resistance "
            f"count the fall); mae edge = placebo depth - zone depth. Intervals: {B.BOOT_CI:.0%} cluster "
            f"bootstrap over (symbol, year) blocks; bonf_lo at {1 - 0.10 / N_HYPOTHESES:.2%}.", "",
            "## Verdict, main sample", "", _fmt_table(verdict(rows)), ""]
    key = rows.assign(key=np.where(rows["side"] == "support", "S", "R") + rows["rank"].astype(str))
    out += ["## Trades by zone (S1, S2, R1, R2)", "", trade_table(key, "key", holds)]
    out += ["## Hold rate by zone", "", _fmt_table(B.summarize(key, by="key")[
        ["n", "resolved", "hold_rate", "placebo_hold", "edge", "edge_lo", "edge_hi", "width_atr", "dist_atr"]]), ""]
    for side, label in (("support", "S1"), ("resistance", "R1")):
        g = key[key["key"] == label]
        g = g.assign(grade=pd.Categorical(g["grade"], NZ.GRADES, ordered=True))
        out += [f"## {label} by grade", "", trade_table(g, "grade", holds)]
        out += [f"## {label} by daily direction", "", trade_table(g, "direction", holds)]
        if "period" in g:
            out += [f"## {label} by period", "", trade_table(g, "period", holds)]
    return "\n".join(out)


def _run(args: tuple) -> pd.DataFrame:
    path, step, entry, holds, windows = args
    return walk(B.load_csv(path), step, entry, holds, symbol=Path(path).stem.upper(), windows=windows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--entry", type=int, default=21)
    ap.add_argument("--holds", type=int, nargs="+", default=[21, 63])
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="START:END")
    ap.add_argument("--exclusions", help="exclusions.csv from clean_data.py")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    holds = tuple(a.holds)
    if not {21, 63} <= set(holds):
        ap.error("--holds must include 21 and 63 (the pre-registered hypotheses use them)")
    wins = B.file_windows(a.csv, a.exclude, a.exclusions)
    tasks = [(p, a.step, a.entry, holds, w) for p, w in zip(a.csv, wins)]
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
    print(verdict(main_rows).to_string())
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        flat = rows.drop(columns=["n_asof"]).copy()
        for c in [c for c in flat.columns if c == "placebo" or c.startswith("p_")]:
            flat[c] = flat[c].map(lambda ps: ";".join(f"{x:.4f}" if isinstance(x, float) else str(x) for x in ps))
        flat.to_csv(out / "rows.csv.gz", index=False)
        meta = {"step": a.step, "entry": a.entry, "holds": " ".join(map(str, holds)), "files": len(a.csv),
                "lookback": NZ.LOOKBACK, "bin_share": NZ.BIN_SHARE, "split": ", ".join(a.split) or "none",
                "excluded": B.describe_windows(a.exclude, a.exclusions),
                "rows": f"{len(rows)} ({int(rows['excluded'].sum())} excluded)"}
        (out / "report.md").write_text(report(main_rows, meta, holds), encoding="utf-8")
        print(f"wrote {out / 'rows.csv.gz'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
