"""
Trading ranges V2: persistent, causally confirmed boxes from ATR ZigZag pivots.

A box is a run of alternating swing pivots (levels.swing_pivots with C.TR_SWING_ATR) whose highs
all sit in one band and whose lows all sit in another: at least C.TR_MIN_TESTS of each, each band at
most C.TR_EDGE_ATR ATRs wide, the box C.TR_MIN_HEIGHT_ATR to C.TR_MAX_HEIGHT_ATR ATRs tall (ATR at
confirmation). It is confirmed on the bar that confirms its last pivot, never earlier, and its edges
are frozen there. From the next bar on, closes decide its state:
  - a close more than C.TR_BREAK_ATR ATRs beyond an edge band breaches it; C.TR_BREAK_CLOSES
    breaching closes in a row break the box (broken_up / broken_down); a close back inside first
    records a failed breakout and the box stays active (breakout_pending while a breach is open);
  - a later pivot within C.TR_TEST_ATR ATRs of an edge band is another test of that edge;
  - with no new test for C.TR_EXPIRE_BARS bars the box expires.
Boxes are searched from the start of the series, the next one only after the previous one ended,
so the boxes known at a date never change when later bars arrive (no repainting). The quality
fields are descriptive inputs, not a calibrated probability; quality_score stays null.

Pure and deterministic: no network, no dependence on price_action.py; levels.py only for pivots.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import levels as L

METHOD = "trading_range_v2"
MIN_BARS = 2 * C.ATR_PERIOD


def _iso(i: int, index: pd.DatetimeIndex) -> str:
    return index[i].strftime("%Y-%m-%d")


def params() -> dict:
    """The TR_* settings, as they go into config_snapshot."""
    return {k[3:].lower(): getattr(C, k) for k in dir(C) if k.startswith("TR_")}


def pivot_rows(bars: pd.DataFrame, atr: pd.Series) -> list[tuple[int, int, float, int]]:
    """(bar, +1 high / -1 low, price, confirming bar) for the box ZigZag, in bar order."""
    raw = L.swing_pivots(bars, atr, C.TR_SWING_ATR)
    pos = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(bars.index)}
    rows = [(pos[p["date"]], kind, float(p["price"]), pos[p["confirmed"]])
            for side, kind in (("highs", 1), ("lows", -1)) for p in raw[side]]
    return sorted(rows)


def _band(prices: list[float], atr: float) -> dict:
    lo, hi = min(prices), max(prices)
    pad = max(0.0, C.TR_MIN_BAND_ATR * atr - (hi - lo)) / 2
    return {"low": lo - pad, "high": hi + pad}


def _try_box(piv: list, k: int, a: np.ndarray) -> tuple[int, dict] | None:
    """The shortest run of pivots from piv[k] that forms a box: (index of its last pivot, geometry),
    or None once a band grows too wide or the height falls outside its limits."""
    highs, lows = [], []
    for j in range(k, len(piv)):
        bar, kind, price, conf = piv[j]
        (highs if kind > 0 else lows).append(price)
        atr = a[conf]
        if not np.isfinite(atr) or atr <= 0:
            return None
        if (highs and max(highs) - min(highs) > C.TR_EDGE_ATR * atr) or \
                (lows and max(lows) - min(lows) > C.TR_EDGE_ATR * atr):
            return None
        if len(highs) >= C.TR_MIN_TESTS and len(lows) >= C.TR_MIN_TESTS:
            upper, lower = _band(highs, atr), _band(lows, atr)
            height = float(np.mean(highs) - np.mean(lows))
            if not C.TR_MIN_HEIGHT_ATR * atr <= height <= C.TR_MAX_HEIGHT_ATR * atr:
                return None
            return j, {"upper_zone": upper, "lower_zone": lower, "highs": highs, "lows": lows,
                       "atr": float(atr), "height": height}
    return None


def _lifecycle(k: int, j: int, geo: dict, piv: list, close: np.ndarray, index: pd.DatetimeIndex,
               t: int) -> dict:
    """The box made of piv[k..j], followed bar by bar up to t."""
    atr = geo["atr"]
    up_z, lo_z = geo["upper_zone"], geo["lower_zone"]
    tol, brk = C.TR_TEST_ATR * atr, C.TR_BREAK_ATR * atr
    start, conf = piv[k][0], piv[j][3]
    tests = {}                                    # confirming bar -> +1 upper / -1 lower
    for bar, kind, price, c in piv[j + 1:]:
        if c > t:
            break
        z = up_z if kind > 0 else lo_z
        if z["low"] - tol <= price <= z["high"] + tol:
            tests[c] = kind
    upper_tests, lower_tests = len(geo["highs"]), len(geo["lows"])
    last_test, run, run_dir, run_start = conf, 0, 0, None
    status, end, breakout, events = "active", None, None, []
    for s in range(conf + 1, t + 1):
        if s in tests:
            last_test = s
            upper_tests += tests[s] > 0
            lower_tests += tests[s] < 0
            events.append({"type": "test", "edge": "upper" if tests[s] > 0 else "lower", "date": _iso(s, index)})
        cl = close[s]
        d = 1 if cl > up_z["high"] + brk else -1 if cl < lo_z["low"] - brk else 0
        if d:
            if d != run_dir:
                run, run_dir, run_start = 0, d, s
            run += 1
            if run >= C.TR_BREAK_CLOSES:
                status, end = ("broken_up" if d > 0 else "broken_down"), s
                edge = up_z["high"] if d > 0 else lo_z["low"]
                breakout = {"direction": "up" if d > 0 else "down", "first_breach": _iso(run_start, index),
                            "confirmed": _iso(s, index), "close_beyond_atr": float(abs(cl - edge) / atr)}
                events.append({"type": "breakout", **breakout})
                break
        elif run and lo_z["low"] <= cl <= up_z["high"]:
            events.append({"type": "failed_breakout", "direction": "up" if run_dir > 0 else "down",
                           "first_breach": _iso(run_start, index), "back_inside": _iso(s, index)})
            run, run_dir, run_start = 0, 0, None
        if s - last_test > C.TR_EXPIRE_BARS:
            status, end = "expired", s
            events.append({"type": "expired", "date": _iso(s, index)})
            break
    if end is None and run:
        status = "breakout_pending"
    inside = close[start:conf + 1]
    upper, lower = up_z["high"], lo_z["low"]
    highs, lows = geo["highs"], geo["lows"]
    return {
        "box_id": f"tr-{_iso(start, index)}-{_iso(conf, index)}",
        "status": status,
        "start_date": _iso(start, index),
        "confirmed_date": _iso(conf, index),
        "end_date": None if end is None else _iso(end, index),
        "last_test_date": _iso(last_test, index),
        "upper": upper, "lower": lower,
        "upper_zone": dict(up_z), "lower_zone": dict(lo_z),
        "height": upper - lower, "height_atr": (upper - lower) / atr, "atr_reference": atr,
        "duration_bars": (end if end is not None else t) - start,
        "upper_tests": int(upper_tests), "lower_tests": int(lower_tests),
        "traversals": j - k,
        "breakout_attempts": sum(e["type"] == "failed_breakout" for e in events),
        "internal_close_ratio": float(np.mean((inside >= lower) & (inside <= upper))),
        "boundary_drift_atr": {"upper": (highs[-1] - highs[0]) / atr, "lower": (lows[-1] - lows[0]) / atr},
        "quality_score": None,
        "breakout": breakout,
        "events": events,
        "_end": end,
    }


def detect_trading_ranges(bars: pd.DataFrame, atr: pd.Series, *, as_of=None, pivots: list | None = None) -> dict:
    """Active box, last box, recent completed boxes and notes as of `as_of` (default: the last bar),
    from bars <= as_of only. `pivots` may hold pivot_rows() of a longer series with the same start:
    the ZigZag is causal, so the rows confirmed by as_of are the ones bars[:as_of] would give."""
    if as_of is not None:
        bars = bars.loc[bars.index <= pd.Timestamp(as_of)]
    out = {"available": False, "method": METHOD, "params": params(), "notes": []}
    if len(bars) < MIN_BARS:
        return {**out, "reason": f"{len(bars)} bars, fewer than {MIN_BARS}"}
    a = atr.reindex(bars.index).to_numpy(dtype=float, na_value=np.nan)
    if not np.isfinite(a).any():
        return {**out, "reason": "no ATR"}
    t = len(bars) - 1
    piv = [p for p in (pivot_rows(bars, atr) if pivots is None else pivots) if p[3] <= t]
    close = bars["close"].to_numpy(dtype=float, na_value=np.nan)
    boxes, k, after = [], 0, -1
    while k < len(piv):
        if piv[k][0] <= after:
            k += 1
            continue
        found = _try_box(piv, k, a)
        if found is None:
            k += 1
            continue
        j, geo = found
        box = _lifecycle(k, j, geo, piv, close, bars.index, t)
        boxes.append(box)
        if box["_end"] is None:
            break
        after, k = box["_end"], j + 1
    for b in boxes:
        b.pop("_end")
    last = boxes[-1] if boxes else None
    active = last if last and last["status"] in ("active", "breakout_pending") else None
    done = [b for b in boxes if b is not active]
    notes = [] if boxes else ["no box: no run of alternating pivots with both edges in narrow bands"]
    return {**out, "available": True, "as_of": _iso(t, bars.index), "active_box": active, "last_box": last,
            "recent_boxes": done[-C.TR_RECENT_BOXES:][::-1], "n_boxes": len(boxes), "notes": notes}
