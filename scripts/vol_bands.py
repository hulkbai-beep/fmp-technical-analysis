#!/usr/bin/env python3
"""
How well calibrated are volatility ranges? Coverage of close +- k x sigma x sqrt(h) bands.

  python scripts/vol_bands.py BARS.csv [BARS.csv ...] [--step 5] [--horizons 5 10 20]
                              [--split 2017-01-01 2022-01-01] [--exclude START:END ...]
                              [--jobs 4] [--out DIR]

Every `step` bars, from bars <= t only, each estimator gives a daily scale u_t:
  - rv20: the skill's base case (scorecard.scenarios): std of 20 daily log returns, the same as
    indicators.realized_vol / sqrt(252);
  - rv60: the same over 60 returns;
  - ewma: RiskMetrics EWMA of squared daily log returns, lambda EWMA_LAMBDA;
  - atr: ATR(14) / close, so k = 1 means a band of one ATR x sqrt(h).
For each horizon h, z = log(close_t+h / close_t) / (u_t sqrt(h)). Reported per estimator and h:
  - cover1, cover2: share with |z| <= 1 and <= 2 (a normal model says 68.3% and 95.4%);
  - k68, k95: the multiple of u sqrt(h) that would have covered 68.3% / 95.4% (1 and 2 if calibrated);
  - touch_dn1, touch_up1: share of paths whose low (high) reached close x exp(-+ u sqrt(h)) within the
    h bars, i.e. how often a 1-unit stop is hit (Brownian motion without drift: about 31.7%).
Tables per period, per ATR % percentile tercile at t, and with and without the --exclude windows (a
start bar is excluded when any bar t..t+h falls in a window). Not a trading rule: it measures the
range estimate the report prints, and how wide a stop has to be for a given hit rate.
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
import regime_split as RS

EWMA_LAMBDA = 0.94
ESTIMATORS = ("rv20", "rv60", "ewma", "atr")
Q68, Q95 = 0.6827, 0.9545


def scales(bars: pd.DataFrame) -> pd.DataFrame:
    """Daily scale per estimator, as of each bar."""
    r = np.log(bars["close"]).diff()
    ind = I.compute_all(bars)
    ew = (r ** 2).ewm(alpha=1 - EWMA_LAMBDA, adjust=False, min_periods=60).mean() ** 0.5
    return pd.DataFrame({"rv20": r.rolling(20, min_periods=20).std(ddof=1),
                         "rv60": r.rolling(60, min_periods=60).std(ddof=1),
                         "ewma": ew, "atr": ind["atr14"] / bars["close"],
                         "vol": [RS.bucket(x, RS.VOL_CUTS, RS.LABELS["vol"])
                                 for x in ind["atr_pct_pctl"].to_numpy(dtype=float)]}, index=bars.index)


def walk(bars: pd.DataFrame, step: int = 5, horizons=(5, 10, 20), symbol: str = "",
         windows=()) -> pd.DataFrame:
    """One row per (start bar, horizon, estimator): z, and whether a 1-unit band was touched below
    and above within the horizon."""
    sc = scales(bars)
    c = bars["close"].to_numpy(dtype=float)
    hi = bars["high"].to_numpy(dtype=float)
    lo = bars["low"].to_numpy(dtype=float)
    idx = bars.index
    rows = []
    start = 260
    for h in horizons:
        excl = RS.excluded_bars(idx, list(windows), (h + 1 + 1) // 2)   # span 2*((h+2)//2) >= h + 1 bars
        for t in range(start, len(bars) - h, step):
            lr = np.log(c[t + h] / c[t])
            pmax = np.log(hi[t + 1:t + h + 1].max() / c[t])
            pmin = np.log(lo[t + 1:t + h + 1].min() / c[t])
            for e in ESTIMATORS:
                u = sc[e].iat[t]
                if not (np.isfinite(u) and u > 0):
                    continue
                w = u * np.sqrt(h)
                rows.append((symbol, idx[t], h, e, lr / w, pmin <= -w, pmax >= w, bool(excl[t]), sc["vol"].iat[t]))
    return pd.DataFrame(rows, columns=["symbol", "date", "h", "estimator", "z", "touch_dn1", "touch_up1",
                                       "excluded", "vol"])


def stats(g: pd.DataFrame) -> dict:
    a = g["z"].abs().to_numpy()
    return {"n": len(g), "cover1": float((a <= 1).mean()), "cover2": float((a <= 2).mean()),
            "k68": float(np.quantile(a, Q68)), "k95": float(np.quantile(a, Q95)),
            "touch_dn1": float(g["touch_dn1"].mean()), "touch_up1": float(g["touch_up1"].mean())}


def table(rows: pd.DataFrame, by: list[str]) -> str:
    recs = {" / ".join(str(x) for x in (k if isinstance(k, tuple) else (k,))): stats(g)
            for k, g in rows.groupby(by, sort=True)}
    cols = ["n", "cover1", "cover2", "k68", "k95", "touch_dn1", "touch_up1"]
    head = "| | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"
    return head + "".join(f"| {k} | " + " | ".join(f"{r[c]:.0f}" if c == "n" else f"{r[c]:.3f}" for c in cols)
                          + " |\n" for k, r in recs.items())


def report(rows: pd.DataFrame, meta: dict) -> str:
    main = rows[~rows["excluded"]]
    out = ["# Volatility band calibration", ""] + [f"- {k}: {v}" for k, v in meta.items()]
    out += ["", "Normal model: cover1 0.683, cover2 0.954, k68 1, k95 2, one-sided 1-unit touch about 0.317.", ""]
    out += ["## Main sample (windows excluded)", "", table(main, ["estimator", "h"]), ""]
    out += ["## Full sample", "", table(rows, ["estimator", "h"]), ""]
    if rows["excluded"].any():
        out += ["## Excluded windows only", "", table(rows[rows["excluded"]], ["estimator", "h"]), ""]
    if "period" in rows:
        out += ["## rv20 (the report's range) by period, main sample", "",
                table(main[main["estimator"] == "rv20"], ["h", "period"]), ""]
        out += ["## atr by period, main sample", "", table(main[main["estimator"] == "atr"], ["h", "period"]), ""]
    out += ["## rv20 by ATR % percentile tercile, main sample", "",
            table(main[(main["estimator"] == "rv20") & (main["vol"] != "n/a")], ["h", "vol"]), ""]
    out += ["## rv20 by symbol, h = 10, main sample", "",
            table(main[(main["estimator"] == "rv20") & (main["h"] == 10)], ["symbol"]), ""]
    return "\n".join(out)


def _run(args) -> pd.DataFrame:
    path, step, horizons, windows = args
    return walk(B.load_csv(path), step, horizons, symbol=Path(path).stem.upper(), windows=windows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 10, 20])
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="START:END")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    windows = RS.parse_windows(a.exclude)
    tasks = [(p, a.step, tuple(a.horizons), windows) for p in a.csv]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            results = list(ex.map(_run, tasks))
    else:
        results = [_run(t) for t in tasks]
    rows = pd.concat([r for r in results if not r.empty], ignore_index=True)
    if rows.empty:
        print("no rows", file=sys.stderr)
        return 1
    if a.split:
        rows["period"] = B.periods(rows["date"], a.split)
    print(table(rows[~rows["excluded"]], ["estimator", "h"]))
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        meta = {"step": a.step, "horizons": " ".join(map(str, a.horizons)), "files": len(a.csv),
                "split": ", ".join(a.split) or "none",
                "excluded": ", ".join(f"{x}..{y}" for x, y in windows) or "none",
                "rows": f"{len(rows)} ({int(rows['excluded'].sum())} excluded)"}
        (out / "report.md").write_text(report(rows, meta), encoding="utf-8")
        print(f"wrote {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
