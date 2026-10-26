"""Tests for scripts/trading_ranges.py on hand-built paths (no network)."""
import numpy as np
import pandas as pd
import pytest

import indicators as I
import trading_ranges as T
from conftest import synthetic_bars


def path(points, leg=10, start="2023-01-02", wick=0.5):
    """Daily bars walking linearly between the given closes, leg bars per segment."""
    closes = [points[0]]
    for a, b in zip(points, points[1:]):
        closes += list(np.linspace(a, b, leg + 1)[1:])
    c = np.array(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.bdate_range(start, periods=len(c), name="date")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick,
                         "close": c, "volume": 1e6}, index=idx)


def atr(bars):
    return I.atr(bars)


def detect(bars, **kw):
    return T.detect_trading_ranges(bars, atr(bars), **kw)


RANGE = [90, 105, 110, 100, 110, 100, 110, 100, 110, 100, 108]


def test_textbook_rectangle_is_one_box_confirmed_after_its_evidence():
    bars = path(RANGE)
    r = detect(bars)
    assert r["available"] and r["n_boxes"] == 1
    box = r["active_box"]
    assert box["status"] == "active" and box["upper_tests"] >= 2 and box["lower_tests"] >= 2
    # every pivot high is 110.5 and every low 99.5; the bands are widened to TR_MIN_BAND_ATR around them
    assert box["upper_zone"]["low"] < 110.5 < box["upper"] and box["lower"] < 99.5 < box["lower_zone"]["high"]
    assert box["upper"] - box["upper_zone"]["low"] == pytest.approx(0.25 * box["atr_reference"])
    assert 2 <= box["height_atr"] <= 8 and box["quality_score"] is None
    # confirmed no earlier than the bar that confirms the second low after the second high
    conf = pd.Timestamp(box["confirmed_date"])
    assert conf > bars.index[50]
    # before confirmation there is no box; afterwards the edges never move
    assert detect(bars, as_of=conf - pd.offsets.BDay(1))["active_box"] is None
    for d in bars.index[bars.index >= conf][::7]:
        b = detect(bars, as_of=d)["active_box"]
        assert (b["box_id"], b["upper"], b["lower"]) == (box["box_id"], box["upper"], box["lower"])


@pytest.mark.parametrize("points", [
    [100 + 6 * i + (4 if i % 2 else 0) for i in range(14)],     # staircase up: higher highs and lows
    [200 - 6 * i - (4 if i % 2 else 0) for i in range(14)],     # staircase down
    list(np.linspace(100, 160, 12)),                            # straight line
])
def test_trends_make_no_box(points):
    r = detect(path(points))
    assert r["available"] and r["n_boxes"] == 0 and r["active_box"] is None and r["notes"]


def test_one_sided_retests_are_not_a_box():
    # the top is hit again and again, the lows keep rising: no lower band
    r = detect(path([90, 110, 96, 110, 100, 110, 104, 110, 107, 110]))
    assert r["n_boxes"] == 0


def test_sustained_breakout_keeps_the_box_and_records_the_break():
    bars = path(RANGE + [125, 130], leg=10)
    r = detect(bars)
    assert r["active_box"] is None
    box = r["last_box"]
    assert box["status"] == "broken_up" and box["breakout"]["direction"] == "up"
    before = detect(bars, as_of=pd.Timestamp(box["breakout"]["first_breach"]) - pd.offsets.BDay(1))["active_box"]
    assert (before["box_id"], before["upper"], before["lower"]) == (box["box_id"], box["upper"], box["lower"])
    assert pd.Timestamp(box["end_date"]) == pd.Timestamp(box["breakout"]["confirmed"])


def test_failed_breakout_is_recorded_and_the_box_stays_active():
    bars = path(RANGE[:-1] + [108], leg=10)
    spike = bars.index[-3]
    bars.loc[spike, ["close", "high"]] = [113.0, 113.5]          # one close above the band, then back inside
    r = detect(bars)
    box = r["active_box"]
    assert box is not None and box["status"] == "active" and box["breakout_attempts"] == 1
    ev = [e for e in box["events"] if e["type"] == "failed_breakout"][0]
    assert ev["direction"] == "up" and ev["first_breach"] == spike.strftime("%Y-%m-%d")
    # on the spike day itself the failure is not known yet
    assert detect(bars, as_of=spike)["active_box"]["status"] == "breakout_pending"


def test_expires_without_new_tests():
    bars = path(RANGE[:-1] + [105] + [105] * 15, leg=10)        # parks mid-box for 150 bars
    box = detect(bars)["last_box"]
    assert box["status"] == "expired"


def test_no_lookahead_cut_series_equals_as_of_and_shared_pivots():
    bars = synthetic_bars(n=700, seed=11)
    a = atr(bars)
    piv = T.pivot_rows(bars, a)
    for t in range(150, 700, 37):
        d = bars.index[t]
        cut = bars.iloc[:t + 1]
        r_cut = T.detect_trading_ranges(cut, I.atr(cut))
        assert r_cut == T.detect_trading_ranges(bars, a, as_of=d)
        assert r_cut == T.detect_trading_ranges(bars, a, as_of=d, pivots=piv)


def test_short_or_missing_inputs_and_determinism():
    bars = path(RANGE)
    assert not detect(bars.iloc[:10])["available"]
    nan_atr = pd.Series(np.nan, index=bars.index)
    assert not T.detect_trading_ranges(bars, nan_atr)["available"]
    assert detect(bars) == detect(bars.copy())
