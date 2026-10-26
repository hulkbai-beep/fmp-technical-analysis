#!/usr/bin/env python3
"""
Do the key levels hold better in some market states? Re-run of the levels walk-forward test, split
by the state at the build bar, with a placebo drawn from the same state.

  python scripts/regime_split.py BARS.csv [BARS.csv ...] [--step 5] [--forward 20]
                                 [--split 2017-01-01 2022-01-01] [--exclude START:END ...]
                                 [--jobs 4] [--out DIR]

Rows come from levels_backtest.walk (S1-S3, R1-R3, outcome as defined there). Each build bar gets
four state variables, all from bars <= it:
  - adx: ADX(14) below 20 / 20-30 / 30 and above;
  - er: Kaufman efficiency ratio over ER_BARS bars, |close_t - close_t-n| / sum |daily change|,
    cut at ER_CUTS (0 = pure chop, 1 = straight line);
  - vr: Lo-MacKinlay variance ratio VR(VR_Q) of the last VR_BARS daily log returns, cut at VR_CUTS
    (below 1 = mean reverting);
  - vol: ATR % percentile within the trailing year (indicators.atr_pct_pctl), terciles.
A state change also changes the hold rate of any zone (ranges hold more, wherever the zone is), so
the plain placebo would credit the state to the level. Here each row's placebo is scored on as-of
bars of the same symbol in the same state bucket (PLACEBO_OFFSETS steps along that bucket's own
sequence), so edge = what the level's location adds within the state.

--exclude drops a window (e.g. 2020-02-20:2020-04-30): a row is dropped when any bar from its build
bar to the last bar its outcome can use (2 x forward - 1 bars later) falls inside, and such as-of
bars are never used as placebos. The report gives both the excluded and the full sample.

Pre-registered reading (written before the run): a state passes when its bucket's edge interval lies
above 0 on the main sample (windows excluded) and its edge is positive in each period. The buckets
tested are er low, adx < 20, vr < 0.9 (range-like) and vol high; the other buckets are shown for
context only.
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

ER_BARS = 20
ER_CUTS = (0.15, 0.35)
VR_Q = 5
VR_BARS = 250
VR_CUTS = (0.9, 1.1)
ADX_CUTS = (20.0, 30.0)
VOL_CUTS = (100 / 3, 200 / 3)
VARIABLES = ("adx", "er", "vr", "vol")
LABELS = {"adx": ("adx<20", "adx 20-30", "adx>=30"), "er": ("er low", "er mid", "er high"),
          "vr": ("vr<0.9", "vr 0.9-1.1", "vr>1.1"), "vol": ("vol low", "vol mid", "vol high")}
TESTED = {"adx": "adx<20", "er": "er low", "vr": "vr<0.9", "vol": "vol high"}


def efficiency_ratio(close: pd.Series, n: int = ER_BARS) -> pd.Series:
    """|close_t - close_t-n| / sum of |daily change| over the n bars; NaN when the path is flat."""
    path = close.diff().abs().rolling(n, min_periods=n).sum()
    return (close - close.shift(n)).abs() / path.where(path > 0)


def variance_ratio(close: pd.Series, q: int = VR_Q, n: int = VR_BARS) -> pd.Series:
    """Var of overlapping q-day log returns / (q x var of daily log returns), over the last n daily
    returns; 1 for a random walk."""
    r = np.log(close).diff()
    rq = r.rolling(q, min_periods=q).sum()
    v1 = r.rolling(n, min_periods=n).var()
    vq = rq.rolling(n - q + 1, min_periods=n - q + 1).var()
    return vq / (q * v1)


def bucket(x: float, cuts: tuple[float, float], labels: tuple[str, str, str]) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return labels[0] if x < cuts[0] else labels[1] if x < cuts[1] else labels[2]


def states(bars: pd.DataFrame) -> pd.DataFrame:
    """State bucket per bar for each variable in VARIABLES."""
    ind = I.compute_all(bars)
    raw = {"adx": (ind["adx14"], ADX_CUTS), "er": (efficiency_ratio(bars["close"]), ER_CUTS),
           "vr": (variance_ratio(bars["close"]), VR_CUTS), "vol": (ind["atr_pct_pctl"], VOL_CUTS)}
    out = pd.DataFrame(index=bars.index)
    for v, (s, cuts) in raw.items():
        out[v] = [bucket(x, cuts, LABELS[v]) for x in s.to_numpy(dtype=float)]
        out[v + "_value"] = s.to_numpy(dtype=float)
    return out


def excluded_bars(index: pd.DatetimeIndex, windows: list[tuple[str, str]], forward: int) -> np.ndarray:
    """True for build bars whose outcome span (the bar itself to 2 x forward - 1 bars later) touches
    any window."""
    inside = np.zeros(len(index), dtype=bool)
    for a, b in windows:
        inside |= (index >= pd.Timestamp(a)) & (index <= pd.Timestamp(b))
    span = 2 * forward
    hit = np.convolve(inside.astype(int), np.ones(span, dtype=int))[span - 1:span - 1 + len(index)]
    return hit > 0          # hit[t] counts inside bars in t..t+span-1


def matched_placebo(rows: pd.DataFrame, bars: pd.DataFrame, asof: pd.DataFrame, key: str, forward: int) -> list:
    """Placebo outcomes per row, from as-of bars of the same bucket `key` that are not excluded."""
    seqs = {k: g.index.to_numpy() for k, g in asof[~asof["excluded"]].groupby(key)}
    pos = {k: {n: i for i, n in enumerate(s)} for k, s in seqs.items()}
    out = []
    for r in rows.itertuples():
        s = seqs.get(getattr(r, key))
        p = pos.get(getattr(r, key), {}).get(r.n_asof)
        if s is None or p is None or len(s) < 2:
            out.append([])
            continue
        res = []
        for k in B.PLACEBO_OFFSETS:
            n2 = s[(p + k) % len(s)]
            if n2 == r.n_asof:
                continue
            t2, c2, a2 = asof.at[n2, "t"], asof.at[n2, "close"], asof.at[n2, "atr"]
            res.append(B.outcome(bars, int(t2), c2 + r.lo_atr * a2, c2 + r.hi_atr * a2, r.side, a2, forward))
        out.append(res)
    return out


def walk(bars: pd.DataFrame, step: int = 5, forward: int = 20, symbol: str = "",
         windows: list[tuple[str, str]] = ()) -> pd.DataFrame:
    """levels_backtest.walk rows plus state buckets, an `excluded` flag and, per variable, the
    same-state placebo (column placebo_<var>); `placebo` is the plain placebo over non-excluded bars."""
    rows = B.walk(bars, "swing", step, forward, symbol=symbol)
    if rows.empty:
        return rows
    st = states(bars)
    excl = excluded_bars(bars.index, list(windows), forward)
    ind = I.compute_all(bars)
    asof = rows.groupby("n_asof")["t"].first().to_frame()
    asof["close"] = bars["close"].to_numpy()[asof["t"]]
    asof["atr"] = ind["atr14"].to_numpy()[asof["t"]]
    asof["excluded"] = excl[asof["t"]]
    asof["all"] = "all"
    for v in VARIABLES:
        asof[v] = st[v].to_numpy()[asof["t"]]
        rows[v] = st[v].to_numpy()[rows["t"]]
        rows[v + "_value"] = st[v + "_value"].to_numpy()[rows["t"]]
    rows["excluded"] = excl[rows["t"]]
    rows["all"] = "all"
    rows["placebo"] = matched_placebo(rows, bars, asof, "all", forward)
    for v in VARIABLES:
        rows["placebo_" + v] = matched_placebo(rows, bars, asof, v, forward)
    return rows


def _run(args: tuple) -> pd.DataFrame:
    path, step, forward, windows = args
    return walk(B.load_csv(path), step, forward, symbol=Path(path).stem.upper(), windows=windows)


def _table(df: pd.DataFrame) -> str:
    cols = ["n", "resolved", "hold_rate", "placebo_hold", "edge", "edge_lo", "edge_hi"]
    df = df[cols].astype(float)
    head = "| | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"

    def fmt(c, v):
        return "" if not np.isfinite(v) else f"{v:.0f}" if c in ("n", "resolved") else f"{v:.3f}"
    return head + "".join(f"| {k} | " + " | ".join(fmt(c, r[c]) for c in cols) + " |\n" for k, r in df.iterrows())


def by_state(rows: pd.DataFrame, v: str) -> pd.DataFrame:
    """B.summarize per bucket of v, with the same-state placebo."""
    g = rows[rows[v] != "n/a"].assign(placebo=rows["placebo_" + v])
    return B.summarize(g, by=v)


def verdict(rows: pd.DataFrame) -> pd.DataFrame:
    """Per tested bucket: edge and interval on the sample, edge per period, pass/fail."""
    out = {}
    for v, b in TESTED.items():
        g = rows[rows[v] == b].assign(placebo=rows["placebo_" + v])
        s = B._stats(g)
        rec = {"n": s["n"], "edge": s["edge"], "edge_lo": s["edge_lo"], "edge_hi": s["edge_hi"]}
        per = {}
        if "period" in g:
            for p, gp in g.groupby("period"):
                per[p] = B._stats(gp, interval=False)["edge"]
        rec.update({f"edge_{p}": e for p, e in per.items()})
        rec["pass"] = bool(np.isfinite(s["edge_lo"]) and s["edge_lo"] > 0 and all(e > 0 for e in per.values()))
        out[f"{v}: {b}"] = rec
    return pd.DataFrame(out).T


def report(rows: pd.DataFrame, meta: dict) -> str:
    main = rows[~rows["excluded"]]
    out = ["# Levels by market state", ""] + [f"- {k}: {v}" for k, v in meta.items()]
    out += ["", f"Edge interval: {B.BOOT_CI:.0%} cluster bootstrap over (symbol, year) blocks. "
            "Placebo: same symbol, same state bucket.", ""]
    for name, g in (("main sample (windows excluded)", main), ("full sample", rows)):
        v = verdict(g)
        cols = [c for c in v.columns if c != "pass"]
        head = "| | " + " | ".join(cols) + " | pass |\n|" + "---|" * (len(cols) + 2) + "\n"
        body = "".join(f"| {k} | " + " | ".join("" if not np.isfinite(float(r[c])) else
                                                 (f"{float(r[c]):.0f}" if c == "n" else f"{float(r[c]):.3f}")
                                                 for c in cols) + f" | {'yes' if r['pass'] else 'no'} |\n"
                       for k, r in v.iterrows())
        out += [f"## Pre-registered buckets: {name}", "", head + body, ""]
    out += ["## Plain placebo, all rows (main sample)", "", _table(B.summarize(main.assign(rank_all="all"),
                                                                                 by="rank_all")), ""]
    for v in VARIABLES:
        out += [f"## By {v} (main sample)", "", _table(by_state(main, v)), ""]
        if "period" in main:
            for p, g in main.groupby("period"):
                out += [f"### By {v}, {p}", "", _table(by_state(g, v)), ""]
    return "\n".join(out)


def parse_windows(items: list[str]) -> list[tuple[str, str]]:
    out = []
    for s in items:
        a, _, b = s.partition(":")
        pd.Timestamp(a), pd.Timestamp(b)
        out.append((a, b))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--forward", type=int, default=20)
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="START:END")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    windows = parse_windows(a.exclude)
    tasks = [(p, a.step, a.forward, windows) for p in a.csv]
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
    print(verdict(rows[~rows["excluded"]]).to_string())
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        pcols = ["placebo"] + ["placebo_" + v for v in VARIABLES]
        rows.assign(**{c: rows[c].map(lambda ps: ";".join(str(x) for x in ps)) for c in pcols}) \
            .drop(columns=["n_asof", "all"]).to_csv(out / "rows.csv.gz", index=False)
        meta = {"step": a.step, "forward": a.forward, "files": len(a.csv),
                "split": ", ".join(a.split) or "none",
                "excluded": ", ".join(f"{x}..{y}" for x, y in windows) or "none",
                "rows": f"{len(rows)} ({int(rows['excluded'].sum())} excluded)",
                "cuts": f"adx {ADX_CUTS}, er{ER_BARS} {ER_CUTS}, vr({VR_Q}, {VR_BARS}) {VR_CUTS}, "
                        f"vol pctl terciles"}
        (out / "report.md").write_text(report(rows, meta), encoding="utf-8")
        print(f"wrote {out / 'rows.csv.gz'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
