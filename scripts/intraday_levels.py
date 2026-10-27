#!/usr/bin/env python3
"""
Support/resistance on hourly bars: the same source screen and key-level test as on daily bars, plus
the levels intraday traders watch (prior session high / low / close / value area, the first hour's
range, session VWAP).

  python scripts/intraday_levels.py HOURLY.csv [...] [--daily DIR] [--build-bar 2] [--forward 14]
                                    [--split 2017-01-01 2022-01-01] [--exclude START:END ...]
                                    [--jobs 4] [--out DIR]

HOURLY.csv as written by fetch_intraday.py (Date = bar start, regular session). With --daily DIR the
bars are checked against DIR/<SYMBOL>.csv (split-adjusted daily closes): when a session's last hourly
close and the daily close differ by more than SPLIT_TOL, that session's prices are scaled by their
ratio to the daily basis and its volume inversely (sessions without a daily bar are left as they are).

Once per session, at the close of its `build_bar`-th bar, from bars <= that bar only:
  - kind `source`: levels.build_levels runs on the hourly bars unchanged (its lookbacks are bar
    counts: 126 bars of pivots and volume profile are about 18 sessions, the "52-week" range 252 bars
    about 36 sessions, SMA50 / SMA200 hourly). Its candidates are grouped as in sr_screen, and these
    intraday sources are added: prev_high, prev_low, prev_close, prev_poc and prev_value_area (volume
    profile of the prior session's bars, PREV_PROFILE_BINS bins), opening_range (this session's first
    bar high and low) and session_vwap (typical price x volume over this session's bars so far).
    Per source and side the nearest candidate becomes a fixed-width zone as in sr_screen;
  - kind `level`: the key levels S1-S3 / R1-R3 that build_levels selects on hourly bars.
Outcomes are levels_backtest.outcome over `forward` hourly bars with the hourly ATR(14); the placebo
moves each zone's shape to other build bars of the same symbol, as in the daily tests. --exclude
works as in regime_split (rows whose outcome span touches a window are dropped; such bars are never
placebos).

Pre-registered reading: a source passes when its edge interval lies above 0 on the main sample and
its edge is positive in train, validation and test. Daily-bar results for comparison:
levels-baseline/screen-report.md and swing-report.md.
"""
from __future__ import annotations

import argparse
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

SPLIT_TOL = 0.02
PREV_PROFILE_BINS = 30


def load_hourly(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()[["open", "high", "low", "close", "volume"]]
    df = df.apply(pd.to_numeric, errors="coerce").dropna(subset=["open", "high", "low", "close"])
    df = df[(df["high"] >= df["low"]) & (df["low"] > 0)]
    return df[~df.index.duplicated(keep="last")]


def align_to_daily(hourly: pd.DataFrame, daily: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Scale sessions whose prices are off the daily (split-adjusted) basis; (bars, share scaled)."""
    day = hourly.index.normalize()
    last = hourly["close"].groupby(day).last()
    ratio = daily["close"].reindex(last.index) / last
    f = ratio.where((ratio - 1).abs() > SPLIT_TOL, 1.0).fillna(1.0)
    fb = f.reindex(day).to_numpy()
    out = hourly.copy()
    for c in ("open", "high", "low", "close"):
        out[c] = out[c] * fb
    out["volume"] = out["volume"] / fb
    return out, float((f != 1.0).mean())


def session_info(bars: pd.DataFrame) -> pd.DataFrame:
    """Per bar: session number, position within the session (1 = first bar)."""
    day = bars.index.normalize()
    sess = pd.Series(pd.factorize(day)[0], index=bars.index)
    pos = sess.groupby(sess).cumcount() + 1
    return pd.DataFrame({"session": sess.to_numpy(), "pos": pos.to_numpy()}, index=bars.index)


def intraday_candidates(bars: pd.DataFrame, si: pd.DataFrame, t: int) -> dict[str, list[float]]:
    """Prior-session and current-session reference prices as of bar t."""
    s = si["session"].iat[t]
    cur = bars.iloc[:t + 1][si["session"].to_numpy()[:t + 1] == s]
    prev = bars[si["session"].to_numpy() == s - 1]
    out: dict[str, list[float]] = {}
    if len(prev):
        out["prev_high"] = [float(prev["high"].max())]
        out["prev_low"] = [float(prev["low"].min())]
        out["prev_close"] = [float(prev["close"].iloc[-1])]
        prof = L.volume_profile(prev, PREV_PROFILE_BINS)
        if prof.get("available"):
            out["prev_poc"] = [prof["poc"]]
            out["prev_value_area"] = [prof["value_area_low"], prof["value_area_high"]]
    if len(cur):
        first = cur.iloc[0]
        out["opening_range"] = [float(first["high"]), float(first["low"])]
        tp = (cur["high"] + cur["low"] + cur["close"]) / 3
        v = cur["volume"].clip(lower=0)
        if v.sum() > 0:
            out["session_vwap"] = [float((tp * v).sum() / v.sum())]
    return out


def walk(bars: pd.DataFrame, build_bar: int = 2, forward: int = 14, symbol: str = "",
         windows=()) -> pd.DataFrame:
    ind = I.compute_all(bars)
    si = session_info(bars)
    excl = RS.excluded_bars(bars.index, list(windows), forward)
    start = 300
    builds = [t for t in np.flatnonzero(si["pos"].to_numpy() == build_bar) if start <= t < len(bars) - forward]
    asof, rows = [], []
    for t in builds:
        ctx = B._context(bars, ind, int(t), "swing")
        lv = L.build_levels(ctx)
        if not lv.get("available"):
            continue
        close, atr = lv["last_close"], lv["atr"]
        asof.append((int(t), close, atr, bool(excl[t])))
        base = {"symbol": symbol, "date": bars.index[t], "t": int(t), "direction": "n/a",
                "excluded": bool(excl[t]), "n_asof": len(asof) - 1}
        groups: dict[str, list[float]] = {}
        for c in lv["candidates"]:
            if c.get("price") is not None and np.isfinite(c["price"]):
                groups.setdefault(SS.group(c["source"]), []).append(float(c["price"]))
        groups.update(intraday_candidates(bars, si, int(t)))
        for g, prices in sorted(groups.items()):
            for side in ("support", "resistance"):
                p = SS.nearest(prices, close, atr, side)
                if p is None:
                    continue
                lo, hi = p - SS.ZONE_HALF_ATR * atr, p + SS.ZONE_HALF_ATR * atr
                rows.append({**base, "kind": "source", "source": g, "side": side, "rank": 1,
                             "lo_atr": (lo - close) / atr, "hi_atr": (hi - close) / atr,
                             "width_atr": (hi - lo) / atr, "dist_atr": abs(p - close) / atr,
                             "outcome": B.outcome(bars, int(t), lo, hi, side, atr, forward)})
        for side, key in (("support", "supports"), ("resistance", "resistances")):
            for rank, z in enumerate(lv[key], 1):
                lo, hi = float(z["zone_low"]), float(z["zone_high"])
                rows.append({**base, "kind": "level", "source": f"{'S' if side == 'support' else 'R'}{rank}",
                             "side": side, "rank": rank,
                             "lo_atr": (lo - close) / atr, "hi_atr": (hi - close) / atr,
                             "width_atr": (hi - lo) / atr, "dist_atr": abs(z["price"] - close) / atr,
                             "outcome": B.outcome(bars, int(t), lo, hi, side, atr, forward)})
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


def verdict(src: pd.DataFrame) -> pd.DataFrame:
    out = {}
    for s, g in src.groupby("source"):
        st = B._stats(g)
        rec = {"n": st["n"], "resolved": st["resolved"], "hold_rate": st["hold_rate"],
               "placebo_hold": st["placebo_hold"], "edge": st["edge"], "edge_lo": st["edge_lo"],
               "edge_hi": st["edge_hi"]}
        per = {p: B._stats(gp, interval=False)["edge"] for p, gp in g.groupby("period")} if "period" in g else {}
        rec.update({f"edge_{p}": e for p, e in per.items()})
        rec["pass"] = bool(np.isfinite(st["edge_lo"]) and st["edge_lo"] > 0 and all(e > 0 for e in per.values()))
        out[s] = rec
    return pd.DataFrame(out).T


def _vtable(v: pd.DataFrame) -> str:
    cols = [c for c in v.columns if c != "pass"]
    head = "| | " + " | ".join(cols) + " | pass |\n|" + "---|" * (len(cols) + 2) + "\n"

    def fmt(c, x):
        x = float(x)
        return "" if not np.isfinite(x) else f"{x:.0f}" if c in ("n", "resolved") else f"{x:.3f}"
    return head + "".join(f"| {k} | " + " | ".join(fmt(c, r[c]) for c in cols)
                          + f" | {'yes' if r['pass'] else 'no'} |\n" for k, r in v.iterrows())


def report(rows: pd.DataFrame, meta: dict) -> str:
    main = rows[~rows["excluded"]]
    out = ["# Support/resistance on hourly bars", ""] + [f"- {k}: {v}" for k, v in meta.items()]
    out += ["", f"Edge interval: {B.BOOT_CI:.0%} cluster bootstrap over (symbol, year) blocks.", ""]
    out += ["## Sources, main sample (windows excluded)", "", _vtable(verdict(main[main["kind"] == "source"])), ""]
    out += ["## Key levels S1-S3 / R1-R3 on hourly bars, main sample", "",
            _vtable(verdict(main[main["kind"] == "level"])), ""]
    out += ["## Sources, full sample", "", _vtable(verdict(rows[rows["kind"] == "source"])), ""]
    src = main[main["kind"] == "source"]
    out += ["## Sources by side, main sample", "",
            _vtable(verdict(src.assign(source=src["source"] + " " + src["side"]))), ""]
    return "\n".join(out)


def _run(args) -> tuple[pd.DataFrame, float]:
    path, daily_dir, build_bar, forward, windows = args
    bars = load_hourly(path)
    sym = Path(path).stem.upper()
    scaled = 0.0
    if daily_dir:
        dp = Path(daily_dir) / f"{sym}.csv"
        if dp.exists():
            bars, scaled = align_to_daily(bars, B.load_csv(dp))
    return walk(bars, build_bar, forward, symbol=sym, windows=windows), scaled


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--daily")
    ap.add_argument("--build-bar", type=int, default=2)
    ap.add_argument("--forward", type=int, default=14)
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="START:END")
    ap.add_argument("--exclusions", help="exclusions.csv from clean_data.py")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    wins = B.file_windows(a.csv, a.exclude, a.exclusions, hourly=True)
    tasks = [(p, a.daily, a.build_bar, a.forward, w) for p, w in zip(a.csv, wins)]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            results = list(ex.map(_run, tasks))
    else:
        results = [_run(t) for t in tasks]
    scaled = {Path(p).stem.upper(): s for p, (_, s) in zip(a.csv, results) if s}
    frames = [r for r, _ in results if not r.empty]
    if not frames:
        print("no rows", file=sys.stderr)
        return 1
    rows = pd.concat(frames, ignore_index=True)
    if a.split:
        rows["period"] = B.periods(rows["date"], a.split)
    print(verdict(rows[(~rows["excluded"]) & (rows["kind"] == "source")]).to_string())
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        rows.assign(placebo=rows["placebo"].map(lambda ps: ";".join(str(x) for x in ps))) \
            .drop(columns=["n_asof"]).to_csv(out / "rows.csv.gz", index=False)
        meta = {"build bar": a.build_bar, "forward": a.forward, "files": len(a.csv),
                "first build": rows["date"].min().strftime("%Y-%m-%d"),
                "last build": rows["date"].max().strftime("%Y-%m-%d"),
                "split": ", ".join(a.split) or "none",
                "excluded": B.describe_windows(a.exclude, a.exclusions),
                "rows": f"{len(rows)} ({int(rows['excluded'].sum())} excluded)",
                "sessions rescaled to the daily basis": ", ".join(f"{k} {v:.1%}" for k, v in scaled.items()) or "none"}
        (out / "report.md").write_text(report(rows, meta), encoding="utf-8")
        print(f"wrote {out / 'rows.csv.gz'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
