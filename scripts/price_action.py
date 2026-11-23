"""
Al Brooks-style price action on daily bars (chart 2): bar types and signal bars, the market state
(tight channel, broad channel, trading range) with a confirmation rule so it does not flip on every
bar, the always-in direction, H1-H4 / L1-L4 pullback counts in trends and inside trading ranges,
wedges (three pushes), double tops and bottoms, major trend reversals, trading-range breakouts with
follow-through and the ones that fail, climaxes, ii / ioi breakout-mode bars, 20-gap-bar EMA tests,
measured moves, and trend channels.

Brooks reads 5-minute charts bar by bar and leaves much to judgement. Every rule here is a fixed,
mechanical daily-bar approximation, documented in references/patterns.md. Swings are the ATR ZigZag
of swings.py, the same swings the key levels and the swing structure use. The setups are a
pre-registered event library: each one names a signal bar, is entered on a stop beyond that bar on the
next bar (no trigger, no trade) and is measured with the forward-return statistics of signal_stats
from that entry. They are context. Everything is causal: values at bar t use bars <= t, and a
swing pivot exists only from the bar that confirms it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import indicators as I
import candlesticks as CS
import signal_stats as SS
import swings as SW

N_STATE, N_RANGE, SLOPE = C.PA_STATE_BARS, C.PA_RANGE_BARS, C.PA_EMA_SLOPE_BARS
WARMUP = max(N_STATE, N_RANGE) + SLOPE      # events and states start at this bar index (EMA settled)

BAR_TYPES = {2: "bull_trend_bar", 1: "weak_bull_bar", 0: "doji", -1: "weak_bear_bar", -2: "bear_trend_bar"}
STATES = {2: "tight_bull_channel", 1: "bull_channel", 0: "trading_range", -1: "bear_channel",
          -2: "tight_bear_channel"}
ALWAYS_IN = {1: "long", -1: "short", 0: "none"}
SHAPES = {0: None, 1: "inside", 2: "ii", 3: "ioi", -1: "outside"}

_RANGE = (f"a trading range ({N_RANGE}-bar span <= {C.PA_RANGE_MAX_ATR:g} x ATR14, >= "
          f"{C.PA_RANGE_MIN_CROSSES} EMA crosses)")
_ZZ = f"ATR ZigZag, {C.SWING_ATR:g} x ATR14"
SIGNALS = [
    {"id": "pa_h2_ema", "name": "H2 pullback near the rising 20 EMA", "bias": +1, "regime": "current_side",
     "rule": f"signal bar: in a pullback after its first high count (H1) and a lower high since, "
             f"EMA{C.PA_EMA_PERIOD} rising over {SLOPE} bars, signal-bar high > EMA, pullback low <= EMA + "
             f"{C.PA_PULLBACK_EMA_ATR:g} x ATR14; the bar that triggers the entry is the H2"},
    {"id": "pa_l2_ema", "name": "L2 rally near the falling 20 EMA", "bias": -1, "regime": "current_side",
     "rule": f"signal bar: in a rally after its first low count (L1) and a higher low since, "
             f"EMA{C.PA_EMA_PERIOD} falling over {SLOPE} bars, signal-bar low < EMA, rally high >= EMA - "
             f"{C.PA_PULLBACK_EMA_ATR:g} x ATR14; the bar that triggers the entry is the L2"},
    {"id": "pa_wedge_bottom", "name": "Wedge bottom (three pushes down)", "bias": +1, "regime": "current_side",
     "rule": f"three successively lower confirmed swing lows ({_ZZ}) within {C.PA_WEDGE_MAX_BARS} bars, >= "
             f"{C.PA_WEDGE_MIN_SPACING} bars apart, the third push shorter than the second and the lowest low of "
             f"the window; signal bar = the bar that confirms the third low"},
    {"id": "pa_wedge_top", "name": "Wedge top (three pushes up)", "bias": -1, "regime": "current_side",
     "rule": f"three successively higher confirmed swing highs ({_ZZ}) within {C.PA_WEDGE_MAX_BARS} bars, >= "
             f"{C.PA_WEDGE_MIN_SPACING} bars apart, the third push shorter than the second and the highest high of "
             f"the window; signal bar = the bar that confirms the third high"},
    {"id": "pa_failed_breakdown", "name": "Failed breakdown below a trading range", "bias": +1,
     "regime": "current_side",
     "rule": f"in {_RANGE}, a bar trades below the range low and a close back above it follows on that bar or "
             f"within {C.PA_BREAKOUT_FAIL_BARS} bars; signal bar = the bar that closes back inside"},
    {"id": "pa_failed_breakout", "name": "Failed breakout above a trading range", "bias": -1,
     "regime": "current_side",
     "rule": f"in {_RANGE}, a bar trades above the range high and a close back below it follows on that bar or "
             f"within {C.PA_BREAKOUT_FAIL_BARS} bars; signal bar = the bar that closes back inside"},
    {"id": "pa_gap20_bull_test", "name": "First EMA test after 20 bull gap bars", "bias": +1,
     "regime": "current_side",
     "rule": f"low <= EMA after at least {C.PA_GAP_BARS} bars in a row with low > EMA; signal bar = that bar"},
    {"id": "pa_gap20_bear_test", "name": "First EMA test after 20 bear gap bars", "bias": -1,
     "regime": "current_side",
     "rule": f"high >= EMA after at least {C.PA_GAP_BARS} bars in a row with high < EMA; signal bar = that bar"},
    {"id": "pa_tr_h2", "name": "H2 in the bottom third of a trading range", "bias": +1, "regime": "current_side",
     "rule": f"signal bar: in {_RANGE}, in a pullback after its first high count and a lower high since, pullback "
             f"low in the bottom third of the range; the bar that triggers the entry is the H2"},
    {"id": "pa_tr_l2", "name": "L2 in the top third of a trading range", "bias": -1, "regime": "current_side",
     "rule": f"signal bar: in {_RANGE}, in a rally after its first low count and a higher low since, rally "
             f"high in the top third of the range; the bar that triggers the entry is the L2"},
    {"id": "pa_double_bottom", "name": "Double bottom", "bias": +1, "regime": "current_side",
     "rule": f"two consecutive confirmed swing lows ({_ZZ}) within {C.PA_DOUBLE_TOL_ATR:g} x ATR14 of each other "
             f"and at most {C.PA_DOUBLE_MAX_BARS} bars apart; signal bar = the bar that confirms the second low"},
    {"id": "pa_double_top", "name": "Double top", "bias": -1, "regime": "current_side",
     "rule": f"two consecutive confirmed swing highs ({_ZZ}) within {C.PA_DOUBLE_TOL_ATR:g} x ATR14 of each other "
             f"and at most {C.PA_DOUBLE_MAX_BARS} bars apart; signal bar = the bar that confirms the second high"},
    {"id": "pa_mtr_bottom", "name": "Major trend reversal up (bottom)", "bias": +1, "regime": "current_side",
     "rule": f"a confirmed swing low ({_ZZ}) that is the lowest low of {C.PA_TREND_EXTREME_BARS} bars with the "
             f"EMA{C.PA_EMA_PERIOD} falling, a rally to a confirmed swing high with at least one close above the EMA, "
             f"then a test whose low stays above that swing low - {C.PA_DOUBLE_TOL_ATR:g} x ATR14; signal bar = the "
             f"first strong bull signal bar within {C.PA_MTR_SIGNAL_BARS} bars of the test's low"},
    {"id": "pa_mtr_top", "name": "Major trend reversal down (top)", "bias": -1, "regime": "current_side",
     "rule": f"a confirmed swing high ({_ZZ}) that is the highest high of {C.PA_TREND_EXTREME_BARS} bars with the "
             f"EMA{C.PA_EMA_PERIOD} rising, a selloff to a confirmed swing low with at least one close below the EMA, "
             f"then a test whose high stays below that swing high + {C.PA_DOUBLE_TOL_ATR:g} x ATR14; signal bar = the "
             f"first strong bear signal bar within {C.PA_MTR_SIGNAL_BARS} bars of the test's high"},
    {"id": "pa_breakout_up", "name": "Breakout above a trading range with follow-through", "bias": +1,
     "regime": "current_side",
     "rule": f"a bull trend bar closes above the high of {_RANGE} and the next bar is a bull bar closing above that "
             f"close; signal bar = that follow-through bar"},
    {"id": "pa_breakout_down", "name": "Breakout below a trading range with follow-through", "bias": -1,
     "regime": "current_side",
     "rule": f"a bear trend bar closes below the low of {_RANGE} and the next bar is a bear bar closing below that "
             f"close; signal bar = that follow-through bar"},
]
SIGNAL_IDS = [s["id"] for s in SIGNALS]
_BY_ID = {s["id"]: s for s in SIGNALS}

APPROXIMATION = ("Daily-bar approximation of Al Brooks' price action: Brooks reads intraday (5-minute) "
                 "charts with discretion; these are fixed rules, and their statistics come from this "
                 "stock's own daily history, not from Brooks' books.")
ENTRY_RULE = ("Stop entry: a bullish setup is entered when the bar after its signal bar trades above the signal "
              "bar's high, at that high or at the open if the bar opens above it; a bearish setup mirrors this "
              "below the signal bar's low. A setup whose next bar does not trigger is no trade. Returns run from "
              "the entry price to the close h bars after the entry bar; MAE counts the entry bar too (the order of "
              "its high and low is unknown). Baselines are every day entered the same way.")
SIGNAL_BAR_RULE = (f"strong bull signal bar: close > open, body > {C.PA_DOJI_BODY:g} x range, close in the top third "
                   f"of the range, range <= {C.PA_SIGNAL_MAX_ATR:g} x ATR14; strong bear signal bar: the mirror image")
STATE_RULE = (f"the state read from the last {N_STATE} bars (the reading) replaces the current state once the two "
              f"have differed {C.PA_STATE_CONFIRM_BARS} bars in a row, or at once on the follow-through bar of a "
              f"trading-range breakout. While the last close is on a channel's own side of the EMA the channel "
              f"gets the benefit of the doubt: its share thresholds are {C.PA_STATE_EXIT_MARGIN:g} lower "
              f"({C.PA_CHANNEL_SHARE:g} to enter a channel, {C.PA_CHANNEL_SHARE - C.PA_STATE_EXIT_MARGIN:g} to stay "
              f"in it) and the trading-range test does not end it; a close on the other side of the EMA is read "
              f"with the entry thresholds. After a breakout with follow-through the reading is at least a "
              f"channel that way for up to {C.PA_BREAKOUT_HOLD_BARS} bars while every close holds beyond the range")
CLIMAX_RULE = (f"{C.PA_CLIMAX_BARS} or more trend bars in a row in one direction, the last closing more than "
               f"{C.PA_CLIMAX_EMA_ATR:g} x ATR14 beyond the EMA")


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


def signal_bars(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray) -> tuple:
    """(strong bull signal bar, strong bear signal bar) per bar: see SIGNAL_BAR_RULE."""
    rng = h - l
    with np.errstate(invalid="ignore", divide="ignore"):
        pos = np.where(rng > 0, (c - l) / rng, np.nan)
        body = np.where(rng > 0, np.abs(c - o) / rng, 0.0) > C.PA_DOJI_BODY
        size = rng <= C.PA_SIGNAL_MAX_ATR * atr
        bull = (c > o) & body & (pos >= 1.0 - C.PA_SIGNAL_CLOSE) & size
        bear = (c < o) & body & (pos <= C.PA_SIGNAL_CLOSE) & size
    return bull, bear


def bar_shapes(h: np.ndarray, l: np.ndarray) -> np.ndarray:
    """1 inside bar (high <= prior high and low >= prior low), 2 ii (two inside bars in a row), 3 ioi
    (inside, outside, inside), -1 outside bar (high > prior high and low < prior low), 0 otherwise."""
    n = len(h)
    inside, outside = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    with np.errstate(invalid="ignore"):
        inside[1:] = (h[1:] <= h[:-1]) & (l[1:] >= l[:-1])
        outside[1:] = (h[1:] > h[:-1]) & (l[1:] < l[:-1])
    out = np.where(outside, -1, inside.astype(int))
    out[1:] = np.where(inside[1:] & inside[:-1], 2, out[1:])
    out[2:] = np.where(inside[2:] & outside[1:-1] & inside[:-2], 3, out[2:])
    return out


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


def _pullback(high: np.ndarray, low: np.ndarray, bull: bool, context: np.ndarray | None = None) -> tuple:
    """pullback_counts plus what is known on the bar before a count: (count, pullback extreme at a count
    bar, next count, pullback extreme so far). next count k at bar s means: if bar s+1 trades above bar
    s's high (below its low on the bear side), it is the k-th count, so bar s is that entry's signal bar."""
    x, y = (high, low) if bull else (-low, -high)     # mirror: bear counting is bull counting on -price
    n = len(x)
    count, nxt = np.zeros(n, dtype=int), np.zeros(n, dtype=int)
    extreme, sofar = np.full(n, np.nan), np.full(n, np.nan)
    in_pb, armed, cnt, leg, pb = False, False, 0, -np.inf, np.inf
    for t in range(1, n):
        if not (np.isfinite(x[t]) and np.isfinite(x[t - 1])) or (context is not None and not context[t]):
            in_pb = False
            continue
        if not in_pb:
            if x[t] < x[t - 1]:
                in_pb, armed, cnt, leg, pb = True, True, 0, x[t - 1], y[t]
                nxt[t], sofar[t] = 1, pb
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
        if in_pb and armed and cnt < C.PA_COUNT_MAX:
            nxt[t], sofar[t] = cnt + 1, pb
    return (count, extreme, nxt, sofar) if bull else (count, -extreme, nxt, -sofar)


def pullback_counts(high: np.ndarray, low: np.ndarray, bull: bool,
                    context: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Brooks' bar counting: (count at each bar, pullback extreme at that bar).

    Bull side: a pullback starts on a bar whose high is below the prior bar's high. Inside it, each
    bar whose high goes above the prior bar's high is the next count (H1, H2, ...), provided a lower
    high came after the previous count. A high above the leg's high ends the pullback (that bar can
    still be a count). The count is abandoned after PA_COUNT_MAX, and on any bar outside `context`
    (the trend the counts belong to, e.g. a rising EMA). The bear side mirrors it with lows (L1, L2, ...).
    """
    return _pullback(high, low, bull, context)[:2]


def zigzag_pivots(high: np.ndarray, low: np.ndarray, atr: np.ndarray) -> list[dict]:
    """ATR ZigZag swing pivots (swings.zigzag, C.SWING_ATR) in confirmation order: {kind: "H"/"L", i,
    confirm, price}. A pivot is known from bar `confirm` on, the bar that reversed C.SWING_ATR ATRs
    from it. The first bar is never a pivot (no left side)."""
    piv, _ = SW.zigzag(high, low, atr, C.SWING_ATR)
    return [{"kind": "H" if kind > 0 else "L", "i": int(i), "confirm": int(conf), "price": float(price)}
            for i, kind, price, conf in piv if i > 0]


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


def doubles(pivots: list[dict], high: np.ndarray, low: np.ndarray, atr: np.ndarray) -> list[dict]:
    """Double tops and bottoms: two consecutive swing highs (lows) within PA_DOUBLE_TOL_ATR ATRs (of the
    second one's bar) and at most PA_DOUBLE_MAX_BARS apart: {side, points [(i, price) x 2], confirm,
    variant}. variant "reversal": the pair is the highest high (lowest low) of PA_TREND_EXTREME_BARS bars,
    the end of a move; "flag": a lower double top or higher double bottom (Brooks' double-top bear flag,
    double-bottom bull flag)."""
    out, last = [], {"H": None, "L": None}
    for p in pivots:
        q, last[p["kind"]] = last[p["kind"]], p
        if q is None:
            continue
        tol = C.PA_DOUBLE_TOL_ATR * atr[p["i"]]
        if not np.isfinite(tol) or abs(p["price"] - q["price"]) > tol or p["i"] - q["i"] > C.PA_DOUBLE_MAX_BARS:
            continue
        up = p["kind"] == "H"
        lo = max(0, p["i"] - C.PA_TREND_EXTREME_BARS + 1)
        if up:
            extreme = max(p["price"], q["price"]) >= np.nanmax(high[lo:p["i"] + 1])
        else:
            extreme = min(p["price"], q["price"]) <= np.nanmin(low[lo:p["i"] + 1])
        out.append({"side": "top" if up else "bottom", "points": [(q["i"], q["price"]), (p["i"], p["price"])],
                    "confirm": p["confirm"], "variant": "reversal" if extreme else "flag"})
    return out


def major_reversals(pivots: list[dict], high: np.ndarray, low: np.ndarray, close: np.ndarray, ema: np.ndarray,
                    rising: np.ndarray, falling: np.ndarray, atr: np.ndarray, bull_sig: np.ndarray,
                    bear_sig: np.ndarray) -> list[dict]:
    """Major trend reversals (Brooks' MTR), bottom side: a swing low that is the lowest low of
    PA_TREND_EXTREME_BARS bars while the EMA falls (the trend's extreme); a rally to the next swing high
    with at least one close above the EMA (the break of the trend); then, once that high is confirmed, a
    test whose low stays above the extreme - PA_DOUBLE_TOL_ATR ATRs; the first strong bull signal bar
    within PA_MTR_SIGNAL_BARS bars of the test's low is the setup. Tops mirror it.
    {side, extreme (i, price), break (i, price), test (i, price), signal, variant}; variant "higher_low" /
    "lower_high" when the test stops more than the tolerance short of the extreme, else "double"."""
    out, n = [], len(close)
    for j in range(1, len(pivots)):
        ext, brk = pivots[j - 1], pivots[j]
        if ext["kind"] == brk["kind"]:
            continue
        up = ext["kind"] == "L"                      # a bottom: the reversal is up
        i0, i1 = ext["i"], brk["i"]
        lo = max(0, i0 - C.PA_TREND_EXTREME_BARS + 1)
        with np.errstate(invalid="ignore"):
            if up:
                trend = falling[i0] and ext["price"] <= np.nanmin(low[lo:i0 + 1])
                broke = bool((close[i0 + 1:i1 + 1] > ema[i0 + 1:i1 + 1]).any())
            else:
                trend = rising[i0] and ext["price"] >= np.nanmax(high[lo:i0 + 1])
                broke = bool((close[i0 + 1:i1 + 1] < ema[i0 + 1:i1 + 1]).any())
        if not (trend and broke):
            continue
        end = pivots[j + 1]["confirm"] if j + 1 < len(pivots) else n - 1
        sig = bull_sig if up else bear_sig
        k, test = i1, np.inf if up else -np.inf      # the test's extreme so far: bars after the break pivot
        for t in range(i1 + 1, end + 1):
            v = low[t] if up else high[t]
            if np.isfinite(v) and (v < test if up else v > test):
                k, test = t, float(v)
            if t < brk["confirm"]:
                continue
            tol = C.PA_DOUBLE_TOL_ATR * atr[t]
            if not np.isfinite(tol) or (test < ext["price"] - tol if up else test > ext["price"] + tol):
                break                                # the trend resumed: a new extreme, not a test
            if t - k < C.PA_MTR_SIGNAL_BARS and sig[t]:
                short = test > ext["price"] + tol if up else test < ext["price"] - tol
                out.append({"side": "bottom" if up else "top", "extreme": (i0, ext["price"]),
                            "break": (i1, brk["price"]), "test": (k, test), "signal": t,
                            "variant": ("higher_low" if up else "lower_high") if short else "double"})
                break
    return sorted(out, key=lambda r: r["signal"])


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


def breakouts(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, types: np.ndarray,
              atr: np.ndarray, rh: np.ndarray, rl: np.ndarray, is_tr: np.ndarray) -> list[dict]:
    """Trading-range breakouts by a trend bar: a bull trend bar closing above the high (a bear trend bar
    below the low) of the trading range of the previous bars.

    {side, bar, level, range_atr, close_position, gap_open, trend_bars, follow, end, back}:
    range_atr / close_position describe the breakout bar; gap_open: it opened beyond the prior bar's high
    (low); trend_bars: trend bars in a row that way ending on it; follow: the next bar when that is a bar
    the same way (not a doji) closing beyond the breakout bar's close, the follow-through, else None;
    end: the last bar of the breakout, the bar before the first close back at or inside the range edge,
    PA_BREAKOUT_HOLD_BARS bars after the breakout bar at most; back: that first close back, or None. While
    one breakout lasts no new one starts on its side. Every field is known on the bar it names: a longer
    series only extends end / fills back and follow."""
    out, n = [], len(close)
    for side, sign, edge in (("up", 1, rh), ("down", -1, rl)):
        with np.errstate(invalid="ignore"):
            beyond = (close > edge) if sign > 0 else (close < edge)
        run = _runs(types == 2 * sign)
        last_end = -1
        for b in np.flatnonzero(is_tr & (types == 2 * sign) & beyond).tolist():
            if b <= last_end:
                continue
            level = float(edge[b])
            end, back = b, None
            for t in range(b + 1, min(n, b + C.PA_BREAKOUT_HOLD_BARS + 1)):
                if (close[t] <= level) if sign > 0 else (close[t] >= level):
                    back = t
                    break
                end = t
            follow = None
            if b + 1 < n and back != b + 1 and types[b + 1] * sign >= 1 and (close[b + 1] - close[b]) * sign > 0:
                follow = b + 1
            rng = high[b] - low[b]
            out.append({"side": side, "bar": b, "level": level,
                        "range_atr": float(rng / atr[b]) if np.isfinite(atr[b]) and atr[b] > 0 else None,
                        "close_position": float((close[b] - low[b]) / rng) if rng > 0 else None,
                        "gap_open": bool(open_[b] > high[b - 1]) if sign > 0 else bool(open_[b] < low[b - 1]),
                        "trend_bars": int(run[b]), "follow": follow, "end": end, "back": back})
            last_end = end
    return sorted(out, key=lambda r: r["bar"])


def market_states(warm: np.ndarray, rising: np.ndarray, falling: np.ndarray, is_tr: np.ndarray,
                  above_close: np.ndarray, below_close: np.ndarray, above_bar: np.ndarray, below_bar: np.ndarray,
                  bo_live: np.ndarray, fast: np.ndarray, side: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The market state with confirmation: (state, reading, pending). See STATE_RULE.

    reading: what the last PA_STATE_BARS bars say at each bar: tight channel (the EMA moving that way
    and PA_TIGHT_SHARE of the bars entirely beyond it), else broad channel (the EMA moving that way,
    PA_CHANNEL_SHARE of the closes beyond it, and no trading range by the range test), else trading
    range; a live breakout with follow-through (bo_live) makes it at least a channel that way. A channel
    that is the current state gets the benefit of the doubt while the last close is on its own side of
    the EMA (side: +1 above, -1 below): there its share thresholds are PA_STATE_EXIT_MARGIN lower and the
    range test does not end it. A close on the other side is read with the entry thresholds.
    state: the reading replaces the state once the two have differed PA_STATE_CONFIRM_BARS bars in a
    row (the state then takes the latest reading), or at once on a bar flagged in `fast` (the
    follow-through bar of a breakout).
    pending: how many bars in a row the reading has differed from the state (0 when they agree)."""
    n = len(warm)
    state, reading, pending = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, dtype=int)
    cur, run = np.nan, 0
    for t in range(n):
        if not warm[t]:
            cur, run = np.nan, 0
            continue
        k = int(cur) if np.isfinite(cur) else 0
        bull_doubt, bear_doubt = k > 0 and side[t] > 0, k < 0 and side[t] < 0
        ease = lambda on: C.PA_STATE_EXIT_MARGIN if on else 0.0  # noqa: E731
        r = 0
        if rising[t] and above_close[t] >= C.PA_CHANNEL_SHARE - ease(bull_doubt) and not (is_tr[t] and not bull_doubt):
            r = 1
        if falling[t] and below_close[t] >= C.PA_CHANNEL_SHARE - ease(bear_doubt) and not (is_tr[t] and not bear_doubt):
            r = -1
        if rising[t] and above_bar[t] >= C.PA_TIGHT_SHARE - ease(bull_doubt and k == 2):
            r = 2
        if falling[t] and below_bar[t] >= C.PA_TIGHT_SHARE - ease(bear_doubt and k == -2):
            r = -2
        if bo_live[t] > 0:
            r = max(r, 1)
        elif bo_live[t] < 0:
            r = min(r, -1)
        run = run + 1 if np.isfinite(cur) and r != cur else 0
        if not np.isfinite(cur) or (r != cur and (run >= C.PA_STATE_CONFIRM_BARS or fast[t])):
            cur, run = r, 0
        state[t], reading[t], pending[t] = cur, r, run
    return state, reading, pending


def climaxes(types: np.ndarray, close: np.ndarray, ema: np.ndarray, atr: np.ndarray) -> np.ndarray:
    """Bars in the climax run ending at each bar: positive for a buy climax, negative for a sell climax,
    0 for none. See CLIMAX_RULE."""
    up, dn = _runs(types == 2), _runs(types == -2)
    with np.errstate(invalid="ignore"):
        far_up = (close - ema) > C.PA_CLIMAX_EMA_ATR * atr
        far_dn = (ema - close) > C.PA_CLIMAX_EMA_ATR * atr
    return np.where((up >= C.PA_CLIMAX_BARS) & far_up, up, np.where((dn >= C.PA_CLIMAX_BARS) & far_dn, -dn, 0))


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

    # what the last PA_STATE_BARS bars say (this bar included), and the trading range of those bars
    share = lambda m: pd.Series(m.astype(float)).rolling(N_STATE, min_periods=N_STATE).mean().to_numpy()  # noqa: E731
    above_bar, below_bar = share(l > ema), share(h < ema)
    above_close, below_close = share(c > ema), share(c < ema)
    rh_i, rl_i, tr_i = _range_window(h, l, cross, atr, N_RANGE, 0)

    # trading range of the bars before t: breakouts, their follow-through and their failure
    rh_p, rl_p, tr_p = _range_window(h, l, cross, atr, N_RANGE, 1)
    tr_p = tr_p & warm
    fails = failed_breakouts(c, h, l, rh_p, rl_p, tr_p)
    bos = breakouts(o, h, l, c, types, atr, rh_p, rl_p, tr_p)
    bo_live, fast = np.zeros(n, dtype=int), np.zeros(n, dtype=bool)
    for b in bos:                                  # from the follow-through bar the breakout is the state
        if b["follow"] is not None:
            bo_live[b["follow"]:b["end"] + 1] = 1 if b["side"] == "up" else -1
            fast[b["follow"]] = True
    with np.errstate(invalid="ignore"):
        side = np.nan_to_num(np.sign(c - ema))
    state, reading, pending = market_states(warm, rising, falling, tr_i, above_close, below_close, above_bar,
                                            below_bar, bo_live, fast, side)

    tr = tr_i & warm
    h_cnt, h_ext, h_next, h_pb = _pullback(h, l, True, rising)
    l_cnt, l_ext, l_next, l_pb = _pullback(h, l, False, falling)
    th_cnt, th_ext, th_next, th_pb = _pullback(h, l, True, tr)       # counts inside a trading range
    tl_cnt, tl_ext, tl_next, tl_pb = _pullback(h, l, False, tr)
    gap_bull, gap_bear = _runs(l > ema), _runs(h < ema)
    bull_sig, bear_sig = signal_bars(o, h, l, c, atr)
    pivots = zigzag_pivots(h, l, atr)
    wds = wedges(pivots, h, l)
    dbl = doubles(pivots, h, l, atr)
    mtr = major_reversals(pivots, h, l, c, ema, rising, falling, atr, bull_sig, bear_sig)
    ai = always_in(c, h, l, ema, types, state)
    return {"n": n, "ema": ema, "atr": atr, "warm": warm, "types": types, "rising": rising, "falling": falling,
            "state": state, "state_reading": reading, "state_pending": pending, "bo_live": bo_live,
            "above_close": above_close, "above_bar": above_bar, "below_bar": below_bar,
            "rh": rh_i, "rl": rl_i, "tr": tr, "fails": fails, "breakouts": bos,
            "h_cnt": h_cnt, "h_ext": h_ext, "h_next": h_next, "h_pb": h_pb,
            "l_cnt": l_cnt, "l_ext": l_ext, "l_next": l_next, "l_pb": l_pb,
            "th_cnt": th_cnt, "th_ext": th_ext, "th_next": th_next, "th_pb": th_pb,
            "tl_cnt": tl_cnt, "tl_ext": tl_ext, "tl_next": tl_next, "tl_pb": tl_pb,
            "gap_bull": gap_bull, "gap_bear": gap_bear, "bull_sig": bull_sig, "bear_sig": bear_sig,
            "shape": bar_shapes(h, l), "climax": climaxes(types, c, ema, atr),
            "pivots": pivots, "wedges": wds, "doubles": dbl, "mtrs": mtr, "always_in": ai,
            "open": o, "close": c, "high": h, "low": l}


def _listed(fr: dict) -> dict:
    """{setup id: {signal bar: (reference price, pattern record)}} for the setups that come from pattern
    lists. The reference price is what the setup hangs on (third push, second bottom, the test's extreme,
    the failed breakout's extreme, the broken range edge): where it is located from."""
    if "_listed" in fr:
        return fr["_listed"]
    out = fr["_listed"] = {sid: {} for sid in SIGNAL_IDS}
    for w in fr["wedges"]:
        out["pa_wedge_top" if w["side"] == "top" else "pa_wedge_bottom"][w["confirm"]] = (w["pushes"][2][1], w)
    for d in fr["doubles"]:
        out["pa_double_top" if d["side"] == "top" else "pa_double_bottom"][d["confirm"]] = (d["points"][1][1], d)
    for m in fr["mtrs"]:
        out["pa_mtr_top" if m["side"] == "top" else "pa_mtr_bottom"][m["signal"]] = (m["test"][1], m)
    for f in fr["fails"]:
        up = f["side"] == "up"
        seg = slice(f["breakout"], f["fail"] + 1)
        ref = float(np.nanmax(fr["high"][seg]) if up else np.nanmin(fr["low"][seg]))
        out["pa_failed_breakout" if up else "pa_failed_breakdown"][f["fail"]] = (ref, f)
    for b in fr["breakouts"]:
        if b["follow"] is not None:
            out["pa_breakout_up" if b["side"] == "up" else "pa_breakout_down"][b["follow"]] = (b["level"], b)
    return out


def events(bars: pd.DataFrame, ind: pd.DataFrame, fr: dict | None = None) -> pd.DataFrame:
    """One bool column per price-action setup (SIGNALS order), True on its signal bar: the bar whose
    high (low) the next bar must take out for the setup to be entered (ENTRY_RULE)."""
    fr = fr or analyze_frame(bars, ind)
    n, ema, atr, h, l = fr["n"], fr["ema"], fr["atr"], fr["high"], fr["low"]
    near, third = C.PA_PULLBACK_EMA_ATR, C.PA_RANGE_THIRD * (fr["rh"] - fr["rl"])
    with np.errstate(invalid="ignore"):
        out = {
            "pa_h2_ema": (fr["h_next"] == 2) & fr["rising"] & (h > ema) & (fr["h_pb"] <= ema + near * atr),
            "pa_l2_ema": (fr["l_next"] == 2) & fr["falling"] & (l < ema) & (fr["l_pb"] >= ema - near * atr),
            "pa_gap20_bull_test": (l <= ema) & (np.r_[0, fr["gap_bull"][:-1]] >= C.PA_GAP_BARS),
            "pa_gap20_bear_test": (h >= ema) & (np.r_[0, fr["gap_bear"][:-1]] >= C.PA_GAP_BARS),
            "pa_tr_h2": (fr["th_next"] == 2) & fr["tr"] & (fr["th_pb"] <= fr["rl"] + third),
            "pa_tr_l2": (fr["tl_next"] == 2) & fr["tr"] & (fr["tl_pb"] >= fr["rh"] - third),
        }
    for sid, hits in _listed(fr).items():
        if sid not in out:
            out[sid] = np.zeros(n, dtype=bool)
            out[sid][sorted(hits)] = True
    warm = fr["warm"]
    return pd.DataFrame({k: np.asarray(out[k], dtype=bool) & warm for k in SIGNAL_IDS}, index=bars.index)


def reference_price(fr: dict, sid: str, s: int) -> float | None:
    """The price setup `sid` with signal bar s hangs on: its pullback low / rally high, the tested EMA
    bar's extreme, or the pattern's own extreme (_listed)."""
    hit = _listed(fr).get(sid, {}).get(s)
    if hit:
        return _f(hit[0])
    key = {"pa_h2_ema": "h_pb", "pa_l2_ema": "l_pb", "pa_tr_h2": "th_pb", "pa_tr_l2": "tl_pb",
           "pa_gap20_bull_test": "low", "pa_gap20_bear_test": "high"}.get(sid)
    return _f(fr[key][s]) if key else None


# ---------------------------------------------------------------------------- stop entries
def stop_entries(bars: pd.DataFrame) -> dict:
    """{+1: (triggered, entry price), -1: (...)} for every bar taken as a signal bar (ENTRY_RULE):
    triggered[s] says bar s+1 traded beyond bar s's high (low); the entry price is that high (low), or
    bar s+1's open when it opens beyond it. The last bar is never triggered (its next bar is unknown)."""
    o, h, l = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low"))
    nxt = lambda a: np.r_[a[1:], np.nan]  # noqa: E731
    with np.errstate(invalid="ignore"):
        up, dn = nxt(h) > h, nxt(l) < l
        return {1: (up, np.where(up, np.fmax(h, nxt(o)), np.nan)), -1: (dn, np.where(dn, np.fmin(l, nxt(o)), np.nan))}


def stop_outcomes(bars: pd.DataFrame, h: int, side: int) -> tuple[np.ndarray, np.ndarray]:
    """Forward return and MAE of a stop entry for every bar taken as a signal bar (NaN where the next
    bar does not trigger or the exit lies outside the data). Entry on bar s+1 at stop_entries' price;
    return to the close of bar s+1+h; MAE from the worst low (high for a bearish entry) of bars
    s+1 .. s+1+h. Dividend-adjusted closes and extremes where they are complete for the window, with
    the entry price scaled by the entry bar's adjustment factor, so both legs share one price basis."""
    trig, entry = stop_entries(bars)[1 if side > 0 else -1]
    entry = pd.Series(entry, index=bars.index)

    def pair(close, low, high, factor):
        px = entry * factor.shift(-1)                              # the entry bar's adjustment
        worst = (high.rolling(h + 1, min_periods=h + 1).max() if side < 0
                 else low.rolling(h + 1, min_periods=h + 1).min()).shift(-(h + 1))    # bars s+1 .. s+1+h
        return close.shift(-(h + 1)) / px - 1.0, worst / px - 1.0

    one = pd.Series(1.0, index=bars.index)
    fwd, mae = pair(bars["close"], bars["low"], bars["high"], one)
    if "adj_close" in bars.columns:
        missing = pd.Series(np.nan, index=bars.index)
        a_fwd, a_mae = pair(bars["adj_close"], bars.get("adj_low", missing), bars.get("adj_high", missing),
                            bars["adj_close"] / bars["close"])
        fwd, mae = a_fwd.where(a_fwd.notna(), fwd), a_mae.where(a_mae.notna(), mae)
    keep = pd.Series(trig, index=bars.index)
    return (fwd.where(keep).to_numpy(dtype=float, na_value=np.nan),
            mae.where(keep).to_numpy(dtype=float, na_value=np.nan))


class StopEntryCache(SS.OutcomeCache):
    """signal_stats' outcome cache with stop entries: the setups and their baselines (every day entered
    the same way) are measured from the entry price of ENTRY_RULE instead of the signal bar's close."""

    def outcomes(self, h: int, side: int) -> tuple:
        if (h, side) not in self._out:
            self._out[(h, side)] = stop_outcomes(self.bars, h, side)
        return self._out[(h, side)]


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
    piv = sorted(fr["pivots"], key=lambda q: q["i"])       # ZigZag pivots alternate by construction
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


def trend_channels(ctx) -> dict:
    """The trendlines of the horizon's key-levels window (the same lines as levels.trendlines) with their
    channel lines: {"up": line or None, "down": line or None, ...}; each line is swings.trendlines' dict
    plus channel_line (swings.channel_line, or None)."""
    bars, atr = ctx["bars"], ctx["ind"]["atr14"]
    lookback = int(ctx["hz"]["levels_lookback"])
    window = bars.iloc[-lookback:]
    pivots = SW.daily_pivots(bars, atr, lookback)
    lines = SW.trendlines(window, atr, pivots)
    out = {"window_bars": int(len(window)), "window_start": _iso(window.index[0]), "tol_atr": lines["tol_atr"],
           "rule": lines["rule"], "channel_rule": SW.CHANNEL_RULE.format(t=C.TRENDLINE_TOL_ATR)}
    for kind, other in (("up", "highs"), ("down", "lows")):
        line = lines[kind]
        out[kind] = None if line is None else {**line, "channel_line": SW.channel_line(window, atr, line, pivots[other])}
    return out


def _line_at(ch: dict, bias: int, price: float, when, atr: float) -> dict | None:
    """The trendline or channel line a setup's reference price sits on (within C.TRENDLINE_TOL_ATR ATRs):
    for a bullish setup the up trendline or the channel line of the down trend, for a bearish one the
    down trendline or the channel line of the up trend. A line drawn through the reference swing itself
    says nothing about it, so a trendline counts there only with a third touch, a channel line never."""
    if not np.isfinite(atr) or atr <= 0:
        return None
    day = _iso(when)
    for kind, which in ((("up", "trendline"), ("down", "channel_line")) if bias > 0
                        else (("down", "trendline"), ("up", "channel_line"))):
        line = ch.get(kind)
        if not line or day < line["anchor"]["date"] or day not in ch["_ago"]:
            continue
        if which == "trendline":
            if day in (line["anchor"]["date"], line["second"]["date"]) and line["touches"] < 3:
                continue
            offset = 0.0
        else:
            cl = line.get("channel_line")
            if not cl or day == cl["through"]["date"]:
                continue
            offset = cl["offset"]
        value = line["anchor"]["price"] + line["slope_per_bar"] * (line["span_bars"] - ch["_ago"][day]) + offset
        if abs(price - value) <= C.TRENDLINE_TOL_ATR * atr:
            return {"kind": kind, "line": which, "price": float(value), "touches": line["touches"],
                    "state": line["state"]}
    return None


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


def _signal_bar(fr: dict, dates, s: int, side: int) -> dict:
    """The signal bar of a setup, read against SIGNAL_BAR_RULE (strong, or weak with the reasons)."""
    o, h, l, c, atr = (float(fr[k][s]) for k in ("open", "high", "low", "close", "atr"))
    rng = h - l
    pos = (c - l) / rng if rng > 0 else None
    strong = bool((fr["bull_sig"] if side > 0 else fr["bear_sig"])[s])
    why = []
    if not strong:
        body = rng > 0 and abs(c - o) / rng > C.PA_DOJI_BODY and ((c > o) if side > 0 else (c < o))
        if not body:
            why.append("not a bull bar" if side > 0 else "not a bear bar")
        if pos is None or (pos < 1.0 - C.PA_SIGNAL_CLOSE if side > 0 else pos > C.PA_SIGNAL_CLOSE):
            why.append("close below the top third" if side > 0 else "close above the bottom third")
        if not rng <= C.PA_SIGNAL_MAX_ATR * atr:
            why.append(f"range over {C.PA_SIGNAL_MAX_ATR:g} ATR")
    return {"date": _iso(dates[s]), "high": h, "low": l, "close_position": pos,
            "range_atr": rng / atr if np.isfinite(atr) and atr > 0 else None, "strong": strong, "weak_because": why}


def _reference_bar(fr: dict, sid: str, s: int, price: float) -> int:
    """The bar that made a setup's reference price: the pattern's own bar, else the latest bar up to the
    signal bar whose low (high) is that price."""
    hit = _listed(fr).get(sid, {}).get(s)
    if hit:
        rec = hit[1]
        for key in ("test", ):
            if key in rec:
                return int(rec[key][0])
        if "pushes" in rec:
            return int(rec["pushes"][2][0])
        if "points" in rec:
            return int(rec["points"][1][0])
        if "bar" in rec:
            return int(rec["bar"])
    series = fr["low"] if _BY_ID[sid]["bias"] > 0 else fr["high"]
    for i in range(s, max(-1, s - C.PA_WEDGE_MAX_BARS), -1):
        if series[i] == price:
            return i
    return s


def _setup_details(a: dict, fr: dict, dates, stop: dict, ch: dict) -> dict:
    """An active row with what a chart reader asks next: the entry (ENTRY_RULE: pending or triggered),
    the signal bar's quality, the pattern's variant, and where the setup formed (the range third and
    the trendline or channel line its reference price sits on; the key zone is added by locate())."""
    n, sid = fr["n"], a["id"]
    side = 1 if _BY_ID[sid]["bias"] > 0 else -1
    s = n - 1 - int(a["bars_ago"])
    pending = s == n - 1
    entry = {"order": "buy_stop" if side > 0 else "sell_stop",
             "trigger_price": float(fr["high"][s] if side > 0 else fr["low"][s]),
             "status": "pending" if pending else "triggered",
             "entry_date": None if pending else _iso(dates[s + 1]),
             "entry_price": None if pending else _f(stop[side][1][s])}
    hit = _listed(fr).get(sid, {}).get(s)
    ref = reference_price(fr, sid, s)
    location = {"ref_price": ref, "ref_date": None, "range_third": None, "line": None}
    if ref is not None:
        r = _reference_bar(fr, sid, s, ref)
        location["ref_date"] = _iso(dates[r])
        location["line"] = _line_at(ch, side, ref, dates[r], float(fr["atr"][r]))
        rh, rl = float(fr["rh"][s]), float(fr["rl"][s])
        if fr["tr"][s] and rh > rl:
            share = (ref - rl) / (rh - rl)
            location["range_third"] = ("bottom" if share <= C.PA_RANGE_THIRD else
                                       "top" if share >= 1.0 - C.PA_RANGE_THIRD else "middle")
    return {**a, "entry": entry, "signal_bar": _signal_bar(fr, dates, s, side),
            "variant": hit[1].get("variant") if hit else None, "location": location,
            "at_level": None, "level": None}


def _caveats(ticker: str) -> list[str]:
    return [
        APPROXIMATION,
        "Brooks' own probabilities (for example that most trading-range breakouts fail) are rules of thumb; "
        "only the statistics measured here are reported.",
        f"Statistics come from {ticker}'s own daily history, in-sample and with no costs. Every setup is entered "
        "on a stop beyond its signal bar on the next bar; a setup that does not trigger is no trade, and the "
        "baselines are every day entered the same way.",
        f"Swings are ATR ZigZag pivots, known only once price has reversed {C.SWING_ATR:g} ATRs from them, so "
        "wedges, double tops and bottoms and measured moves appear late by construction.",
        "The place of a setup (key zone, trendline, range third) is a label: the statistics are those of the "
        "setup wherever it formed.",
    ]


def build_price_action(ctx) -> dict:
    """Module output: the current price-action reading, the latest patterns, measured moves, trend
    channels, and statistics for the setups whose signal bar is one of the last C.SIGNAL_ACTIVE_BARS
    bars (triggered, or still pending on the last bar) plus the library."""
    bars, ind = ctx.get("bars"), ctx.get("ind")
    hz = ctx["hz"]
    horizons = [int(x) for x in hz["stat_horizons"]]
    primary = int(hz["primary"])
    if primary not in horizons:
        horizons.append(primary)
    base = {"approximation": APPROXIMATION, "ema_period": C.PA_EMA_PERIOD, "primary_horizon": primary,
            "stat_horizons": horizons, "entry_rule": ENTRY_RULE, "signal_bar_rule": SIGNAL_BAR_RULE}
    if bars is None or ind is None or len(bars) <= WARMUP:
        return {**base, "available": False, "notes": [], "active": [], "lapsed": [], "library": [],
                "reason": f"fewer than {WARMUP + 1} bars: no price-action reading"}
    fr = frame_for(ctx)
    dates, n = bars.index, fr["n"]
    notes: list[str] = []
    close, ema, atr = float(fr["close"][-1]), float(fr["ema"][-1]), float(fr["atr"][-1])
    o, hh, ll = (float(bars[k].iloc[-1]) for k in ("open", "high", "low"))
    rng = hh - ll
    ago = lambda i: n - 1 - int(i)  # noqa: E731
    pt = lambda q: {"date": _iso(dates[q[0]]), "price": float(q[1])}  # noqa: E731

    state_code, reading = fr["state"][-1], fr["state_reading"][-1]
    st_since, st_bars = _since(fr["state"], dates)
    ai_since, _ = _since(fr["always_in"], dates)
    slope = "rising" if fr["rising"][-1] else "falling" if fr["falling"][-1] else "flat"
    tr = {"active": bool(fr["tr"][-1])}
    if tr["active"]:
        tr.update(high=float(fr["rh"][-1]), low=float(fr["rl"][-1]), window_start=_iso(dates[n - N_RANGE]),
                  height_atr=(float(fr["rh"][-1]) - float(fr["rl"][-1])) / atr if atr > 0 else None,
                  close_position=(close - float(fr["rl"][-1])) / (float(fr["rh"][-1]) - float(fr["rl"][-1]))
                  if fr["rh"][-1] > fr["rl"][-1] else None)
    w = fr["wedges"][-1] if fr["wedges"] else None
    f = fr["fails"][-1] if fr["fails"] else None
    d2 = fr["doubles"][-1] if fr["doubles"] else None
    m = fr["mtrs"][-1] if fr["mtrs"] else None
    b = fr["breakouts"][-1] if fr["breakouts"] else None

    # the last ii / ioi (Brooks' breakout mode): a stop above its last bar buys, a stop below sells
    mode = None
    hits = np.flatnonzero(fr["shape"] >= 2)
    if hits.size:
        i = int(hits[-1])
        after_h, after_l = fr["high"][i + 1:], fr["low"][i + 1:]
        up = np.flatnonzero(after_h > fr["high"][i])
        dn = np.flatnonzero(after_l < fr["low"][i])
        first = min([x[0] for x in (up, dn) if x.size], default=None)
        broke = None if first is None else ("both" if up.size and dn.size and up[0] == dn[0] == first
                                            else "up" if up.size and up[0] == first else "down")
        mode = {"pattern": SHAPES[int(fr["shape"][i])], "date": _iso(dates[i]), "bars_ago": ago(i),
                "high": float(fr["high"][i]), "low": float(fr["low"][i]), "broke": broke}
    cx = fr["climax"]
    cx_hits = np.flatnonzero(cx != 0)
    last_cx = None
    if cx_hits.size:
        i = int(cx_hits[-1])
        last_cx = {"side": "buy" if cx[i] > 0 else "sell", "date": _iso(dates[i]), "bars_ago": ago(i),
                   "bars": int(abs(cx[i]))}

    ev = events(bars, ind, fr)
    stop = stop_entries(bars)
    window = C.SIGNAL_ACTIVE_BARS
    live, lapsed, triggered = {}, [], {}
    for spec in SIGNALS:
        sid, side = spec["id"], (1 if spec["bias"] > 0 else -1)
        col, trig = ev[sid].to_numpy(dtype=bool), stop[side][0]
        triggered[sid] = int((col & trig).sum())
        keep = col & trig
        keep[-1] = col[-1]                         # the last bar's setup is pending: its next bar is not in yet
        live[sid] = keep
        for s in np.flatnonzero(col[:-1] & ~trig[:-1]).tolist():
            if s >= n - window and not keep[s + 1:].any():
                lapsed.append({"id": sid, "name": spec["name"], "bias": spec["bias"], "fired_date": _iso(dates[s]),
                               "bars_ago": ago(s),
                               "trigger_price": float(fr["high"][s] if side > 0 else fr["low"][s])})
    cache = StopEntryCache(bars, ind)
    ch = trend_channels(ctx)
    lines = {**ch, "_ago": {_iso(dt): k for k, dt in enumerate(dates[::-1][:ch["window_bars"]])}}   # date -> bars ago
    active = [_setup_details(a, fr, dates, stop, lines) for a in
              SS.active_rows(bars, ind, pd.DataFrame(live, index=bars.index), _BY_ID, horizons, primary, notes, cache)]
    library = [{**r, "triggered_total": triggered[r["id"]]}
               for r in SS.library_rows(bars, ind, ev, SIGNALS, primary, cache)]
    act = {a["id"] for a in active}
    pend = None if reading == state_code or not np.isfinite(reading) else {
        "label": STATES.get(int(reading)), "bars": int(fr["state_pending"][-1])}
    return {
        **base, "available": True, "notes": notes,
        "bar": {"type": BAR_TYPES[int(fr["types"][-1])], "body_share": abs(close - o) / rng if rng > 0 else 0.0,
                "close_position": (close - ll) / rng if rng > 0 else None,
                "shape": SHAPES[int(fr["shape"][-1])],
                "signal": "bull" if fr["bull_sig"][-1] else "bear" if fr["bear_sig"][-1] else None},
        "ema": ema, "ema_slope": slope, "close_vs_ema_atr": (close - ema) / atr if atr > 0 else None,
        "state": {"label": STATES.get(int(state_code)) if np.isfinite(state_code) else None,
                  "since": st_since, "bars": st_bars,
                  "share_closes_above_ema": _f(fr["above_close"][-1]),
                  "share_bars_above_ema": _f(fr["above_bar"][-1]), "share_bars_below_ema": _f(fr["below_bar"][-1]),
                  "window_bars": N_STATE, "confirm_bars": C.PA_STATE_CONFIRM_BARS, "pending": pend,
                  "rule": STATE_RULE},
        "always_in": {"direction": ALWAYS_IN[int(fr["always_in"][-1])], "since": ai_since},
        "trading_range": tr,
        "gap_bars": {"bull": int(fr["gap_bull"][-1]), "bear": int(fr["gap_bear"][-1])},
        "climax": {"active": bool(cx[-1] != 0), "side": None if cx[-1] == 0 else "buy" if cx[-1] > 0 else "sell",
                   "bars": int(abs(cx[-1])), "last": last_cx, "rule": CLIMAX_RULE},
        "breakout_mode": mode,
        "last_high_count": _last_count(fr["h_cnt"], fr["rising"], "H", dates, n),
        "last_low_count": _last_count(fr["l_cnt"], fr["falling"], "L", dates, n),
        "last_range_high_count": _last_count(fr["th_cnt"], fr["tr"], "H", dates, n),
        "last_range_low_count": _last_count(fr["tl_cnt"], fr["tr"], "L", dates, n),
        "last_wedge": None if w is None else {
            "side": w["side"], "confirmed_date": _iso(dates[w["confirm"]]), "bars_ago": ago(w["confirm"]),
            "pushes": [{"date": _iso(dates[i]), "price": p} for i, p in w["pushes"]]},
        "last_double": None if d2 is None else {
            "side": d2["side"], "variant": d2["variant"], "confirmed_date": _iso(dates[d2["confirm"]]),
            "bars_ago": ago(d2["confirm"]), "points": [pt(q) for q in d2["points"]]},
        "last_mtr": None if m is None else {
            "side": m["side"], "variant": m["variant"], "signal_date": _iso(dates[m["signal"]]),
            "bars_ago": ago(m["signal"]), "extreme": pt(m["extreme"]), "break": pt(m["break"]), "test": pt(m["test"])},
        "last_failed_breakout": None if f is None else {
            "side": f["side"], "date": _iso(dates[f["fail"]]), "breakout_date": _iso(dates[f["breakout"]]),
            "level": f["level"], "bars_ago": ago(f["fail"])},
        "last_breakout": None if b is None else {
            "side": b["side"], "date": _iso(dates[b["bar"]]), "bars_ago": ago(b["bar"]), "level": b["level"],
            "range_atr": b["range_atr"], "close_position": b["close_position"], "gap_open": b["gap_open"],
            "trend_bars": b["trend_bars"],
            "follow_through": None if b["bar"] == n - 1 else b["follow"] is not None,
            "back_inside_date": None if b["back"] is None else _iso(dates[b["back"]]),
            "holding": b["back"] is None},
        "measured_moves": measured_moves(fr, dates),
        "trend_channels": ch,
        "active": active, "lapsed": lapsed, "inactive_ids": [s for s in SIGNAL_IDS if s not in act],
        "library": library,
        "caveats": _caveats(ctx.get("ticker", "this stock")),
    }


def locate(ctx, pa: dict, lv: dict | None) -> dict:
    """pa with the key zone each active setup formed at: at_level (True, False, or None when the key
    levels are unavailable) and level ({label S1.. / R1.. / Z, side, price, zone_low, zone_high,
    sources}; the zone's test record stays with the key levels). A bullish setup is at a level when its
    reference price (location.ref_price: the pullback low, third push, second bottom, the test's low...)
    reached a support zone or the zone the close is in, and its signal bar closed at or above that
    zone's bottom; bearish setups mirror this (the rule of candlesticks.locate). Nothing else changes."""
    if not isinstance(pa, dict) or not pa.get("available"):
        return pa
    bars, atr = ctx.get("bars"), (lv or {}).get("atr")
    if not isinstance(lv, dict) or not lv.get("available") or not atr or bars is None:
        return {**pa, "notes": list(pa.get("notes") or []) +
                ["key levels unavailable: the setups' key zone (at_level) is unknown"]}
    zones = [(f"S{i}", z) for i, z in enumerate(lv.get("supports") or [], 1)]
    zones += [(f"R{i}", z) for i, z in enumerate(lv.get("resistances") or [], 1)]
    if lv.get("current_zone"):
        zones.append(("Z", lv["current_zone"]))
    side = {"S": "support", "R": "resistance", "Z": "current"}
    pos = {_iso(d): i for i, d in enumerate(bars.index)}
    close = bars["close"].to_numpy(dtype=float)
    active = []
    for a in pa.get("active") or []:
        s, ref = pos.get(a.get("fired_date")), _f((a.get("location") or {}).get("ref_price"))
        if s is None or ref is None:
            active.append(a)
            continue
        found = CS._level_for(int(a["bias"]), ref, ref, float(close[s]), zones, C.TEST_TOL_ATR * float(atr))
        level = None
        if found:
            label, z = found
            level = {"label": label, "side": side[label[0]], "price": z.get("price"), "zone_low": z.get("zone_low"),
                     "zone_high": z.get("zone_high"), "sources": list(z.get("sources") or [])}
        active.append({**a, "at_level": level is not None, "level": level})
    return {**pa, "active": active}


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
    dates, n = bars.index, fr["n"]
    inwin = lambda i: first <= i < end  # noqa: E731
    d = lambda i: _iso(dates[i])  # noqa: E731
    ev = events(bars, ctx["ind"], fr)
    stop = stop_entries(bars)
    sig, trig = {}, {}
    for spec in SIGNALS:
        sig[spec["id"]] = ev[spec["id"]].to_numpy()
        trig[spec["id"]] = stop[1 if spec["bias"] > 0 else -1][0]
    # a setup's entry bar: the bar after a signal bar that triggered (for H2 / L2 that is the count bar)
    entered = {sid: np.r_[False, (sig[sid] & trig[sid])[:-1]] for sid in SIGNAL_IDS}
    took = lambda sid, i: None if i == n - 1 else bool(trig[sid][i])  # noqa: E731  (None: the next bar is not in yet)

    markers = []
    for i in range(first, end):
        trend_h, trend_l = fr["h_cnt"][i] and fr["rising"][i], fr["l_cnt"][i] and fr["falling"][i]
        if trend_h:
            markers.append({"date": d(i), "kind": f"H{fr['h_cnt'][i]}", "side": 1, "price": float(fr["low"][i]),
                            "setup": bool(entered["pa_h2_ema"][i] and fr["h_cnt"][i] == 2)})
        elif fr["th_cnt"][i] and fr["tr"][i]:
            markers.append({"date": d(i), "kind": f"H{fr['th_cnt'][i]}", "side": 1, "price": float(fr["low"][i]),
                            "range": True, "setup": bool(entered["pa_tr_h2"][i] and fr["th_cnt"][i] == 2)})
        if trend_l:
            markers.append({"date": d(i), "kind": f"L{fr['l_cnt'][i]}", "side": -1, "price": float(fr["high"][i]),
                            "setup": bool(entered["pa_l2_ema"][i] and fr["l_cnt"][i] == 2)})
        elif fr["tl_cnt"][i] and fr["tr"][i]:
            markers.append({"date": d(i), "kind": f"L{fr['tl_cnt'][i]}", "side": -1, "price": float(fr["high"][i]),
                            "range": True, "setup": bool(entered["pa_tr_l2"][i] and fr["tl_cnt"][i] == 2)})
        if sig["pa_gap20_bull_test"][i]:
            markers.append({"date": d(i), "kind": "G20", "side": 1, "price": float(fr["low"][i]), "setup": True,
                            "triggered": took("pa_gap20_bull_test", i)})
        if sig["pa_gap20_bear_test"][i]:
            markers.append({"date": d(i), "kind": "G20", "side": -1, "price": float(fr["high"][i]), "setup": True,
                            "triggered": took("pa_gap20_bear_test", i)})
        if fr["climax"][i] and not (i and fr["climax"][i - 1]):         # the bar a climax run first qualifies
            buy = fr["climax"][i] > 0
            markers.append({"date": d(i), "kind": "CX", "side": -1 if buy else 1, "setup": False,
                            "price": float(fr["high"][i] if buy else fr["low"][i])})
        if fr["shape"][i] >= 2:
            markers.append({"date": d(i), "kind": SHAPES[int(fr["shape"][i])], "side": 0, "setup": False,
                            "price": float(fr["high"][i]), "low": float(fr["low"][i])})
    for f in fr["fails"]:
        if inwin(f["fail"]):
            up = f["side"] == "up"
            sid = "pa_failed_breakout" if up else "pa_failed_breakdown"
            markers.append({"date": d(f["fail"]), "kind": "FB", "side": -1 if up else 1, "setup": True,
                            "price": float(fr["high"][f["fail"]] if up else fr["low"][f["fail"]]), "level": f["level"],
                            "breakout_date": d(f["breakout"]), "triggered": took(sid, f["fail"])})
    for b in fr["breakouts"]:
        if b["follow"] is not None and inwin(b["follow"]):
            up, i = b["side"] == "up", b["follow"]
            markers.append({"date": d(i), "kind": "BO", "side": 1 if up else -1, "setup": True,
                            "price": float(fr["low"][i] if up else fr["high"][i]), "level": b["level"],
                            "breakout_date": d(b["bar"]),
                            "triggered": took("pa_breakout_up" if up else "pa_breakout_down", i)})
    for m in fr["mtrs"]:
        if inwin(m["signal"]):
            up, i = m["side"] == "bottom", m["signal"]
            markers.append({"date": d(i), "kind": "MTR", "side": 1 if up else -1, "setup": True,
                            "price": float(fr["low"][i] if up else fr["high"][i]), "variant": m["variant"],
                            "triggered": took("pa_mtr_bottom" if up else "pa_mtr_top", i)})
    for x in fr["doubles"]:
        if inwin(x["confirm"]):
            up, i = x["side"] == "bottom", x["confirm"]
            markers.append({"date": d(i), "kind": "DB" if up else "DT", "side": 1 if up else -1, "setup": True,
                            "price": float(fr["low"][i] if up else fr["high"][i]), "variant": x["variant"],
                            "triggered": took("pa_double_bottom" if up else "pa_double_top", i)})
    wds = [{"side": w["side"], "confirmed_date": d(w["confirm"]),
            "triggered": took("pa_wedge_top" if w["side"] == "top" else "pa_wedge_bottom", w["confirm"]),
            "pushes": [[d(i), p] for i, p in w["pushes"]]} for w in fr["wedges"] if inwin(w["pushes"][0][0]) and inwin(w["confirm"])]
    dbl = [{"side": x["side"], "variant": x["variant"], "confirmed_date": d(x["confirm"]),
            "points": [[d(i), p] for i, p in x["points"]]} for x in fr["doubles"]
           if inwin(x["points"][0][0]) and inwin(x["confirm"])]
    mtr = [{"side": m["side"], "variant": m["variant"], "signal_date": d(m["signal"]),
            "points": [[d(m[k][0]), m[k][1]] for k in ("extreme", "break", "test")]} for m in fr["mtrs"]
           if inwin(m["extreme"][0]) and inwin(m["signal"])]

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
    ch = pa.get("trend_channels") or {}
    channels = []
    if end == n:                                   # the lines are measured at the last bar: only a window that ends there shows them
        for kind in ("up", "down"):
            line = ch.get(kind)
            if line:
                cl = line.get("channel_line")
                channels.append({"kind": kind, "anchor": line["anchor"], "slope_per_bar": line["slope_per_bar"],
                                 "price": line["price"], "state": line["state"], "touches": line["touches"],
                                 "span_bars": line["span_bars"], "offset": None if not cl else cl["offset"]})
    return {
        "available": True,
        "ema": [[d(k), float(v)] for k, v in zip(range(first, end), fr["ema"][sl]) if np.isfinite(v)],
        "bar_types": [int(v) for v in fr["types"][sl]],
        "states": state,
        "always_in": [int(v) for v in fr["always_in"][sl]],
        "markers": markers, "wedges": wds, "doubles": dbl, "mtrs": mtr, "ranges": ranges, "channels": channels,
        "measured_moves": pa.get("measured_moves") or [],
        "summary": {k: pa.get(k) for k in ("bar", "ema", "ema_slope", "close_vs_ema_atr", "state", "always_in",
                                            "trading_range", "gap_bars", "climax", "breakout_mode",
                                            "last_high_count", "last_low_count", "last_range_high_count",
                                            "last_range_low_count", "last_wedge", "last_double", "last_mtr",
                                            "last_failed_breakout", "last_breakout", "trend_channels")},
        "ema_period": C.PA_EMA_PERIOD,
        "approximation": APPROXIMATION,
    }
