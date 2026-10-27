#!/usr/bin/env python3
"""
Buying in tranches: do limit orders at the node zones (node_zones.py) give a lower average cost
than the same ladder at other prices, a fixed ladder, or buying on a schedule?

  python scripts/tranche_backtest.py BARS.csv [BARS.csv ...] [--step 5] [--window 63]
                                     [--split 2017-01-01 2022-01-01] [--exclude START:END ...]
                                     [--exclusions FILE] [--jobs 4] [--out DIR]

CSV format as for levels_backtest.py (daily bars). Every `step` bars t, from the last
node_zones.LOOKBACK bars <= t only, a buyer splits the money into three equal parts. The first is
bought at the close of t; the other two follow one of these plans over the next `window` bars:

  node: limit orders at the top of the nearest support zone (S1) and the next one (S2), zones
      wholly below the close; dates with fewer than two such zones are left out of every plan;
  node_mid: the same at the zones' midpoints (context);
  pct: limits 5% and 10% below the close;
  atr: limits 2 and 4 ATRs below the close;
  dca: at the closes `window` / 3 and 2 x `window` / 3 bars after t;
  lump: all three parts at the close of t.

A limit fills on the first bar whose low reaches it, at the lower of the bar's open and the limit.
A part still unfilled after `window` bars is bought at the close of bar t + `window`, so every plan
ends fully invested on the same bar and sells at the same price later: comparing plans is comparing
average costs. gain = log(close of t / average cost) in percent (positive: cheaper than buying it all
at t), average cost = the parts' harmonic mean (equal money per part). Costs are left out (the same
three buys for every plan).

The placebo keeps the node ladder's two limits as offsets from the close in ATRs and moves them to
other as-of bars of the same series (levels_backtest.PLACEBO_OFFSETS steps on, never excluded ones).

Pre-registered hypotheses (written before the first run):
  node_vs_placebo: mean node gain - mean placebo gain;
  node_vs_atr, node_vs_pct: the mean of node gain - atr (pct) gain on the same dates.
Each passes the screen when it is at least MIN_EDGE_PCT percent, its 90% cluster-bootstrap interval
over 3-month calendar blocks (BLOCK_MONTHS; (symbol, year) blocks before a review) lies above 0 and it is positive in each period (--split); it passes the
multiple-testing gate when its (1 - 0.10 / 3) interval lies above 0. The other comparisons
(dca, lump, node_mid, fill rates) are context.

In-sample, on split-adjusted daily bars.
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

PCT_LADDER = (0.05, 0.10)
ATR_LADDER = (2.0, 4.0)
MIN_EDGE_PCT = 0.25
BLOCK_MONTHS = 3            # bootstrap blocks: each row's window is 63 bars, about 3 months
HYPOTHESES = ("node_vs_placebo", "node_vs_atr", "node_vs_pct")
PLANS = ("node", "node_mid", "pct", "atr", "dca", "lump")


def ladder_cost(arr: tuple, t: int, limits: list[float], window: int) -> tuple[float, int]:
    """(average cost, limits filled) of one part at the close of t and one limit order per price
    in `limits` over t+1..t+window, unfilled parts bought at the close of t+window."""
    o, high, low, close = arr
    end = t + window
    prices, filled = [close[t]], 0
    for lim in limits:
        px = close[end]
        for i in range(t + 1, end + 1):
            if low[i] <= lim:
                px, filled = min(o[i], lim), filled + 1
                break
        prices.append(px)
    return len(prices) / sum(1.0 / p for p in prices), filled


def gain(close_t: float, cost: float) -> float:
    return 100.0 * math.log(close_t / cost)


def walk(bars: pd.DataFrame, step: int = 5, window: int = 63, symbol: str = "",
         windows: list[tuple[str, str]] = ()) -> pd.DataFrame:
    """One row per as-of bar with two node supports: gains and fills of every plan, the placebo
    gains of the node ladder and the `excluded` flag."""
    atr_s = I.atr(bars).to_numpy(dtype=float)
    arr = tuple(bars[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
    close_a = arr[3]
    third = window // 3
    asof, rows = [], []
    for t in range(NZ.LOOKBACK - 1, len(bars) - window - 1, step):
        atr, c = atr_s[t], close_a[t]
        if not (np.isfinite(atr) and atr > 0):
            continue
        asof.append((t, c, atr))
        sup = sorted([z for z in NZ.node_zones(bars.iloc[t + 1 - NZ.LOOKBACK:t + 1]) if z["zone_high"] < c],
                     key=lambda z: -z["zone_high"])[:2]
        if len(sup) < 2:
            continue
        node = [z["zone_high"] for z in sup]
        plans = {"node": node, "node_mid": [(z["zone_low"] + z["zone_high"]) / 2 for z in sup],
                 "pct": [c * (1 - p) for p in PCT_LADDER], "atr": [c - k * atr for k in ATR_LADDER]}
        r = {"symbol": symbol, "date": bars.index[t], "t": t, "n_asof": len(asof) - 1,
             "off_atr": [(p - c) / atr for p in node], "dist1_pct": 100 * (1 - node[0] / c),
             "dist2_pct": 100 * (1 - node[1] / c), "grade1": sup[0]["grade"]}
        for name, lims in plans.items():
            cost, filled = ladder_cost(arr, t, lims, window)
            r[f"gain_{name}"], r[f"fill_{name}"] = gain(c, cost), filled
        dca = [close_a[t], close_a[t + third], close_a[t + 2 * third]]
        r["gain_dca"], r["fill_dca"] = gain(c, 3 / sum(1 / p for p in dca)), 2
        r["gain_lump"], r["fill_lump"] = 0.0, 0
        r["ret_lump"] = 100.0 * math.log(close_a[t + window] / c)
        rows.append(r)
    excl = B.excluded_bars(bars.index, list(windows), math.ceil((window + 1) / 2))
    pool = B.placebo_pool(asof, excl)
    for r in rows:
        r["excluded"] = bool(excl[r["t"]])
        ps = []
        for n2 in B.pool_offsets(pool, r["n_asof"]):
            t2, c2, atr2 = asof[n2]
            cost, _ = ladder_cost(arr, t2, [c2 + k * atr2 for k in r["off_atr"]], window)
            ps.append(gain(c2, cost))
        r["p_gain_node"] = ps
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- statistics
def _blocks(g: pd.DataFrame) -> np.ndarray:
    return B.block_keys(g, BLOCK_MONTHS)


def _boot(blocks: np.ndarray, stat, ci: float) -> tuple[float, float]:
    if len(blocks) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(B.BOOT_SEED)
    e = stat(blocks[rng.integers(0, len(blocks), size=(B.BOOT_REPS, len(blocks)))].sum(axis=1))
    e = e[np.isfinite(e)]
    a = (1 - ci) / 2
    return (float(np.quantile(e, a)), float(np.quantile(e, 1 - a))) if len(e) else (np.nan, np.nan)


def paired_edge(g: pd.DataFrame, a: str, b: str, ci: float = B.BOOT_CI) -> tuple[float, float, float]:
    """(mean of a - b, interval) over the rows of g."""
    d = (g[a] - g[b]).to_numpy(dtype=float)
    s = pd.Series(d).groupby(_blocks(g)).agg(["sum", "count"])
    sums = s.to_numpy(dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        lo, hi = _boot(sums, lambda s: s[..., 0] / s[..., 1], ci)
    return float(d.mean()) if len(d) else np.nan, lo, hi


def placebo_edge(g: pd.DataFrame, ci: float = B.BOOT_CI) -> tuple[float, float, float]:
    """(mean node gain - mean placebo gain, interval)."""
    keys = _blocks(g)
    rows = []
    for k in np.unique(keys):
        b = g[keys == k]
        p = np.array([v for ps in b["p_gain_node"] for v in ps], dtype=float)
        rows.append([b["gain_node"].sum(), len(b), p.sum(), len(p)])
    s = np.array(rows)
    with np.errstate(invalid="ignore", divide="ignore"):
        stat = lambda x: x[..., 0] / x[..., 1] - x[..., 2] / x[..., 3]  # noqa: E731
        lo, hi = _boot(s, stat, ci)
        return float(stat(s.sum(axis=0))), lo, hi


def _edge(g: pd.DataFrame, name: str, ci: float) -> tuple[float, float, float]:
    if name == "node_vs_placebo":
        return placebo_edge(g, ci)
    return paired_edge(g, "gain_node", "gain_" + name.split("_vs_")[1], ci)


def verdict(rows: pd.DataFrame) -> pd.DataFrame:
    bonf = 1 - 0.10 / len(HYPOTHESES)
    out = {}
    for name in HYPOTHESES:
        e, lo, hi = _edge(rows, name, B.BOOT_CI)
        blo = _edge(rows, name, bonf)[1]
        per = {f"edge_{p}": _edge(gp, name, B.BOOT_CI)[0] for p, gp in rows.groupby("period")} \
            if "period" in rows else {}
        screen = bool(e >= MIN_EDGE_PCT and lo > 0 and all(v > 0 for v in per.values()))
        out[name] = {"rows": len(rows), "edge_pct": e, "edge_lo": lo, "edge_hi": hi, "bonf_lo": blo, **per,
                     "screen": "yes" if screen else "no", "bonferroni": "yes" if screen and blo > 0 else "no"}
    return pd.DataFrame(out).T


def plan_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Per plan: mean and median gain, share of dates cheaper than buying it all at t, 10th
    percentile, mean limits filled, and the mean difference to the node plan with its interval."""
    out = {}
    for p in PLANS:
        g = rows[f"gain_{p}"]
        d, lo, hi = paired_edge(rows, "gain_node", f"gain_{p}") if p != "node" else (np.nan, np.nan, np.nan)
        out[p] = {"mean_gain": g.mean(), "median_gain": g.median(), "beats_lump": (g > 0).mean(),
                  "p10_gain": g.quantile(0.10), "fills": rows[f"fill_{p}"].mean(),
                  "node_minus": d, "node_minus_lo": lo, "node_minus_hi": hi}
    return pd.DataFrame(out).T


def _fmt(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    head = "| | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"

    def f(v):
        if isinstance(v, str):
            return v
        return "" if v is None or not np.isfinite(v) else f"{v:.0f}" if abs(v) >= 1000 else f"{v:.3f}"
    return head + "".join(f"| {k} | " + " | ".join(f(r[c]) for c in cols) + " |\n" for k, r in df.iterrows())


def report(rows: pd.DataFrame, meta: dict) -> str:
    out = ["# Tranche buying at node zones", ""] + [f"- {k}: {v}" for k, v in meta.items()]
    out += ["", "gain = log(close at t / average cost) in percent; positive = cheaper than buying all at t. "
            f"Intervals: {B.BOOT_CI:.0%} cluster bootstrap over {B.blocks_text(BLOCK_MONTHS)}; bonf_lo at "
            f"{1 - 0.10 / len(HYPOTHESES):.2%}.", "", "## Verdict, main sample", "", _fmt(verdict(rows)), "",
            "## Plans, main sample", "", _fmt(plan_table(rows)), "",
            f"Buying it all at t returned {rows['ret_lump'].mean():.2f}% on average over the window "
            f"(median {rows['ret_lump'].median():.2f}%). Node limits: S1 median {rows['dist1_pct'].median():.1f}% "
            f"and S2 {rows['dist2_pct'].median():.1f}% below the close.", ""]
    if "period" in rows:
        for p, g in rows.groupby("period"):
            out += [f"## Plans, {p}", "", _fmt(plan_table(g)), ""]
    up = rows["ret_lump"] > 0
    for label, g in (("price higher after the window", rows[up]), ("price lower after the window", rows[~up])):
        out += [f"## Plans, {label} ({len(g)} dates)", "", _fmt(plan_table(g)), ""]
    return "\n".join(out)


def _run(args: tuple) -> pd.DataFrame:
    path, step, window, windows = args
    return walk(B.load_csv(path), step, window, symbol=Path(path).stem.upper(), windows=windows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--window", type=int, default=63)
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="START:END")
    ap.add_argument("--exclusions", help="exclusions.csv from clean_data.py")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    wins = B.file_windows(a.csv, a.exclude, a.exclusions)
    tasks = [(p, a.step, a.window, w) for p, w in zip(a.csv, wins)]
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
    print(plan_table(main_rows).round(3).to_string())
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        flat = rows.drop(columns=["n_asof"]).copy()
        for c in ("off_atr", "p_gain_node"):
            flat[c] = flat[c].map(lambda xs: ";".join(f"{x:.4f}" for x in xs))
        flat.to_csv(out / "rows.csv.gz", index=False)
        meta = {"step": a.step, "window": a.window, "files": len(a.csv), "lookback": NZ.LOOKBACK,
                "pct ladder": PCT_LADDER, "atr ladder": ATR_LADDER, "split": ", ".join(a.split) or "none",
                "excluded": B.describe_windows(a.exclude, a.exclusions),
                "rows": f"{len(rows)} ({int(rows['excluded'].sum())} excluded)"}
        (out / "report.md").write_text(report(main_rows, meta), encoding="utf-8")
        print(f"wrote {out / 'rows.csv.gz'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
