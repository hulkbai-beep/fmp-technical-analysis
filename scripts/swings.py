"""
Swings: ATR ZigZag swing pivots on daily and weekly bars, and what classic chart reading takes from
them: the Dow-theory structure of the last swings (higher highs and higher lows, the price that ends
the sequence) and trendlines along the swing lows / highs.

Everything is measured as of the last bar given. A swing high (low) is a pivot once price has reversed
C.SWING_ATR ATRs from it, so the swing in progress is not one yet: level candidates and trendlines use
confirmed pivots only, and the structure adds the swing in progress only once a close has passed the
last swing of its side (marked provisional).
levels.py (candidates, trendlines), modules.py (trend_momentum.structure) and regime.py (the weekly
structure) share these functions, so "a swing" means the same thing in every module. Definitions are
documented in references/methodology.md.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config as C
import indicators as I

STRUCTURE_RULE = ("uptrend: the last swing high and the last swing low are both higher than the ones before "
                  "(by more than {e:g} ATR); downtrend: both lower; anything else is mixed. Swings are confirmed "
                  "ATR ZigZag pivots ({m:g} ATR); the swing in progress counts as a new high (low) once a close "
                  "is above the last swing high (below the last swing low). An uptrend's sequence ends with a "
                  "close below its last swing low, a downtrend's with a close above its last swing high (Dow theory)")
TRENDLINE_RULE = ("up: from the lowest swing low of the window through the later swing low that keeps every later "
                  "swing low on or above the line; when those two are less than {b} bars apart, or the line rises "
                  "less than {s:g} ATR a bar (a level, not a trend), it starts from the second one instead. down: "
                  "the mirror through swing highs. A swing within {t:g} ATR of the line touches it (three touches "
                  "confirm it); a close more than {t:g} ATR beyond it breaks it. Straight on an arithmetic price "
                  "scale, one step per trading day")

CHANNEL_RULE = ("the channel line runs parallel to a trendline through the swing on the other side that lies farthest "
                "from it between the trendline's two points (Murphy: from the first prominent peak between the two "
                "lows of an up trendline). A bar after the trendline's second point whose high (low for a down "
                "channel) went more than {t:g} ATR beyond the channel line overshot it")


def _num(x) -> float | None:
    """Plain float, or None for missing / non-finite values."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------- swing pivots
def zigzag(high: np.ndarray, low: np.ndarray, atr: np.ndarray, mult: float = C.SWING_ATR) -> tuple[list, float]:
    """The ATR ZigZag on arrays: ([(bar, +1 high / -1 low, price, confirming bar)], the running extreme of
    the swing in progress, NaN before the first pivot).

    The running high (low) since the last pivot becomes a pivot high (low) once a later bar's low (high)
    is at least mult x ATR of that later bar below (above) it; pivots alternate. A pivot and its
    confirming bar depend on the bars up to that confirming bar only, so a longer series keeps them.
    """
    piv: list[tuple[int, int, float, int]] = []
    direction, hi_i, lo_i = 0, 0, 0
    for i in range(len(high)):
        if not (np.isfinite(high[i]) and np.isfinite(low[i])):
            continue
        if direction >= 0 and (not np.isfinite(high[hi_i]) or high[i] > high[hi_i]):
            hi_i = i
        if direction <= 0 and (not np.isfinite(low[lo_i]) or low[i] < low[lo_i]):
            lo_i = i
        thr = mult * atr[i]
        if not np.isfinite(thr):
            continue
        # a pivot is confirmed by a later bar only: the extreme must sit strictly before bar i
        top = direction >= 0 and hi_i < i and low[i] <= high[hi_i] - thr
        bottom = direction <= 0 and lo_i < i and high[i] >= low[lo_i] + thr
        if top and bottom:                       # undecided start: the earlier extreme came first
            top, bottom = hi_i < lo_i, hi_i >= lo_i
        if top:
            piv.append((hi_i, +1, float(high[hi_i]), i))
            direction, lo_i = -1, hi_i + 1 + int(np.nanargmin(low[hi_i + 1:i + 1]))
        elif bottom:
            piv.append((lo_i, -1, float(low[lo_i]), i))
            direction, hi_i = +1, lo_i + 1 + int(np.nanargmax(high[lo_i + 1:i + 1]))
    if direction > 0:                            # unconfirmed latest swing: the leg out of the last pivot
        tail = float(high[hi_i])
    elif direction < 0:
        tail = float(low[lo_i])
    else:
        tail = float("nan")
    return piv, tail


def swing_pivots(df: pd.DataFrame, atr: pd.Series, mult: float = C.SWING_ATR) -> dict:
    """ATR ZigZag swing pivots from df's high and low columns (see zigzag).

    The latest swing is never confirmed by the data, so it is not a pivot, and neither is the first
    bar (no left side; it only starts the count). Each pivot carries
    swing_atr: the smaller of the legs into and out of it (the one available at either end), in
    ATRs at the pivot bar, and confirmed: the date of the bar that confirmed it. Bar i's pivots
    depend on bars <= their confirmation only, so cutting the series later keeps them. Returns
    {"highs": [{date, price, swing_atr, confirmed}], "lows": [...]}.
    """
    high = df["high"].to_numpy(dtype=float, na_value=np.nan)
    low = df["low"].to_numpy(dtype=float, na_value=np.nan)
    a = atr.reindex(df.index).to_numpy(dtype=float, na_value=np.nan)
    piv, tail = zigzag(high, low, a, mult)
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


def daily_pivots(bars: pd.DataFrame, atr: pd.Series, lookback: int) -> dict:
    """swing_pivots over the last `lookback` bars: the count starts C.SWING_LEAD_BARS bars earlier and
    only pivots dated inside the window are kept."""
    lead = bars.iloc[-(lookback + C.SWING_LEAD_BARS):]
    start = _iso(bars.index[-lookback:][0])
    raw = swing_pivots(lead, atr, C.SWING_ATR)
    return {side: [p for p in raw[side] if p["date"] >= start] for side in ("highs", "lows")}


def weekly_pivots(bars: pd.DataFrame, calendar=None, weeks: int = C.WEEKLY_PIVOT_WEEKS) -> dict:
    """ATR ZigZag swing pivots of the weekly chart: the major swings.

    Weekly bars (indicators.resample_weekly) without the week in progress, so a weekly pivot only
    appears, and is only confirmed, by complete weeks; the reversal is C.SWING_ATR x the weekly
    ATR(C.ATR_PERIOD). The count starts C.WEEKLY_PIVOT_LEAD weeks before the last `weeks` weeks and
    only pivots inside them are kept. Each pivot's date is the daily bar that made the extreme
    (week = the last trading day of its week), so it lines up with the daily chart.
    Returns {"available", "weeks", "window_start", "atr_weekly", "highs": [...], "lows": [...]}.
    """
    wk = I.resample_weekly(bars, calendar)
    wk = wk.loc[~wk["partial"]]
    need = C.ATR_PERIOD + 2
    if len(wk) < need:
        return {"available": False, "reason": f"weekly pivots need {need} complete weekly bars (have {len(wk)})",
                "highs": [], "lows": []}
    atr_w = I.atr(wk, C.ATR_PERIOD)
    lead = wk.iloc[-(weeks + C.WEEKLY_PIVOT_LEAD):]
    start = wk.index[-weeks:][0]
    raw = swing_pivots(lead, atr_w, C.SWING_ATR)
    week_of = bars.index.to_period(C.WEEKLY_RULE)
    out = {"highs": [], "lows": []}
    for side, col, pick in (("highs", "high", "idxmax"), ("lows", "low", "idxmin")):
        for p in raw[side]:
            when = pd.Timestamp(p["date"])
            if when < start:
                continue
            days = bars.loc[week_of == when.to_period(C.WEEKLY_RULE), col]
            out[side].append({**p, "week": p["date"], "date": _iso(getattr(days, pick)())})
    return {"available": True, "weeks": int(min(weeks, len(wk))), "window_start": _iso(start),
            "atr_weekly": _num(atr_w.iloc[-1]), **out}


# ---------------------------------------------------------------------------- structure
def _compare(new: float, old: float, tol: float) -> str:
    if new > old + tol:
        return "higher"
    if new < old - tol:
        return "lower"
    return "equal"


def swing_structure(bars: pd.DataFrame, pivots: dict, atr: float | None, atr_daily: float | None = None,
                    keep: int = C.STRUCTURE_SWINGS) -> dict:
    """The Dow-theory reading of the last swings, as of the last bar of `bars` (daily).

    pivots: confirmed swing pivots with daily dates ({"highs": [...], "lows": [...]}, oldest first).
    The swing in progress joins them as a provisional swing high once a close after the last pivot is
    above the last swing high (its price = the highest high since that pivot), and as a provisional
    swing low once a close is below the last swing low: passing a swing needs no reversal to be a fact.

    highs / lows: the last swing high (low) against the one before it: "higher", "lower", or "equal"
    when they are within C.STRUCTURE_EQUAL_ATR x atr (the ATR of the chart the swings come from).
    state: "uptrend" (both higher), "downtrend" (both lower), else "mixed". invalidation: the price
    that ends the sequence, the last swing low of an uptrend or the last swing high of a downtrend
    (None when mixed), with its signed distance from the close (distance_atr in atr_daily units).
    swings: the last `keep` swings in time order, each labelled against the previous swing of its
    side (HH, LH, EH / HL, LL, EL; None for the first).
    """
    highs, lows = list(pivots.get("highs") or []), list(pivots.get("lows") or [])
    close = _num(bars["close"].iloc[-1]) if len(bars) else None
    atr_daily = atr if atr_daily is None else atr_daily
    if close is None or atr is None or atr <= 0 or atr_daily is None or atr_daily <= 0:
        return {"available": False, "reason": "close or ATR unavailable"}
    if len(highs) < 2 or len(lows) < 2:
        return {"available": False,
                "reason": f"needs two confirmed swing highs and two swing lows (have {len(highs)} and {len(lows)})"}
    highs = [{"date": p["date"], "price": float(p["price"]), "provisional": False} for p in highs]
    lows = [{"date": p["date"], "price": float(p["price"]), "provisional": False} for p in lows]
    since = bars.loc[bars.index > pd.Timestamp(max(highs[-1]["date"], lows[-1]["date"]))]
    if len(since) and since["close"].max() > highs[-1]["price"]:
        highs.append({"date": _iso(since["high"].idxmax()), "price": float(since["high"].max()), "provisional": True})
    if len(since) and since["close"].min() < lows[-1]["price"]:
        lows.append({"date": _iso(since["low"].idxmin()), "price": float(since["low"].min()), "provisional": True})

    tol = C.STRUCTURE_EQUAL_ATR * atr
    hi = _compare(highs[-1]["price"], highs[-2]["price"], tol)
    lo = _compare(lows[-1]["price"], lows[-2]["price"], tol)
    state = "uptrend" if hi == lo == "higher" else "downtrend" if hi == lo == "lower" else "mixed"
    ref = {"uptrend": lows[-1], "downtrend": highs[-1]}.get(state)
    invalidation = None if ref is None else {
        "price": ref["price"], "date": ref["date"], "kind": "swing_low" if state == "uptrend" else "swing_high",
        "distance_pct": ref["price"] / close - 1.0, "distance_atr": (ref["price"] - close) / atr_daily}
    labelled = []
    for side, word, letter in ((highs, "high", "H"), (lows, "low", "L")):
        for k, p in enumerate(side):
            tag = None if k == 0 else {"higher": "H", "lower": "L", "equal": "E"}[
                _compare(p["price"], side[k - 1]["price"], tol)] + letter
            labelled.append({"kind": word, **p, "label": tag})
    labelled.sort(key=lambda p: (p["date"], p["provisional"]))
    return {"available": True, "state": state, "highs": hi, "lows": lo, "invalidation": invalidation,
            "last_high": highs[-1], "prev_high": highs[-2], "last_low": lows[-1], "prev_low": lows[-2],
            "swings": labelled[-keep:], "swing_atr": C.SWING_ATR, "equal_atr": C.STRUCTURE_EQUAL_ATR,
            "rule": STRUCTURE_RULE.format(e=C.STRUCTURE_EQUAL_ATR, m=C.SWING_ATR)}


# ---------------------------------------------------------------------------- trendlines
def _trendline(df: pd.DataFrame, atr: pd.Series, pivots: list[dict], up: bool) -> dict | None:
    """One trendline through swing lows (up) or swing highs (down), or None when the swings make no
    rising (falling) line whose two points are far enough apart and which is steep enough."""
    pos = {_iso(d): i for i, d in enumerate(df.index)}
    pts = sorted((pos[p["date"]], float(p["price"])) for p in pivots if p["date"] in pos)
    a_s = atr.reindex(df.index).to_numpy(dtype=float, na_value=np.nan)
    sign = 1.0 if up else -1.0
    while True:
        if len(pts) < 2:
            return None
        # the anchor: the lowest swing low (highest swing high), the latest one on a tie
        a = max(range(len(pts)), key=lambda k: (-sign * pts[k][1], k))
        x0, y0 = pts[a]
        later = pts[a + 1:]
        if not later:
            return None
        # the flattest line from the anchor keeps every later swing on its far side
        k = min(range(len(later)), key=lambda j: (sign * (later[j][1] - y0) / (later[j][0] - x0), -later[j][0]))
        x1, y1 = later[k]
        slope = (y1 - y0) / (x1 - x0)
        if x1 - x0 >= C.TRENDLINE_MIN_BARS and sign * slope >= C.TRENDLINE_MIN_SLOPE_ATR * a_s[-1]:
            break                                    # (False when the last ATR is missing: no line)
        pts = later[k:]                              # too close or too flat: start the line from the second point
    n = len(df)
    line = y0 + slope * (np.arange(n) - x0)
    tol = C.TRENDLINE_TOL_ATR * a_s
    touches = [df.index[x] for x, y in pts[a:] if np.isfinite(tol[x]) and abs(y - line[x]) <= tol[x]]
    close = df["close"].to_numpy(dtype=float, na_value=np.nan)
    with np.errstate(invalid="ignore"):
        beyond = sign * (line - close) > tol          # a close on the wrong side of the line by more than tol
    beyond[:x1 + 1] = False
    hits = np.flatnonzero(beyond)
    last_close, last_atr, now = close[-1], a_s[-1], float(line[-1])
    return {
        "kind": "up" if up else "down",
        "anchor": {"date": _iso(df.index[x0]), "price": y0},
        "second": {"date": _iso(df.index[x1]), "price": y1},
        "slope_per_bar": float(slope),
        "slope_atr_per_bar": _num(slope / last_atr),
        "span_bars": int(n - 1 - x0),
        "touches": len(touches), "touch_dates": [_iso(d) for d in touches], "confirmed": len(touches) >= 3,
        "price": now,
        "distance_pct": _num(now / last_close - 1.0), "distance_atr": _num((now - last_close) / last_atr),
        "state": "broken" if hits.size else "intact",
        "broken_on": _iso(df.index[hits[0]]) if hits.size else None,
        "closes_beyond": int(hits.size),
        "close_side": "above" if last_close >= now else "below",
    }


def trendlines(df: pd.DataFrame, atr: pd.Series, pivots: dict) -> dict:
    """levels.trendlines: the up line along the swing lows and the down line along the swing highs of
    df (the levels window), each None when the swings do not make one. See TRENDLINE_RULE.

    A line's price is its value at the last bar; slope_per_bar moves it one trading day. state is
    "broken" once any close after the line's second point lay more than C.TRENDLINE_TOL_ATR ATRs
    beyond it (broken_on = the first such bar); close_side says where the last close is now.
    """
    return {"up": _trendline(df, atr, pivots.get("lows") or [], True),
            "down": _trendline(df, atr, pivots.get("highs") or [], False),
            "tol_atr": C.TRENDLINE_TOL_ATR, "min_bars": C.TRENDLINE_MIN_BARS,
            "min_slope_atr": C.TRENDLINE_MIN_SLOPE_ATR,
            "rule": TRENDLINE_RULE.format(t=C.TRENDLINE_TOL_ATR, b=C.TRENDLINE_MIN_BARS, s=C.TRENDLINE_MIN_SLOPE_ATR)}


def channel_line(df: pd.DataFrame, atr: pd.Series, line: dict | None, opposite: list[dict]) -> dict | None:
    """The channel line of one of trendlines()' lines (df, atr: the same window): the parallel through
    the opposite swing (a swing high for an up trendline) farthest from the line between its two points,
    or None when no such swing lies on the far side. See CHANNEL_RULE.

    price: the channel line at the last bar; offset: its distance from the trendline (signed);
    position: where the last close sits between them (0 on the trendline, 1 on the channel line);
    overshoots: bars after the trendline's second point whose high (low) went more than
    C.TRENDLINE_TOL_ATR ATRs beyond the channel line, with the last one's date; close_side: "inside"
    or "beyond" the channel line now."""
    if not line:
        return None
    pos = {_iso(d): i for i, d in enumerate(df.index)}
    x0, x1 = pos.get(line["anchor"]["date"]), pos.get(line["second"]["date"])
    if x0 is None or x1 is None:
        return None
    up = line["kind"] == "up"
    sign = 1.0 if up else -1.0
    y0, slope = float(line["anchor"]["price"]), float(line["slope_per_bar"])
    between = [(pos[p["date"]], float(p["price"])) for p in opposite if p["date"] in pos and x0 < pos[p["date"]] < x1]
    if not between:
        return None
    x, y = max(between, key=lambda q: sign * (q[1] - (y0 + slope * (q[0] - x0))))
    offset = y - (y0 + slope * (x - x0))
    if sign * offset <= 0:
        return None
    n = len(df)
    base = y0 + slope * (np.arange(n) - x0)
    ch = base + offset
    a_s = atr.reindex(df.index).to_numpy(dtype=float, na_value=np.nan)
    ext = df["high" if up else "low"].to_numpy(dtype=float, na_value=np.nan)
    with np.errstate(invalid="ignore"):
        beyond = sign * (ext - ch) > C.TRENDLINE_TOL_ATR * a_s
    beyond[:x1 + 1] = False
    hits = np.flatnonzero(beyond)
    close, last_atr, now = float(df["close"].iloc[-1]), a_s[-1], float(ch[-1])
    return {
        "through": {"date": _iso(df.index[x]), "price": y},
        "offset": float(offset), "price": now,
        "distance_pct": _num(now / close - 1.0), "distance_atr": _num((now - close) / last_atr),
        "position": _num((close - float(base[-1])) / offset),
        "overshoots": int(hits.size),
        "last_overshoot": _iso(df.index[hits[-1]]) if hits.size else None,
        "overshoot_bars_ago": int(n - 1 - hits[-1]) if hits.size else None,
        "close_side": "beyond" if sign * (close - now) > 0 else "inside",
    }
