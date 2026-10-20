"""
Al Brooks-style price action on daily bars (chart 2): bar types, the market state (tight channel,
broad channel, trading range), the always-in direction, H1-H4 / L1-L4 pullback counts, wedges
(three pushes), trading-range breakouts that fail, 20-gap-bar EMA tests and measured moves.

Brooks reads 5-minute charts bar by bar and leaves much to judgement. Every rule here is a fixed,
mechanical daily-bar approximation, documented in references/patterns.md. The setups are a
pre-registered event library measured with the same forward-return statistics as signal_stats;
they do not feed the scorecard. Everything is causal: values at bar t use bars <= t, and a swing
pivot exists only from the bar that confirms it (PA_PIVOT_K bars later).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import indicators as I
import signal_stats as SS

N_STATE, N_RANGE, SLOPE = C.PA_STATE_BARS, C.PA_RANGE_BARS, C.PA_EMA_SLOPE_BARS
WARMUP = max(N_STATE, N_RANGE) + SLOPE      # events and states start at this bar index (EMA settled)

BAR_TYPES = {2: "bull_trend_bar", 1: "weak_bull_bar", 0: "doji", -1: "weak_bear_bar", -2: "bear_trend_bar"}
STATES = {2: "tight_bull_channel", 1: "bull_channel", 0: "trading_range", -1: "bear_channel",
          -2: "tight_bear_channel"}
ALWAYS_IN = {1: "long", -1: "short", 0: "none"}

SIGNALS = [
    {"id": "pa_h2_ema", "name": "H2 pullback near the rising 20 EMA", "bias": +1, "regime": "current_side",
     "rule": f"second high-count bar (high > prior high) of a pullback, EMA{C.PA_EMA_PERIOD} rising over "
             f"{SLOPE} bars, close > EMA, pullback low <= EMA + {C.PA_PULLBACK_EMA_ATR:g} x ATR14"},
    {"id": "pa_l2_ema", "name": "L2 rally near the falling 20 EMA", "bias": -1, "regime": "current_side",
     "rule": f"second low-count bar (low < prior low) of a rally, EMA{C.PA_EMA_PERIOD} falling over "
             f"{SLOPE} bars, close < EMA, rally high >= EMA - {C.PA_PULLBACK_EMA_ATR:g} x ATR14"},
    {"id": "pa_wedge_bottom", "name": "Wedge bottom (three pushes down)", "bias": +1, "regime": "current_side",
     "rule": f"three successively lower confirmed swing lows (k={C.PA_PIVOT_K}) within {C.PA_WEDGE_MAX_BARS} "
             f"bars, >= {C.PA_WEDGE_MIN_SPACING} bars apart, a swing high between each, the third push shorter "
             f"than the second and the lowest low of the window; fires on the confirming bar"},
    {"id": "pa_wedge_top", "name": "Wedge top (three pushes up)", "bias": -1, "regime": "current_side",
     "rule": f"three successively higher confirmed swing highs (k={C.PA_PIVOT_K}) within {C.PA_WEDGE_MAX_BARS} "
             f"bars, >= {C.PA_WEDGE_MIN_SPACING} bars apart, a swing low between each, the third push shorter "
             f"than the second and the highest high of the window; fires on the confirming bar"},
    {"id": "pa_failed_breakdown", "name": "Failed breakdown below a trading range", "bias": +1,
     "regime": "current_side",
     "rule": f"in a trading range ({N_RANGE}-bar span <= {C.PA_RANGE_MAX_ATR:g} x ATR14, >= "
             f"{C.PA_RANGE_MIN_CROSSES} EMA crosses), a bar trades below the range low and a close back above "
             f"it follows on that bar or within {C.PA_BREAKOUT_FAIL_BARS} bars"},
    {"id": "pa_failed_breakout", "name": "Failed breakout above a trading range", "bias": -1,
     "regime": "current_side",
     "rule": f"in a trading range ({N_RANGE}-bar span <= {C.PA_RANGE_MAX_ATR:g} x ATR14, >= "
             f"{C.PA_RANGE_MIN_CROSSES} EMA crosses), a bar trades above the range high and a close back below "
             f"it follows on that bar or within {C.PA_BREAKOUT_FAIL_BARS} bars"},
    {"id": "pa_gap20_bull_test", "name": "First EMA test after 20 bull gap bars", "bias": +1,
     "regime": "current_side",
     "rule": f"low <= EMA after at least {C.PA_GAP_BARS} bars in a row with low > EMA"},
    {"id": "pa_gap20_bear_test", "name": "First EMA test after 20 bear gap bars", "bias": -1,
     "regime": "current_side",
     "rule": f"high >= EMA after at least {C.PA_GAP_BARS} bars in a row with high < EMA"},
]
SIGNAL_IDS = [s["id"] for s in SIGNALS]
_BY_ID = {s["id"]: s for s in SIGNALS}

APPROXIMATION = ("Daily-bar approximation of Al Brooks' price action: Brooks reads intraday (5-minute) "
                 "charts with discretion; these are fixed rules, and their statistics come from this "
                 "stock's own daily history, not from Brooks' books.")


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


# ---------------------------------------------------------------------------- per-bar pieces
def bar_types(bars: pd.DataFrame) -> np.ndarray:
    """2 bull trend bar, 1 weak bull, 0 doji, -1 weak bear, -2 bear trend bar (body share of range)."""
    o, h, l, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    rng = h - l
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(rng > 0, np.abs(c - o) / rng, 0.0)
    direction = np.sign(c - o)
    out = np.where(share >= C.PA_TREND_BAR_BODY, 2, np.where(share > C.PA_DOJI_BODY, 1, 0)) * direction
    return np.nan_to_num(out, nan=0.0).astype(int)


def _ema_crosses(close: np.ndarray, ema: np.ndarray) -> np.ndarray:
    """1 where the close changed sides of the EMA versus the previous close (a close on the EMA keeps its side)."""
    s = np.sign(close - ema)
    side = pd.Series(np.where(s == 0, np.nan, s)).ffill().to_numpy()
    out = np.zeros(len(close))
    out[1:] = (side[1:] != side[:-1]) & ~np.isnan(side[1:]) & ~np.isnan(side[:-1])
    return out


def _range_window(high, low, cross, atr, n, shift: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(range high, range low, is trading range) over bars t-n+1-shift .. t-shift."""
    hs, ls, cs = pd.Series(high), pd.Series(low), pd.Series(cross)
    rh = hs.rolling(n, min_periods=n).max().shift(shift).to_numpy()
    rl = ls.rolling(n, min_periods=n).min().shift(shift).to_numpy()
    # crosses between consecutive bars inside the window: n-1 transitions
    crosses = cs.rolling(n - 1, min_periods=n - 1).sum().shift(shift).to_numpy()
    a = pd.Series(atr).shift(shift).to_numpy()
    with np.errstate(invalid="ignore"):
        is_tr = ((rh - rl) <= C.PA_RANGE_MAX_ATR * a) & (crosses >= C.PA_RANGE_MIN_CROSSES)
    return rh, rl, is_tr


def _runs(mask: np.ndarray) -> np.ndarray:
    """Length of the run of True values ending at each bar."""
    out = np.zeros(len(mask), dtype=int)
    for i, m in enumerate(mask.tolist()):
        out[i] = out[i - 1] + 1 if m and i else int(bool(m))
    return out


def pullback_counts(high: np.ndarray, low: np.ndarray, bull: bool,
                    context: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Brooks' bar counting: (count at each bar, pullback extreme at that bar).

    Bull side: a pullback starts on a bar whose high is below the prior bar's high. Inside it, each
    bar whose high goes above the prior bar's high is the next count (H1, H2, ...), provided a lower
    high came after the previous count. A high above the leg's high ends the pullback (that bar can
    still be a count). The count is abandoned after PA_COUNT_MAX, and on any bar outside `context`
    (the trend the counts belong to, e.g. a rising EMA). The bear side mirrors it with lows (L1, L2, ...).
    """
    x, y = (high, low) if bull else (-low, -high)     # mirror: bear counting is bull counting on -price
    n = len(x)
    count = np.zeros(n, dtype=int)
    extreme = np.full(n, np.nan)
    in_pb, armed, cnt, leg, pb = False, False, 0, -np.inf, np.inf
    for t in range(1, n):
        if not (np.isfinite(x[t]) and np.isfinite(x[t - 1])) or (context is not None and not context[t]):
            in_pb = False
            continue
        if not in_pb:
            if x[t] < x[t - 1]:
                in_pb, armed, cnt, leg, pb = True, True, 0, x[t - 1], y[t]
            continue
        pb = min(pb, y[t])
        if x[t] > x[t - 1]:
            if armed:
                cnt += 1
                if cnt > C.PA_COUNT_MAX:          # no longer a pullback in this trend: start over
                    in_pb = False
                    continue
                count[t], extreme[t] = cnt, pb
                armed = False
        elif x[t] < x[t - 1]:
            armed = True
        if x[t] > leg:
            in_pb = False
    return count, (extreme if bull else -extreme)


def confirmed_pivots(high: np.ndarray, low: np.ndarray, k: int) -> list[dict]:
    """Fractal swing pivots in confirmation order: {kind: "H"/"L", i, confirm, price}. Bar i is a
    pivot high when its high is strictly above the highs of the k bars on each side; it becomes known
    at bar i + k."""
    out = []
    for t in range(2 * k, len(high)):
        i = t - k
        for kind, s, beats in (("H", high, np.greater), ("L", low, np.less)):
            v = s[i]
            if np.isfinite(v) and beats(v, s[i - k:i]).all() and beats(v, s[i + 1:t + 1]).all():
                out.append({"kind": kind, "i": i, "confirm": t, "price": float(v)})
    return out


def wedges(pivots: list[dict], high: np.ndarray, low: np.ndarray) -> list[dict]:
    """Three-push wedges, each with side ("top"/"bottom"), the three pushes [(i, price)] and the
    confirming bar of the third push."""
    out = []
    by_kind = {"H": [], "L": []}
    for p in pivots:
        same, other = by_kind[p["kind"]], by_kind["L" if p["kind"] == "H" else "H"]
        same.append(p)
        if len(same) < 3:
            continue
        p1, p2, p3 = same[-3:]
        up = p["kind"] == "H"
        rising = p1["price"] < p2["price"] < p3["price"] if up else p1["price"] > p2["price"] > p3["price"]
        # momentum fades: the third push goes less far beyond the second than the second beyond the first
        fading = abs(p3["price"] - p2["price"]) < abs(p2["price"] - p1["price"])
        if not (rising and fading) or p3["i"] - p1["i"] > C.PA_WEDGE_MAX_BARS:
            continue
        if min(p2["i"] - p1["i"], p3["i"] - p2["i"]) < C.PA_WEDGE_MIN_SPACING:
            continue
        # a swing in between each push (known by the time the third push is confirmed)
        between = lambda a, b: any(a["i"] < q["i"] < b["i"] and q["confirm"] <= p3["confirm"] for q in other)  # noqa: E731
        if not (between(p1, p2) and between(p2, p3)):
            continue
        lo = max(0, p3["i"] - C.PA_WEDGE_MAX_BARS + 1)
        extreme = np.nanmax(high[lo:p3["i"] + 1]) if up else np.nanmin(low[lo:p3["i"] + 1])
        if p3["price"] != extreme:
            continue
        out.append({"side": "top" if up else "bottom", "pushes": [(q["i"], q["price"]) for q in (p1, p2, p3)],
                    "confirm": p3["confirm"]})
    return out


def failed_breakouts(close: np.ndarray, high: np.ndarray, low: np.ndarray, rh: np.ndarray, rl: np.ndarray,
                     is_tr: np.ndarray) -> list[dict]:
    """Trading-range breakouts (against the range of the previous bars) that close back inside the
    range on the breakout bar or within PA_BREAKOUT_FAIL_BARS bars: {side, breakout, fail, level}."""
    out, n = [], len(close)
    for side, beyond, back in (("up", high > rh, lambda t, lv: close[t] < lv),
                               ("down", low < rl, lambda t, lv: close[t] > lv)):
        last_fail = -1
        for b in np.flatnonzero(is_tr & beyond).tolist():
            if b <= last_fail:
                continue
            level = rh[b] if side == "up" else rl[b]
            for t in range(b, min(n, b + C.PA_BREAKOUT_FAIL_BARS + 1)):
                if back(t, level):
                    out.append({"side": side, "breakout": b, "fail": t, "level": float(level)})
                    last_fail = t
                    break
    return sorted(out, key=lambda r: r["fail"])


def always_in(close: np.ndarray, high: np.ndarray, low: np.ndarray, ema: np.ndarray, types: np.ndarray,
              state: np.ndarray) -> np.ndarray:
    """1 / -1 / 0: flips long after PA_ALWAYS_IN_BARS bull trend bars in a row closing above the EMA,
    the last close above the high of the bar before them, or when a tight bull channel is on; flips
    short on the mirror image."""
    m, n = C.PA_ALWAYS_IN_BARS, len(close)
    out = np.zeros(n, dtype=int)
    bull = (types == 2) & (close > ema)
    bear = (types == -2) & (close < ema)
    cur = 0
    for t in range(n):
        if t >= m:
            if bull[t - m + 1:t + 1].all() and close[t] > high[t - m]:
                cur = 1
            elif bear[t - m + 1:t + 1].all() and close[t] < low[t - m]:
                cur = -1
        if state[t] == 2:
            cur = 1
        elif state[t] == -2:
            cur = -1
        out[t] = cur
    return out


# ---------------------------------------------------------------------------- everything at once
def analyze_frame(bars: pd.DataFrame, ind: pd.DataFrame) -> dict:
    """All per-bar price-action series and pattern lists for bars (causal, positions = bar index)."""
    o, h, l, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    n = len(bars)
    ema = I.ema(bars["close"], C.PA_EMA_PERIOD).to_numpy(dtype=float)
    atr = ind["atr14"].to_numpy(dtype=float)
    warm = (np.arange(n) >= WARMUP) & np.isfinite(atr)
    types = bar_types(bars)
    ema_prev = pd.Series(ema).shift(SLOPE).to_numpy()
    rising, falling = ema > ema_prev, ema < ema_prev
    cross = _ema_crosses(c, ema)

    # market state on the last PA_STATE_BARS bars, this bar included
    share = lambda m: pd.Series(m.astype(float)).rolling(N_STATE, min_periods=N_STATE).mean().to_numpy()  # noqa: E731
    above_bar, below_bar = share(l > ema), share(h < ema)
    above_close, below_close = share(c > ema), share(c < ema)
    rh_i, rl_i, tr_i = _range_window(h, l, cross, atr, N_RANGE, 0)
    state = np.full(n, np.nan)
    state[warm] = 0
    with np.errstate(invalid="ignore"):
        state = np.where(warm & ~tr_i & rising & (above_close >= C.PA_CHANNEL_SHARE), 1, state)
        state = np.where(warm & ~tr_i & falling & (below_close >= C.PA_CHANNEL_SHARE), -1, state)
        state = np.where(warm & rising & (above_bar >= C.PA_TIGHT_SHARE), 2, state)
        state = np.where(warm & falling & (below_bar >= C.PA_TIGHT_SHARE), -2, state)

    # trading range of the bars before t: breakouts and their failure
    rh_p, rl_p, tr_p = _range_window(h, l, cross, atr, N_RANGE, 1)
    tr_p = tr_p & warm
    fails = failed_breakouts(c, h, l, rh_p, rl_p, tr_p)

    h_cnt, h_ext = pullback_counts(h, l, bull=True, context=rising)
    l_cnt, l_ext = pullback_counts(h, l, bull=False, context=falling)
    gap_bull, gap_bear = _runs(l > ema), _runs(h < ema)
    pivots = confirmed_pivots(h, l, C.PA_PIVOT_K)
    wds = wedges(pivots, h, l)
    ai = always_in(c, h, l, ema, types, state)
    return {"n": n, "ema": ema, "atr": atr, "warm": warm, "types": types, "rising": rising, "falling": falling,
            "state": state, "above_close": above_close, "above_bar": above_bar, "below_bar": below_bar,
            "rh": rh_i, "rl": rl_i, "tr": tr_i & warm, "fails": fails,
            "h_cnt": h_cnt, "h_ext": h_ext, "l_cnt": l_cnt, "l_ext": l_ext,
            "gap_bull": gap_bull, "gap_bear": gap_bear, "pivots": pivots, "wedges": wds, "always_in": ai,
            "close": c, "high": h, "low": l}


def events(bars: pd.DataFrame, ind: pd.DataFrame, fr: dict | None = None) -> pd.DataFrame:
    """One bool column per price-action setup (SIGNALS order), True on the bar the rule completes."""
    fr = fr or analyze_frame(bars, ind)
    n, ema, atr, c, h, l = fr["n"], fr["ema"], fr["atr"], fr["close"], fr["high"], fr["low"]
    near = C.PA_PULLBACK_EMA_ATR
    with np.errstate(invalid="ignore"):
        out = {
            "pa_h2_ema": (fr["h_cnt"] == 2) & fr["rising"] & (c > ema) & (fr["h_ext"] <= ema + near * atr),
            "pa_l2_ema": (fr["l_cnt"] == 2) & fr["falling"] & (c < ema) & (fr["l_ext"] >= ema - near * atr),
            "pa_gap20_bull_test": (l <= ema) & (np.r_[0, fr["gap_bull"][:-1]] >= C.PA_GAP_BARS),
            "pa_gap20_bear_test": (h >= ema) & (np.r_[0, fr["gap_bear"][:-1]] >= C.PA_GAP_BARS),
        }
    for key in ("pa_wedge_bottom", "pa_wedge_top", "pa_failed_breakdown", "pa_failed_breakout"):
        out[key] = np.zeros(n, dtype=bool)
    for w in fr["wedges"]:
        out["pa_wedge_top" if w["side"] == "top" else "pa_wedge_bottom"][w["confirm"]] = True
    for f in fr["fails"]:
        out["pa_failed_breakout" if f["side"] == "up" else "pa_failed_breakdown"][f["fail"]] = True
    warm = fr["warm"]
    return pd.DataFrame({k: np.asarray(out[k], dtype=bool) & warm for k in SIGNAL_IDS}, index=bars.index)


def frame_for(ctx) -> dict:
    """analyze_frame for ctx's bars, computed once per ctx (the module and the chart share it)."""
    bars = ctx["bars"]
    memo = ctx.get("_pa_frame")
    if memo is not None and memo[0] is bars:
        return memo[1]
    fr = analyze_frame(bars, ctx["ind"])
    ctx["_pa_frame"] = (bars, fr)
    return fr


# ---------------------------------------------------------------------------- summaries
def measured_moves(fr: dict, dates) -> list[dict]:
    """Measured-move targets at the last bar: leg moves (A-B-C swings: C +/- |B - A|) from the last
    confirmed swings, and range moves (range high / low +/- its height) when a trading range is on."""
    n, close, atr = fr["n"], fr["close"][-1], fr["atr"][-1]
    piv = []                                   # zigzag: alternate kinds, keep the more extreme of a repeat
    for p in sorted(fr["pivots"], key=lambda q: q["i"]):
        if piv and piv[-1]["kind"] == p["kind"]:
            better = p["price"] > piv[-1]["price"] if p["kind"] == "H" else p["price"] < piv[-1]["price"]
            if better:
                piv[-1] = p
            continue
        piv.append(p)
    out = []

    def add(kind, side, target, start_i, points):
        hi_after = np.nanmax(fr["high"][start_i + 1:]) if start_i + 1 < n else np.nan
        lo_after = np.nanmin(fr["low"][start_i + 1:]) if start_i + 1 < n else np.nan
        reached = bool(hi_after >= target) if side == "up" else bool(lo_after <= target)
        out.append({"kind": kind, "side": side, "target": target, "from_date": _iso(dates[start_i]),
                    "points": points, "reached": reached,
                    "distance_pct": target / close - 1.0 if close else None,
                    "distance_atr": (target - close) / atr if np.isfinite(atr) and atr > 0 else None})

    for end in (len(piv), len(piv) - 1):
        if end < 3:
            continue
        a, b, cc = piv[end - 3:end]
        if a["i"] < n - 1 - C.PA_MM_MAX_AGE:
            continue
        pts = [{"label": lab, "date": _iso(dates[q["i"]]), "price": q["price"]} for lab, q in zip("ABC", (a, b, cc))]
        if a["kind"] == "L" and cc["price"] > a["price"] and np.nanmin(fr["low"][cc["i"]:]) >= a["price"]:
            add("leg", "up", cc["price"] + (b["price"] - a["price"]), cc["i"], pts)
        elif a["kind"] == "H" and cc["price"] < a["price"] and np.nanmax(fr["high"][cc["i"]:]) <= a["price"]:
            add("leg", "down", cc["price"] - (a["price"] - b["price"]), cc["i"], pts)
    if fr["tr"][-1]:
        rh, rl = float(fr["rh"][-1]), float(fr["rl"][-1])
        start = n - N_RANGE
        pts = [{"label": "range_high", "date": _iso(dates[start]), "price": rh},
               {"label": "range_low", "date": _iso(dates[start]), "price": rl}]
        add("range", "up", rh + (rh - rl), n - 1, pts)
        add("range", "down", rl - (rh - rl), n - 1, pts)
    return out


def _since(arr: np.ndarray, dates) -> tuple[str | None, int]:
    """(first date of the run of the last value, run length)."""
    if not len(arr):
        return None, 0
    v, i = arr[-1], len(arr) - 1
    while i > 0 and arr[i - 1] == v:
        i -= 1
    return _iso(dates[i]), len(arr) - i


def _last_count(cnt: np.ndarray, show: np.ndarray, prefix: str, dates, n: int) -> dict | None:
    hits = np.flatnonzero((cnt > 0) & show)
    if not hits.size:
        return None
    i = int(hits[-1])
    return {"label": f"{prefix}{cnt[i]}", "date": _iso(dates[i]), "bars_ago": n - 1 - i}


def _caveats(ticker: str) -> list[str]:
    return [
        APPROXIMATION,
        "Brooks' own probabilities (for example that most trading-range breakouts fail) are rules of thumb; "
        "only the statistics measured here are reported.",
        f"Statistics come from {ticker}'s own daily history, in-sample, with entry at the signal-day close and "
        "no costs (the same method as the indicator signals).",
        "Swing pivots for wedges and measured moves are known only k bars after the swing, so those setups fire "
        "late by construction.",
        "Price-action setups do not change the scorecard; they are context next to it.",
    ]


def build_price_action(ctx) -> dict:
    """Module output: the current price-action reading, the latest patterns, measured moves, and
    statistics for the setups that fired in the last C.SIGNAL_ACTIVE_BARS bars plus the library."""
    bars, ind = ctx.get("bars"), ctx.get("ind")
    hz = ctx["hz"]
    horizons = [int(x) for x in hz["stat_horizons"]]
    primary = int(hz["primary"])
    if primary not in horizons:
        horizons.append(primary)
    base = {"approximation": APPROXIMATION, "ema_period": C.PA_EMA_PERIOD, "primary_horizon": primary,
            "stat_horizons": horizons}
    if bars is None or ind is None or len(bars) <= WARMUP:
        return {**base, "available": False, "notes": [], "active": [], "library": [],
                "reason": f"fewer than {WARMUP + 1} bars: no price-action reading"}
    fr = frame_for(ctx)
    dates, n = bars.index, fr["n"]
    notes: list[str] = []
    close, ema, atr = float(fr["close"][-1]), float(fr["ema"][-1]), float(fr["atr"][-1])
    o, hh, ll = (float(bars[k].iloc[-1]) for k in ("open", "high", "low"))
    rng = hh - ll

    state_code = fr["state"][-1]
    st_since, st_bars = _since(fr["state"], dates)
    ai_since, _ = _since(fr["always_in"], dates)
    slope = "rising" if fr["rising"][-1] else "falling" if fr["falling"][-1] else "flat"
    tr = {"active": bool(fr["tr"][-1])}
    if tr["active"]:
        tr.update(high=float(fr["rh"][-1]), low=float(fr["rl"][-1]), window_start=_iso(dates[n - N_RANGE]),
                  height_atr=(float(fr["rh"][-1]) - float(fr["rl"][-1])) / atr if atr > 0 else None)
    w = fr["wedges"][-1] if fr["wedges"] else None
    f = fr["fails"][-1] if fr["fails"] else None

    ev = events(bars, ind, fr)
    cache = SS.OutcomeCache(bars, ind)
    active = SS.active_rows(bars, ind, ev, _BY_ID, horizons, primary, notes, cache)
    library = SS.library_rows(bars, ind, ev, SIGNALS, primary, cache)
    act = {a["id"] for a in active}
    return {
        **base, "available": True, "notes": notes,
        "bar": {"type": BAR_TYPES[int(fr["types"][-1])], "body_share": abs(close - o) / rng if rng > 0 else 0.0,
                "close_position": (close - ll) / rng if rng > 0 else None},
        "ema": ema, "ema_slope": slope, "close_vs_ema_atr": (close - ema) / atr if atr > 0 else None,
        "state": {"label": STATES.get(int(state_code)) if np.isfinite(state_code) else None,
                  "since": st_since, "bars": st_bars,
                  "share_closes_above_ema": _f(fr["above_close"][-1]),
                  "share_bars_above_ema": _f(fr["above_bar"][-1]), "share_bars_below_ema": _f(fr["below_bar"][-1]),
                  "window_bars": N_STATE},
        "always_in": {"direction": ALWAYS_IN[int(fr["always_in"][-1])], "since": ai_since},
        "trading_range": tr,
        "gap_bars": {"bull": int(fr["gap_bull"][-1]), "bear": int(fr["gap_bear"][-1])},
        "last_high_count": _last_count(fr["h_cnt"], fr["rising"], "H", dates, n),
        "last_low_count": _last_count(fr["l_cnt"], fr["falling"], "L", dates, n),
        "last_wedge": None if w is None else {
            "side": w["side"], "confirmed_date": _iso(dates[w["confirm"]]), "bars_ago": n - 1 - w["confirm"],
            "pushes": [{"date": _iso(dates[i]), "price": p} for i, p in w["pushes"]]},
        "last_failed_breakout": None if f is None else {
            "side": f["side"], "date": _iso(dates[f["fail"]]), "breakout_date": _iso(dates[f["breakout"]]),
            "level": f["level"], "bars_ago": n - 1 - f["fail"]},
        "measured_moves": measured_moves(fr, dates),
        "active": active, "inactive_ids": [s for s in SIGNAL_IDS if s not in act], "library": library,
        "caveats": _caveats(ctx.get("ticker", "this stock")),
    }


# ---------------------------------------------------------------------------- chart payload
def chart_payload(ctx, modules: dict, win_index: pd.DatetimeIndex) -> dict:
    """Series and markers for chart 2 on the chart window (dates are the window's daily bars)."""
    pa = modules.get("price_action") or {}
    if not pa.get("available"):
        return {"available": False, "reason": pa.get("reason") or "price-action module unavailable"}
    bars = ctx["bars"]
    fr = frame_for(ctx)
    pos = bars.index.get_indexer(win_index[[0, -1]])
    first, end = int(pos[0]), int(pos[1]) + 1
    dates = bars.index
    inwin = lambda i: first <= i < end  # noqa: E731
    d = lambda i: _iso(dates[i])  # noqa: E731
    ev = events(bars, ctx["ind"], fr)
    sig = {sid: ev[sid].to_numpy() for sid in SIGNAL_IDS}

    markers = []
    for i in range(first, end):
        if fr["h_cnt"][i] and fr["rising"][i]:
            markers.append({"date": d(i), "kind": f"H{fr['h_cnt'][i]}", "side": 1, "price": float(fr["low"][i]),
                            "setup": bool(sig["pa_h2_ema"][i])})
        if fr["l_cnt"][i] and fr["falling"][i]:
            markers.append({"date": d(i), "kind": f"L{fr['l_cnt'][i]}", "side": -1, "price": float(fr["high"][i]),
                            "setup": bool(sig["pa_l2_ema"][i])})
        if sig["pa_gap20_bull_test"][i]:
            markers.append({"date": d(i), "kind": "G20", "side": 1, "price": float(fr["low"][i]), "setup": True})
        if sig["pa_gap20_bear_test"][i]:
            markers.append({"date": d(i), "kind": "G20", "side": -1, "price": float(fr["high"][i]), "setup": True})
    for f in fr["fails"]:
        if inwin(f["fail"]):
            up = f["side"] == "up"
            markers.append({"date": d(f["fail"]), "kind": "FB", "side": -1 if up else 1, "setup": True,
                            "price": float(fr["high"][f["fail"]] if up else fr["low"][f["fail"]]), "level": f["level"],
                            "breakout_date": d(f["breakout"])})
    wds = [{"side": w["side"], "confirmed_date": d(w["confirm"]),
            "pushes": [[d(i), p] for i, p in w["pushes"]]} for w in fr["wedges"] if inwin(w["pushes"][0][0]) and inwin(w["confirm"])]

    ranges, tr = [], fr["tr"]
    i = first
    while i < end:
        if not tr[i]:
            i += 1
            continue
        j = i
        while j + 1 < end and tr[j + 1]:
            j += 1
        s = max(first, i - N_RANGE + 1)
        ranges.append({"start": d(s), "end": d(j), "high": float(np.nanmax(fr["high"][i - N_RANGE + 1:j + 1])),
                       "low": float(np.nanmin(fr["low"][i - N_RANGE + 1:j + 1]))})
        i = j + 1

    sl = slice(first, end)
    state = [None if not np.isfinite(v) else int(v) for v in fr["state"][sl]]
    return {
        "available": True,
        "ema": [[d(k), float(v)] for k, v in zip(range(first, end), fr["ema"][sl]) if np.isfinite(v)],
        "bar_types": [int(v) for v in fr["types"][sl]],
        "states": state,
        "always_in": [int(v) for v in fr["always_in"][sl]],
        "markers": markers, "wedges": wds, "ranges": ranges,
        "measured_moves": pa.get("measured_moves") or [],
        "summary": {k: pa.get(k) for k in ("bar", "ema", "ema_slope", "close_vs_ema_atr", "state", "always_in",
                                            "trading_range", "gap_bars", "last_high_count", "last_low_count",
                                            "last_wedge", "last_failed_breakout")},
        "ema_period": C.PA_EMA_PERIOD,
        "approximation": APPROXIMATION,
    }
