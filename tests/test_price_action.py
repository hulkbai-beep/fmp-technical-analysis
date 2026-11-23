"""price_action.py: bar types and signal bars, pullback counts and their signal bars, ZigZag pivots, wedges,
double tops / bottoms, major trend reversals, breakouts and failed breakouts, the market state with
confirmation, climaxes, always-in, events, stop entries and their statistics, the module output, the
setups' place and the chart payload. No network."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config as C
import indicators as I
import price_action as PA
import signal_stats as SS
import swings as SW


def frame(rows, start="2024-01-01"):
    """Bars from [open, high, low, close] rows (volume 1M)."""
    idx = pd.bdate_range(start, periods=len(rows), name="date")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 1e6
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c]
    return df


def test_bar_types():
    bars = frame([[10, 11, 9.8, 10.9],     # body 0.9 / range 1.2 -> bull trend bar
                  [10.9, 11, 10, 10.1],    # bear trend bar
                  [10, 11, 9, 10.05],      # doji
                  [10, 11, 9, 10.6],       # body 0.3 of range -> weak bull
                  [10.6, 11, 9, 10],       # weak bear
                  [10, 10, 10, 10]])       # no range -> doji
    assert PA.bar_types(bars).tolist() == [2, -2, 0, 1, -1, 0]


def test_pullback_counts_h1_h2_and_reset_on_new_high():
    # rally, pullback with two attempts higher, then a new high
    highs = np.array([10, 11, 12, 11.5, 11.8, 11.2, 11.6, 12.5, 12.4])
    lows = highs - 1
    cnt, ext = PA.pullback_counts(highs, lows, bull=True)
    assert cnt.tolist() == [0, 0, 0, 0, 1, 0, 2, 0, 0]
    assert ext[6] == pytest.approx(10.2)          # the pullback low so far
    # after the new high (bar 7) a fresh pullback starts at bar 8 with no count yet
    cnt, _ = PA.pullback_counts(highs, lows, bull=True, context=np.array([True] * 6 + [False] * 3))
    assert cnt[6] == 0                            # context lost: no count


def test_pullback_counts_mirror_for_lows():
    lows = np.array([20, 19, 18, 18.5, 18.2, 18.8, 18.4, 17.5])
    highs = lows + 1
    cnt, ext = PA.pullback_counts(highs, lows, bull=False)
    assert cnt.tolist() == [0, 0, 0, 0, 1, 0, 2, 0]
    assert ext[6] == pytest.approx(19.8)          # the rally high so far


def test_pullback_counts_stop_after_max():
    highs = np.array([10, 11] + [10.5, 10.8] * (C.PA_COUNT_MAX + 1), dtype=float)
    cnt, _ = PA.pullback_counts(highs, highs - 1, bull=True)
    assert cnt.max() == C.PA_COUNT_MAX


def test_signal_bars():
    atr = np.full(6, 1.0)
    rows = np.array([[10.0, 11.0, 9.8, 10.9],      # bull bar closing near its high: strong bull signal bar
                     [10.9, 11.0, 9.9, 10.0],      # the mirror image: strong bear signal bar
                     [10.0, 11.0, 9.0, 10.5],      # bull bar closing mid-range: neither
                     [10.0, 11.0, 9.0, 10.05],     # doji closing mid-range
                     [10.0, 13.0, 9.9, 12.9],      # closes on its high but is 3.1 ATRs tall: too big
                     [10.0, 10.0, 10.0, 10.0]])    # no range
    bull, bear = PA.signal_bars(rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3], atr)
    assert bull.tolist() == [True, False, False, False, False, False]
    assert bear.tolist() == [False, True, False, False, False, False]
    # the close must be in the outer third, and the body must not be a doji's
    tail = np.array([[10.5, 11.0, 9.5, 10.6], [9.6, 11.0, 9.5, 10.9]])      # tiny body on the high; long body
    b2, _ = PA.signal_bars(tail[:, 0], tail[:, 1], tail[:, 2], tail[:, 3], np.ones(2))
    assert b2.tolist() == [False, True]


def test_bar_shapes():
    h = np.array([10, 9.8, 9.7, 12, 11, 11.5, 11.4, 11.3], dtype=float)
    l = np.array([8, 8.2, 8.3, 7, 8, 8.5, 8.6, 8.7], dtype=float)
    # inside, ii, outside, inside (after an outside bar and an inside bar before it: ioi), then a higher low
    assert PA.bar_shapes(h, l).tolist() == [0, 1, 2, -1, 3, 0, 1, 2]


def test_next_count_names_the_signal_bar_of_each_count():
    rng = np.random.default_rng(3)
    high = 100 + np.cumsum(rng.normal(0, 1, 400))
    low = high - 1 - rng.random(400)
    for bull in (True, False):
        count, extreme, nxt, sofar = PA._pullback(high, low, bull)
        assert (count > 0).sum() > 30
        broke = high[1:] > high[:-1] if bull else low[1:] < low[:-1]
        # the bar before a count announced it, and an announced count happens whenever the next bar breaks out
        assert (nxt[:-1][count[1:] > 0] == count[1:][count[1:] > 0]).all()
        assert (count[1:][(nxt[:-1] > 0) & broke] == nxt[:-1][(nxt[:-1] > 0) & broke]).all()
        # the pullback extreme known on the signal bar is the count bar's extreme unless that bar extends it
        on = np.flatnonzero(count[1:] > 0)
        worse = np.minimum(sofar[on], low[on + 1]) if bull else np.maximum(sofar[on], high[on + 1])
        assert np.allclose(extreme[on + 1], worse)
    cnt, ext = PA.pullback_counts(high, low, True)
    assert (cnt == PA._pullback(high, low, True)[0]).all()


def test_zigzag_pivots_are_known_only_from_their_confirming_bar():
    bars = zigzag([100, 110, 105, 116, 112, 119, 112])
    atr = I.compute_all(bars)["atr14"].to_numpy()
    h, l = bars["high"].to_numpy(), bars["low"].to_numpy()
    piv = PA.zigzag_pivots(h, l, atr)
    assert [(p["kind"], round(p["price"])) for p in piv] == [("H", 110), ("L", 105), ("H", 116), ("L", 112), ("H", 119)]
    for p in piv:
        assert p["confirm"] > p["i"]
        # the bar that confirms it is at least SWING_ATR ATRs away from the pivot
        gap = p["price"] - l[p["confirm"]] if p["kind"] == "H" else h[p["confirm"]] - p["price"]
        assert gap >= C.SWING_ATR * atr[p["confirm"]]
        cut = p["confirm"]
        assert p in PA.zigzag_pivots(h[:cut + 1], l[:cut + 1], atr[:cut + 1])
        assert p not in PA.zigzag_pivots(h[:cut], l[:cut], atr[:cut])
    # the same swings as the key levels use
    sw = SW.swing_pivots(bars, I.compute_all(bars)["atr14"])
    assert [p["price"] for p in piv if p["kind"] == "H"] == [q["price"] for q in sw["highs"]]


def zigzag(points, step=1.0):
    """Bars that walk linearly between the given closes (one bar per unit of `step`)."""
    closes = []
    for a, b in zip(points[:-1], points[1:]):
        k = max(1, int(round(abs(b - a) / step)))
        closes += list(np.linspace(a, b, k + 1)[:-1])
    closes.append(points[-1])
    c = np.array(closes)
    return frame([[x, x + 0.2, x - 0.2, x] for x in c])


def test_wedge_top_with_fading_pushes_fires_on_confirmation():
    bars = zigzag([100, 110, 105, 116, 112, 119, 112])    # pushes 110, 116, 119: 6 then 3 -> fading
    fr = PA.analyze_frame(bars, I.compute_all(bars))
    tops = [w for w in fr["wedges"] if w["side"] == "top"]
    assert len(tops) == 1
    assert [round(p) for _, p in tops[0]["pushes"]] == [110, 116, 119]
    third = next(p for p in fr["pivots"] if p["i"] == tops[0]["pushes"][2][0])
    assert tops[0]["confirm"] == third["confirm"] > third["i"]      # known once the third push is a confirmed swing
    ev = PA.events(bars, I.compute_all(bars), fr)
    assert np.flatnonzero(ev["pa_wedge_top"].to_numpy()).tolist() == [tops[0]["confirm"]]


def test_accelerating_pushes_are_not_a_wedge():
    bars = zigzag([100, 104, 101, 108, 105, 116, 110])    # 4 then 8: accelerating
    fr = PA.analyze_frame(bars, I.compute_all(bars))
    assert not [w for w in fr["wedges"] if w["side"] == "top"]


def test_failed_breakouts():
    n = 8
    close = np.full(n, 10.0)
    high, low = close + 0.5, close - 0.5
    rh, rl = np.full(n, 10.5), np.full(n, 9.5)
    is_tr = np.ones(n, dtype=bool)
    high[3], close[3] = 10.9, 10.7             # breaks out, closes outside
    close[4] = 10.3                            # back inside next bar: failed
    low[6], close[6] = 9.2, 9.6                # wick below, closes back inside the same bar
    out = PA.failed_breakouts(close, high, low, rh, rl, is_tr)
    assert [(f["side"], f["breakout"], f["fail"]) for f in out] == [("up", 3, 4), ("down", 6, 6)]


def test_breakout_that_holds_is_not_failed():
    n = 8
    close = np.full(n, 10.0)
    high, low = close + 0.5, close - 0.5
    high[3:], close[3:] = 11.5, 11.2           # stays above the range
    out = PA.failed_breakouts(close, high, low, np.full(n, 10.5), np.full(n, 9.5), np.ones(n, dtype=bool))
    assert out == []


def test_always_in_flips_on_consecutive_trend_bars():
    close = np.array([10, 10.2, 11, 12, 12.1, 11, 10, 10.2])
    high = close + 0.1
    low = close - 0.1
    ema = np.full(8, 10.5)
    types = np.array([0, 0, 2, 2, 0, -2, -2, 0])
    state = np.zeros(8)
    ai = PA.always_in(close, high, low, ema, types, state)
    # long after bars 2-3; bar 5 closes above the EMA, so bars 5-6 are not two bear trend bars below it
    assert ai.tolist() == [0, 0, 0, 1, 1, 1, 1, 1]
    close2 = close.copy()
    close2[5], close2[6] = 10.4, 10.0
    ai2 = PA.always_in(close2, close2 + 0.1, close2 - 0.1, ema, types, state)
    assert ai2[6] == -1
    # a tight channel sets the direction by itself
    assert PA.always_in(close, high, low, ema, np.zeros(8, dtype=int), np.full(8, -2.0)).tolist() == [-1] * 8


# ---------------------------------------------------------------------------- market state
def states(above_close, rising=True, falling=False, is_tr=False, below_close=0.0, above_bar=0.0, below_bar=0.0,
           bo_live=0, fast=False, warm=True, side=1):
    n = len(above_close)
    arr = lambda v, dtype=float: np.asarray(v, dtype=dtype) if np.ndim(v) else np.full(n, v, dtype=dtype)  # noqa: E731
    return PA.market_states(arr(warm, bool), arr(rising, bool), arr(falling, bool), arr(is_tr, bool),
                            arr(above_close), arr(below_close), arr(above_bar), arr(below_bar),
                            arr(bo_live, int), arr(fast, bool), arr(side, int))


def test_a_new_state_needs_three_bars_in_a_row():
    share = [0.7] * 5 + [0.4] * 2 + [0.7] * 3 + [0.4] * 4       # two bars of a range reading, then four
    state, reading, pending = states(share)
    assert reading.tolist() == [1] * 5 + [0] * 2 + [1] * 3 + [0] * 4
    assert state.tolist() == [1] * 12 + [0] * 2                 # the first reading is taken as it comes
    assert pending.tolist() == [0] * 5 + [1, 2] + [0] * 3 + [1, 2, 0, 0]
    assert C.PA_STATE_CONFIRM_BARS == 3


def test_three_bars_of_any_other_reading_end_a_state():
    # a tight channel read as a broad one for two bars, then as a range: three bars against it, so it ends
    tight = [0.9] * 4 + [0.6] * 3
    state, reading, pending = states([0.9] * 6 + [0.3], above_bar=tight)
    assert reading.tolist() == [2] * 4 + [1, 1, 0]
    assert state.tolist() == [2] * 6 + [0] and pending.tolist() == [0] * 4 + [1, 2, 0]


def test_the_benefit_of_the_doubt_needs_the_close_on_the_channels_side():
    low, enter = C.PA_CHANNEL_SHARE - C.PA_STATE_EXIT_MARGIN / 2, C.PA_CHANNEL_SHARE + 0.1
    shares = [enter] * 3 + [low] * 6
    assert states(shares)[0].tolist() == [1] * 9                             # closes above the EMA: 55% keeps the channel
    below = states(shares, side=[1] * 3 + [-1] * 6)
    assert below[1].tolist() == [1] * 3 + [0] * 6 and below[0].tolist() == [1] * 5 + [0] * 4
    # the range test ends a channel once the closes are on the other side of the EMA
    boxed = states([0.7] * 7, is_tr=[False] * 3 + [True] * 4, side=[1] * 4 + [-1] * 3)
    assert boxed[0].tolist() == [1] * 6 + [0]
    # a tight channel is not kept on 70% by bars that close below the EMA
    tight = [C.PA_TIGHT_SHARE + 0.05] * 3 + [C.PA_TIGHT_SHARE - 0.05] * 4
    assert states([0.9] * 7, above_bar=tight)[0].tolist() == [2] * 7
    assert states([0.9] * 7, above_bar=tight, side=[1] * 3 + [-1] * 4)[0].tolist() == [2] * 5 + [1] * 2


def test_the_current_state_is_read_with_lower_thresholds():
    low, enter = C.PA_CHANNEL_SHARE - C.PA_STATE_EXIT_MARGIN / 2, C.PA_CHANNEL_SHARE + 0.1
    state, _, _ = states([low] * 4 + [enter] * 3 + [low] * 5)
    assert state.tolist() == [0] * 6 + [1] * 6                  # 55% does not start a channel but keeps one
    # the tight channel likewise: 80% of the bars beyond the EMA to enter, 70% to stay
    tight = [C.PA_TIGHT_SHARE + 0.05] * 3 + [C.PA_TIGHT_SHARE - 0.05] * 4 + [C.PA_TIGHT_SHARE - 0.15] * 4
    state, _, _ = states([0.9] * 11, above_bar=tight)
    assert state.tolist() == [2] * 9 + [1] * 2
    # the range test keeps a channel from starting, and does not end one that is on
    assert states([0.7] * 6, is_tr=True)[0].tolist() == [0] * 6
    assert states([0.7] * 6, is_tr=[False] * 3 + [True] * 3)[0].tolist() == [1] * 6
    # a channel ends when the EMA stops moving its way, or the closes are lost
    assert states([0.7] * 8, rising=[True] * 4 + [False] * 4)[0].tolist() == [1] * 6 + [0] * 2
    # bear side, and no state before the warm-up
    state, _, _ = states([0.0] * 6, rising=False, falling=True, below_close=0.7, warm=[False] * 2 + [True] * 4)
    assert np.isnan(state[:2]).all() and state[2:].tolist() == [-1] * 4


def test_a_breakout_with_follow_through_switches_the_state_at_once():
    n = 14
    live = np.zeros(n, dtype=int)
    live[5:9] = 1                                               # follow-through bar 5, the breakout lasts to bar 8
    fast = np.arange(n) == 5
    state, reading, _ = states([0.4] * n, bo_live=live, fast=fast)
    assert reading.tolist() == [0] * 5 + [1] * 4 + [0] * 5
    assert state.tolist() == [0] * 5 + [1] * 6 + [0] * 3        # on at once, off after three range readings
    down, _, _ = states([0.4] * n, bo_live=-live, fast=fast)
    assert down.tolist() == [0] * 5 + [-1] * 6 + [0] * 3


def test_the_state_changes_less_often_than_the_raw_reading(make_bars):
    bars = make_bars(n=1500, seed=5, vol=0.018)
    fr = PA.analyze_frame(bars, I.compute_all(bars))
    flips = lambda a: int((np.diff(a[np.isfinite(a)]) != 0).sum())  # noqa: E731
    assert 0 < flips(fr["state"]) < flips(fr["state_reading"])
    # every change of state takes the latest reading, after three bars in a row of readings against the old
    # state or on a breakout's follow-through bar
    follow = {b["follow"] for b in fr["breakouts"]}
    changes = np.flatnonzero(np.diff(fr["state"]) != 0) + 1
    for t in changes:
        if np.isfinite(fr["state"][t - 1]):
            assert fr["state"][t] == fr["state_reading"][t]
            assert (fr["state_reading"][t - 2:t + 1] != fr["state"][t - 1]).all() or t in follow
    assert any(t not in follow for t in changes)


# ---------------------------------------------------------------------------- breakouts, doubles, reversals
def test_breakouts_follow_through_and_how_long_they_last():
    n = 14
    close = np.full(n, 10.0)
    types = np.zeros(n, dtype=int)
    close[3], types[3] = 11.0, 2                    # a bull trend bar closes above the range high (10.5)
    close[4], types[4] = 11.2, 1                    # a bull bar closing above that close: follow-through
    close[5], types[5] = 11.6, 2                    # still the same breakout, not a new one
    close[6] = 11.0
    close[7] = 10.4                                 # the first close back inside ends it
    close[9], types[9] = 11.0, 2                    # a second breakout ...
    close[10] = 11.3                                # ... followed by a doji: no follow-through
    close[11:] = 11.1                               # but it holds above the range to the end
    open_ = close - 0.6
    open_[3] = 10.9                                 # opened above the prior bar's high: a gap
    open_[9] = 10.1                                 # no gap
    high, low = close + 0.2, close - 0.8
    out = PA.breakouts(open_, high, low, close, types, np.ones(n), np.full(n, 10.5), np.full(n, 9.5), np.ones(n, dtype=bool))
    first, second = out
    assert (first["side"], first["bar"], first["level"], first["follow"], first["end"], first["back"]) == ("up", 3, 10.5, 4, 6, 7)
    assert first["range_atr"] == pytest.approx(1.0) and first["close_position"] == pytest.approx(0.8)
    assert first["gap_open"] is True and first["trend_bars"] == 1
    assert (second["bar"], second["follow"], second["end"], second["back"], second["gap_open"]) == (9, None, 13, None, False)
    # the mirror image below the range, and nothing outside a trading range
    down = PA.breakouts(20 - open_, 20 - low, 20 - high, 20 - close, -types, np.ones(n), np.full(n, 10.5),
                        np.full(n, 9.5), np.ones(n, dtype=bool))
    assert [(b["side"], b["bar"], b["follow"], b["back"]) for b in down] == [("down", 3, 4, 7), ("down", 9, None, None)]
    assert PA.breakouts(open_, high, low, close, types, np.ones(n), np.full(n, 10.5), np.full(n, 9.5),
                        np.zeros(n, dtype=bool)) == []


def double_frame(points):
    bars = zigzag(points)
    ind = I.compute_all(bars)
    return bars, ind, PA.analyze_frame(bars, ind)


def test_double_bottom_fires_when_the_second_low_is_confirmed():
    bars, ind, fr = double_frame([105, 110, 100, 108, 100.1, 109])     # lows 99.8 and 99.9: 0.1 apart
    (d,) = [x for x in fr["doubles"] if x["side"] == "bottom"]
    assert [round(p, 1) for _, p in d["points"]] == [99.8, 99.9]
    second = next(p for p in fr["pivots"] if p["i"] == d["points"][1][0])
    assert d["confirm"] == second["confirm"] and d["variant"] == "reversal"     # the lowest low of the window
    ev = PA.events(bars, ind, fr)
    assert np.flatnonzero(ev["pa_double_bottom"].to_numpy()).tolist() == [d["confirm"]]
    assert PA.reference_price(fr, "pa_double_bottom", d["confirm"]) == pytest.approx(99.9)
    # a double bottom above an earlier, lower low is the flag variant (Brooks' double-bottom bull flag)
    _, _, lower = double_frame([95, 110, 100, 108, 100.1, 109])
    assert [x["variant"] for x in lower["doubles"] if x["side"] == "bottom"] == ["flag"]
    # two lows further apart than the tolerance are not a double bottom
    _, _, apart = double_frame([105, 110, 100, 108, 101.5, 109])
    assert not [x for x in apart["doubles"] if x["side"] == "bottom"]
    assert C.PA_DOUBLE_TOL_ATR * apart["atr"][-1] < 1.5


def test_double_top_mirrors_it():
    bars, ind, fr = double_frame([105, 100, 110, 102, 109.9, 101])
    (d,) = [x for x in fr["doubles"] if x["side"] == "top"]
    assert [round(p, 1) for _, p in d["points"]] == [110.2, 110.1] and d["variant"] == "reversal"
    assert PA.events(bars, ind, fr)["pa_double_top"].to_numpy()[d["confirm"]]


def mtr_case(test_low=93.0, signal_at=22, early_signal=False, close_above_ema=True, falling=True):
    """Arrays for one bottom: the trend's low at bar 10 (90), a rally to bar 18 (100, confirmed at 21), then a test."""
    n = 30
    low = np.full(n, 95.0)
    low[:10] = 96.0
    low[10] = 90.0
    low[19:] = [96.0, 94.0, test_low, test_low + 0.5] + [test_low + 1.0] * 7
    high = low + 2.0
    high[18] = 100.0
    close = low + 1.0
    ema = np.full(n, 97.0)
    if close_above_ema:
        close[15] = 98.0
    bull = np.zeros(n, dtype=bool)
    bull[signal_at] = True
    bull[20] = early_signal                         # before the rally's high is a confirmed swing: not yet a test
    piv = [{"kind": "L", "i": 10, "confirm": 13, "price": 90.0}, {"kind": "H", "i": 18, "confirm": 21, "price": 100.0}]
    return piv, high, low, close, ema, np.zeros(n, dtype=bool), np.full(n, falling), np.ones(n), bull, np.zeros(n, dtype=bool)


def test_major_trend_reversal_bottom():
    (m,) = PA.major_reversals(*mtr_case(early_signal=True))
    assert (m["side"], m["extreme"], m["break"], m["test"], m["signal"], m["variant"]) == \
        ("bottom", (10, 90.0), (18, 100.0), (21, 93.0), 22, "higher_low")
    # a test within the tolerance of the low is the double-bottom kind; one that undercuts it further is no test
    assert PA.major_reversals(*mtr_case(test_low=90.2))[0]["variant"] == "double"
    assert PA.major_reversals(*mtr_case(test_low=90.0 - C.PA_DOUBLE_TOL_ATR - 0.1)) == []
    # the signal bar must come within PA_MTR_SIGNAL_BARS bars of the test's low
    assert PA.major_reversals(*mtr_case(signal_at=21 + C.PA_MTR_SIGNAL_BARS - 1))[0]["signal"] == 23
    assert PA.major_reversals(*mtr_case(signal_at=21 + C.PA_MTR_SIGNAL_BARS)) == []
    # no trend before the low, or a rally that never closed above the EMA: no major reversal
    assert PA.major_reversals(*mtr_case(falling=False)) == []
    assert PA.major_reversals(*mtr_case(close_above_ema=False)) == []
    piv, high, low, *rest = mtr_case()
    low[5] = 89.0                                   # the swing low is not the lowest low of the window
    assert PA.major_reversals(piv, high, low, *rest) == []


def test_major_trend_reversal_top_mirrors_it():
    piv, high, low, close, ema, rising, falling, atr, bull, bear = mtr_case()
    flip = lambda a: 200.0 - a  # noqa: E731
    piv = [{**p, "kind": "H" if p["kind"] == "L" else "L", "price": 200.0 - p["price"]} for p in piv]
    (m,) = PA.major_reversals(piv, flip(low), flip(high), flip(close), flip(ema), falling, rising, atr, bear, bull)
    assert (m["side"], m["extreme"], m["test"], m["signal"], m["variant"]) == ("top", (10, 110.0), (21, 107.0), 22, "lower_high")


def test_climaxes():
    types = np.array([2, 2, 2, 2, 0, -2, -2, -2, 2, 2])
    close = np.array([10, 11, 12.5, 13, 13, 12, 10, 7.5, 8, 9], dtype=float)
    out = PA.climaxes(types, close, np.full(10, 10.0), np.ones(10))
    # three bull trend bars closing more than 2 ATRs above the EMA, then four; three bear trend bars the other way
    assert out.tolist() == [0, 0, 3, 4, 0, 0, 0, -3, 0, 0]
    assert PA.climaxes(types, close, np.full(10, 10.0), np.full(10, 2.0)).tolist() == [0] * 10    # not far enough


# ---------------------------------------------------------------------------- stop entries
STOP_ROWS = [[10.0, 11.0, 9.0, 10.5],
             [10.6, 11.5, 10.4, 11.2],      # trades above 11.0: bar 0 is triggered long at 11.0
             [11.8, 12.0, 11.6, 11.9],      # opens above 11.5: bar 1 is triggered long at the open
             [11.9, 11.95, 11.0, 11.1],     # no new high; trades below 11.6: bar 2 is triggered short at 11.6
             [10.9, 11.2, 10.5, 10.6],      # opens below 11.0: bar 3 is triggered short at the open
             [10.6, 10.9, 10.55, 10.8]]


def test_stop_entries():
    stop = PA.stop_entries(frame(STOP_ROWS))
    up, up_px = stop[1]
    dn, dn_px = stop[-1]
    assert up.tolist() == [True, True, False, False, False, False]         # the last bar has no next bar
    assert up_px[:2].tolist() == [11.0, 11.8] and np.isnan(up_px[2:]).all()
    assert dn.tolist() == [False, False, True, True, False, False]
    assert dn_px[2:4].tolist() == [11.6, 10.9] and np.isnan(dn_px[[0, 1, 4, 5]]).all()


def test_stop_outcomes_run_from_the_entry_price():
    bars = frame(STOP_ROWS)
    fwd, mae = PA.stop_outcomes(bars, 1, 1)
    assert fwd[0] == pytest.approx(11.9 / 11.0 - 1) and mae[0] == pytest.approx(10.4 / 11.0 - 1)   # bars 1-2
    assert fwd[1] == pytest.approx(11.1 / 11.8 - 1) and mae[1] == pytest.approx(11.0 / 11.8 - 1)   # bars 2-3
    assert np.isnan(fwd[2:]).all() and np.isnan(mae[2:]).all()             # not triggered
    fwd, mae = PA.stop_outcomes(bars, 1, -1)
    assert fwd[2] == pytest.approx(10.6 / 11.6 - 1) and mae[2] == pytest.approx(11.95 / 11.6 - 1)  # the worst high
    assert fwd[3] == pytest.approx(10.8 / 10.9 - 1) and np.isnan(fwd[[0, 1, 4, 5]]).all()
    # an exit beyond the data is no outcome
    assert np.isnan(PA.stop_outcomes(bars, 5, 1)[0]).all()


def test_stop_outcomes_use_adjusted_prices_on_the_entry_bar_s_basis():
    bars = frame(STOP_ROWS)
    factor = np.array([0.9, 0.9, 1.0, 1.0, 1.0, 1.0])          # a dividend goes ex on bar 2
    for c in ("open", "high", "low", "close"):
        bars["adj_" + c] = bars[c] * factor
    fwd, mae = PA.stop_outcomes(bars, 1, 1)
    assert fwd[0] == pytest.approx(11.9 / (11.0 * 0.9) - 1)    # entered on bar 1 (factor 0.9), out on bar 2
    assert mae[0] == pytest.approx(10.4 * 0.9 / (11.0 * 0.9) - 1)
    assert fwd[1] == pytest.approx(11.1 / 11.8 - 1)            # entered on bar 2: no adjustment
    plain = bars.drop(columns=[c for c in bars.columns if c.startswith("adj_")])
    assert PA.stop_outcomes(plain, 1, 1)[0][0] == pytest.approx(11.9 / 11.0 - 1)


def test_stop_entry_cache_measures_setups_and_baselines_the_same_way(make_bars):
    bars = make_bars(n=600, seed=2)
    ind = I.compute_all(bars)
    cache = PA.StopEntryCache(bars, ind)
    fwd, _ = cache.outcomes(10, 1)
    np.testing.assert_array_equal(fwd, PA.stop_outcomes(bars, 10, 1)[0])
    base = cache.baseline(10, None, 1)
    assert base["n"] == int(np.isfinite(fwd).sum()) < len(bars) - 11       # only the days whose next bar triggers
    plain = SS.OutcomeCache(bars, ind).baseline(10, None, 1)
    assert base["median"] != plain["median"]                               # not the close-to-close baseline


def test_h2_setup_is_entered_on_the_h2_bar(make_bars):
    bars = make_bars(n=1500, seed=11, vol=0.02)
    ind = I.compute_all(bars)
    fr = PA.analyze_frame(bars, ind)
    ev = PA.events(bars, ind, fr)
    up = PA.stop_entries(bars)[1][0]
    sig = np.flatnonzero(ev["pa_h2_ema"].to_numpy() & up)
    assert sig.size > 5
    # the bar that triggers an H2 signal bar is a second high count, unless the EMA stopped rising on it
    assert ((fr["h_cnt"][sig + 1] == 2) | ~fr["rising"][sig + 1]).all()
    sig = np.flatnonzero(ev["pa_tr_h2"].to_numpy() & up)
    assert sig.size > 2 and ((fr["th_cnt"][sig + 1] == 2) | ~fr["tr"][sig + 1]).all()
    # a range H2 hangs on a pullback low in the bottom third of the range
    third = fr["rl"][sig] + C.PA_RANGE_THIRD * (fr["rh"][sig] - fr["rl"][sig])
    assert (fr["th_pb"][sig] <= third).all()


def test_events_shape_and_warmup(make_bars):
    bars = make_bars(n=900, seed=11, vol=0.02)
    ind = I.compute_all(bars)
    ev = PA.events(bars, ind)
    assert list(ev.columns) == PA.SIGNAL_IDS and ev.index.equals(bars.index)
    assert all(ev[c].dtype == bool for c in ev.columns)
    assert not ev.iloc[:PA.WARMUP].to_numpy().any()
    assert ev.to_numpy().any()


def test_patterns_md_lists_every_rule_as_coded():
    text = (Path(__file__).resolve().parents[1] / "references" / "patterns.md").read_text(encoding="utf-8")
    for s in PA.SIGNALS:
        assert f"`{s['id']}`" in text and f"`{s['rule']}`" in text, s["id"]


def ctx_for(bars, horizon="swing"):
    return {"ticker": "TEST", "bars": bars, "ind": I.compute_all(bars), "hz": C.HORIZONS[horizon]}


def test_module_output(make_bars):
    out = PA.build_price_action(ctx_for(make_bars(n=1200, seed=4, vol=0.018)))
    json.dumps(out, allow_nan=False, default=str)
    assert out["available"] and out["state"]["label"] in PA.STATES.values()
    assert out["state"]["confirm_bars"] == C.PA_STATE_CONFIRM_BARS and "3 bars in a row" in out["state"]["rule"]
    assert out["state"]["pending"] is None or out["state"]["pending"]["label"] != out["state"]["label"]
    assert out["always_in"]["direction"] in ("long", "short", "none")
    assert out["bar"]["type"] in PA.BAR_TYPES.values() and out["bar"]["shape"] in PA.SHAPES.values()
    assert out["bar"]["signal"] in ("bull", "bear", None)
    assert out["entry_rule"] == PA.ENTRY_RULE and out["signal_bar_rule"] == PA.SIGNAL_BAR_RULE
    assert set(out["climax"]) == {"active", "side", "bars", "last", "rule"}
    assert out["breakout_mode"] is None or out["breakout_mode"]["pattern"] in ("ii", "ioi")
    for k in ("last_double", "last_mtr", "last_breakout", "last_range_high_count", "last_range_low_count",
              "trend_channels", "lapsed"):
        assert k in out, k
    assert [r["id"] for r in out["library"]] == PA.SIGNAL_IDS
    for r in out["library"]:                       # signal bars, the ones that triggered, the de-clustered sample
        assert r["fires_total"] >= r["triggered_total"] >= r["n"]
    assert sum(r["triggered_total"] for r in out["library"]) > 100
    assert set(out["inactive_ids"]) | {a["id"] for a in out["active"]} == set(PA.SIGNAL_IDS)
    for a in out["active"]:
        assert a["bars_ago"] < C.SIGNAL_ACTIVE_BARS and str(C.HORIZONS["swing"]["primary"]) in json.dumps(a["horizons"])
    for m in out["measured_moves"]:
        assert m["kind"] in ("leg", "range") and m["side"] in ("up", "down") and np.isfinite(m["target"])


def active_cases(make_bars, want):
    """(bars, module output, active row) for synthetic histories whose last bars hold a setup `want` accepts."""
    for seed in range(60):
        bars = make_bars(n=700, seed=seed, vol=0.02)
        out = PA.build_price_action(ctx_for(bars))
        for a in out["active"]:
            if want(a):
                yield bars, out, a


def test_active_setups_are_triggered_or_pending(make_bars):
    seen = set()
    for bars, out, a in active_cases(make_bars, lambda a: True):
        s = len(bars) - 1 - a["bars_ago"]
        long = a["bias"] > 0
        e, sb = a["entry"], a["signal_bar"]
        assert a["fired_date"] == sb["date"] == str(bars.index[s].date())
        assert e["order"] == ("buy_stop" if long else "sell_stop")
        assert e["trigger_price"] == (bars["high"].iloc[s] if long else bars["low"].iloc[s])
        if a["bars_ago"] == 0:                      # the last bar: its next bar is not in yet
            assert (e["status"], e["entry_date"], e["entry_price"]) == ("pending", None, None)
        else:
            nxt = bars.iloc[s + 1]
            assert e["status"] == "triggered" and e["entry_date"] == str(bars.index[s + 1].date())
            if long:
                assert nxt["high"] > e["trigger_price"] and e["entry_price"] == max(e["trigger_price"], nxt["open"])
            else:
                assert nxt["low"] < e["trigger_price"] and e["entry_price"] == min(e["trigger_price"], nxt["open"])
        assert sb["strong"] is (not sb["weak_because"]) and 0 <= sb["close_position"] <= 1
        assert set(a["location"]) == {"ref_price", "ref_date", "range_third", "line"}
        assert a["location"]["range_third"] in ("bottom", "middle", "top", None)
        assert (a["at_level"], a["level"]) == (None, None)      # the key zone comes from locate()
        seen.add(e["status"])
    assert seen == {"pending", "triggered"}


def test_setups_that_did_not_trigger_are_listed_as_lapsed(make_bars):
    found = 0
    for seed in range(40):
        bars = make_bars(n=700, seed=seed, vol=0.02)
        out = PA.build_price_action(ctx_for(bars))
        for x in out["lapsed"]:
            s = len(bars) - 1 - x["bars_ago"]
            long = x["bias"] > 0
            assert 0 < x["bars_ago"] < C.SIGNAL_ACTIVE_BARS and x["id"] not in {a["id"] for a in out["active"]}
            nxt = bars.iloc[s + 1]
            assert (nxt["high"] <= x["trigger_price"]) if long else (nxt["low"] >= x["trigger_price"])
            found += 1
    assert found > 3


def test_setup_statistics_count_triggered_setups_only(make_bars):
    bars = make_bars(n=1500, seed=11, vol=0.02)
    ind = I.compute_all(bars)
    ev = PA.events(bars, ind)
    h = C.HORIZONS["swing"]["primary"]
    lib = {r["id"]: r for r in PA.build_price_action(ctx_for(bars))["library"]}
    for sid in ("pa_h2_ema", "pa_double_bottom", "pa_failed_breakout"):
        side = PA._BY_ID[sid]["bias"]
        fwd, mae = PA.stop_outcomes(bars, h, side)
        kept = SS.decluster(np.flatnonzero(ev[sid].to_numpy() & np.isfinite(fwd)), h)
        assert lib[sid]["n"] == len(kept) > 0
        assert lib[sid]["median"] == pytest.approx(np.median(fwd[kept]))
        assert lib[sid]["triggered_total"] == int((ev[sid].to_numpy() & PA.stop_entries(bars)[side][0]).sum())


def test_locate_names_the_key_zone_of_each_active_setup(make_bars):
    bars = make_bars(n=300, seed=1)
    ctx = ctx_for(bars)
    day, close = str(bars.index[-2].date()), float(bars["close"].iloc[-2])
    row = lambda sid, bias, ref: {"id": sid, "bias": bias, "fired_date": day, "at_level": None, "level": None,  # noqa: E731
                                  "location": {"ref_price": ref, "ref_date": day, "range_third": None, "line": None}}
    pa = {"available": True, "notes": [], "active": [row("pa_h2_ema", 1, close - 1.0), row("pa_l2_ema", -1, close + 1.0),
                                                     row("pa_wedge_bottom", 1, close - 0.5)]}
    lv = {"available": True, "atr": 2.0, "current_zone": None,
          "supports": [{"price": close - 1.2, "zone_low": close - 1.5, "zone_high": close - 0.9, "sources": ["pivot_low"]}],
          "resistances": [{"price": close + 1.1, "zone_low": close + 0.8, "zone_high": close + 1.4, "sources": ["pivot_high"]}]}
    out = PA.locate(ctx, pa, lv)
    a, b, c = out["active"]
    assert a["at_level"] is True and a["level"]["label"] == "S1" and a["level"]["side"] == "support"
    assert a["level"]["zone_low"] == close - 1.5 and a["level"]["sources"] == ["pivot_low"]
    assert b["at_level"] is True and b["level"]["label"] == "R1"
    assert (c["at_level"], c["level"]) == (False, None)         # its low stopped 0.4 above the zone (tolerance 0.2)
    assert "score" not in json.dumps(out)
    # without key levels the place stays unknown, and an unavailable module is left alone
    none = PA.locate(ctx, pa, {"available": False})
    assert [x["at_level"] for x in none["active"]] == [None] * 3 and "key levels unavailable" in none["notes"][-1]
    assert PA.locate(ctx, {"available": False}, lv) == {"available": False}


def test_trend_channels_are_the_levels_trendlines_with_channel_lines(make_bars):
    lines = 0
    for seed in range(12):
        bars = make_bars(n=700, seed=seed, vol=0.02)
        for horizon in ("swing", "position"):
            ctx = ctx_for(bars, horizon)
            ch = PA.trend_channels(ctx)
            lookback = C.HORIZONS[horizon]["levels_lookback"]
            atr = ctx["ind"]["atr14"]
            piv = SW.daily_pivots(bars, atr, lookback)
            ref = SW.trendlines(bars.iloc[-lookback:], atr, piv)
            assert ch["window_bars"] == lookback and ch["rule"] == ref["rule"]
            for kind in ("up", "down"):
                if ref[kind] is None:
                    assert ch[kind] is None
                    continue
                lines += 1
                assert {k: v for k, v in ch[kind].items() if k != "channel_line"} == ref[kind]
                cl = ch[kind]["channel_line"]
                if cl:
                    assert (cl["offset"] > 0) == (kind == "up")
                    assert cl["price"] == pytest.approx(ref[kind]["price"] + cl["offset"])
                    assert ref[kind]["anchor"]["date"] < cl["through"]["date"] < ref[kind]["second"]["date"]
    assert lines > 10


def test_a_setup_on_a_trendline_or_channel_line_is_labelled():
    ch = {"up": {"anchor": {"date": "2024-01-01", "price": 100.0}, "second": {"date": "2024-02-12", "price": 106.0},
                 "slope_per_bar": 0.2, "span_bars": 60, "touches": 3, "state": "intact",
                 "channel_line": {"offset": 8.0, "through": {"date": "2024-01-22", "price": 111.0}}},
          "down": None, "_ago": {"2024-03-11": 10, "2024-02-12": 30, "2024-01-22": 45}}
    hit = PA._line_at(ch, 1, 110.1, pd.Timestamp("2024-03-11"), 1.0)       # the line is at 110.0 fifty bars on
    assert hit == {"kind": "up", "line": "trendline", "price": pytest.approx(110.0), "touches": 3, "state": "intact"}
    assert PA._line_at(ch, 1, 110.0 + C.TRENDLINE_TOL_ATR + 0.1, pd.Timestamp("2024-03-11"), 1.0) is None
    top = PA._line_at(ch, -1, 118.0, pd.Timestamp("2024-03-11"), 1.0)      # a bearish setup at the channel line
    assert (top["kind"], top["line"], top["price"]) == ("up", "channel_line", pytest.approx(118.0))
    # the swing a line is drawn through is on it by construction: only a line with a third touch counts there
    assert PA._line_at(ch, 1, 106.0, pd.Timestamp("2024-02-12"), 1.0)["line"] == "trendline"
    two = {**ch, "up": {**ch["up"], "touches": 2}}
    assert PA._line_at(two, 1, 106.0, pd.Timestamp("2024-02-12"), 1.0) is None
    assert PA._line_at(ch, -1, 111.0, pd.Timestamp("2024-01-22"), 1.0) is None     # the channel line's own swing
    assert PA._line_at(ch, 1, 110.0, pd.Timestamp("2023-12-01"), 1.0) is None      # before the line starts


def test_module_short_history_is_unavailable(make_bars):
    out = PA.build_price_action(ctx_for(make_bars(n=PA.WARMUP)))
    assert out["available"] is False and out["reason"]


def test_chart_payload_matches_the_window(make_bars):
    bars = make_bars(n=700, seed=8, vol=0.02)
    ctx = ctx_for(bars)
    mods = {"price_action": PA.build_price_action(ctx)}
    win = bars.index[-252:]
    p = PA.chart_payload(ctx, mods, win)
    assert p["available"]
    assert len(p["bar_types"]) == len(p["states"]) == len(p["always_in"]) == 252
    assert all(m["date"] >= str(win[0].date()) for m in p["markers"])
    assert all(r["start"] <= r["end"] for r in p["ranges"])
    kinds = {m["kind"] for m in p["markers"]}
    assert kinds <= {"H1", "H2", "H3", "H4", "L1", "L2", "L3", "L4", "G20", "FB", "BO", "MTR", "DB", "DT", "CX", "ii", "ioi"}
    assert {"FB", "DB", "DT"} <= kinds and kinds & {"H2", "L2"}
    for m in p["markers"]:
        assert m["side"] in (1, -1, 0) and np.isfinite(m["price"])
        if m["kind"] in ("G20", "FB", "BO", "MTR", "DB", "DT"):
            assert m["triggered"] in (True, False, None)
    for x in p["doubles"]:
        assert x["side"] in ("top", "bottom") and len(x["points"]) == 2 and x["variant"] in ("reversal", "flag")
    for x in p["mtrs"]:
        assert len(x["points"]) == 3
    tc = mods["price_action"]["trend_channels"]
    assert [c["kind"] for c in p["channels"]] == [k for k in ("up", "down") if tc[k]]
    for c in p["channels"]:
        assert c["anchor"] == tc[c["kind"]]["anchor"] and c["slope_per_bar"] == tc[c["kind"]]["slope_per_bar"]
    assert set(p["summary"]) >= {"state", "climax", "breakout_mode", "last_double", "last_mtr", "last_breakout",
                                 "trend_channels"}
    json.dumps(p, allow_nan=False, default=str)
    # a window that ends before the last bar stops there, and shows no lines measured at the last bar
    p2 = PA.chart_payload(ctx, mods, bars.index[-300:-48])
    assert len(p2["bar_types"]) == 252 and all(m["date"] <= str(bars.index[-49].date()) for m in p2["markers"])
    assert p2["channels"] == []


def test_chart_marks_an_h2_only_on_the_bar_that_entered_it(make_bars):
    bars = make_bars(n=900, seed=11, vol=0.02)
    ctx = ctx_for(bars)
    p = PA.chart_payload(ctx, {"price_action": PA.build_price_action(ctx)}, bars.index[-504:])
    ev = PA.events(bars, ctx["ind"])
    up = PA.stop_entries(bars)[1][0]
    pos = {str(d.date()): i for i, d in enumerate(bars.index)}
    bold = [m for m in p["markers"] if m["kind"] == "H2" and m["setup"] and not m.get("range")]
    assert bold
    for m in bold:
        i = pos[m["date"]]
        assert ev["pa_h2_ema"].iloc[i - 1] and up[i - 1]
