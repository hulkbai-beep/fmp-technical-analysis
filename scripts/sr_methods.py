#!/usr/bin/env python3
"""
Daily screen of the support/resistance methods not tested before, with the gates written down
before the run.

  python scripts/sr_methods.py BARS.csv [BARS.csv ...] [--hourly] [--step 5] [--forward 20]
                               [--split 2017-01-01 2022-01-01] [--exclude START:END ...]
                               [--exclusions FILE] [--jobs 4] [--out DIR]

CSV format as for levels_backtest.py. Every `step` bars, from bars <= the build bar only, each
method offers zones. Unless noted, per method and side the zone nearest the close that lies wholly
on its side within REACH_ATR ATRs is scored with levels_backtest.outcome (held: 1 ATR beyond the
near edge before a close beyond the far edge; gap and ambiguous left out) over `forward` bars, and
the placebo moves the zone's shape (its edges' offsets from the close, in ATRs) to other
non-excluded build bars of the same series (levels_backtest.PLACEBO_OFFSETS steps on). Methods:

  fresh, tested_1, tested_2, tested_3plus: swing pivots (levels.swing_pivots at SWING_ATR) whose
      bar lies in the last PIVOT_BARS bars and that were confirmed by the build bar; zone = price
      +- ZONE_HALF_ATR ATRs, classed by how often price has tested it since the confirmation
      (levels.zone_tests on the bars after the confirming bar);
  flip: a pivot high whose zone is now below the close (a pivot low now above it) and whose last
      test was the break to this side: its first retest after the breakout (role reversal);
  confirm_close, confirm_wick: the key levels S1-S3 / R1-R3 (levels.build_levels). A row counts
      only when the first touching bar closes back beyond the near edge (confirm_close); for
      confirm_wick its wick into the zone side is also at least WICK_SHARE of its range. The
      outcome is read from the next bar on, over `forward` bars, with the same held / broken
      rule; an unconfirmed touch is `unconfirmed` and not counted. The placebo zones have to pass
      the same confirmation;
  fib_ret, alt_ret: the latest leg A -> B of a FIB_SWING_ATR ZigZag; a zone at B - r (B - A) for
      every r in FIB_RATIOS (fib_ret) and ALT_RATIOS (alt_ret) that lies wholly on one side of
      the close within reach, half width FIB_ZONE_SHARE x |B - A| (so the ratios' zones do not
      overlap);
  fib_ext, alt_ext: when the close is already beyond B of the leg A -> B before the latest pivot,
      zones at A + k (B - A) still ahead of the close, k in FIB_EXT / ALT_EXT, same half width;
  pivot_w, pivot_m: floor pivots P, R1 = 2P - L, S1 = 2P - H, R2 = P + (H - L), S2 = P - (H - L)
      of the last completed week / month before the build bar's own;
  camarilla_w, camarilla_m: C +- 1.1 (H - L) / 4 and C +- 1.1 (H - L) / 2 of the same periods;
  kumo: the Ichimoku cloud edges at the build bar (spans computed 26 bars earlier); kijun: the
      Ichimoku base line (midpoint of the last 26 bars).

Trade measure for every row (and its placebos): a limit order at the near edge on the first touch
(filled at the open when the bar opens beyond the near edge; no trade when it opens beyond the
stop), stop STOP_ATR ATRs beyond the far edge, target 1 ATR beyond the near edge. On the fill bar
only the stop counts; later, a bar that reaches both counts as the stop and a gap past either
fills at the open; still open `forward` bars after the fill: out at that bar's close. COST per
side. Confirmation rows buy (sell) at the confirming bar's close. pnl in ATRs at the build bar.

Gates (pre-registered): a hypothesis passes the screen when its edge is at least MIN_EDGE, its
90% interval lies above 0 and its edge is positive in each period; it also passes the multiple-
testing gate when its (1 - 0.10 / N_HYPOTHESES) interval lies above 0. The edge is hold rate -
moved-zone placebo hold rate, except for fib_ret and fib_ext, where it is the hold rate of the
Fibonacci zones - that of the other ratios of the same swings (the moved-zone placebo is shown
beside it). Hypotheses here: fresh, tested_3plus, flip, confirm_close, confirm_wick, fib_ret,
fib_ext, pivot_w, pivot_m, camarilla_w, camarilla_m, kumo, kijun; tested_1 and tested_2 are
context. N_HYPOTHESES counts the plan's daily hypotheses with room for the hourly ones.

--hourly: HOURLY.csv files as written by clean_data.py (regular-session bars on the daily price
basis). Zones are built once per session at the close of its second bar (as in
intraday_levels.py), from HOURLY_START bars on; every bar count above is in hourly bars (ATR,
pivots, Ichimoku), and the floor and Camarilla pivots of the previous session are added as
pivot_d and camarilla_d. --forward defaults to 14 bars (two sessions); --exclusions also applies
the hourly-only rows.
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
import levels_backtest as B

ZONE_HALF_ATR = 0.25
REACH_ATR = 4.0
PIVOT_BARS = 252
WICK_SHARE = 0.5
FIB_SWING_ATR = 3.0
FIB_ZONE_SHARE = 0.02
FIB_RATIOS = (0.382, 0.5, 0.618)
ALT_RATIOS = (0.30, 0.45, 0.70)
FIB_EXT = (1.272, 1.618)
ALT_EXT = (1.15, 1.45, 1.80)
CAMARILLA = (1.1 / 4, 1.1 / 2)
TENKAN, KIJUN, SENKOU_B = 9, 26, 52
STOP_ATR = 0.5
COST = 0.0005
MIN_EDGE = 0.02
N_HYPOTHESES = 20
SCREEN_CI = 0.90
HYPOTHESES = ("fresh", "tested_3plus", "flip", "confirm_close", "confirm_wick", "fib_ret", "fib_ext",
              "pivot_w", "pivot_m", "camarilla_w", "camarilla_m", "kumo", "kijun")
HOURLY_HYPOTHESES = HYPOTHESES + ("pivot_d", "camarilla_d")
CONTEXT = ("tested_1", "tested_2")
HOURLY_BUILD_BAR = 2
HOURLY_START = 300
VERSUS = {"fib_ret": "alt_ret", "fib_ext": "alt_ext"}
CONFIRM = {"confirm_close": 0.0, "confirm_wick": WICK_SHARE}


# ----------------------------------------------------------------------------- outcomes, trades
def first_touch(bars_np: tuple, t: int, lo: float, hi: float, side: str, forward: int) -> tuple[int | None, bool]:
    """(first bar in t+1..t+forward whose range overlaps [lo, hi], gapped) - gapped when a whole bar
    passed beyond the far edge first."""
    o, high, low, close = bars_np
    sup = side == "support"
    for i in range(t + 1, min(len(high), t + 1 + forward)):
        if low[i] <= hi and high[i] >= lo:
            return i, False
        if (high[i] < lo) if sup else (low[i] > hi):
            return None, True
    return None, False


def confirmed_outcome(bars_np: tuple, t: int, lo: float, hi: float, side: str, atr: float, forward: int,
                      wick: float) -> str | None:
    """'held' / 'broken' / 'open' read after a confirmed touch, 'unconfirmed', 'gap' or None."""
    o, high, low, close = bars_np
    i, gapped = first_touch(bars_np, t, lo, hi, side, forward)
    if gapped:
        return "gap"
    if i is None:
        return None
    sup = side == "support"
    rng = high[i] - low[i]
    back = close[i] > hi if sup else close[i] < lo
    tail = (min(o[i], close[i]) - low[i]) if sup else (high[i] - max(o[i], close[i]))
    if not back or (wick > 0 and not (rng > 0 and tail >= wick * rng)):
        return "unconfirmed"
    for j in range(i + 1, min(len(high), i + 1 + forward)):
        if (high[j] >= hi + B.REACTION_ATR * atr) if sup else (low[j] <= lo - B.REACTION_ATR * atr):
            return "held"
        if (close[j] < lo) if sup else (close[j] > hi):
            return "broken"
    return "open"


def trade(bars_np: tuple, t: int, lo: float, hi: float, side: str, atr: float, forward: int,
          wick: float | None = None) -> float:
    """pnl in ATRs of the zone trade described in the module docstring (NaN: no trade)."""
    o, high, low, close = bars_np
    sup = side == "support"
    sgn = 1.0 if sup else -1.0
    stop = lo - STOP_ATR * atr if sup else hi + STOP_ATR * atr
    target = hi + B.REACTION_ATR * atr if sup else lo - B.REACTION_ATR * atr
    if wick is not None:
        if confirmed_outcome(bars_np, t, lo, hi, side, atr, forward, wick) in (None, "gap", "unconfirmed"):
            return np.nan
        i, _ = first_touch(bars_np, t, lo, hi, side, forward)
        entry, start = close[i], i + 1
    else:
        i, gapped = first_touch(bars_np, t, lo, hi, side, forward)
        if i is None:
            return np.nan
        if (o[i] <= stop) if sup else (o[i] >= stop):
            return np.nan
        entry = min(o[i], hi) if sup else max(o[i], lo)
        if (low[i] <= stop) if sup else (high[i] >= stop):
            return sgn * (stop - entry) / atr - 2 * COST * entry / atr
        start = i + 1
    end = min(len(high), start + forward)
    exit_ = close[end - 1] if end > start else entry
    for j in range(start, end):
        if (o[j] <= stop) if sup else (o[j] >= stop):
            exit_ = o[j]
            break
        if (o[j] >= target) if sup else (o[j] <= target):
            exit_ = o[j]
            break
        if (low[j] <= stop) if sup else (high[j] >= stop):
            exit_ = stop
            break
        if (high[j] >= target) if sup else (low[j] <= target):
            exit_ = target
            break
    return sgn * (exit_ - entry) / atr - 2 * COST * entry / atr


# ----------------------------------------------------------------------------- level sources
def pivot_list(bars: pd.DataFrame, atr: pd.Series, mult: float) -> list[tuple[int, int, float, int]]:
    """(bar, +1 high / -1 low, price, confirming bar), in bar order."""
    raw = L.swing_pivots(bars, atr, mult)
    pos = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(bars.index)}
    return sorted((pos[p["date"]], k, float(p["price"]), pos[p["confirmed"]])
                  for side, k in (("highs", 1), ("lows", -1)) for p in raw[side])


def period_levels(bars: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Per bar: floor pivots and Camarilla levels of the last completed period (W-FRI or M)
    before the bar's own period (NaN before the second period)."""
    per = bars.index.to_period(freq)
    g = bars.groupby(per).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    prev = g.shift(1).reindex(per)
    h, lo, c = (prev[k].to_numpy() for k in ("high", "low", "close"))
    p = (h + lo + c) / 3
    r = h - lo
    out = {"P": p, "R1": 2 * p - lo, "S1": 2 * p - h, "R2": p + r, "S2": p - r}
    for k, m in zip((3, 4), CAMARILLA):
        out[f"cR{k}"], out[f"cS{k}"] = c + m * r, c - m * r
    return pd.DataFrame(out, index=bars.index)


def ichimoku(bars: pd.DataFrame) -> pd.DataFrame:
    """kijun, kumo_top, kumo_bottom per bar, causal (the spans plotted 26 bars ahead are read at the
    bar they are plotted on)."""
    def mid(n):
        return (bars["high"].rolling(n).max() + bars["low"].rolling(n).min()) / 2
    tenkan, kijun = mid(TENKAN), mid(KIJUN)
    a = ((tenkan + kijun) / 2).shift(KIJUN)
    b = mid(SENKOU_B).shift(KIJUN)
    return pd.DataFrame({"kijun": kijun, "kumo_top": np.fmax(a, b), "kumo_bottom": np.fmin(a, b)})


def nearest(prices, close: float, atr: float, side: str, half: float | None = None):
    """Nearest price whose zone (price +- half, default ZONE_HALF_ATR ATRs) lies wholly on `side`
    of the close within REACH_ATR ATRs."""
    h = ZONE_HALF_ATR * atr if half is None else half
    ps = [p for p in prices if p is not None and np.isfinite(p)]
    if side == "support":
        ok = [p for p in ps if p + h < close and close - p <= REACH_ATR * atr]
        return max(ok) if ok else None
    ok = [p for p in ps if p - h > close and p - close <= REACH_ATR * atr]
    return min(ok) if ok else None


def touch_class(n: int) -> str:
    return "fresh" if n == 0 else "tested_1" if n == 1 else "tested_2" if n == 2 else "tested_3plus"


def pivot_zones(bars: pd.DataFrame, piv: list, t: int, close: float, atr: float) -> list[tuple]:
    """(method, side, lo, hi, ratio) rows for the touch classes and the flip."""
    h = ZONE_HALF_ATR * atr
    cand: dict[tuple[str, str], list[float]] = {}
    for bar, kind, price, conf in piv:
        if conf >= t or bar < t - PIVOT_BARS:
            continue
        lo, hi = price - h, price + h
        tests = L.zone_tests(bars.iloc[conf + 1:t + 1], lo, hi, atr)
        side = "support" if hi < close else "resistance" if lo > close else None
        if side is None:
            continue
        cand.setdefault((touch_class(len(tests)), side), []).append(price)
        if tests and tests[-1]["result"] == "break" and ((kind > 0) == (side == "support")):
            cand.setdefault(("flip", side), []).append(price)
    out = []
    for (m, side), prices in cand.items():
        p = nearest(prices, close, atr, side)
        if p is not None:
            out.append((m, side, p - h, p + h, np.nan))
    return out


def fib_zones(fpiv: list, t: int, close: float, atr: float) -> list[tuple]:
    """Retracement rows from the latest leg and extension rows from the leg before the latest pivot."""
    done = [p for p in fpiv if p[3] <= t]
    out = []
    if len(done) >= 2:
        a, b = done[-2][2], done[-1][2]
        leg, half = b - a, FIB_ZONE_SHARE * abs(b - a)
        for m, ratios in (("fib_ret", FIB_RATIOS), ("alt_ret", ALT_RATIOS)):
            for r in ratios:
                p = b - r * leg
                side = "support" if p + half < close else "resistance" if p - half > close else None
                if side and abs(p - close) <= REACH_ATR * atr:
                    out.append((m, side, p - half, p + half, r))
    if len(done) >= 3:
        a, b = done[-3][2], done[-2][2]
        leg, half = b - a, FIB_ZONE_SHARE * abs(b - a)
        up = leg > 0
        if (close > b) if up else (close < b):
            side = "resistance" if up else "support"
            for m, ks in (("fib_ext", FIB_EXT), ("alt_ext", ALT_EXT)):
                for k in ks:
                    p = a + k * leg
                    ahead = (p - half > close) if up else (p + half < close)
                    if ahead and abs(p - close) <= REACH_ATR * atr:
                        out.append((m, side, p - half, p + half, k))
    return out


def fixed_zones(groups: dict[str, list[float]], close: float, atr: float) -> list[tuple]:
    h = ZONE_HALF_ATR * atr
    out = []
    for m, prices in groups.items():
        for side in ("support", "resistance"):
            p = nearest(prices, close, atr, side)
            if p is not None:
                out.append((m, side, p - h, p + h, np.nan))
    return out


# ----------------------------------------------------------------------------- walk
def walk(bars: pd.DataFrame, step: int = 5, forward: int = 20, symbol: str = "",
         windows: list[tuple[str, str]] = (), hourly: bool = False) -> pd.DataFrame:
    """One row per (build bar, method, side[, ratio]) with outcome, pnl, placebo outcomes and pnls.
    hourly: builds at the second bar of each session, with the previous session's pivots."""
    ind = I.compute_all(bars)
    atr_s = ind["atr14"]
    np_bars = tuple(bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    piv = pivot_list(bars, atr_s, C.SWING_ATR)
    fpiv = pivot_list(bars, atr_s, FIB_SWING_ATR)
    pw, pm, ichi = period_levels(bars, "W-FRI"), period_levels(bars, "M"), ichimoku(bars)
    pd_ = period_levels(bars, "D") if hourly else None
    start = max(int(C.HORIZONS["swing"]["levels_lookback"]), C.HIGH_52W_BARS, 220)
    if hourly:
        import intraday_levels as IL
        pos = IL.session_info(bars)["pos"].to_numpy()
        builds = [int(t) for t in np.flatnonzero(pos == HOURLY_BUILD_BAR) if HOURLY_START <= t < len(bars) - forward]
    else:
        builds = range(start, len(bars) - forward, step)
    asof, rows = [], []
    for t in builds:
        close, atr = float(np_bars[3][t]), float(atr_s.iat[t])
        if not (np.isfinite(atr) and atr > 0):
            continue
        asof.append((t, close, atr))
        zones = pivot_zones(bars, piv, t, close, atr) + fib_zones(fpiv, t, close, atr)
        groups = {"pivot_w": [pw[k].iat[t] for k in ("P", "R1", "S1", "R2", "S2")],
                  "pivot_m": [pm[k].iat[t] for k in ("P", "R1", "S1", "R2", "S2")],
                  "camarilla_w": [pw[k].iat[t] for k in ("cR3", "cS3", "cR4", "cS4")],
                  "camarilla_m": [pm[k].iat[t] for k in ("cR3", "cS3", "cR4", "cS4")],
                  "kumo": [ichi["kumo_top"].iat[t], ichi["kumo_bottom"].iat[t]],
                  "kijun": [ichi["kijun"].iat[t]]}
        if hourly:
            groups["pivot_d"] = [pd_[k].iat[t] for k in ("P", "R1", "S1", "R2", "S2")]
            groups["camarilla_d"] = [pd_[k].iat[t] for k in ("cR3", "cS3", "cR4", "cS4")]
        zones += fixed_zones(groups, close, atr)
        lv = L.build_levels(B._context(bars, ind, t, "swing"))
        if lv.get("available"):
            for side, key in (("support", "supports"), ("resistance", "resistances")):
                for z in lv[key]:
                    for m in CONFIRM:
                        zones.append((m, side, float(z["zone_low"]), float(z["zone_high"]), np.nan))
        for m, side, lo, hi, ratio in zones:
            rows.append({"symbol": symbol, "date": bars.index[t], "t": t, "method": m, "side": side,
                         "ratio": ratio, "lo_atr": (lo - close) / atr, "hi_atr": (hi - close) / atr,
                         "width_atr": (hi - lo) / atr, "dist_atr": abs((lo + hi) / 2 - close) / atr,
                         "n_asof": len(asof) - 1})
    excl = B.excluded_bars(bars.index, list(windows), forward)
    pool = B.placebo_pool(asof, excl)

    def score(m, t, lo, hi, side, atr):
        if m in CONFIRM:
            return (confirmed_outcome(np_bars, t, lo, hi, side, atr, forward, CONFIRM[m]),
                    trade(np_bars, t, lo, hi, side, atr, forward, wick=CONFIRM[m]))
        return B.outcome(bars, t, lo, hi, side, atr, forward), trade(np_bars, t, lo, hi, side, atr, forward)

    for r in rows:
        t, close, atr = asof[r["n_asof"]]
        r["excluded"] = bool(excl[t])
        r["outcome"], r["pnl"] = score(r["method"], t, close + r["lo_atr"] * atr, close + r["hi_atr"] * atr,
                                       r["side"], atr)
        r["placebo"], r["placebo_pnl"] = [], []
        for n2 in B.pool_offsets(pool, r["n_asof"]):
            t2, c2, a2 = asof[n2]
            o, p = score(r["method"], t2, c2 + r["lo_atr"] * a2, c2 + r["hi_atr"] * a2, r["side"], a2)
            r["placebo"].append(o)
            r["placebo_pnl"].append(p)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- statistics
def _blocks(g: pd.DataFrame) -> pd.Series:
    return pd.Series(B.block_keys(g), index=g.index)


def _boot(blocks: np.ndarray, stat, ci: float, reps: int = B.BOOT_REPS) -> tuple[float, float]:
    if len(blocks) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(B.BOOT_SEED)
    sums = blocks[rng.integers(0, len(blocks), size=(reps, len(blocks)))].sum(axis=1)
    e = stat(sums)
    e = e[np.isfinite(e)]
    if not len(e):
        return np.nan, np.nan
    a = (1 - ci) / 2
    return float(np.quantile(e, a)), float(np.quantile(e, 1 - a))


def versus_counts(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Per bootstrap block (B.block_keys): held / broken of a, then of b (fib vs alt)."""
    def cnt(g):
        k = _blocks(g)
        return pd.DataFrame({"h": (g["outcome"] == "held").groupby(k).sum(),
                             "b": (g["outcome"] == "broken").groupby(k).sum()})
    ca, cb = cnt(a), cnt(b)
    return ca.join(cb, how="outer", lsuffix="_a", rsuffix="_b").fillna(0.0)


def hold_edge(g: pd.DataFrame, versus: pd.DataFrame | None, ci: float | None) -> dict:
    """Hold rate, comparison hold rate (placebo, or the versus rows), edge and its interval (none
    when ci is None)."""
    if versus is None:
        c = B._counts(g)
        blocks = np.array([B._counts(x) for _, x in g.groupby(_blocks(g), sort=True)]) if len(g) else np.empty((0, 4))
    else:
        vc = versus_counts(g, versus)
        c = vc.sum().to_numpy(dtype=float)
        blocks = vc.to_numpy(dtype=float)
    hold = c[0] / (c[0] + c[1]) if c[0] + c[1] else np.nan
    comp = c[2] / (c[2] + c[3]) if c[2] + c[3] else np.nan
    lo, hi = _boot(blocks, B._edge, ci) if ci else (np.nan, np.nan)
    return {"resolved": int(c[0] + c[1]), "hold_rate": hold, "compare_hold": comp, "edge": hold - comp,
            "edge_lo": lo, "edge_hi": hi}


def pnl_edge(g: pd.DataFrame, ci: float) -> dict:
    """Mean trade pnl (ATRs), placebo mean, difference and its interval."""
    p = g["pnl"].to_numpy(dtype=float)
    pp = [x for xs in g["placebo_pnl"] for x in xs]
    k = _blocks(g)
    rows = []
    for _, x in g.groupby(k, sort=True):
        a = x["pnl"].to_numpy(dtype=float)
        b = np.array([v for vs in x["placebo_pnl"] for v in vs], dtype=float)
        rows.append([np.nansum(a), np.isfinite(a).sum(), np.nansum(b), np.isfinite(b).sum()])
    blocks = np.array(rows, dtype=float) if rows else np.empty((0, 4))

    def stat(s):
        with np.errstate(invalid="ignore", divide="ignore"):
            return s[..., 0] / s[..., 1] - s[..., 2] / s[..., 3]
    lo, hi = _boot(blocks, stat, ci)
    m, pm = np.nanmean(p) if np.isfinite(p).any() else np.nan, np.nanmean(pp) if len(pp) else np.nan
    return {"trades": int(np.isfinite(p).sum()), "pnl": m, "placebo_pnl": pm, "pnl_edge": m - pm,
            "pnl_lo": lo, "pnl_hi": hi}


def verdict(rows: pd.DataFrame, methods=HOURLY_HYPOTHESES + CONTEXT) -> pd.DataFrame:
    """Per method: hold-rate edge with its screen and multiple-testing intervals, per-period edges,
    the trade measure and pass flags."""
    bonf = 1 - (1 - SCREEN_CI) / N_HYPOTHESES
    out = {}
    for m in methods:
        g = rows[rows["method"] == m]
        if g.empty:
            continue
        vs = rows[rows["method"] == VERSUS[m]] if m in VERSUS else None
        s = hold_edge(g, vs, SCREEN_CI)
        s["bonf_lo"] = hold_edge(g, vs, bonf)["edge_lo"]
        if m in VERSUS:
            s["placebo_edge"] = hold_edge(g, None, None)["edge"]
        per = {}
        if "period" in g:
            for p, gp in g.groupby("period"):
                vp = vs[vs["period"] == p] if vs is not None else None
                per[p] = hold_edge(gp, vp, None)["edge"]
        rec = {"n": len(g), **s, **{f"edge_{p}": e for p, e in per.items()}, **pnl_edge(g, SCREEN_CI)}
        rec["screen"] = bool(np.isfinite(s["edge_lo"]) and s["edge_lo"] > 0 and s["edge"] >= MIN_EDGE
                             and all(np.isfinite(e) and e > 0 for e in per.values()))
        rec["bonferroni"] = bool(rec["screen"] and np.isfinite(s["bonf_lo"]) and s["bonf_lo"] > 0)
        out[m] = rec
    return pd.DataFrame(out).T


def _fmt_table(df: pd.DataFrame, ints=("n", "resolved", "trades")) -> str:
    cols = list(df.columns)
    head = "| | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"

    def f(c, v):
        if isinstance(v, (bool, np.bool_)):
            return "yes" if v else "no"
        try:
            v = float(v)
        except (TypeError, ValueError):
            return str(v)
        return "" if not np.isfinite(v) else f"{v:.0f}" if c in ints else f"{v:.3f}"
    return head + "".join(f"| {k} | " + " | ".join(f(c, r[c]) for c in cols) + " |\n" for k, r in df.iterrows())


def report(rows: pd.DataFrame, meta: dict) -> str:
    main = rows[~rows["excluded"]]
    out = [f"# Support/resistance methods, {meta.get('bars', 'daily').split(',')[0]} screen", ""] + [f"- {k}: {v}" for k, v in meta.items()]
    out += ["", f"Edge = hold rate - placebo hold rate (fib_ret / fib_ext: - the other ratios of the same swings). "
            f"Intervals: cluster bootstrap over {B.BOOT_BLOCKS}, {SCREEN_CI:.0%} and the multiple-testing "
            f"{1 - (1 - SCREEN_CI) / N_HYPOTHESES:.2%} (bonf_lo). pnl in ATRs per trade after costs.", ""]
    keep = ["n", "resolved", "hold_rate", "compare_hold", "edge", "edge_lo", "edge_hi", "bonf_lo"]
    for name, g in (("main sample", main), ("full sample", rows)):
        v = verdict(g)
        cols = keep + [c for c in v.columns if c.startswith("edge_") and c not in ("edge_lo", "edge_hi")] + \
            (["placebo_edge"] if "placebo_edge" in v else []) + ["screen", "bonferroni"]
        out += [f"## Verdict, {name}", "", _fmt_table(v[cols]), ""]
        if name == "main sample":
            out += ["## Trade measure, main sample", "",
                    _fmt_table(v[["trades", "pnl", "placebo_pnl", "pnl_edge", "pnl_lo", "pnl_hi"]]), ""]
            sides = main.assign(method=main["method"] + " " + main["side"])
            ms = [f"{m} {s}" for m in HOURLY_HYPOTHESES + CONTEXT for s in ("support", "resistance")]
            vs = verdict(sides, [m for m in ms if m in set(sides["method"])])
            out += ["## By side, main sample (placebo comparison)", "",
                    _fmt_table(vs[["n", "resolved", "hold_rate", "compare_hold", "edge", "edge_lo", "edge_hi"]]), ""]
    fib = main[main["method"].isin(["fib_ret", "alt_ret", "fib_ext", "alt_ext"])]
    if not fib.empty:
        by = {f"{m} {r:g}": B._stats(g, interval=True) for (m, r), g in fib.groupby(["method", "ratio"])}
        t = pd.DataFrame(by).T[["n", "resolved", "hold_rate", "placebo_hold", "edge", "edge_lo", "edge_hi"]]
        out += ["## Fibonacci and other ratios one by one (vs moved placebo), main sample", "", _fmt_table(t), ""]
    return "\n".join(out)


def _run(args: tuple) -> pd.DataFrame:
    path, step, forward, windows, hourly = args
    if hourly:
        import intraday_levels as IL
        bars = IL.load_hourly(path)
    else:
        bars = B.load_csv(path)
    return walk(bars, step, forward, symbol=Path(path).stem.upper(), windows=windows, hourly=hourly)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--hourly", action="store_true")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--forward", type=int, help="bars (default 20, hourly 14)")
    ap.add_argument("--split", nargs="*", default=[], metavar="DATE")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="START:END")
    ap.add_argument("--exclusions", help="exclusions.csv from clean_data.py")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    a.forward = a.forward or (14 if a.hourly else 20)
    wins = B.file_windows(a.csv, a.exclude, a.exclusions, hourly=a.hourly)
    tasks = [(p, a.step, a.forward, w, a.hourly) for p, w in zip(a.csv, wins)]
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
    v = verdict(rows[~rows["excluded"]])
    print(v[["n", "hold_rate", "compare_hold", "edge", "edge_lo", "bonf_lo", "pnl_edge", "screen"]].to_string())
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        rows.assign(placebo=rows["placebo"].map(lambda ps: ";".join(str(x) for x in ps)),
                    placebo_pnl=rows["placebo_pnl"].map(lambda ps: ";".join(f"{x:.4f}" for x in ps))) \
            .drop(columns=["n_asof"]).to_csv(out / "rows.csv.gz", index=False)
        meta = {"bars": "hourly, built at the second bar of each session" if a.hourly else f"daily, every {a.step}",
                "forward": a.forward, "files": len(a.csv), "split": ", ".join(a.split) or "none",
                "excluded": B.describe_windows(a.exclude, a.exclusions),
                "rows": f"{len(rows)} ({int(rows['excluded'].sum())} excluded)",
                "zones": f"+-{ZONE_HALF_ATR} ATR within {REACH_ATR} ATR; Fibonacci +-{FIB_ZONE_SHARE:.0%} of the leg "
                         f"({FIB_SWING_ATR} ATR ZigZag)", "trade": f"stop {STOP_ATR} ATR beyond, target 1 ATR, "
                                                              f"cost {COST:.2%} per side"}
        (out / "report.md").write_text(report(rows, meta), encoding="utf-8")
        print(f"wrote {out / 'rows.csv.gz'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
