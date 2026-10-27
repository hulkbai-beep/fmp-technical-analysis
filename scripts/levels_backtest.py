#!/usr/bin/env python3
"""
Walk-forward test of the key levels (module H): do price reactions happen at the zones, or would a
zone of the same width a little further away have done as well?

  python scripts/levels_backtest.py BARS.csv [BARS.csv ...] [--horizon swing|position]
                                    [--step 5] [--forward 20] [--split 2017-01-01 2022-01-01]
                                    [--source levels|boxes] [--jobs 4] [--out DIR]

--source boxes tests the edges of the active trading-range box (trading_ranges.py) instead: its
lower band while the close is above it, its upper band while the close is below it.

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

--exclude START:END drops a window (e.g. the 2020 crash): a row is dropped when any bar from its
build bar to the last bar its outcome can use falls inside, and such as-of bars are never placebos.
--exclusions FILE adds per-symbol windows from clean_data.py's exclusions.csv. The report covers the
rows left; rows.csv keeps all with an `excluded` flag.

In-sample and without costs; it measures where price reacts, not a trading rule.
"""
from __future__ import annotations

import argparse
import bisect
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


def excluded_bars(index: pd.DatetimeIndex, windows: list[tuple[str, str]], forward: int) -> np.ndarray:
    """True for build bars whose outcome span (the bar itself to 2 x forward - 1 bars later) touches
    any window. Window dates are inclusive whole days, so on intraday bars they cover every bar of
    their first and last day."""
    inside = np.zeros(len(index), dtype=bool)
    day = pd.DatetimeIndex(index).normalize()
    for a, b in windows:
        inside |= (day >= pd.Timestamp(a)) & (day <= pd.Timestamp(b))
    span = 2 * forward
    hit = np.convolve(inside.astype(int), np.ones(span, dtype=int))[span - 1:span - 1 + len(index)]
    return hit > 0          # hit[t] counts inside bars in t..t+span-1


def parse_windows(items: list[str]) -> list[tuple[str, str]]:
    """START:END strings to (start, end) pairs, both checked as dates."""
    out = []
    for s in items:
        a, _, b = s.partition(":")
        pd.Timestamp(a), pd.Timestamp(b)
        out.append((a, b))
    return out


def load_exclusions(path: str | Path, hourly: bool = False) -> dict[str, list[tuple[str, str]]]:
    """Per-symbol windows from clean_data.py's exclusions.csv: rows that apply to `all` bars, plus
    the `hourly` rows when the test runs on hourly bars."""
    ex = pd.read_csv(path, dtype=str)
    keep = ex["applies"].isin(["all", "hourly"] if hourly else ["all"])
    out: dict[str, list[tuple[str, str]]] = {}
    for r in ex[keep].itertuples():
        out.setdefault(r.symbol.upper(), []).append((r.start, r.end))
    return out


def file_windows(paths: list[str], exclude: list[str], exclusions: str | None,
                 hourly: bool = False) -> list[list[tuple[str, str]]]:
    """Per file: the --exclude windows plus its symbol's --exclusions rows (symbol = file stem)."""
    base = parse_windows(exclude)
    per = load_exclusions(exclusions, hourly) if exclusions else {}
    return [base + per.get(Path(p).stem.upper(), []) for p in paths]


def describe_windows(exclude: list[str], exclusions: str | None) -> str:
    parts = [", ".join(x.replace(":", "..") for x in exclude)] if exclude else []
    if exclusions:
        parts.append(f"per-symbol windows from {Path(exclusions).name}")
    return "; ".join(parts) or "none"


def placebo_pool(asof: list[tuple], excluded: np.ndarray) -> list[int]:
    """As-of positions that may serve as placebos: those whose build bar is not excluded (all of
    them when every one is)."""
    return [n for n, a in enumerate(asof) if not excluded[a[0]]] or list(range(len(asof)))


def pool_offsets(pool: list[int], n_asof: int) -> list[int]:
    """The PLACEBO_OFFSETS steps after n_asof along the pool (cyclic)."""
    p = bisect.bisect_left(pool, n_asof)
    return [pool[(p + k) % len(pool)] for k in PLACEBO_OFFSETS]


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
         build=L.build_levels, symbol: str = "", windows: list[tuple[str, str]] = ()) -> pd.DataFrame:
    """One row per (as-of bar, level): symbol, rank, side, daily direction, width and distance in
    ATRs, outcome, the placebo outcomes (from non-excluded as-of bars) and the `excluded` flag."""
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
    excl = excluded_bars(bars.index, list(windows), forward)
    pool = placebo_pool(asof, excl)
    for r in rows:
        r["excluded"] = bool(excl[r["t"]])
        r["placebo"] = []
        for n2 in pool_offsets(pool, r["n_asof"]):
            t2, close2, atr2 = asof[n2]
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


def box_build(bars: pd.DataFrame):
    """A `build` for walk() that offers the edges of the active trading-range box (trading_ranges.py)
    instead of the key levels: the lower band as a support while the close is above it, the upper
    band as a resistance while the close is below it; no zones without an active box or while a
    breakout is pending. Boxes are detected on the whole series up to each as-of bar (the
    detector's pivots are causal), not on walk()'s shorter context window."""
    import trading_ranges as T
    atr = I.atr(bars)
    piv = T.pivot_rows(bars, atr)

    def build(ctx: dict) -> dict:
        close = float(ctx["bars"]["close"].iloc[-1])
        a = float(ctx["ind"]["atr14"].iloc[-1])
        if not (np.isfinite(a) and a > 0):
            return {"available": False}
        box = T.detect_trading_ranges(bars, atr, as_of=ctx["as_of"], pivots=piv).get("active_box")
        sup, res = [], []
        if box and box["status"] == "active":
            lz, uz = box["lower_zone"], box["upper_zone"]
            if close > lz["high"]:
                sup.append({"price": (lz["low"] + lz["high"]) / 2, "zone_low": lz["low"], "zone_high": lz["high"]})
            if close < uz["low"]:
                res.append({"price": (uz["low"] + uz["high"]) / 2, "zone_low": uz["low"], "zone_high": uz["high"]})
        return {"available": True, "last_close": close, "atr": a, "supports": sup, "resistances": res}
    return build


def _run(args: tuple) -> pd.DataFrame:
    path, horizon, step, forward, source, windows = args
    bars = load_csv(path)
    build = box_build(bars) if source == "boxes" else L.build_levels
    return walk(bars, horizon, step, forward, build=build, symbol=Path(path).stem.upper(), windows=windows)


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

    out = [f"# Walk-forward check: {meta.get('source', 'levels')}", ""]
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
    ap.add_argument("--source", default="levels", choices=["levels", "boxes"],
                    help="zones to test: the key levels, or the edges of the active trading-range box")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="START:END")
    ap.add_argument("--exclusions", help="exclusions.csv from clean_data.py")
    ap.add_argument("--jobs", type=int, default=1, help="files run in parallel")
    ap.add_argument("--out", help="directory for rows.csv and report.md")
    a = ap.parse_args(argv)
    wins = file_windows(a.csv, a.exclude, a.exclusions)
    tasks = [(p, a.horizon, a.step, a.forward, a.source, w) for p, w in zip(a.csv, wins)]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            results = list(ex.map(_run, tasks))
    else:
        results = [_run(t) for t in tasks]
    for path, rows in zip(a.csv, results):
        if not rows.empty:
            print(f"\n{Path(path).stem}\n{summarize(rows[~rows['excluded']], interval=False).round(3).to_string()}")
    rows = pd.concat(results, ignore_index=True)
    if rows.empty:
        print("no levels built", file=sys.stderr)
        return 1
    if a.split:
        rows["period"] = periods(rows["date"], a.split)
    main_rows = rows[~rows["excluded"]]
    if len(results) > 1:
        print(f"\nall files\n{summarize(main_rows).round(3).to_string()}")
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        rows.assign(placebo=rows["placebo"].map(lambda ps: ";".join(str(x) for x in ps))) \
            .drop(columns=["n_asof"]).to_csv(out / "rows.csv", index=False)
        meta = {"source": a.source, "horizon": a.horizon, "step": a.step, "forward": a.forward, "files": len(a.csv),
                "first build": rows["date"].min().strftime("%Y-%m-%d"),
                "last build": rows["date"].max().strftime("%Y-%m-%d"),
                "split": ", ".join(a.split) or "none", "excluded": describe_windows(a.exclude, a.exclusions),
                "rows": f"{len(rows)} ({int(rows['excluded'].sum())} excluded)"}
        (out / "report.md").write_text(report(main_rows, meta), encoding="utf-8")
        print(f"\nwrote {out / 'rows.csv'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
