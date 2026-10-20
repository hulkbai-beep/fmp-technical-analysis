"""candlesticks.py: each pattern on constructed bars, the doji marker, the module output and the
chart payload. No network."""
import json
from pathlib import Path

import pandas as pd
import pytest

import candlesticks as CS
import config as C
import indicators as I

ATR = 1.0


def frame(rows):
    """Bars from [open, high, low, close] rows, after a lead-in, with ATR14 fixed at 1.0."""
    idx = pd.bdate_range("2024-01-01", periods=len(rows), name="date")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 1e6
    return df, pd.DataFrame({"atr14": ATR}, index=idx)


def decline(n=7, start=110.0):
    """n small bearish bars stepping down 1.0 a bar (no pattern of their own)."""
    return [[start - i + 0.3, start - i + 0.4, start - i - 0.4, start - i - 0.3] for i in range(n)]


def advance(n=7, start=90.0):
    return [[start + i - 0.3, start + i + 0.4, start + i - 0.4, start + i + 0.3] for i in range(n)]


def fired(rows):
    bars, ind = frame(rows)
    fr = CS.pattern_frame(bars, ind)
    last = fr.iloc[-1]
    return {k for k in fr.columns if last[k]}


def test_lead_ins_fire_nothing_at_the_end():
    assert fired(decline()) == set()
    assert fired(advance()) == set()


def test_bullish_engulfing():
    rows = decline() + [[103.6, 103.7, 102.6, 102.8], [102.7, 104.0, 102.5, 103.9]]
    assert "cs_bullish_engulfing" in fired(rows)


def test_bearish_engulfing():
    rows = advance() + [[96.2, 97.2, 96.1, 97.0], [97.1, 97.3, 95.8, 96.0]]
    assert "cs_bearish_engulfing" in fired(rows)


def test_engulfing_needs_the_prior_trend():
    rows = advance() + [[97.6, 97.7, 96.6, 96.8], [96.7, 98.0, 96.5, 97.9]]
    assert "cs_bullish_engulfing" not in fired(rows)


def test_hammer_and_shooting_star():
    assert "cs_hammer" in fired(decline() + [[103.5, 103.7, 102.2, 103.6]])          # long lower shadow
    assert "cs_shooting_star" in fired(advance() + [[96.5, 97.8, 96.4, 96.4]])       # long upper shadow
    assert "cs_hammer" not in fired(decline() + [[103.5, 103.6, 103.4, 103.55]])     # range < 0.5 ATR


def test_morning_and_evening_star():
    assert "cs_morning_star" in fired(decline() + [[104, 104.1, 102.9, 103.0], [102.8, 103.0, 102.6, 102.85],
                                                   [102.9, 104.0, 102.8, 103.8]])
    assert "cs_evening_star" in fired(advance() + [[96, 97.1, 95.9, 97.0], [97.1, 97.3, 97.0, 97.15],
                                                   [97.1, 97.2, 96.0, 96.2]])


def test_piercing_line_and_dark_cloud():
    assert "cs_piercing_line" in fired(decline() + [[104, 104.1, 102.9, 103.0], [102.8, 103.8, 102.7, 103.7]])
    assert "cs_dark_cloud_cover" in fired(advance() + [[96, 97.1, 95.9, 97.0], [97.2, 97.3, 96.2, 96.3]])


def test_harami():
    assert "cs_bullish_harami" in fired(decline() + [[104, 104.1, 102.9, 103.0], [103.2, 103.6, 103.1, 103.5]])
    assert "cs_bearish_harami" in fired(advance() + [[96, 97.1, 95.9, 97.0], [96.8, 96.9, 96.3, 96.4]])


def test_three_white_soldiers_and_black_crows():
    soldiers = [[103.0, 103.9, 102.9, 103.8], [103.5, 104.6, 103.4, 104.5], [104.2, 105.3, 104.1, 105.2]]
    assert "cs_three_white_soldiers" in fired(decline() + soldiers)
    crows = [[97.0, 97.1, 96.1, 96.2], [96.5, 96.6, 95.4, 95.5], [95.8, 95.9, 94.7, 94.8]]
    assert "cs_three_black_crows" in fired(advance() + crows)


def test_doji_marker():
    assert "doji" in fired(decline() + [[103.5, 104.0, 103.0, 103.52]])


def test_frame_columns_and_types(make_bars):
    bars = make_bars(n=600, seed=21, vol=0.02)
    fr = CS.pattern_frame(bars, I.compute_all(bars))
    assert list(fr.columns) == [*CS.SIGNAL_IDS, "doji"]
    assert all(fr[c].dtype == bool for c in fr.columns)
    assert not fr.iloc[:C.ATR_PERIOD].to_numpy().any()       # no ATR yet: nothing fires


def test_patterns_md_lists_every_rule_as_coded():
    text = (Path(__file__).resolve().parents[1] / "references" / "patterns.md").read_text(encoding="utf-8")
    for s in CS.SIGNALS:
        assert f"`{s['id']}`" in text and f"`{s['rule']}`" in text, s["id"]


def test_module_and_chart_payload(make_bars):
    bars = make_bars(n=900, seed=12, vol=0.02)
    ctx = {"ticker": "TEST", "bars": bars, "ind": I.compute_all(bars), "hz": C.HORIZONS["position"]}
    out = CS.build_candlesticks(ctx)
    json.dumps(out, allow_nan=False, default=str)
    assert out["available"] and out["primary_horizon"] == 60
    assert [r["id"] for r in out["library"]] == CS.SIGNAL_IDS
    assert all(r["horizon"] == 60 for r in out["library"])
    assert len(out["recent"]) <= 5 and all(r["id"] in CS.SIGNAL_IDS for r in out["recent"])
    p = CS.chart_payload(ctx, {"candlesticks": out}, bars.index[-252:])
    assert p["available"] and p["markers"]
    assert all(m["start"] <= m["date"] and m["date"] >= str(bars.index[-252].date()) for m in p["markers"])
    assert {m["bias"] for m in p["markers"]} <= {-1, 0, 1}


def test_module_short_history_is_unavailable():
    bars, ind = frame(decline(5))
    out = CS.build_candlesticks({"bars": bars, "ind": ind, "hz": C.HORIZONS["swing"]})
    assert out["available"] is False


@pytest.mark.parametrize("sid", CS.SIGNAL_IDS)
def test_every_pattern_has_a_bias_and_bar_count(sid):
    spec = next(s for s in CS.SIGNALS if s["id"] == sid)
    assert spec["bias"] in (-1, 1) and spec["bars"] in (1, 2, 3) and spec["regime"] == "current_side"
