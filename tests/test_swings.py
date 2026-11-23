"""Tests for scripts/swings.py: weekly swing pivots, the swing structure and trendlines, on hand-built
bars with known answers (no network). swing_pivots itself is tested in test_levels.py."""
import numpy as np
import pandas as pd
import pytest

import config as C
import swings as SW


def frame(closes, highs=None, lows=None, start="2024-01-01"):
    close = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(close), name="date")
    high = close + 0.5 if highs is None else np.asarray(highs, dtype=float)
    low = close - 0.5 if lows is None else np.asarray(lows, dtype=float)
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": 1000.0}, index=idx)


def day(df, i):
    return df.index[i].strftime("%Y-%m-%d")


def piv(df, points):
    """[(bar, price)] -> pivots as swing_pivots returns them."""
    return [{"date": day(df, i), "price": float(p), "swing_atr": 3.0, "confirmed": day(df, i + 1)} for i, p in points]


def flat_atr(df, value=1.0):
    return pd.Series(value, index=df.index)


# ---------------------------------------------------------------------------- daily pivots
def test_daily_pivots_keep_only_pivots_inside_the_window_and_start_the_count_before_it():
    # 10 -> 20 -> 10 -> 20 ... in legs of 5 bars: a pivot every 5 bars
    wave = [10, 12, 14, 16, 18, 20, 18, 16, 14, 12]
    close = np.array(wave * 8, dtype=float)
    df = frame(close)
    out = SW.daily_pivots(df, flat_atr(df), lookback=30)
    start = day(df, len(df) - 30)
    dates = [p["date"] for p in out["highs"] + out["lows"]]
    assert dates and all(d >= start for d in dates)
    # the window's first bar is a swing low (bar 50, low 9.5): found because the count starts earlier
    assert out["lows"][0] == {"date": start, "price": 9.5, "swing_atr": pytest.approx(11.0),
                              "confirmed": day(df, len(df) - 29)}
    full = SW.swing_pivots(df, flat_atr(df))
    assert out["highs"] == [p for p in full["highs"] if p["date"] >= start]


# ---------------------------------------------------------------------------- weekly pivots
def weekly_wave(weeks=60, partial_days=0, spike=None):
    """Daily bars whose weekly mid follows a triangle wave of 16 weeks between 100 and 140. In every week
    Tuesday makes the low (mid - 3) and Wednesday the high (mid + 3); the other days stay within 1."""
    n = weeks * 5 + partial_days
    idx = pd.bdate_range("2023-01-02", periods=n, name="date")          # a Monday
    week = np.arange(n) // 5
    phase = week % 16
    mid = 100.0 + 5.0 * np.where(phase <= 8, phase, 16 - phase)
    dow = np.arange(n) % 5
    high = mid + np.where(dow == 2, 3.0, 1.0)
    low = mid - np.where(dow == 1, 3.0, 1.0)
    df = pd.DataFrame({"open": mid, "high": high, "low": low, "close": mid, "volume": 1000.0}, index=idx)
    if spike is not None:
        df.iloc[-1, df.columns.get_loc("high")] = spike
    return df


def test_weekly_pivots_are_the_swings_of_complete_weeks_dated_by_the_daily_bar():
    df = weekly_wave(60)
    out = SW.weekly_pivots(df, weeks=40)
    assert out["available"] is True and out["weeks"] == 40
    assert out["window_start"] == day(df, 20 * 5 + 4)                   # the Friday of week 20
    assert out["atr_weekly"] == pytest.approx(SW.I.atr(SW.I.resample_weekly(df), C.ATR_PERIOD).iloc[-1])
    # peaks in weeks 24, 40, 56 (phase 8) and troughs in weeks 32, 48 (phase 0); 56 is confirmed in week 58
    assert [p["price"] for p in out["highs"]] == [143.0, 143.0, 143.0]
    assert [p["price"] for p in out["lows"]] == [97.0, 97.0]
    for p, week in zip(out["highs"], (24, 40, 56)):
        assert p["date"] == day(df, week * 5 + 2)                       # the Wednesday that made the high
        assert p["week"] == day(df, week * 5 + 4)                       # its week's last trading day
    for p, week in zip(out["lows"], (32, 48)):
        assert p["date"] == day(df, week * 5 + 1) and p["week"] == day(df, week * 5 + 4)
    assert all(p["week"] >= out["window_start"] and p["swing_atr"] > C.SWING_ATR for p in out["highs"] + out["lows"])


def test_weekly_pivots_ignore_the_week_in_progress():
    whole = SW.weekly_pivots(weekly_wave(57))                            # week 56, the peak, is the last week
    assert [p["week"] for p in whole["highs"]] == [day(weekly_wave(57), w * 5 + 4) for w in (8, 24, 40)]   # not 56 yet
    # three more days: a collapse that would confirm the peak, and a spike that would be a new one
    partial = weekly_wave(57, partial_days=3)
    partial.iloc[-3:, partial.columns.get_loc("low")] = 60.0
    partial.iloc[-1, partial.columns.get_loc("high")] = 400.0
    assert SW.weekly_pivots(partial) == whole
    # once that week is complete it counts
    done = weekly_wave(57, partial_days=5)
    done.iloc[-5:, done.columns.get_loc("low")] = 60.0
    assert [p["date"] for p in SW.weekly_pivots(done)["highs"]][3:] == [day(done, 56 * 5 + 2)]


def test_weekly_pivots_need_enough_complete_weeks():
    out = SW.weekly_pivots(weekly_wave(C.ATR_PERIOD + 1))
    assert out == {"available": False, "highs": [], "lows": [],
                   "reason": f"weekly pivots need {C.ATR_PERIOD + 2} complete weekly bars (have {C.ATR_PERIOD + 1})"}
    assert SW.weekly_pivots(weekly_wave(C.ATR_PERIOD + 2))["available"] is True


# ---------------------------------------------------------------------------- swing structure
def structure(closes, highs, lows, atr=1.0, **kw):
    df = frame(closes)
    return df, SW.swing_structure(df, {"highs": piv(df, highs), "lows": piv(df, lows)}, atr, **kw)


def test_structure_uptrend_and_the_price_that_ends_it():
    closes = [110.0] * 45 + [115.0] * 5
    df, st = structure(closes, highs=[(10, 110), (30, 120)], lows=[(20, 100), (40, 108)], atr=2.0)
    assert (st["available"], st["state"], st["highs"], st["lows"]) == (True, "uptrend", "higher", "higher")
    assert st["last_high"] == {"date": day(df, 30), "price": 120.0, "provisional": False}
    assert st["prev_low"] == {"date": day(df, 20), "price": 100.0, "provisional": False}
    inv = st["invalidation"]
    assert inv["kind"] == "swing_low" and inv["price"] == 108.0 and inv["date"] == day(df, 40)
    assert inv["distance_pct"] == pytest.approx(108 / 115 - 1) and inv["distance_atr"] == pytest.approx(-3.5)
    assert [(s["kind"], s["price"], s["label"]) for s in st["swings"]] == [
        ("high", 110.0, None), ("low", 100.0, None), ("high", 120.0, "HH"), ("low", 108.0, "HL")]
    assert "Dow theory" in st["rule"] and st["equal_atr"] == C.STRUCTURE_EQUAL_ATR


def test_structure_downtrend_mirrors_it():
    closes = [100.0] * 45 + [95.0] * 5
    df, st = structure(closes, highs=[(10, 120), (30, 110)], lows=[(20, 100), (40, 92)])
    assert (st["state"], st["highs"], st["lows"]) == ("downtrend", "lower", "lower")
    inv = st["invalidation"]
    assert inv["kind"] == "swing_high" and inv["price"] == 110.0 and inv["distance_atr"] == pytest.approx(15.0)


def test_structure_counts_the_swing_in_progress_once_a_close_passes_the_last_swing():
    # after the last pivot (a low at bar 40) price closes above the last swing high of 120
    closes = [100.0] * 41 + [112.0, 118.0, 123.0, 126.0, 124.0]
    highs = [c + 0.5 for c in closes]
    highs[44] = 127.5
    df = frame(closes, highs=highs)
    pivots = {"highs": piv(df, [(10, 115), (30, 120)]), "lows": piv(df, [(20, 105), (40, 100)])}
    st = SW.swing_structure(df, pivots, 1.0)
    assert st["last_high"] == {"date": day(df, 44), "price": 127.5, "provisional": True}
    assert st["prev_high"]["price"] == 120.0 and st["highs"] == "higher"
    assert st["lows"] == "lower" and st["state"] == "mixed" and st["invalidation"] is None
    assert st["swings"][-1] == {"kind": "high", "date": day(df, 44), "price": 127.5, "provisional": True, "label": "HH"}
    # a high above the last swing high is not enough: it takes a close
    wick = df.copy()
    wick["close"] = wick["close"].clip(upper=119.0)
    assert SW.swing_structure(wick, pivots, 1.0)["last_high"]["price"] == 120.0


def test_structure_a_close_below_the_last_swing_low_ends_the_higher_lows():
    closes = [110.0] * 41 + [112.0, 109.0, 106.0]
    df, st = structure(closes, highs=[(10, 110), (30, 120)], lows=[(20, 100), (40, 108)])
    assert st["last_low"] == {"date": day(df, 43), "price": 105.5, "provisional": True}
    assert (st["highs"], st["lows"], st["state"]) == ("higher", "lower", "mixed")


def test_structure_equal_swings_and_missing_swings():
    closes = [110.0] * 50
    _, st = structure(closes, highs=[(10, 120.0), (30, 120.2)], lows=[(20, 100), (40, 108)])
    assert (st["highs"], st["lows"], st["state"], st["invalidation"]) == ("equal", "higher", "mixed", None)
    _, st = structure(closes, highs=[(10, 120.0), (30, 120.2)], lows=[(20, 100), (40, 108)], atr=0.5)
    assert st["highs"] == "higher" and st["state"] == "uptrend"          # 0.2 > 0.25 x 0.5
    _, st = structure(closes, highs=[(30, 120)], lows=[(20, 100), (40, 108)])
    assert st == {"available": False, "reason": "needs two confirmed swing highs and two swing lows (have 1 and 2)"}
    df = frame(closes)
    assert SW.swing_structure(df, {"highs": [], "lows": []}, None)["available"] is False


def test_structure_measures_weekly_swings_in_weekly_atrs_and_the_distance_in_daily_ones():
    closes = [111.0] * 50
    _, st = structure(closes, highs=[(10, 110), (30, 112)], lows=[(20, 100), (40, 108)], atr=10.0, atr_daily=2.0)
    assert st["highs"] == "equal"                                        # 2 < 0.25 x 10
    _, st = structure(closes, highs=[(10, 110), (30, 120)], lows=[(20, 100), (40, 108)], atr=10.0, atr_daily=2.0)
    assert st["invalidation"]["distance_atr"] == pytest.approx(-1.5)     # (108 - 111) / 2


def test_structure_keeps_the_last_swings_only():
    closes = [120.0] * 90
    _, st = structure(closes, highs=[(10, 110), (30, 120), (50, 125), (70, 131)],
                      lows=[(20, 100), (40, 108), (60, 112), (80, 111.9)], keep=3)
    assert [(s["kind"], s["label"]) for s in st["swings"]] == [("low", "HL"), ("high", "HH"), ("low", "EL")]


# ---------------------------------------------------------------------------- trendlines
def line_frame(n=100, lows=((10, 100.0), (40, 106.0), (70, 115.0)), gap=2.0):
    """Closes `gap` above the line through the first two points (so no close is beyond it)."""
    (x0, y0), (x1, y1) = lows[0], lows[1]
    line = y0 + (y1 - y0) / (x1 - x0) * (np.arange(n) - x0)
    return frame(line + gap), line


def test_up_trendline_runs_from_the_lowest_low_along_the_flattest_line():
    df, line = line_frame()
    lows = piv(df, [(10, 100.0), (40, 106.0), (70, 115.0)])
    tl = SW.trendlines(df, flat_atr(df), {"lows": lows, "highs": []})
    up = tl["up"]
    assert tl["down"] is None and up["kind"] == "up"
    assert up["anchor"] == {"date": day(df, 10), "price": 100.0}
    assert up["second"] == {"date": day(df, 40), "price": 106.0}          # 0.2 a bar; through bar 70 it is 0.25
    assert up["slope_per_bar"] == pytest.approx(0.2) and up["slope_atr_per_bar"] == pytest.approx(0.2)
    assert up["price"] == pytest.approx(line[-1]) == pytest.approx(117.8)
    assert up["distance_pct"] == pytest.approx(117.8 / 119.8 - 1) and up["distance_atr"] == pytest.approx(-2.0)
    assert up["span_bars"] == 89
    assert up["touches"] == 2 and up["confirmed"] is False                # bar 70's low is 3 above the line
    assert (up["state"], up["broken_on"], up["closes_beyond"], up["close_side"]) == ("intact", None, 0, "above")
    assert tl["tol_atr"] == C.TRENDLINE_TOL_ATR and tl["min_bars"] == C.TRENDLINE_MIN_BARS and "arithmetic" in tl["rule"]


def test_a_third_swing_on_the_line_confirms_it():
    df, _ = line_frame()
    lows = piv(df, [(10, 100.0), (40, 106.0), (70, 112.2)])               # the line is at 112 on bar 70
    up = SW.trendlines(df, flat_atr(df), {"lows": lows, "highs": []})["up"]
    assert up["touches"] == 3 and up["confirmed"] is True
    assert up["touch_dates"] == [day(df, 10), day(df, 40), day(df, 70)]
    far = piv(df, [(10, 100.0), (40, 106.0), (70, 112.3)])                # 0.3 away: more than 0.25 ATR
    assert SW.trendlines(df, flat_atr(df), {"lows": far, "highs": []})["up"]["touches"] == 2


def test_a_close_beyond_the_line_breaks_it_and_a_wick_does_not():
    df, line = line_frame()
    lows = piv(df, [(10, 100.0), (40, 106.0)])
    wick = df.copy()
    wick.iloc[80, wick.columns.get_loc("low")] = line[80] - 5.0           # a low through the line, the close above
    assert SW.trendlines(wick, flat_atr(wick), {"lows": lows, "highs": []})["up"]["state"] == "intact"
    edge = df.copy()
    edge.iloc[80, edge.columns.get_loc("close")] = line[80] - 0.25        # exactly the tolerance: not beyond it
    assert SW.trendlines(edge, flat_atr(edge), {"lows": lows, "highs": []})["up"]["state"] == "intact"
    broken = df.copy()
    broken.iloc[[80, 85], broken.columns.get_loc("close")] = [line[80] - 0.3, line[85] - 1.0]
    up = SW.trendlines(broken, flat_atr(broken), {"lows": lows, "highs": []})["up"]
    assert (up["state"], up["broken_on"], up["closes_beyond"], up["close_side"]) == ("broken", day(df, 80), 2, "above")
    # closes before the second point do not count: the line did not exist yet
    early = df.copy()
    early.iloc[30, early.columns.get_loc("close")] = line[30] - 3.0
    assert SW.trendlines(early, flat_atr(early), {"lows": lows, "highs": []})["up"]["state"] == "intact"
    below = df.copy()
    below.iloc[-1, below.columns.get_loc("close")] = line[-1] - 4.0
    up = SW.trendlines(below, flat_atr(below), {"lows": lows, "highs": []})["up"]
    assert (up["state"], up["broken_on"], up["close_side"]) == ("broken", day(df, 99), "below")
    assert up["distance_atr"] == pytest.approx(4.0)


def test_trendline_restarts_from_the_second_point_when_the_two_are_too_close_or_the_line_too_flat():
    df, _ = line_frame(lows=((20, 101.0), (60, 113.0)))
    close = piv(df, [(10, 100.0), (20, 101.0), (60, 113.0)])              # 10 bars apart: fewer than 20
    up = SW.trendlines(df, flat_atr(df), {"lows": close, "highs": []})["up"]
    assert up["anchor"]["date"] == day(df, 20) and up["second"]["date"] == day(df, 60)
    assert up["slope_per_bar"] == pytest.approx(0.3) and up["touch_dates"] == [day(df, 20), day(df, 60)]
    flat = piv(df, [(10, 100.0), (50, 100.2), (90, 110.2)])               # 0.005 ATR a bar: a level, not a trend
    up = SW.trendlines(df, flat_atr(df), {"lows": flat, "highs": []})["up"]
    assert up["anchor"]["date"] == day(df, 50) and up["slope_per_bar"] == pytest.approx(0.25)
    # the same flat pair with nothing after it makes no line
    assert SW.trendlines(df, flat_atr(df), {"lows": flat[:2], "highs": []})["up"] is None
    # the slope floor is in ATRs: with a small ATR the flat pair is a line
    assert SW.trendlines(df, flat_atr(df, 0.2), {"lows": flat[:2], "highs": []})["up"] is not None


def test_no_up_line_through_falling_lows_and_no_line_from_one_swing():
    df, _ = line_frame()
    falling = piv(df, [(10, 110.0), (40, 105.0), (70, 100.0)])            # the lowest low is the last one
    assert SW.trendlines(df, flat_atr(df), {"lows": falling, "highs": []})["up"] is None
    assert SW.trendlines(df, flat_atr(df), {"lows": falling[:1], "highs": []})["up"] is None
    assert SW.trendlines(df, flat_atr(df), {"lows": [], "highs": []}) == {
        "up": None, "down": None, "tol_atr": C.TRENDLINE_TOL_ATR, "min_bars": C.TRENDLINE_MIN_BARS,
        "min_slope_atr": C.TRENDLINE_MIN_SLOPE_ATR,
        "rule": SW.TRENDLINE_RULE.format(t=C.TRENDLINE_TOL_ATR, b=C.TRENDLINE_MIN_BARS, s=C.TRENDLINE_MIN_SLOPE_ATR)}


def test_down_trendline_mirrors_the_up_line():
    n = 100
    line = 150.0 - 0.2 * (np.arange(n) - 10)
    df = frame(line - 2.0)
    highs = piv(df, [(10, 150.0), (40, 144.0), (70, 135.0)])              # -0.2 a bar; through bar 70 it is -0.25
    down = SW.trendlines(df, flat_atr(df), {"lows": [], "highs": highs})["down"]
    assert down["kind"] == "down" and down["second"] == {"date": day(df, 40), "price": 144.0}
    assert down["slope_per_bar"] == pytest.approx(-0.2) and down["price"] == pytest.approx(132.2)
    assert (down["state"], down["close_side"], down["touches"]) == ("intact", "below", 2)
    assert down["distance_atr"] == pytest.approx(2.0)
    up_break = df.copy()
    up_break.iloc[90, up_break.columns.get_loc("close")] = line[90] + 0.5
    down = SW.trendlines(up_break, flat_atr(up_break), {"lows": [], "highs": highs})["down"]
    assert (down["state"], down["broken_on"]) == ("broken", day(df, 90))


def test_trendlines_ignore_pivots_outside_the_window():
    df, _ = line_frame()
    window = df.iloc[30:]
    lows = piv(df, [(10, 100.0), (40, 106.0), (70, 115.0)])               # bar 10 lies before the window
    up = SW.trendlines(window, flat_atr(window), {"lows": lows, "highs": []})["up"]
    assert up["anchor"]["date"] == day(df, 40) and up["second"]["date"] == day(df, 70)
    assert up["slope_per_bar"] == pytest.approx(0.3)


# ---------------------------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("t", (420, 555, 698))
def test_swings_do_not_look_ahead(t):
    """Weekly pivots, the structure and the trendlines as of bar t do not change when later bars do."""
    from conftest import synthetic_bars
    bars = synthetic_bars(700, seed=11, vol=0.02)
    wild = bars.copy()
    rng = np.random.default_rng(t)
    for col in ("open", "high", "low", "close"):
        wild.iloc[t + 1:, wild.columns.get_loc(col)] = np.exp(rng.uniform(-3.0, 9.0, len(bars) - t - 1))

    def read(df):
        head = df.iloc[:t + 1]
        atr = SW.I.atr(head, C.ATR_PERIOD)
        daily = SW.daily_pivots(head, atr, 252)
        weekly = SW.weekly_pivots(head)
        return (daily, weekly, SW.swing_structure(head, daily, float(atr.iloc[-1])),
                SW.swing_structure(head, weekly, weekly["atr_weekly"], float(atr.iloc[-1])),
                SW.trendlines(head.iloc[-252:], atr, daily))

    daily, weekly, st, st_w, tl = read(bars)
    assert read(wild) == (daily, weekly, st, st_w, tl)
    assert weekly["available"] and st["available"] and st_w["available"]
    # a pivot is known only once a later bar (a later complete week) has confirmed it
    assert all(p["date"] < p["confirmed"] <= day(bars, t) for p in daily["highs"] + daily["lows"])
    assert all(p["week"] < p["confirmed"] <= day(bars, t) for p in weekly["highs"] + weekly["lows"])
