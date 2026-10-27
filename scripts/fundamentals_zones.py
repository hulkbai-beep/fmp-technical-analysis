#!/usr/bin/env python3
"""
Do support zones work better on stocks whose fundamentals look good? A test against random levels.

  python scripts/fundamentals_zones.py --daily DIR --fund DIR --mcap DIR --out DIR
                                       [--exclusions FILE] [--exclude START:END ...] [--jobs 4]

Every STEP bars from START, as of that bar only, per stock:

Zones. Two support methods, each giving at most one zone wholly below the close, nearest first,
with its top within REACH_ATR ATRs of the close:
  vp     long-window volume profile (the method the user's sample table was reverse-engineered to):
         the last VP_BARS daily bars, each bar's volume spread evenly over its high-low range in
         bins of VP_BIN x close, smoothed (Gaussian, VP_SIGMA bins), cut at the troughs; a node with
         at least VP_MIN_SHARE of the volume becomes the zone where it is at least VP_CORE x its peak;
  pivot  ATR ZigZag swing lows (levels.swing_pivots) confirmed by bar t, from the last PIVOT_BARS
         bars, zone = price +- PIVOT_HALF_ATR ATRs.
Trade. A limit buy at the zone top, good for FILL_BARS bars after t (a bar opening below it fills
at the open), sold at the close of bar t + FILL_BARS + HOLD: payoff = (that close - fill) / ATR at
t, 0 when unfilled. Also `held` / `broken` after the fill as levels_backtest.outcome (1 ATR above
the zone before a close below it).
Random levels. PLACEBOS zones per event on the same stock and as-of bar, with the same width, their
distance below the close = the zone's x U(1 - JITTER, 1 + JITTER), traded the same way (so they are
as deep on average, fill about as often and earn the stock's drift as long). zone edge = mean
payoff of the zone - mean payoff of its random levels, over all events (unfilled = 0).

Fundamentals, as of the as-of date: only quarters whose filingDate is before it (a filing date
missing or less than MIN_FILE_DAYS after the period end is a placeholder: period end + LAG_DAYS
instead; a quarter known more than 200 days earlier is stale and ignored). TTM = the last four quarters, each about a quarter apart.
  val_pct  TTM price/sales (FMP daily market cap / TTM revenue) as a percentile of the stock's own
           daily values over the previous VAL_YEARS years (at least VAL_MIN_DAYS of them);
  fscore   Piotroski F-score, TTM against the TTM a year earlier (9 points);
  growth   TTM revenue against the TTM a year earlier.
Groups: good = fscore >= 6 and val_pct <= 0.5 (quality at a price not above its own median);
cheap/dear = val_pct <= 1/3 / >= 2/3; quality hi/lo = fscore >= 7 / <= 4; growth hi/lo = >10% / <0.

Pre-registered reading (written before the run): the claim "zones work better with fundamentals"
passes when, in the research period (START .. HOLDOUT, COVID window excluded), the good group's
zone edge has a 90% interval above 0 AND exceeds the rest's zone edge with a 90% interval above 0,
for at least one method; the holdout (HOLDOUT on) is read once, afterwards, for that method only.
Intervals: bootstrap over calendar months (all stocks of a month together), BOOT_REPS resamples.
The random-level rows by group show what fundamentals do on their own.
"""
from __future__ import annotations

import argparse
import json
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
import indicators as I
import levels as L
import levels_backtest as B

START = "2010-01-01"
HOLDOUT = "2022-01-01"
COVID = ("2020-02-15", "2020-06-30")
STEP = 5
REACH_ATR = 4.0
FILL_BARS = 20
HOLD = 20
PLACEBOS = 10
JITTER = 0.5
VP_BARS, VP_BIN, VP_SIGMA, VP_MIN_SHARE, VP_CORE = 756, 0.0025, 2.0, 0.01, 0.3
PIVOT_BARS, PIVOT_HALF_ATR = 252, 0.25
LAG_DAYS = 90               # quarters without a usable filing date count as public this long after period end
MIN_FILE_DAYS = 10
VAL_YEARS, VAL_MIN_DAYS = 5, 750
BOOT_REPS, BOOT_CI, SEED = 2000, 0.90, 20261027
METHODS = ("vp", "pivot")


# ---------------------------------------------------------------------------------------- zones
def _smooth(v: np.ndarray, sigma: float) -> np.ndarray:
    r = int(4 * sigma)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    return np.convolve(np.pad(v, r, mode="reflect") if len(v) > r else np.pad(v, r), k / k.sum(), "valid")


def vp_nodes(high: np.ndarray, low: np.ndarray, vol: np.ndarray, step: float) -> list[tuple[float, float]]:
    """Volume-profile nodes (lo, hi) of bars given, bins of `step`."""
    base = np.floor(low.min() / step) * step
    n = int(np.ceil((high.max() - base) / step)) + 1
    a = np.clip(((low - base) / step).astype(int), 0, n - 1)
    b = np.clip(((high - base) / step).astype(int), 0, n - 1)
    per = vol / (b - a + 1)
    d = np.zeros(n + 1)
    np.add.at(d, a, per)
    np.add.at(d, b + 1, -per)
    vs = _smooth(np.cumsum(d)[:n], VP_SIGMA)
    inner = np.where((vs[1:-1] < vs[:-2]) & (vs[1:-1] <= vs[2:]))[0] + 1
    bounds = [0, *inner.tolist(), n]
    total = vs.sum()
    out = []
    for s, e in zip(bounds[:-1], bounds[1:]):
        seg = vs[s:e]
        if not len(seg) or seg.sum() < VP_MIN_SHARE * total:
            continue
        idx = np.where(seg >= VP_CORE * seg.max())[0]
        out.append((base + (s + idx[0]) * step, base + (s + idx[-1] + 1) * step))
    return out


def pick(zones, close: float, atr: float):
    """Nearest zone wholly below the close with its top within REACH_ATR ATRs, or None."""
    ok = [z for z in zones if z[1] < close and close - z[1] <= REACH_ATR * atr]
    return max(ok, key=lambda z: z[1]) if ok else None


def trade(o, h, l, c, t: int, lo: float, hi: float, atr: float):
    """(payoff in ATRs, filled, outcome) of a limit buy at hi placed after bar t, good for FILL_BARS
    bars (a bar opening below hi fills at the open) and sold at the close of bar t + FILL_BARS + HOLD.
    Payoff 0 when unfilled, so every as-of bar counts and no fill-based selection enters; for a
    price path without structure the expected payoff of any level is the drift earned while held."""
    exit_ = c[t + FILL_BARS + HOLD]
    for i in range(t + 1, t + 1 + FILL_BARS):
        if l[i] <= hi:
            fill = min(o[i], hi)
            res = None
            for k in range(i, min(len(c), i + FILL_BARS)):
                if h[k] >= hi + B.REACTION_ATR * atr and not (k == i and o[i] > hi):
                    res = "held"
                    break
                if c[k] < lo:
                    res = "broken"
                    break
            return (exit_ - fill) / atr, True, res
    return 0.0, False, None


# ---------------------------------------------------------------------------------- fundamentals
def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def quarters(raw: dict) -> pd.DataFrame:
    """One row per fiscal quarter: statement items, `known` = date it became public."""
    frames = []
    cols = {"income": ["revenue", "grossProfit", "netIncome", "weightedAverageShsOutDil"],
            "balance": ["totalAssets", "totalCurrentAssets", "totalCurrentLiabilities", "longTermDebt"],
            "cashflow": ["operatingCashFlow"]}
    for key, names in cols.items():
        rows = raw.get(key) or []
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([{**{n: _num(r.get(n)) for n in names}, "date": r.get("date"),
                            f"f_{key}": r.get("filingDate") or r.get("acceptedDate")} for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        frames.append(df.drop_duplicates("date").set_index("date"))
    q = frames[0].join(frames[1:], how="inner").sort_index()
    filed = pd.concat([pd.to_datetime(q[f"f_{k}"].str[:10], errors="coerce") for k in cols], axis=1).max(axis=1)
    end = q.index.to_series()
    # a filing date on or within MIN_FILE_DAYS of the period end is FMP's placeholder, not a filing;
    # a late one (a restatement or refiling) is kept as is, which can only make data later, not earlier
    bad = filed.isna() | (filed < end + pd.Timedelta(days=MIN_FILE_DAYS))
    q["known"] = filed.where(~bad, end + pd.Timedelta(days=LAG_DAYS))
    q["fallback"] = bad
    return q.drop(columns=[f"f_{k}" for k in cols])


def split_factor(splits: list, a, b) -> float:
    """Product of numerator / denominator of the splits dated in (a, b]."""
    f = 1.0
    for r in splits or []:
        d = pd.Timestamp(r["date"])
        if a < d <= b and _num(r.get("denominator")):
            f *= _num(r.get("numerator")) / _num(r.get("denominator"))
    return f


def share_growth(q: pd.DataFrame, splits: list) -> pd.Series:
    """Diluted shares against four quarters earlier. FMP restates most share counts for splits; when
    a split falls in between and dividing by it brings the ratio closer to 1, the count was not."""
    sh = q["weightedAverageShsOutDil"]
    r = sh / sh.shift(4)
    out = r.copy()
    dates = q.index
    for i in range(4, len(q)):
        f = split_factor(splits, dates[i - 4], dates[i])
        if f != 1.0 and np.isfinite(r.iloc[i]) and r.iloc[i] > 0 and abs(np.log(r.iloc[i] / f)) < abs(np.log(r.iloc[i])):
            out.iloc[i] = r.iloc[i] / f
    return out


def ttm_table(q: pd.DataFrame, splits: list | None = None) -> pd.DataFrame:
    """Per quarter (as the latest one): TTM items, the same a year earlier and the F-score."""
    if len(q) < 8:
        return pd.DataFrame()
    gaps = q.index.to_series().diff().dt.days
    ok4 = gaps.rolling(3).max().le(120) & gaps.rolling(3).min().ge(60)
    flow = ["revenue", "grossProfit", "netIncome", "operatingCashFlow"]
    t = q[flow].rolling(4).sum().where(ok4)
    t["assets"] = q["totalAssets"]
    t["ltd_ratio"] = q["longTermDebt"].fillna(0) / q["totalAssets"]
    t["cur_ratio"] = q["totalCurrentAssets"] / q["totalCurrentLiabilities"]
    t["share_growth"] = share_growth(q, splits or [])
    t["roa"] = t["netIncome"] / q["totalAssets"]
    t["gm"] = t["grossProfit"] / t["revenue"]
    t["turn"] = t["revenue"] / q["totalAssets"]
    p = t.shift(4)
    yoy = (q.index.to_series().diff(4).dt.days.between(330, 400))
    f = ((t["netIncome"] > 0).astype(int) + (t["operatingCashFlow"] > 0) + (t["roa"] > p["roa"])
         + (t["operatingCashFlow"] > t["netIncome"]) + (t["ltd_ratio"] <= p["ltd_ratio"])
         + (t["cur_ratio"] > p["cur_ratio"]) + (t["share_growth"] <= 1.005)
         + (t["gm"] > p["gm"]) + (t["turn"] > p["turn"]))
    have = t[["netIncome", "operatingCashFlow"]].notna().all(axis=1) & p["revenue"].notna() & yoy
    out = pd.DataFrame({"known": q["known"], "fallback": q["fallback"], "ttm_rev": t["revenue"],
                        "fscore": f.where(have), "growth": (t["revenue"] / p["revenue"] - 1).where(have)})
    return out.dropna(subset=["ttm_rev"])


def daily_fundamentals(raw: dict, mcap: list, dates: pd.DatetimeIndex, splits: list | None = None) -> pd.DataFrame:
    """Per trading date: fscore, growth, val_pct, using only what was public before that date."""
    q = quarters(raw)
    tt = ttm_table(q, splits) if len(q) else pd.DataFrame()
    out = pd.DataFrame(index=dates, columns=["fscore", "growth", "val_pct", "fallback"], dtype=float)
    if tt.empty or not mcap:
        return out
    tt = tt.sort_values("known")
    # latest quarter known strictly before each date (filed on day d -> usable from d + 1)
    pos = np.searchsorted(tt["known"].to_numpy(dtype="datetime64[ns]"), dates.to_numpy(dtype="datetime64[ns]"), "left") - 1
    sel = np.where(pos >= 0, pos, 0)
    cur = tt.iloc[sel].reset_index(drop=True).astype({"fallback": float})
    cur.loc[pos < 0, ["fscore", "growth", "ttm_rev", "fallback"]] = np.nan
    cur.loc[pos < 0, "known"] = pd.NaT
    # a quarter older than ~2 filings is stale (missing data), not current
    age = dates.to_numpy() - cur["known"].to_numpy(dtype="datetime64[ns]")
    stale = ~(age <= np.timedelta64(200, "D"))         # NaT (nothing known yet) counts as stale
    m = pd.Series({pd.Timestamp(r["date"]): _num(r.get("marketCap")) for r in mcap}).sort_index()
    m = m[m > 0].reindex(dates).ffill(limit=5)
    ps = pd.Series(m.to_numpy() / cur["ttm_rev"].to_numpy(dtype=float), index=dates)
    ps[stale] = np.nan
    w = int(VAL_YEARS * 252)
    vals = ps.to_numpy()
    pct = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        if not np.isfinite(vals[i]):
            continue
        hist = vals[max(0, i - w):i]
        hist = hist[np.isfinite(hist)]
        if len(hist) >= VAL_MIN_DAYS:
            pct[i] = (hist < vals[i]).mean()
    out["fscore"] = np.where(stale, np.nan, cur["fscore"].to_numpy(dtype=float))
    out["growth"] = np.where(stale, np.nan, cur["growth"].to_numpy(dtype=float))
    out["val_pct"] = pct
    out["fallback"] = cur["fallback"].to_numpy(dtype=float)
    return out


# ---------------------------------------------------------------------------------------- walk
def walk(bars: pd.DataFrame, fund: pd.DataFrame, symbol: str, excluded: np.ndarray,
         covid: np.ndarray) -> pd.DataFrame:
    o, h, l, c, v = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    atr = I.atr(bars).to_numpy(dtype=float)
    piv = L.swing_pivots(bars, I.atr(bars), C.SWING_ATR)["lows"]
    pidx = bars.index.get_indexer(pd.to_datetime([p["date"] for p in piv]))
    pconf = bars.index.get_indexer(pd.to_datetime([p["confirmed"] for p in piv]))
    pprice = np.array([p["price"] for p in piv])
    rows = []
    first = max(VP_BARS, int(bars.index.searchsorted(pd.Timestamp(START))))
    for t in range(first, len(bars) - FILL_BARS - HOLD, STEP):
        a = atr[t]
        if not np.isfinite(a) or a <= 0:
            continue
        s = slice(t + 1 - VP_BARS, t + 1)
        zones = {"vp": pick(vp_nodes(h[s], l[s], v[s], VP_BIN * c[t]), c[t], a)}
        ok = (pconf <= t) & (pconf >= 0) & (pidx >= t - PIVOT_BARS)
        zones["pivot"] = pick([(p - PIVOT_HALF_ATR * a, p + PIVOT_HALF_ATR * a) for p in pprice[ok]], c[t], a)
        f = fund.iloc[t]
        for m, z in zones.items():
            if z is None:
                continue
            pnl, filled, res = trade(o, h, l, c, t, z[0], z[1], a)
            rows.append({"symbol": symbol, "date": bars.index[t], "t": t, "method": m, "lo": z[0], "hi": z[1],
                         "close": c[t], "atr": a, "dist": (c[t] - z[1]) / a, "width": (z[1] - z[0]) / a,
                         "pnl": pnl, "filled": filled, "outcome": res, "excluded": bool(excluded[t]), "covid": bool(covid[t]),
                         "fscore": f["fscore"], "growth": f["growth"], "val_pct": f["val_pct"],
                         "fallback": f["fallback"]})
    return pd.DataFrame(rows)


def placebos(df: pd.DataFrame, bars: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """PLACEBOS random-level trades per row: the same zone (width) with its distance below the close
    multiplied by U(1 - JITTER, 1 + JITTER), so the levels are as deep as the zone on average and
    earn the stock's drift for as long. Mean payoff, fill share, held and broken shares."""
    o, h, l, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    cols = {f"pl_{x}": [] for x in ("pnl", "fills", "held", "broken")}
    for r in df.itertuples():
        his = r.close - r.dist * r.atr * rng.uniform(1 - JITTER, 1 + JITTER, PLACEBOS)
        res = [trade(o, h, l, c, r.t, hi - r.width * r.atr, hi, r.atr) for hi in his]
        cols["pl_pnl"].append(float(np.mean([x[0] for x in res])))
        cols["pl_fills"].append(sum(x[1] for x in res) / PLACEBOS)
        cols["pl_held"].append(sum(x[2] == "held" for x in res) / PLACEBOS)
        cols["pl_broken"].append(sum(x[2] == "broken" for x in res) / PLACEBOS)
    return df.assign(**cols)


def _run(args):
    path, fund_dir, mcap_dir, windows = args
    sym = Path(path).stem.upper()
    fp, mp = Path(fund_dir) / f"{sym}.json", Path(mcap_dir) / f"{sym}.json"
    if not fp.exists() or not mp.exists():
        return pd.DataFrame(), sym
    bars = B.load_csv(path)
    sp = Path(mcap_dir).parent / "splits" / f"{sym}.json"
    splits = json.loads(sp.read_text()) if sp.exists() else []
    fund = daily_fundamentals(json.loads(fp.read_text()), json.loads(mp.read_text()), bars.index, splits)
    span = (FILL_BARS + HOLD) // 2      # excluded_bars covers the build bar to 2 x this - 1 bars on
    excluded = B.excluded_bars(bars.index, windows, span)
    covid = B.excluded_bars(bars.index, [COVID], span)
    return walk(bars, fund, sym, excluded, covid), sym


def _run_placebo(args):
    path, df = args
    rng = np.random.default_rng([SEED, zlib.crc32(Path(path).stem.encode())])
    return placebos(df, B.load_csv(path), rng)


# ---------------------------------------------------------------------------------------- stats
def groups(df: pd.DataFrame) -> dict[str, pd.Series]:
    f, v, g = df["fscore"], df["val_pct"], df["growth"]
    good = (f >= 6) & (v <= 0.5)
    known = f.notna() & v.notna()
    return {"all": pd.Series(True, index=df.index), "known": known,
            "good": good & known, "rest": ~good & known,
            "cheap": v <= 1 / 3, "dear": v >= 2 / 3,
            "quality hi": f >= 7, "quality lo": f <= 4,
            "growth hi": g > 0.10, "growth lo": g < 0}


def _month_blocks(g: pd.DataFrame, k: str = "pl") -> np.ndarray:
    """Per calendar month: [zone pnl sum, zone trades, random pnl sum, random trades, held, broken,
    random held, random broken]."""
    x = g.assign(zf=1.0, zp=g["pnl"], pf=1.0, pp=g[f"{k}_pnl"], fz=g["filled"].astype(float),
                 fp=g[f"{k}_fills"], hp=g[f"{k}_held"], bp=g[f"{k}_broken"],
                 zh=(g["outcome"] == "held").astype(float), zb=(g["outcome"] == "broken").astype(float))
    m = pd.DatetimeIndex(x["date"]).to_period("M")
    return x.groupby(m)[["zp", "zf", "pp", "pf", "zh", "zb", "hp", "bp", "fz", "fp"]].sum().to_numpy(dtype=float)


def _edge(s):
    with np.errstate(invalid="ignore", divide="ignore"):
        return s[..., 0] / s[..., 1] - s[..., 2] / s[..., 3]


def summary(g: pd.DataFrame, k: str = "pl", rng_seed: int = SEED) -> dict:
    b = _month_blocks(g, k)
    tot = b.sum(axis=0)
    out = {"events": len(g), "zone_fill": tot[8] / tot[1] if tot[1] else np.nan,
           "random_fill": tot[9] / tot[1] if tot[1] else np.nan,
           "zone_pnl": tot[0] / tot[1] if tot[1] else np.nan,
           "random_pnl": tot[2] / tot[3] if tot[3] else np.nan,
           "zone_hold": tot[4] / (tot[4] + tot[5]) if tot[4] + tot[5] else np.nan,
           "random_hold": tot[6] / (tot[6] + tot[7]) if tot[6] + tot[7] else np.nan}
    out["edge"] = out["zone_pnl"] - out["random_pnl"]
    out["edge_lo"], out["edge_hi"] = _boot(b, _edge, rng_seed)
    return out


def _boot(b: np.ndarray, stat, seed: int) -> tuple[float, float]:
    if len(b) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    e = stat(b[rng.integers(0, len(b), size=(BOOT_REPS, len(b)))].sum(axis=1))
    e = e[np.isfinite(e)]
    a = (1 - BOOT_CI) / 2
    return (float(np.quantile(e, a)), float(np.quantile(e, 1 - a))) if len(e) else (np.nan, np.nan)


def difference(g: pd.DataFrame, mask_a: pd.Series, mask_b: pd.Series, k: str = "pl") -> tuple[float, float, float]:
    """Zone edge in group a minus zone edge in group b, with its month-bootstrap interval (both
    groups resampled with the same months)."""
    ka = pd.DatetimeIndex(g["date"]).to_period("M")
    months = sorted(set(ka))
    def blocks(m):
        x = g[m]
        bb = pd.DataFrame(_month_blocks(x, k), index=sorted(set(pd.DatetimeIndex(x["date"]).to_period("M"))))
        return bb.reindex(months).fillna(0.0).to_numpy()
    A, Bk = blocks(mask_a), blocks(mask_b)
    est = _edge(A.sum(0)) - _edge(Bk.sum(0))
    rng = np.random.default_rng(SEED + 1)
    idx = rng.integers(0, len(months), size=(BOOT_REPS, len(months)))
    e = _edge(A[idx].sum(1)) - _edge(Bk[idx].sum(1))
    e = e[np.isfinite(e)]
    a = (1 - BOOT_CI) / 2
    if not len(e):
        return float(est), np.nan, np.nan
    return float(est), float(np.quantile(e, a)), float(np.quantile(e, 1 - a))


def table(rows: pd.DataFrame, k: str = "pl") -> str:
    out = []
    for m in METHODS:
        g = rows[rows["method"] == m]
        for name, mask in groups(g).items():
            s = summary(g[mask], k)
            out.append(f"| {m} | {name} | {s['events']} | {s['zone_fill']:.0%} / {s['random_fill']:.0%} | {s['zone_pnl']:+.3f} | "
                       f"{s['random_pnl']:+.3f} | {s['edge']:+.3f} [{s['edge_lo']:+.3f}, {s['edge_hi']:+.3f}] | "
                       f"{s['zone_hold']:.1%} | {s['random_hold']:.1%} |")
    head = ("| method | group | events | filled zone / random | zone payoff | random payoff | zone edge [90%] | zone hold | random hold |\n"
            "|---|---|---|---|---|---|---|---|---|")
    return head + "\n" + "\n".join(out)


def contrasts(rows: pd.DataFrame, k: str = "pl") -> str:
    out = []
    pairs = [("good", "rest"), ("cheap", "dear"), ("quality hi", "quality lo"), ("growth hi", "growth lo")]
    for m in METHODS:
        g = rows[rows["method"] == m].reset_index(drop=True)
        gr = groups(g)
        for a, b in pairs:
            est, lo, hi = difference(g, gr[a], gr[b], k)
            out.append(f"| {m} | {a} − {b} | {est:+.3f} | [{lo:+.3f}, {hi:+.3f}] |")
    return "| method | zone edge difference | estimate | 90% interval |\n|---|---|---|---|\n" + "\n".join(out)


def report(rows: pd.DataFrame, meta: dict) -> str:
    parts = ["# Support zones × fundamentals", "",
             f"- symbols: {meta['symbols']} ({meta['n_symbols']}); skipped (no fundamentals): {meta['skipped']}",
             f"- as-of every {STEP} bars from {START}; limit buy at zone top for {FILL_BARS} bars, "
             f"payoff = close at as-of + {FILL_BARS + HOLD} bars − fill, in ATRs (0 if unfilled); "
             f"{PLACEBOS} random levels per event",
             f"- exclusions: {meta['windows']}",
             f"- quarters dated by period end + {LAG_DAYS}d (no filing date): {meta['fallback']:.1%} of events", ""]
    for label, sel in meta["slices"]:
        g = rows[sel].reset_index(drop=True)
        parts += [f"## {label}", "", table(g), "", contrasts(g), ""]
    return "\n".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--daily", required=True)
    ap.add_argument("--fund", required=True)
    ap.add_argument("--mcap", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclusions")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--holdout", action="store_true", help="also report HOLDOUT on (read once)")
    a = ap.parse_args(argv)
    paths = sorted(str(p) for p in Path(a.daily).glob("*.csv"))
    win = B.file_windows(paths, a.exclude, a.exclusions)
    with ProcessPoolExecutor(a.jobs) as ex:
        res = list(ex.map(_run, [(p, a.fund, a.mcap, w) for p, w in zip(paths, win)]))
    kept = [(p, df) for p, (df, _) in zip(paths, res) if len(df)]
    skipped = [s for df, s in res if not len(df)]
    allrows = pd.concat([df for _, df in kept], ignore_index=True)
    with ProcessPoolExecutor(a.jobs) as ex:
        done = list(ex.map(_run_placebo, [(p, df) for p, df in kept]))
    rows = pd.concat(done, ignore_index=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows.to_csv(out / "rows.csv.gz", index=False)
    d = pd.DatetimeIndex(rows["date"])
    research = (d < pd.Timestamp(HOLDOUT)) & ~rows["excluded"].to_numpy()
    covid = rows["covid"].to_numpy()
    slices = [(f"Research {START[:4]}–2021, COVID window excluded (main)", research & ~covid),
              (f"Research {START[:4]}–2021, COVID window included", research)]
    if a.holdout:
        slices.append((f"Holdout {HOLDOUT[:4]} on", (d >= pd.Timestamp(HOLDOUT)) & ~rows["excluded"].to_numpy()))
    meta = {"symbols": " ".join(sorted({s for s in rows["symbol"]})), "n_symbols": rows["symbol"].nunique(),
            "skipped": " ".join(skipped) or "none", "windows": B.describe_windows(a.exclude, a.exclusions)
            + f"; COVID {COVID[0]}..{COVID[1]} as a separate flag",
            "fallback": float(rows["fallback"].fillna(0).mean()), "slices": slices}
    (out / "report.md").write_text(report(rows, meta))
    print((out / "report.md").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
