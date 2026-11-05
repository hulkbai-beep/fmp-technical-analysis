"""
Key levels (module H): ATR ZigZag swing pivots, supply/demand base zones, a volume profile, the
52-week range and unfilled gaps, plus the current SMA50/SMA200 and anchored VWAPs, as weighted
candidate prices. Per side of the last close, a Gaussian density of the candidates gives the zones
(its peaks, as wide as the density stays above half the peak); each zone is scored by how price
reacted on earlier tests (holds, breaks, role reversals, recent ones counting more) plus the
weight of candidates in it. A support zone lies wholly below the close and a resistance zone
wholly above it.

The zones are reference only: a map of where price traded or turned before, not a forecast of where
it will turn next. The stops and targets in scorecard.py come from volatility bands.

Everything uses split-adjusted OHLC (the prices that actually traded, continuous across splits)
over the last ctx["hz"]["levels_lookback"] bars, as of the last complete bar. Those prices are not
dividend-adjusted, so on an ex-dividend bar (bars["dividend"] > 0) a gap down is measured from the
prior bar's low net of the dividend: the mechanical ex-dividend drop is no gap. A dividend can only
explain a drop, so gaps up are measured from the prior bar as it traded. Definitions are documented
in references/methodology.md.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config as C
from dividends import NOTE_DATES_MAX, bar_dividends

PROFILE_NOTE = "approximation: each bar's volume spread evenly across its high-low range"
PIVOT_NOTE = ("a swing pivot is confirmed once price reverses {m:g} ATR from it, so the latest swing "
              "is not a pivot yet (the only right-side dependence; levels are measured as of the last bar)")
GAP_DIVIDEND_METHOD = ("on an ex-dividend bar t paying D, a gap down needs high[t] < low[t-1] - D and its zone's "
                       "top is low[t-1] - D: a drop the dividend explains is not a gap (split-adjusted bars are "
                       "not dividend-adjusted); a dividend cannot explain a rise, so a gap up is measured from "
                       "high[t-1] as it traded, as on any other bar")
EXTREMES_RULE = ("at a high: the last close is the highest close of the period (52 weeks = the last 252 bars, or "
                 "all available history) or within {m:g} ATR of the period's highest high; lows mirror this")

_SOURCE_TYPES = {
    "pivot_high": "pivot", "pivot_low": "pivot",
    "poc": "volume_profile", "value_area_low": "volume_profile",
    "value_area_high": "volume_profile", "hvn": "volume_profile",
    "high_52w": "range_52w", "low_52w": "range_52w",
    "demand": "supply_demand", "supply": "supply_demand",
    "sma50": "moving_average", "sma200": "moving_average",
}


def _num(x) -> float | None:
    """Plain float, or None for missing / non-finite values."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def source_type(source: str) -> str:
    """Family of a candidate source (pivot, volume_profile, gap, ...)."""
    if source.startswith("gap_"):
        return "gap"
    if source.startswith("avwap_"):
        return "anchored_vwap"
    return _SOURCE_TYPES.get(source, source)


# ---------------------------------------------------------------------------- swing pivots
def swing_pivots(df: pd.DataFrame, atr: pd.Series, mult: float = C.SWING_ATR) -> dict:
    """ATR ZigZag swing pivots from df's high and low columns.

    The running high (low) since the last pivot becomes a pivot high (low) once a later bar's
    low (high) is at least mult x ATR of that later bar below (above) it; pivots alternate. The
    latest swing is never confirmed by the data, so it is not a pivot, and neither is the first
    bar (no left side; it only starts the count). Each pivot carries
    swing_atr: the smaller of the legs into and out of it (the one available at either end), in
    ATRs at the pivot bar, and confirmed: the date of the bar that confirmed it. Bar i's pivots
    depend on bars <= their confirmation only, so cutting the series later keeps them. Returns
    {"highs": [{date, price, swing_atr, confirmed}], "lows": [...]}.
    """
    high = df["high"].to_numpy(dtype=float, na_value=np.nan)
    low = df["low"].to_numpy(dtype=float, na_value=np.nan)
    a = atr.reindex(df.index).to_numpy(dtype=float, na_value=np.nan)
    piv: list[tuple[int, int, float, int]] = []     # (bar, +1 high / -1 low, price, confirming bar)
    direction, hi_i, lo_i = 0, 0, 0
    for i in range(len(df)):
        if not (np.isfinite(high[i]) and np.isfinite(low[i])):
            continue
        if direction >= 0 and (not np.isfinite(high[hi_i]) or high[i] > high[hi_i]):
            hi_i = i
        if direction <= 0 and (not np.isfinite(low[lo_i]) or low[i] < low[lo_i]):
            lo_i = i
        thr = mult * a[i]
        if not np.isfinite(thr):
            continue
        # a pivot is confirmed by a later bar only: the extreme must sit strictly before bar i
        top = direction >= 0 and hi_i < i and low[i] <= high[hi_i] - thr
        bottom = direction <= 0 and lo_i < i and high[i] >= low[lo_i] + thr
        if top and bottom:                       # undecided start: the earlier extreme came first
            top, bottom = hi_i < lo_i, hi_i >= lo_i
        if top:
            piv.append((hi_i, +1, high[hi_i], i))
            direction, lo_i = -1, hi_i + 1 + int(np.nanargmin(low[hi_i + 1:i + 1]))
        elif bottom:
            piv.append((lo_i, -1, low[lo_i], i))
            direction, hi_i = +1, lo_i + 1 + int(np.nanargmax(high[lo_i + 1:i + 1]))
    if direction > 0:                            # unconfirmed latest swing: the leg out of the last pivot
        tail = high[hi_i]
    elif direction < 0:
        tail = low[lo_i]
    else:
        tail = np.nan
    out = {"highs": [], "lows": []}
    for j, (i, kind, price, conf) in enumerate(piv):
        if i == 0:
            continue
        legs = [abs(price - piv[j - 1][2])] if j > 0 else []
        legs.append(abs((piv[j + 1][2] if j + 1 < len(piv) else tail) - price))
        legs = [x for x in legs if np.isfinite(x)]
        swing = min(legs) / a[i] if legs and np.isfinite(a[i]) and a[i] > 0 else np.nan
        out["highs" if kind > 0 else "lows"].append(
            {"date": _iso(df.index[i]), "price": float(price), "swing_atr": _num(swing),
             "confirmed": _iso(df.index[conf])})
    return out


# ---------------------------------------------------------------------------- supply / demand
def base_zones(df: pd.DataFrame, atr: pd.Series) -> list[dict]:
    """Supply/demand zones: the range of a quiet base bar that an impulse left behind.

    Bar b is a demand base when its high-low range is at most C.BASE_MAX_ATR x ATR[b] and, within
    the next C.IMPULSE_BARS bars, a close is at least C.IMPULSE_ATR x ATR[b] above close[b] with
    no low below low[b] on the way (supply mirrors it). The zone is low[b]..high[b]. Zones that a
    later close has gone through (below the zone for demand, above it for supply) are dropped;
    fresh = no bar after the impulse has traded back into the zone. Chronological.
    """
    o = df["open"].to_numpy(dtype=float, na_value=np.nan)
    h = df["high"].to_numpy(dtype=float, na_value=np.nan)
    lo = df["low"].to_numpy(dtype=float, na_value=np.nan)
    c = df["close"].to_numpy(dtype=float, na_value=np.nan)
    a = atr.reindex(df.index).to_numpy(dtype=float, na_value=np.nan)
    n, out = len(df), []
    for b in range(n - 1):
        if not (np.isfinite(a[b]) and a[b] > 0 and h[b] - lo[b] <= C.BASE_MAX_ATR * a[b]):
            continue
        for kind, sign in (("demand", 1.0), ("supply", -1.0)):
            done = None
            for j in range(b + 1, min(n, b + 1 + C.IMPULSE_BARS)):
                if (lo[j] < lo[b]) if sign > 0 else (h[j] > h[b]):
                    break
                if sign * (c[j] - c[b]) >= C.IMPULSE_ATR * a[b]:
                    done = j
                    break
            if done is None or sign * (c[b + 1] - o[b + 1]) <= 0:   # the first impulse bar moves away
                continue
            later = slice(done + 1, n)
            if (c[later] < lo[b]).any() if sign > 0 else (c[later] > h[b]).any():
                continue
            fresh = not ((lo[later] <= h[b]).any() if sign > 0 else (h[later] >= lo[b]).any())
            out.append({"date": _iso(df.index[b]), "kind": kind, "zone_low": float(lo[b]),
                        "zone_high": float(h[b]), "fresh": bool(fresh)})
    return out


# ---------------------------------------------------------------------------- volume profile
def volume_profile(df: pd.DataFrame, bins: int = C.PROFILE_BINS) -> dict:
    """Volume-by-price profile of df: POC, value area, high/low-volume nodes and the bins.

    `bins` equal-width bins span the window's lowest low to highest high. Each bar's volume is
    spread uniformly over its [low, high] range in proportion to the overlap with each bin; a
    bar with high == low puts all its volume in the bin holding that price (the upper bin when
    it sits on an inner edge).
    """
    d = df[["high", "low", "volume"]].dropna()
    high = d["high"].to_numpy(dtype=float)
    low = d["low"].to_numpy(dtype=float)
    vol = np.clip(d["volume"].to_numpy(dtype=float), 0.0, None)
    total = float(vol.sum())
    if total <= 0:
        return {"available": False, "reason": "no traded volume in the window", "note": PROFILE_NOTE}

    edges, volumes = _bin_volumes(high, low, vol, bins)
    poc = int(np.argmax(volumes))                      # ties: the lowest-priced bin
    va_lo, va_hi = _value_area(volumes, C.VALUE_AREA_SHARE)
    mean_bin = total / len(volumes)
    hvn = [p for p, v in _plateau_extrema(volumes, edges, peaks=True) if v > C.HVN_FACTOR * mean_bin]
    lvn = [p for p, v in _plateau_extrema(volumes, edges, peaks=False) if v < C.LVN_FACTOR * mean_bin]
    return {
        "available": True,
        "poc": float((edges[poc] + edges[poc + 1]) / 2.0),
        "value_area_low": float(edges[va_lo]),
        "value_area_high": float(edges[va_hi + 1]),
        "value_area_volume_share": float(volumes[va_lo:va_hi + 1].sum() / total),
        "hvn": [float(p) for p in hvn],
        "lvn": [float(p) for p in lvn],
        "price_low": float(edges[0]),
        "price_high": float(edges[-1]),
        "bin_width": float(edges[1] - edges[0]),
        "total_volume": total,
        "bins": [{"low": float(a), "high": float(b), "volume": float(v)}
                 for a, b, v in zip(edges[:-1], edges[1:], volumes)],
        "lookback_bars": len(df),
        "note": PROFILE_NOTE,
    }


def _bin_volumes(high: np.ndarray, low: np.ndarray, vol: np.ndarray,
                 bins: int) -> tuple[np.ndarray, np.ndarray]:
    """(bin edges, volume per bin); a single zero-width bin if the whole window traded at one price."""
    lo, hi = low.min(), high.max()
    if hi <= lo:
        return np.array([lo, hi]), np.array([vol.sum()])
    edges = np.linspace(lo, hi, bins + 1)
    span = high - low
    overlap = np.minimum(high[:, None], edges[None, 1:]) - np.maximum(low[:, None], edges[None, :-1])
    weights = np.clip(overlap, 0.0, None) / np.where(span > 0, span, np.inf)[:, None]
    flat = np.flatnonzero(span <= 0)
    weights[flat, np.clip(np.searchsorted(edges, high[flat], side="right") - 1, 0, bins - 1)] = 1.0
    return edges, vol @ weights


def _value_area(volumes: np.ndarray, share: float) -> tuple[int, int]:
    """First and last bin of the value area.

    Starts at the POC bin and repeatedly adds the neighbouring bin (one below or one above) with
    more volume, both on a tie, until the area holds at least `share` of the total volume.
    """
    target = share * volumes.sum() * (1.0 - 1e-9)      # tolerance for float sums
    lo = hi = int(np.argmax(volumes))
    acc = volumes[lo]
    top = len(volumes) - 1
    while acc < target and (lo > 0 or hi < top):
        below = volumes[lo - 1] if lo > 0 else -np.inf
        above = volumes[hi + 1] if hi < top else -np.inf
        if above >= below:
            hi += 1
            acc += above
        if below >= above:
            lo -= 1
            acc += below
    return lo, hi


def _plateau_extrema(volumes: np.ndarray, edges: np.ndarray, peaks: bool) -> list[tuple[float, float]]:
    """(price, volume) of interior local maxima (peaks) or minima of the bin volumes.

    Runs of equal volume are one plateau, reported at its price midpoint, so a flat stretch of
    empty bins (e.g. a price gap) is one low-volume node. Runs touching the first or last bin
    have no neighbour on one side and are never extrema.
    """
    tol = 1e-9 * volumes.max()
    starts = np.flatnonzero(np.r_[True, np.abs(np.diff(volumes)) > tol])
    ends = np.r_[starts[1:], len(volumes)] - 1
    level = volumes[starts]
    out = []
    for r in range(1, len(starts) - 1):
        left, mid, right = level[r - 1], level[r], level[r + 1]
        if (mid > left and mid > right) if peaks else (mid < left and mid < right):
            out.append(((edges[starts[r]] + edges[ends[r] + 1]) / 2.0, float(mid)))
    return out


def _zone_volume(profile: dict, lo: float, hi: float) -> float:
    """Profile volume inside [lo, hi], taking volume as uniform within each bin."""
    b_lo = np.array([b["low"] for b in profile["bins"]])
    b_hi = np.array([b["high"] for b in profile["bins"]])
    vol = np.array([b["volume"] for b in profile["bins"]])
    width = b_hi - b_lo
    overlap = np.clip(np.minimum(hi, b_hi) - np.maximum(lo, b_lo), 0.0, None)
    inside = np.where(width > 0, overlap / np.where(width > 0, width, 1.0),
                      ((b_lo >= lo) & (b_lo <= hi)).astype(float))
    return float(vol @ inside)


# ---------------------------------------------------------------------------- gaps
def _gap_inputs(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(high, low, high[t-1], low[t-1], D[t]) as arrays on df's bars: the prior bar's high and low
    (NaN at bar 0) and the dividend going ex on each bar (0 without a "dividend" column)."""
    high = df["high"].to_numpy(dtype=float, na_value=np.nan)
    low = df["low"].to_numpy(dtype=float, na_value=np.nan)
    return high, low, np.r_[np.nan, high[:-1]], np.r_[np.nan, low[:-1]], bar_dividends(df).to_numpy(dtype=float)


def _later_extremes(high: np.ndarray, low: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lowest low / highest high strictly after each bar (+inf / -inf after the last bar)."""
    later_low = np.r_[np.fmin.accumulate(low[::-1])[::-1][1:], np.inf]
    later_high = np.r_[np.fmax.accumulate(high[::-1])[::-1][1:], -np.inf]
    return np.where(np.isnan(later_low), np.inf, later_low), np.where(np.isnan(later_high), -np.inf, later_high)


def unfilled_gaps(df: pd.DataFrame) -> list[dict]:
    """Price gaps between consecutive bars of df that later bars have not fully filled.

    Gap up at t: low[t] > high[t-1], zone high[t-1]..low[t]; filled once a later bar's low is
    <= high[t-1], otherwise its top shrinks to the lowest later low (partially filled). Gap down
    mirrors it: high[t] < low[t-1], zone high[t]..low[t-1], bottom rises to the highest later high.
    On an ex-dividend bar t (df["dividend"] = D > 0) the prior bar's low is taken net of the
    dividend for a gap down, low[t-1] - D, in the test and as the zone's top: a drop the dividend
    explains is no gap. A dividend can only explain a drop, so a gap up keeps high[t-1] as it traded,
    in the test, the fill and the zone. Returns unfilled gaps chronologically with the remaining zone
    (top/bottom), the original one and ex_dividend (D when bar t is an ex-dividend bar, up or down,
    else None). Without a "dividend" column, or with zeros in it, nothing changes.
    """
    if len(df) < 2:
        return []
    high, low, prev_high, prev_low, div = _gap_inputs(df)
    later_low, later_high = _later_extremes(high, low)

    out = []
    for t in range(1, len(df)):
        ph, pl = prev_high[t], prev_low[t] - div[t]       # only a drop is net of the dividend (x - 0.0 == x)
        ex_div = float(div[t]) if div[t] > 0 else None
        if low[t] > ph and later_low[t] > ph:
            top = min(low[t], later_low[t])
            out.append({"date": _iso(df.index[t]), "direction": "up",
                        "top": float(top), "bottom": float(ph), "filled": False,
                        "partially_filled": bool(top < low[t]),
                        "gap_top": float(low[t]), "gap_bottom": float(ph), "ex_dividend": ex_div})
        elif high[t] < pl and later_high[t] < pl:
            bottom = max(high[t], later_high[t])
            out.append({"date": _iso(df.index[t]), "direction": "down",
                        "top": float(pl), "bottom": float(bottom), "filled": False,
                        "partially_filled": bool(bottom > high[t]),
                        "gap_top": float(pl), "gap_bottom": float(high[t]), "ex_dividend": ex_div})
    return out


def ex_dividend_gaps(df: pd.DataFrame) -> list[dict]:
    """Ex-dividend bars whose raw prices gap down from the prior bar (high[t] < low[t-1]) but that
    are no gap down net of the dividend (high[t] >= low[t-1] - D, see unfilled_gaps): the drop is the
    dividend. Filled or not: raw_unfilled says whether later bars of df left the raw gap open, i.e.
    whether it would be an unfilled-gap level without the dividend adjustment. Chronological
    [{date, amount, raw_direction, raw_unfilled}]. A dividend can only explain a drop and gaps up are
    measured raw, so raw_direction is always "down" (kept for compatibility)."""
    if len(df) < 2:
        return []
    high, low, prev_high, prev_low, div = _gap_inputs(df)
    later_low, later_high = _later_extremes(high, low)
    out = []
    for t in np.flatnonzero(div > 0):
        if t == 0:
            continue
        d = div[t]
        if high[t] < prev_low[t] and not high[t] < prev_low[t] - d:
            out.append({"date": _iso(df.index[t]), "amount": float(d), "raw_direction": "down",
                        "raw_unfilled": bool(later_high[t] < prev_low[t])})
    return out


# ---------------------------------------------------------------------------- zones / tests / select
def _decay(age_bars, half_life: float):
    """Recency factor 0.5 ** (age / half_life)."""
    return 0.5 ** (np.asarray(age_bars, dtype=float) / half_life)


def density_zones(candidates: list[dict], atr: float, bound: tuple[float, float] | None = None) -> list[dict]:
    """Zones from a weighted Gaussian density of candidate prices, ascending.

    Each candidate adds weight x a kernel of width C.KDE_BANDWIDTH_ATR x atr at its price (a
    candidate with a zone spreads its weight evenly across it). Every local peak of the density is
    a zone: it spans the prices around the peak where the density is at least C.ZONE_HALF_MAX x
    the peak, without crossing the lowest point between it and a neighbouring peak, clamped to
    C.ZONE_MIN_ATR..C.ZONE_MAX_ATR ATRs around the peak and to `bound` (lo, hi). Its members are
    the candidates between those lowest points; price = weighted mean of the members inside the
    zone (the peak if none); weight = summed member weight; sources = the sources of the members
    inside the zone.
    """
    pts = [c for c in candidates if _num(c.get("price")) is not None and float(c.get("weight", 1.0)) > 0]
    if not pts:
        return []
    bw = C.KDE_BANDWIDTH_ATR * atr
    xs, ws = [], []
    for c in pts:
        w = float(c.get("weight", 1.0))
        lo, hi = float(c.get("zone_low", c["price"])), float(c.get("zone_high", c["price"]))
        k = max(1, int(math.ceil((hi - lo) / (bw / 2.0))) + 1) if hi > lo else 1
        xs.extend(np.linspace(lo, hi, k) if k > 1 else [float(c["price"])])
        ws.extend([w / k] * k)
    xs, ws = np.array(xs), np.array(ws)
    step = max(bw / 20.0, (xs.max() - xs.min() + 6 * bw) / 4000.0)
    grid = np.arange(xs.min() - 3 * bw, xs.max() + 3 * bw + step, step)
    dens = np.exp(-0.5 * ((grid[:, None] - xs[None, :]) / bw) ** 2) @ ws
    up = np.r_[True, dens[1:] > dens[:-1]]          # rising into i
    down = np.r_[dens[:-1] >= dens[1:], True]       # not rising out of i
    peaks = np.flatnonzero(up & down & (dens > 1e-12 * dens.max()))
    cuts = [0] + [p + int(np.argmin(dens[p:q + 1])) for p, q in zip(peaks[:-1], peaks[1:])] + [len(grid) - 1]
    prices = np.array([float(c["price"]) for c in pts])
    out = []
    for n, p in enumerate(peaks):
        a, b = cuts[n], cuts[n + 1]
        half = C.ZONE_HALF_MAX * dens[p]
        i = p
        while i > a and dens[i - 1] >= half:
            i -= 1
        j = p
        while j < b and dens[j + 1] >= half:
            j += 1
        peak = float(grid[p])
        lo = max(float(grid[i]), peak - C.ZONE_MAX_ATR * atr / 2.0)
        hi = min(float(grid[j]), peak + C.ZONE_MAX_ATR * atr / 2.0)
        if hi - lo < C.ZONE_MIN_ATR * atr:
            lo, hi = peak - C.ZONE_MIN_ATR * atr / 2.0, peak + C.ZONE_MIN_ATR * atr / 2.0
        if bound is not None:
            lo, hi = max(lo, bound[0]), min(hi, bound[1])
            if hi <= lo:
                continue
        members = [c for c, x in zip(pts, prices) if (x >= grid[a] if n == 0 else x > grid[a]) and x <= grid[b]]
        if not members:
            continue
        inside = [c for c in members if lo <= c["price"] <= hi]
        price = (sum(c["price"] * c.get("weight", 1.0) for c in inside) / sum(c.get("weight", 1.0) for c in inside)
                 if inside else min(max(peak, lo), hi))
        # sources name only the candidates inside the band, so a 52-week extreme outside it does not count
        sources = list(dict.fromkeys(m["source"] for m in inside))
        out.append({"price": float(price), "zone_low": float(lo), "zone_high": float(hi),
                    "sources": sources, "source_types": list(dict.fromkeys(map(source_type, sources))),
                    "n_candidates": len(members),
                    "weight": float(sum(float(m.get("weight", 1.0)) for m in members))})
    return out


def zone_tests(df: pd.DataFrame, lo: float, hi: float, atr: float) -> list[dict]:
    """Earlier tests of the zone [lo, hi] in df, chronological.

    With tolerance t = C.TEST_TOL_ATR x atr, a bar is above the zone when its low > hi + t, below
    it when its high < lo - t, and in it otherwise. A test is a run of bars in the zone between a
    bar on one side (the entry side) and the next bar outside it, or a jump straight from one side
    to the other. It is a break when price leaves on the other side, a hold when it leaves on the
    entry side and then trades C.REACTION_ATR x atr beyond the near edge within C.REACTION_BARS
    bars (before testing again), and weak otherwise. A hold right after a break is a flip (role
    reversal). Tests still open at the last bar, holds still able to complete and a run at the
    first bar (no entry side) are left out. Each test: {end (bar index), entry, result, flip}.
    """
    high = df["high"].to_numpy(dtype=float, na_value=np.nan)
    low = df["low"].to_numpy(dtype=float, na_value=np.nan)
    tol = C.TEST_TOL_ATR * atr
    state = np.where(low > hi + tol, 1, np.where(high < lo - tol, -1, 0))
    n = len(state)
    out: list[dict] = []
    i = 0
    while i < n and state[i] == 0:                  # in the zone at the start: no entry side
        i += 1
    while i < n:
        entry = int(state[i])                       # bar i is outside the zone
        j = i + 1
        while j < n and state[j] == 0:
            j += 1
        if j >= n:
            break                                   # still in the zone (or never came back) at the last bar
        if j == i + 1 and state[j] == entry:
            i = j                                   # stayed on the same side
            continue
        if state[j] != entry:
            result = "break"
        else:
            nxt = j
            while nxt < n and state[nxt] == entry:
                nxt += 1                            # bars until the next test
            stop = min(nxt, j + C.REACTION_BARS, n)
            far = (high[j:stop].max() >= hi + C.REACTION_ATR * atr) if entry > 0 else \
                  (low[j:stop].min() <= lo - C.REACTION_ATR * atr)
            if far:
                result = "hold"
            elif stop == n and nxt == n and n - j < C.REACTION_BARS:
                i = j
                continue                            # could still become a hold
            else:
                result = "weak"
        flip = result == "hold" and bool(out) and out[-1]["result"] == "break"
        out.append({"end": j, "entry": entry, "result": result, "flip": flip})
        i = j
    return out


def score_zones(zones: list[dict], df: pd.DataFrame, profile: dict, atr: float, half_life: float) -> list[dict]:
    """Copies of the zones with their test record, volume_share and score.

    score = sum over tests of recency x (C.LEVEL_HOLD_POINTS per hold, + C.LEVEL_FLIP_POINTS when
    it is a flip, - C.LEVEL_BREAK_POINTS per break) + C.LEVEL_CONFLUENCE_WEIGHT x zone weight,
    recency = 0.5 ** (bars since the test / half_life). touches = number of tests = holds + breaks
    + weak; flips are holds; fresh = no test.
    volume_share = profile volume inside the zone / total profile volume (shown, not scored).
    """
    n = len(df)
    out = []
    for z in zones:
        tests = zone_tests(df, z["zone_low"], z["zone_high"], atr)
        pts = 0.0
        for t in tests:
            r = float(_decay(n - 1 - t["end"], half_life))
            if t["result"] == "hold":
                pts += r * (C.LEVEL_HOLD_POINTS + (C.LEVEL_FLIP_POINTS if t["flip"] else 0.0))
            elif t["result"] == "break":
                pts -= r * C.LEVEL_BREAK_POINTS
        share = (_zone_volume(profile, z["zone_low"], z["zone_high"]) / profile["total_volume"]
                 if profile.get("available") else 0.0)
        holds = sum(t["result"] == "hold" for t in tests)
        last = max((t["end"] for t in tests), default=None)
        out.append({**z, "touches": len(tests), "holds": int(holds),
                    "breaks": int(sum(t["result"] == "break" for t in tests)),
                    "weak": int(sum(t["result"] == "weak" for t in tests)),
                    "flips": int(sum(t["flip"] for t in tests)), "fresh": not tests,
                    "last_test": _iso(df.index[last]) if last is not None else None,
                    "reaction_score": float(pts), "volume_share": float(share),
                    "score": float(pts + C.LEVEL_CONFLUENCE_WEIGHT * z.get("weight", 0.0))})
    return out


def qualifies(cluster: dict, min_score: float = C.LEVEL_MIN_SCORE) -> bool:
    """Strong enough to be a key level: score >= min_score, or the zone holds the 52-week high
    or low (structural extremes, which price has rarely revisited by construction)."""
    extreme = {"high_52w", "low_52w"} & set(cluster.get("sources") or ())
    return bool(extreme) or float(cluster.get("score") or 0.0) >= min_score


def select_levels(clusters: list[dict], close: float, atr: float, per_side: int = C.LEVELS_PER_SIDE,
                  min_score: float = C.LEVEL_MIN_SCORE, reach_atr: float = math.inf) -> tuple[list[dict], list[dict]]:
    """(supports, resistances): per side, the nearest qualifying zone plus the highest-scoring
    other qualifying zones within reach_atr ATRs of the close (nearest ones fill any shortfall),
    per_side in all, nearest first, with signed distances (price / close - 1 and
    (price - close) / atr; negative below).

    Supports lie wholly below the close (zone_high < close), resistances wholly above it
    (zone_low > close); a zone containing the close belongs to neither side.
    """
    def with_distance(c: dict) -> dict:
        return {**c, "distance_pct": c["price"] / close - 1.0, "distance_atr": (c["price"] - close) / atr}

    def pick(side: list[dict]) -> list[dict]:
        if not side:
            return []
        rest = side[1:]
        near = [c for c in rest if abs(c["price"] - close) <= reach_atr * atr]
        best = sorted(near, key=lambda c: -float(c.get("score") or 0.0))[:per_side - 1]
        fill = [c for c in rest if c not in best][:max(0, per_side - 1 - len(best))]
        chosen = [side[0]] + best + fill
        return sorted(chosen, key=lambda c: abs(c["price"] - close))

    strong = [c for c in clusters if qualifies(c, min_score)]
    below = sorted((c for c in strong if c.get("zone_high", c["price"]) < close), key=lambda c: close - c["price"])
    above = sorted((c for c in strong if c.get("zone_low", c["price"]) > close), key=lambda c: c["price"] - close)
    return [with_distance(c) for c in pick(below)], [with_distance(c) for c in pick(above)]


# ---------------------------------------------------------------------------- builder
def _range_52w(bars: pd.DataFrame, notes: list) -> dict:
    recent = bars.iloc[-C.HIGH_52W_BARS:]
    if len(recent) < C.HIGH_52W_BARS:
        notes.append(f"52-week high/low computed over {len(recent)} bars (fewer than {C.HIGH_52W_BARS})")
    return {"high_52w": _num(recent["high"].max()), "high_52w_date": _iso(recent["high"].idxmax()),
            "low_52w": _num(recent["low"].min()), "low_52w_date": _iso(recent["low"].idxmin()),
            "range_52w_bars": len(recent)}


def _avwap_candidates(ctx, last_date: pd.Timestamp, notes: list) -> list[dict]:
    """Anchored-VWAP values at the last bar from modules.anchored_vwap_series, for anchors at least
    C.LEVEL_AVWAP_MIN_BARS bars old (a younger VWAP only averages the last few bars); none if it fails."""
    try:
        from modules import anchored_vwap_series
        series = anchored_vwap_series(ctx)
    except Exception as exc:  # the levels stand without AVWAPs; record why they are missing
        notes.append(f"anchored VWAPs not used as level candidates ({type(exc).__name__}: {exc})")
        return []
    out = []
    for name, s in series.items():
        s = s.loc[s.index <= last_date]
        value, first = _num(s.get(last_date)), s.first_valid_index()
        if value is None or first is None:
            continue
        age = int((s.index > first).sum())
        if age < C.LEVEL_AVWAP_MIN_BARS:
            notes.append(f"anchored VWAP {name} not used as a level: anchored {age} bar(s) ago "
                         f"(needs {C.LEVEL_AVWAP_MIN_BARS})")
            continue
        out.append({"price": value, "source": f"avwap_{name}"})
    return out


def _gap_candidates(g: dict, atr: float) -> list[dict]:
    """One zone candidate for a gap at most C.GAP_ZONE_MAX_ATR ATRs wide, else its two edges."""
    if g["top"] - g["bottom"] <= C.GAP_ZONE_MAX_ATR * atr:
        return [{"price": (g["top"] + g["bottom"]) / 2.0, "zone_low": g["bottom"], "zone_high": g["top"],
                 "source": f"gap_{g['direction']}", "date": g["date"]}]
    return [{"price": g["top"], "source": f"gap_{g['direction']}_top", "date": g["date"]},
            {"price": g["bottom"], "source": f"gap_{g['direction']}_bottom", "date": g["date"]}]


def _gap_adjustment(gap_bars: pd.DataFrame, window: pd.DataFrame, ctx, notes: list) -> dict:
    """levels.gap_adjustment: the method, the ex-dividend bars in the window and the raw gaps on them
    that the dividend explains (ex_dividend_gaps), with a note when there are any, and a note when
    the dividend data could not be loaded (gaps then stay unadjusted)."""
    neutralized = ex_dividend_gaps(gap_bars)
    if neutralized:
        gone = [g["date"] for g in neutralized if g["raw_unfilled"]]
        what = (f"{len(gone)} of them would otherwise be unfilled-gap levels ({', '.join(gone[-NOTE_DATES_MAX:])})"
                if gone else "all were filled later, so no gap level changes")
        notes.append(f"{len(neutralized)} raw gap(s) on ex-dividend bars are no larger than the dividend and are "
                     f"not treated as gaps: {what}; see gap_adjustment.neutralized")
    why = (ctx.get("skipped") or {}).get("dividends")
    if why:
        notes.append(f"dividend data unavailable ({why}): gaps on ex-dividend bars are not adjusted for the dividend")
    return {"method": GAP_DIVIDEND_METHOD, "ex_dividend_bars": int((bar_dividends(window) > 0).sum()),
            "neutralized": neutralized}


def _candidates(pivots: dict, bases: list, profile: dict, r52: dict, gaps: list, ind_row: pd.Series,
                avwaps: list, atr: float, age, notes: list) -> list[dict]:
    """Weighted candidates: C.LEVEL_SOURCE_WEIGHTS by source type, x swing size for pivots, x
    C.LEVEL_FRESH_FACTOR for untested supply/demand zones, x recency (age(date)) when dated."""
    W = C.LEVEL_SOURCE_WEIGHTS
    cands = []
    for side, source in (("highs", "pivot_high"), ("lows", "pivot_low")):
        for p in pivots[side]:
            size = min(max((p["swing_atr"] or C.SWING_ATR) / C.SWING_ATR, 1.0), C.SWING_WEIGHT_CAP)
            cands.append({"price": p["price"], "source": source, "date": p["date"],
                          "weight": W["pivot"] * size * age(p["date"])})
    for z in bases:
        w = W["supply_demand"] * (C.LEVEL_FRESH_FACTOR if z["fresh"] else 1.0) * age(z["date"])
        cands.append({"price": (z["zone_low"] + z["zone_high"]) / 2.0, "zone_low": z["zone_low"],
                      "zone_high": z["zone_high"], "source": z["kind"], "date": z["date"], "weight": w})
    if profile["available"]:
        vp, edge = W["volume_profile"], W["volume_profile"] * C.LEVEL_VALUE_AREA_WEIGHT
        cands += [{"price": profile["poc"], "source": "poc", "weight": vp},
                  {"price": profile["value_area_low"], "source": "value_area_low", "weight": edge},
                  {"price": profile["value_area_high"], "source": "value_area_high", "weight": edge}]
        cands += [{"price": p, "source": "hvn", "weight": edge} for p in profile["hvn"]]
    cands += [{"price": r52["high_52w"], "source": "high_52w", "date": r52["high_52w_date"], "weight": W["range_52w"]},
              {"price": r52["low_52w"], "source": "low_52w", "date": r52["low_52w_date"], "weight": W["range_52w"]}]
    for g in gaps:
        cands += [{**c, "weight": W["gap"] * age(g["date"])} for c in _gap_candidates(g, atr)]
    for name in ("sma50", "sma200"):
        v = _num(ind_row.get(name))
        if v is None:
            notes.append(f"{name.upper()} unavailable at the last bar: not used as a level")
        else:
            cands.append({"price": v, "source": name, "weight": W["moving_average"]})
    return cands + [{**c, "weight": W["anchored_vwap"]} for c in avwaps]


def _extremes(bars: pd.DataFrame, r52: dict, close: float, near: float) -> dict:
    """Whether the close is at the 52-week high/low and at the high/low of all available history:
    the highest (lowest) close of the period, the last bar included, or within `near` of the
    period's highest high (lowest low). A record close that ends well below its own bar's high is
    still at the high."""
    recent = bars.iloc[-C.HIGH_52W_BARS:]
    return {"at_high_52w": bool(close >= recent["close"].max() or close >= r52["high_52w"] - near),
            "at_low_52w": bool(close <= recent["close"].min() or close <= r52["low_52w"] + near),
            "at_all_time_high": bool(close >= bars["close"].max() or close >= bars["high"].max() - near),
            "at_all_time_low": bool(close <= bars["close"].min() or close <= bars["low"].min() + near),
            "rule": EXTREMES_RULE.format(m=C.EXTREME_NEAR_ATR)}


def _shortfall_note(side: str, n: int, weak: int, extremes: dict) -> str:
    """Note for a side with fewer than C.LEVELS_PER_SIDE levels, naming the extreme price is at."""
    word, where = ("high", "above") if side == "resistance" else ("low", "below")
    msg = (f"no {side} levels {where} the last close in the window" if n == 0
           else f"only {n} {side} level(s) {where} the last close in the window")
    if weak:
        msg += f" ({weak} weaker zone(s) with score < {C.LEVEL_MIN_SCORE:g} not used)"
    if extremes[f"at_all_time_{word}"]:
        return f"price at an all-time {word} (available history): {msg}"
    if extremes[f"at_{word}_52w"]:
        return f"price at a 52-week {word}: {msg}"
    return msg


def build_levels(ctx) -> dict:
    """H: swing pivots, supply/demand zones, volume profile, 52-week range, gaps, SMAs and anchored
    VWAPs as weighted candidates; per side of the close, density zones scored by earlier reactions
    and candidate weight; the nearest qualifying zone and the strongest within reach, up to
    C.LEVELS_PER_SIDE supports and resistances around the last close."""
    notes: list[str] = []
    bars = ctx.get("bars")
    if bars is not None and ctx.get("as_of"):
        bars = bars.loc[bars.index <= pd.Timestamp(ctx["as_of"])]
    if bars is None or len(bars) == 0:
        return {"available": False, "reason": "no price bars up to as_of", "notes": notes}
    last_date = bars.index[-1]
    ind = ctx["ind"].loc[:last_date]
    ind_row = ind.loc[last_date]
    close, atr = _num(bars["close"].iloc[-1]), _num(ind_row.get("atr14"))
    if close is None or atr is None or atr <= 0:
        return {"available": False, "notes": notes,
                "reason": f"close or ATR({C.ATR_PERIOD}) unavailable at {_iso(last_date)} "
                          f"(levels are built and measured in ATR units)"}

    lookback = int(ctx["hz"]["levels_lookback"])
    reach = float(ctx["hz"].get("levels_reach_atr", math.inf))
    window = bars.iloc[-lookback:]
    if len(window) < lookback:
        notes.append(f"levels window has only {len(window)} bars (lookback {lookback})")
    start = _iso(window.index[0])
    half_life = C.LEVEL_HALF_LIFE * lookback
    pos = {_iso(d): i for i, d in enumerate(bars.index)}

    def age(date: str) -> float:
        return float(_decay(len(bars) - 1 - pos.get(date, len(bars) - 1), half_life))

    atr_s = ind["atr14"]
    lead = bars.iloc[-(lookback + C.IMPULSE_BARS * 5):]   # a few bars before the window to start the swing count
    raw = swing_pivots(lead, atr_s, C.SWING_ATR)
    pivots = {side: [p for p in raw[side] if p["date"] >= start] for side in ("highs", "lows")}
    bases = base_zones(window, atr_s)
    profile = volume_profile(window, C.PROFILE_BINS)
    if not profile["available"]:
        notes.append(f"volume profile skipped: {profile['reason']}")
    gap_bars = bars.iloc[-(lookback + 1):]                # one extra bar: the gap into the window's first bar
    gaps = unfilled_gaps(gap_bars)
    gap_adjustment = _gap_adjustment(gap_bars, window, ctx, notes)
    r52 = _range_52w(bars, notes)

    avwaps = _avwap_candidates({**ctx, "bars": bars, "ind": ind}, last_date, notes)
    candidates = _candidates(pivots, bases, profile, r52, gaps, ind_row, avwaps, atr, age, notes)
    # each side is built on its own and bounded by the close, so no zone straddles it; a candidate
    # exactly at the close is on neither side
    below = density_zones([c for c in candidates if c["price"] < close], atr,
                          (-math.inf, float(np.nextafter(close, -math.inf))))
    above = density_zones([c for c in candidates if c["price"] > close], atr,
                          (float(np.nextafter(close, math.inf)), math.inf))
    zones = score_zones(below + above, window, profile, atr, half_life)
    supports, resistances = select_levels(zones, close, atr, C.LEVELS_PER_SIDE, C.LEVEL_MIN_SCORE, reach)

    extremes = _extremes(bars, r52, close, C.EXTREME_NEAR_ATR * atr)
    weak = {"support": sum(not qualifies(c, C.LEVEL_MIN_SCORE) for c in zones[:len(below)]),
            "resistance": sum(not qualifies(c, C.LEVEL_MIN_SCORE) for c in zones[len(below):])}
    for side, levels in (("support", supports), ("resistance", resistances)):
        if len(levels) < C.LEVELS_PER_SIDE:
            notes.append(_shortfall_note(side, len(levels), weak[side], extremes))

    return {
        "available": True,
        "as_of": _iso(last_date),
        "last_close": close,
        "atr": atr,
        "lookback_bars": len(window),
        "window_start": start,
        "window_end": _iso(last_date),
        "pivots": {"method": "atr_zigzag", "swing_atr": C.SWING_ATR, **pivots,
                   "note": PIVOT_NOTE.format(m=C.SWING_ATR)},
        "supply_demand": bases,
        "volume_profile": profile,
        **r52,
        "extremes": extremes,
        "gaps": gaps,
        "gap_adjustment": gap_adjustment,
        "candidates": candidates,
        "n_zones": len(zones),
        "selection": {"min_score": C.LEVEL_MIN_SCORE, "reach_atr": reach, "weak_zones": weak,
                      "rule": f"per side: the nearest zone with score >= {C.LEVEL_MIN_SCORE:g} (or holding "
                              f"the 52-week high/low), then the highest-scoring such zones within "
                              f"{reach:g} ATR of the close, {C.LEVELS_PER_SIDE} in all"},
        "supports": supports,
        "resistances": resistances,
        "evidence": {"note": C.LEVELS_EVIDENCE_NOTE},
        "notes": notes,
    }
