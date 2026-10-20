"""
Classic candlestick patterns on daily bars (chart 3): engulfing, hammer / shooting star, morning /
evening star, piercing line / dark cloud cover, harami and three white soldiers / black crows, plus
doji markers.

Each pattern is a fixed rule (references/patterns.md) with a prior-trend condition, measured
with the same forward-return statistics as signal_stats; the library table reports every pattern's
record on this stock at the primary horizon so a pattern without an edge is visible as such.
Patterns do not feed the scorecard. Causal: a pattern ending at bar t uses bars <= t only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import signal_stats as SS

TB = C.CS_TREND_BARS

SIGNALS = [
    {"id": "cs_bullish_engulfing", "name": "Bullish engulfing", "bias": +1, "bars": 2,
     "rule": "after a decline: a bearish bar, then a bullish bar opening at or below its close and closing at or "
             "above its open, with the larger body"},
    {"id": "cs_bearish_engulfing", "name": "Bearish engulfing", "bias": -1, "bars": 2,
     "rule": "after an advance: a bullish bar, then a bearish bar opening at or above its close and closing at or "
             "below its open, with the larger body"},
    {"id": "cs_hammer", "name": "Hammer", "bias": +1, "bars": 1,
     "rule": f"after a decline: lower shadow >= {C.CS_SHADOW_BODY:g} x body, upper shadow <= "
             f"{C.CS_SMALL_SHADOW:g} x range, range >= {C.CS_MIN_RANGE_ATR:g} x ATR14"},
    {"id": "cs_shooting_star", "name": "Shooting star", "bias": -1, "bars": 1,
     "rule": f"after an advance: upper shadow >= {C.CS_SHADOW_BODY:g} x body, lower shadow <= "
             f"{C.CS_SMALL_SHADOW:g} x range, range >= {C.CS_MIN_RANGE_ATR:g} x ATR14"},
    {"id": "cs_morning_star", "name": "Morning star", "bias": +1, "bars": 3,
     "rule": f"after a decline: a long bearish bar (body >= {C.CS_LONG_BODY_ATR:g} x ATR14), a small body "
             f"(<= {C.CS_STAR_BODY:g} x the first), then a bullish bar closing above the first body's midpoint"},
    {"id": "cs_evening_star", "name": "Evening star", "bias": -1, "bars": 3,
     "rule": f"after an advance: a long bullish bar (body >= {C.CS_LONG_BODY_ATR:g} x ATR14), a small body "
             f"(<= {C.CS_STAR_BODY:g} x the first), then a bearish bar closing below the first body's midpoint"},
    {"id": "cs_piercing_line", "name": "Piercing line", "bias": +1, "bars": 2,
     "rule": "after a decline: a long bearish bar, then a bullish bar opening below its close and closing above "
             "its body midpoint but below its open"},
    {"id": "cs_dark_cloud_cover", "name": "Dark cloud cover", "bias": -1, "bars": 2,
     "rule": "after an advance: a long bullish bar, then a bearish bar opening above its close and closing below "
             "its body midpoint but above its open"},
    {"id": "cs_bullish_harami", "name": "Bullish harami", "bias": +1, "bars": 2,
     "rule": "after a decline: a long bearish bar, then a smaller bullish body inside its body"},
    {"id": "cs_bearish_harami", "name": "Bearish harami", "bias": -1, "bars": 2,
     "rule": "after an advance: a long bullish bar, then a smaller bearish body inside its body"},
    {"id": "cs_three_white_soldiers", "name": "Three white soldiers", "bias": +1, "bars": 3,
     "rule": f"after a decline: three bullish trend bars (body >= half the range, upper shadow <= "
             f"{C.CS_SOLDIER_SHADOW:g} x range), each closing higher and opening inside the previous body"},
    {"id": "cs_three_black_crows", "name": "Three black crows", "bias": -1, "bars": 3,
     "rule": f"after an advance: three bearish trend bars (body >= half the range, lower shadow <= "
             f"{C.CS_SOLDIER_SHADOW:g} x range), each closing lower and opening inside the previous body"},
]
for _s in SIGNALS:
    _s["regime"] = "current_side"
    _s["rule"] += (f"; decline / advance = close of the bar before the pattern below / above the close "
                   f"{TB} bars earlier")
SIGNAL_IDS = [s["id"] for s in SIGNALS]
_BY_ID = {s["id"]: s for s in SIGNALS}
DOJI_RULE = (f"body <= {C.CS_DOJI_BODY:g} x range and range >= {C.CS_MIN_RANGE_ATR:g} x ATR14 "
             "(marked on the chart; no direction, so no statistics)")


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _sh(a: np.ndarray, k: int) -> np.ndarray:
    """a shifted forward by k bars (a[t-k] at t), NaN-padded."""
    out = np.full(len(a), np.nan)
    if k < len(a):
        out[k:] = a[:len(a) - k]
    return out


def pattern_frame(bars: pd.DataFrame, ind: pd.DataFrame) -> pd.DataFrame:
    """One bool column per pattern (SIGNALS order) plus "doji", True on the pattern's last bar."""
    o, h, l, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    atr = ind["atr14"].to_numpy(dtype=float)
    body, rng = np.abs(c - o), h - l
    upper, lower = h - np.maximum(o, c), np.minimum(o, c) - l
    bull, bear = c > o, c < o
    mid = (o + c) / 2.0

    def trend(k: int) -> tuple[np.ndarray, np.ndarray]:
        """(decline, advance) before a k-bar pattern ending at t: close[t-k] vs close[t-k-TB]."""
        p, q = _sh(c, k), _sh(c, k + TB)
        ok = np.isfinite(p) & np.isfinite(q) & np.isfinite(_sh(atr, k))
        return ok & (p < q), ok & (p > q)

    S = lambda a, k: _sh(a.astype(float), k)    # noqa: E731
    B = lambda a, k: S(a, k) == 1.0              # noqa: E731  shifted bool
    with np.errstate(invalid="ignore"):
        long_body = lambda k: S(body, k) >= C.CS_LONG_BODY_ATR * _sh(atr, k + 1)  # noqa: E731
        min_rng = rng >= C.CS_MIN_RANGE_ATR * _sh(atr, 1)
        d1, a1 = trend(1)
        d2, a2 = trend(2)
        d3, a3 = trend(3)
        trend_bar = body >= 0.5 * rng
        soldier = bull & trend_bar & (upper <= C.CS_SOLDIER_SHADOW * rng)
        crow = bear & trend_bar & (lower <= C.CS_SOLDIER_SHADOW * rng)
        out = {
            "cs_bullish_engulfing": d2 & B(bear, 1) & bull & (o <= S(c, 1)) & (c >= S(o, 1)) & (body > S(body, 1)),
            "cs_bearish_engulfing": a2 & B(bull, 1) & bear & (o >= S(c, 1)) & (c <= S(o, 1)) & (body > S(body, 1)),
            "cs_hammer": d1 & min_rng & (lower >= C.CS_SHADOW_BODY * body) & (upper <= C.CS_SMALL_SHADOW * rng),
            "cs_shooting_star": a1 & min_rng & (upper >= C.CS_SHADOW_BODY * body) & (lower <= C.CS_SMALL_SHADOW * rng),
            "cs_morning_star": (d3 & B(bear, 2) & long_body(2) & (S(body, 1) <= C.CS_STAR_BODY * S(body, 2))
                                & bull & (c > S(mid, 2))),
            "cs_evening_star": (a3 & B(bull, 2) & long_body(2) & (S(body, 1) <= C.CS_STAR_BODY * S(body, 2))
                                & bear & (c < S(mid, 2))),
            "cs_piercing_line": (d2 & B(bear, 1) & long_body(1) & bull & (o < S(c, 1)) & (c > S(mid, 1))
                                 & (c < S(o, 1))),
            "cs_dark_cloud_cover": (a2 & B(bull, 1) & long_body(1) & bear & (o > S(c, 1)) & (c < S(mid, 1))
                                    & (c > S(o, 1))),
            "cs_bullish_harami": (d2 & B(bear, 1) & long_body(1) & bull & (o >= S(c, 1)) & (c <= S(o, 1))
                                  & (body < S(body, 1))),
            "cs_bearish_harami": (a2 & B(bull, 1) & long_body(1) & bear & (o <= S(c, 1)) & (c >= S(o, 1))
                                  & (body < S(body, 1))),
            "cs_three_white_soldiers": (d3 & B(soldier, 2) & B(soldier, 1) & soldier
                                        & (S(c, 1) > S(c, 2)) & (c > S(c, 1))
                                        & (S(o, 1) > S(o, 2)) & (S(o, 1) <= S(c, 2)) & (o > S(o, 1)) & (o <= S(c, 1))),
            "cs_three_black_crows": (a3 & B(crow, 2) & B(crow, 1) & crow
                                     & (S(c, 1) < S(c, 2)) & (c < S(c, 1))
                                     & (S(o, 1) < S(o, 2)) & (S(o, 1) >= S(c, 2)) & (o < S(o, 1)) & (o >= S(c, 1))),
            "doji": min_rng & (body <= C.CS_DOJI_BODY * rng),
        }
    return pd.DataFrame({k: np.asarray(out[k], dtype=bool) for k in [*SIGNAL_IDS, "doji"]}, index=bars.index)


def events(bars: pd.DataFrame, ind: pd.DataFrame) -> pd.DataFrame:
    """The directional patterns only (SIGNALS order), for the statistics."""
    return pattern_frame(bars, ind)[SIGNAL_IDS]


def _frame_for(ctx) -> pd.DataFrame:
    bars = ctx["bars"]
    memo = ctx.get("_cs_frame")
    if memo is not None and memo[0] is bars:
        return memo[1]
    fr = pattern_frame(bars, ctx["ind"])
    ctx["_cs_frame"] = (bars, fr)
    return fr


def _caveats(ticker: str) -> list[str]:
    return [
        "Candlestick patterns are fixed daily-bar rules; published descriptions vary, so another tool may "
        "label different bars.",
        f"Statistics come from {ticker}'s own daily history, in-sample, with entry at the close of the pattern's "
        "last bar and no costs (the same method as the indicator signals).",
        "Library rows compare each pattern with every day's forward return on the same side (unconditional); "
        "active rows also give the same-regime baseline.",
        "Doji bars are marked on the chart but carry no direction, so they have no statistics.",
        "Candlestick patterns do not change the scorecard; they are context next to it.",
    ]


def build_candlesticks(ctx) -> dict:
    """Module output: active patterns (last C.SIGNAL_ACTIVE_BARS bars) with per-horizon statistics, the
    whole library at the primary horizon, the most recent patterns and the last doji."""
    bars, ind = ctx.get("bars"), ctx.get("ind")
    hz = ctx["hz"]
    horizons = [int(x) for x in hz["stat_horizons"]]
    primary = int(hz["primary"])
    if primary not in horizons:
        horizons.append(primary)
    base = {"primary_horizon": primary, "stat_horizons": horizons, "doji_rule": DOJI_RULE}
    if bars is None or ind is None or len(bars) < TB + 4:
        return {**base, "available": False, "reason": "too few bars for candlestick patterns", "notes": [],
                "active": [], "library": []}
    fr = _frame_for(ctx)
    ev = fr[SIGNAL_IDS]
    notes: list[str] = []
    cache = SS.OutcomeCache(bars, ind)
    active = SS.active_rows(bars, ind, ev, _BY_ID, horizons, primary, notes, cache)
    library = SS.library_rows(bars, ind, ev, SIGNALS, primary, cache)
    n = len(bars)
    recent = []
    for pos in range(n - 1, -1, -1):
        for sid in SIGNAL_IDS:
            if ev[sid].iat[pos]:
                recent.append({"id": sid, "bias": _BY_ID[sid]["bias"], "date": _iso(bars.index[pos]),
                               "bars_ago": n - 1 - pos})
        if len(recent) >= 5:
            break
    dojis = np.flatnonzero(fr["doji"].to_numpy())
    act = {a["id"] for a in active}
    return {
        **base, "available": True, "notes": notes,
        "active": active, "inactive_ids": [s for s in SIGNAL_IDS if s not in act], "library": library,
        "recent": recent[:5],
        "last_doji": None if not dojis.size else {"date": _iso(bars.index[dojis[-1]]), "bars_ago": n - 1 - int(dojis[-1])},
        "caveats": _caveats(ctx.get("ticker", "this stock")),
    }


def chart_payload(ctx, modules: dict, win_index: pd.DatetimeIndex) -> dict:
    """Pattern markers for chart 3 on the chart window."""
    cs = modules.get("candlesticks") or {}
    if not cs.get("available"):
        return {"available": False, "reason": cs.get("reason") or "candlestick module unavailable"}
    bars = ctx["bars"]
    fr = _frame_for(ctx)
    first, last = (int(x) for x in bars.index.get_indexer(win_index[[0, -1]]))
    hi, lo = bars["high"].to_numpy(dtype=float), bars["low"].to_numpy(dtype=float)
    markers = []
    for pos in range(first, last + 1):
        for sid in [*SIGNAL_IDS, "doji"]:
            if not fr[sid].iat[pos]:
                continue
            spec = _BY_ID.get(sid, {"bias": 0, "bars": 1})
            k = spec["bars"]
            s = max(0, pos - k + 1)
            markers.append({"date": _iso(bars.index[pos]), "start": _iso(bars.index[s]), "id": sid,
                            "bias": spec["bias"], "bars": k,
                            "price": float(np.nanmin(lo[s:pos + 1]) if spec["bias"] >= 0 else np.nanmax(hi[s:pos + 1]))})
    return {"available": True, "markers": markers}
