"""
Key levels (module H): fractal swing pivots, a volume profile, the 52-week range and unfilled
gaps, plus the current SMA50/SMA200 and anchored VWAPs, merged into ranked support and resistance
zones around the last close.

Everything uses split-adjusted OHLC (the prices that actually traded, continuous across splits)
over the last ctx["hz"]["levels_lookback"] bars, as of the last complete bar. Definitions are
documented in references/methodology.md.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

import config as C

LEVEL_SOURCE_BONUS = getattr(C, "LEVEL_SOURCE_BONUS", 0.5)  # score per extra distinct source type

PROFILE_NOTE = "approximation: each bar's volume spread evenly across its high-low range"
PIVOT_NOTE = ("a pivot needs {k} bars on each side inside the data, so the last {k} bars cannot be "
              "pivots yet (the only right-side dependence; levels are measured as of the last bar)")

_SOURCE_TYPES = {
    "pivot_high": "pivot", "pivot_low": "pivot",
    "poc": "volume_profile", "value_area_low": "volume_profile",
    "value_area_high": "volume_profile", "hvn": "volume_profile",
    "high_52w": "range_52w", "low_52w": "range_52w",
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
    """Family of a candidate source; each family counts once in the multi-source score bonus."""
    if source.startswith("gap_"):
        return "gap"
    if source.startswith("avwap_"):
        return "anchored_vwap"
    return _SOURCE_TYPES.get(source, source)


# ---------------------------------------------------------------------------- pivots
def fractal_pivots(df: pd.DataFrame, k: int = C.FRACTAL_K) -> dict:
    """Fractal swing pivots from df's high and low columns.

    Bar i is a pivot high when high[i] is strictly greater than every other high in bars
    i-k..i+k; a pivot low is the strictly lowest low. Equal extremes never form a pivot, and the
    first and last k bars of df cannot be pivots (no full window).
    Returns {"highs": [{date, price}], "lows": [...]}, chronological.
    """
    return {"highs": _fractal_side(df["high"], k, np.greater),
            "lows": _fractal_side(df["low"], k, np.less)}


def _fractal_side(s: pd.Series, k: int, beats) -> list[dict]:
    x = s.to_numpy(dtype=float, na_value=np.nan)
    if len(x) < 2 * k + 1:
        return []
    win = sliding_window_view(x, 2 * k + 1)          # row j covers bars j..j+2k, centre j+k
    others = np.delete(win, k, axis=1)
    is_pivot = beats(win[:, k:k + 1], others).all(axis=1)   # a NaN anywhere compares False
    return [{"date": _iso(s.index[i]), "price": float(x[i])} for i in np.flatnonzero(is_pivot) + k]


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
def unfilled_gaps(df: pd.DataFrame) -> list[dict]:
    """Price gaps between consecutive bars of df that later bars have not fully filled.

    Gap up at t: low[t] > high[t-1], zone high[t-1]..low[t]; filled once a later bar's low is
    <= high[t-1], otherwise its top shrinks to the lowest later low (partially filled). Gap down
    mirrors it: high[t] < low[t-1], zone high[t]..low[t-1], bottom rises to the highest later high.
    Returns unfilled gaps chronologically with the remaining zone (top/bottom) and the original one.
    """
    high = df["high"].to_numpy(dtype=float, na_value=np.nan)
    low = df["low"].to_numpy(dtype=float, na_value=np.nan)
    if len(df) < 2:
        return []
    # lowest low / highest high strictly after each bar (+-inf after the last bar)
    later_low = np.r_[np.fmin.accumulate(low[::-1])[::-1][1:], np.inf]
    later_high = np.r_[np.fmax.accumulate(high[::-1])[::-1][1:], -np.inf]
    later_low = np.where(np.isnan(later_low), np.inf, later_low)
    later_high = np.where(np.isnan(later_high), -np.inf, later_high)

    out = []
    for t in range(1, len(df)):
        if low[t] > high[t - 1] and later_low[t] > high[t - 1]:
            top = min(low[t], later_low[t])
            out.append({"date": _iso(df.index[t]), "direction": "up",
                        "top": float(top), "bottom": float(high[t - 1]), "filled": False,
                        "partially_filled": bool(top < low[t]),
                        "gap_top": float(low[t]), "gap_bottom": float(high[t - 1])})
        elif high[t] < low[t - 1] and later_high[t] < low[t - 1]:
            bottom = max(high[t], later_high[t])
            out.append({"date": _iso(df.index[t]), "direction": "down",
                        "top": float(low[t - 1]), "bottom": float(bottom), "filled": False,
                        "partially_filled": bool(bottom > high[t]),
                        "gap_top": float(low[t - 1]), "gap_bottom": float(high[t])})
    return out


# ---------------------------------------------------------------------------- merge / rank / select
def merge_levels(candidates: list[dict], atr: float, merge_atr: float = C.LEVEL_MERGE_ATR) -> list[dict]:
    """Greedy one-pass clustering of candidate prices, ascending.

    A candidate joins the current cluster when it is within merge_atr x atr of the cluster's
    running mean price (simple mean of members), otherwise it starts a new cluster. Cluster
    price = mean of member prices; zone_low/zone_high = lowest/highest member price.
    """
    pts = sorted((c for c in candidates if _num(c.get("price")) is not None), key=lambda c: c["price"])
    tol = merge_atr * atr
    groups: list[list[dict]] = []
    for c in pts:
        if groups and abs(c["price"] - _mean_price(groups[-1])) <= tol:
            groups[-1].append(c)
        else:
            groups.append([c])
    return [_cluster(g) for g in groups]


def _mean_price(members: list[dict]) -> float:
    return sum(float(m["price"]) for m in members) / len(members)


def _cluster(members: list[dict]) -> dict:
    prices = [float(m["price"]) for m in members]
    sources = list(dict.fromkeys(m["source"] for m in members))
    return {"price": _mean_price(members), "zone_low": min(prices), "zone_high": max(prices),
            "sources": sources, "source_types": list(dict.fromkeys(map(source_type, sources))),
            "n_candidates": len(members)}


def score_levels(clusters: list[dict], df: pd.DataFrame, profile: dict, atr: float) -> list[dict]:
    """Copies of the clusters with touches, volume_share and score.

    Band = [zone_low - C.TOUCH_ATR x atr, zone_high + C.TOUCH_ATR x atr]. touches = bars of df
    whose high or low lies in the band (a bar that only crosses it with its body does not
    count); volume_share = profile volume inside the band / total profile volume.
    score = touches / max touches + volume_share / max volume_share (over all clusters)
            + LEVEL_SOURCE_BONUS x (distinct source types - 1).
    """
    if not clusters:
        return []
    tol = C.TOUCH_ATR * atr
    band_lo = np.array([c["zone_low"] for c in clusters]) - tol
    band_hi = np.array([c["zone_high"] for c in clusters]) + tol
    high = df["high"].to_numpy(dtype=float, na_value=np.nan)[:, None]
    low = df["low"].to_numpy(dtype=float, na_value=np.nan)[:, None]
    touched = ((high >= band_lo) & (high <= band_hi)) | ((low >= band_lo) & (low <= band_hi))
    touches = touched.sum(axis=0)
    if profile.get("available"):
        share = np.array([_zone_volume(profile, a, b) for a, b in zip(band_lo, band_hi)])
        share = share / profile["total_volume"]
    else:
        share = np.zeros(len(clusters))
    t_norm = touches / touches.max() if touches.max() > 0 else np.zeros(len(clusters))
    v_norm = share / share.max() if share.max() > 0 else np.zeros(len(clusters))
    return [{**c, "touches": int(t), "volume_share": float(v),
             "score": float(tn + vn + LEVEL_SOURCE_BONUS * (len(c["source_types"]) - 1))}
            for c, t, v, tn, vn in zip(clusters, touches, share, t_norm, v_norm)]


def select_levels(clusters: list[dict], close: float, atr: float,
                  per_side: int = C.LEVELS_PER_SIDE) -> tuple[list[dict], list[dict]]:
    """(supports, resistances): the per_side clusters nearest the close below / above it, nearest
    first, with signed distances (price / close - 1 and (price - close) / atr; negative below)."""
    def with_distance(c: dict) -> dict:
        return {**c, "distance_pct": c["price"] / close - 1.0, "distance_atr": (c["price"] - close) / atr}

    below = sorted((c for c in clusters if c["price"] < close), key=lambda c: close - c["price"])
    above = sorted((c for c in clusters if c["price"] > close), key=lambda c: c["price"] - close)
    return [with_distance(c) for c in below[:per_side]], [with_distance(c) for c in above[:per_side]]


# ---------------------------------------------------------------------------- builder
def _range_52w(bars: pd.DataFrame, notes: list) -> dict:
    recent = bars.iloc[-C.HIGH_52W_BARS:]
    if len(recent) < C.HIGH_52W_BARS:
        notes.append(f"52-week high/low computed over {len(recent)} bars (fewer than {C.HIGH_52W_BARS})")
    return {"high_52w": _num(recent["high"].max()), "high_52w_date": _iso(recent["high"].idxmax()),
            "low_52w": _num(recent["low"].min()), "low_52w_date": _iso(recent["low"].idxmin()),
            "range_52w_bars": len(recent)}


def _avwap_candidates(ctx, last_date: pd.Timestamp, notes: list) -> list[dict]:
    """Anchored-VWAP values at the last bar from modules.anchored_vwap_series; none if it fails."""
    try:
        from modules import anchored_vwap_series
        values = {name: _num(s.get(last_date)) for name, s in anchored_vwap_series(ctx).items()}
    except Exception as exc:  # the levels stand without AVWAPs; record why they are missing
        notes.append(f"anchored VWAPs not used as level candidates ({type(exc).__name__}: {exc})")
        return []
    return [{"price": v, "source": f"avwap_{name}"} for name, v in values.items() if v is not None]


def _candidates(pivots: dict, profile: dict, r52: dict, gaps: list, ind_row: pd.Series,
                avwaps: list, notes: list) -> list[dict]:
    cands = [{"price": p["price"], "source": "pivot_high", "date": p["date"]} for p in pivots["highs"]]
    cands += [{"price": p["price"], "source": "pivot_low", "date": p["date"]} for p in pivots["lows"]]
    if profile["available"]:
        cands += [{"price": profile["poc"], "source": "poc"},
                  {"price": profile["value_area_low"], "source": "value_area_low"},
                  {"price": profile["value_area_high"], "source": "value_area_high"}]
        cands += [{"price": p, "source": "hvn"} for p in profile["hvn"]]
    cands += [{"price": r52["high_52w"], "source": "high_52w", "date": r52["high_52w_date"]},
              {"price": r52["low_52w"], "source": "low_52w", "date": r52["low_52w_date"]}]
    for g in gaps:
        cands += [{"price": g["top"], "source": f"gap_{g['direction']}_top", "date": g["date"]},
                  {"price": g["bottom"], "source": f"gap_{g['direction']}_bottom", "date": g["date"]}]
    for name in ("sma50", "sma200"):
        v = _num(ind_row.get(name))
        if v is None:
            notes.append(f"{name.upper()} unavailable at the last bar: not used as a level")
        else:
            cands.append({"price": v, "source": name})
    return cands + avwaps


def _extremes(bars: pd.DataFrame, r52: dict, close: float, near: float) -> dict:
    """Whether the close is within `near` of the 52-week high/low, and whether that extreme is
    also the extreme of all available history."""
    at_high = bool(close >= r52["high_52w"] - near)
    at_low = bool(close <= r52["low_52w"] + near)
    return {"at_high_52w": at_high, "at_low_52w": at_low,
            "at_all_time_high": at_high and bool(r52["high_52w"] >= bars["high"].max()),
            "at_all_time_low": at_low and bool(r52["low_52w"] <= bars["low"].min())}


def _shortfall_note(side: str, n: int, extremes: dict) -> str:
    """Note for a side with fewer than C.LEVELS_PER_SIDE levels, naming the extreme price is at."""
    word, where = ("high", "above") if side == "resistance" else ("low", "below")
    msg = (f"no {side} levels {where} the last close in the window" if n == 0
           else f"only {n} {side} level(s) {where} the last close in the window")
    if extremes[f"at_all_time_{word}"]:
        return f"price at an all-time {word} (available history): {msg}"
    if extremes[f"at_{word}_52w"]:
        return f"price at a 52-week {word}: {msg}"
    return msg


def build_levels(ctx) -> dict:
    """H: pivots, volume profile, 52-week range and gaps merged into the nearest supports and
    resistances (C.LEVELS_PER_SIDE each) around the last close."""
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
                          f"(levels are merged and measured in ATR units)"}

    lookback = int(ctx["hz"]["levels_lookback"])
    window = bars.iloc[-lookback:]
    if len(window) < lookback:
        notes.append(f"levels window has only {len(window)} bars (lookback {lookback})")
    start = _iso(window.index[0])

    k = C.FRACTAL_K
    raw = fractal_pivots(bars.iloc[-(lookback + k):], k)  # +k bars: a left side for the first window bars
    pivots = {side: [p for p in raw[side] if p["date"] >= start] for side in ("highs", "lows")}
    profile = volume_profile(window, C.PROFILE_BINS)
    if not profile["available"]:
        notes.append(f"volume profile skipped: {profile['reason']}")
    gaps = unfilled_gaps(bars.iloc[-(lookback + 1):])     # one extra bar: the gap into the window's first bar
    r52 = _range_52w(bars, notes)

    avwaps = _avwap_candidates({**ctx, "bars": bars, "ind": ind}, last_date, notes)
    candidates = _candidates(pivots, profile, r52, gaps, ind_row, avwaps, notes)
    clusters = score_levels(merge_levels(candidates, atr, C.LEVEL_MERGE_ATR), window, profile, atr)
    supports, resistances = select_levels(clusters, close, atr, C.LEVELS_PER_SIDE)

    extremes = _extremes(bars, r52, close, C.LEVEL_MERGE_ATR * atr)
    for side, levels in (("support", supports), ("resistance", resistances)):
        if len(levels) < C.LEVELS_PER_SIDE:
            notes.append(_shortfall_note(side, len(levels), extremes))

    return {
        "available": True,
        "as_of": _iso(last_date),
        "last_close": close,
        "atr": atr,
        "lookback_bars": len(window),
        "window_start": start,
        "window_end": _iso(last_date),
        "pivots": {"k": k, **pivots, "note": PIVOT_NOTE.format(k=k)},
        "volume_profile": profile,
        **r52,
        "extremes": extremes,
        "gaps": gaps,
        "candidates": candidates,
        "n_clusters": len(clusters),
        "supports": supports,
        "resistances": resistances,
        "notes": notes,
    }
