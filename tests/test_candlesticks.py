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


# ---------------------------------------------------------------------------- where a pattern formed
def located(rows, pattern, levels):
    """locate() for one active pattern that fired on the last bar of rows."""
    bars, _ = frame(rows)
    cs = {"available": True, "notes": [], "recent": ["kept"],
          "active": [{"id": pattern, "fired_date": bars.index[-1].strftime("%Y-%m-%d"), "n": 12}]}
    out = CS.locate({"bars": bars}, cs, levels)
    assert out["recent"] == ["kept"] and out["at_level_rule"] == CS.AT_LEVEL_RULE
    return out["active"][0]


def zone(lo, hi, **kw):
    return {"price": (lo + hi) / 2, "zone_low": lo, "zone_high": hi, "sources": ["pivot_low"], "score": 3.0, **kw}


def test_a_bullish_pattern_is_at_a_level_when_its_low_reached_a_support_zone():
    rows = decline() + [[103.6, 103.7, 102.6, 102.8], [102.7, 104.0, 102.5, 103.9]]      # low 102.5, close 103.9
    assert C.TEST_TOL_ATR == 0.1
    lv = {"available": True, "atr": 2.0, "supports": [zone(101.0, 102.35), zone(95.0, 96.0)],
          "resistances": [zone(102.0, 103.0)], "current_zone": None}
    a = located(rows, "cs_bullish_engulfing", lv)
    # 102.5 is within 0.1 ATR (0.2) of the zone's top, and the close is above its bottom
    assert a["at_level"] is True and a["n"] == 12
    assert a["level"] == {"label": "S1", "side": "support", "price": pytest.approx(101.675), "zone_low": 101.0,
                          "zone_high": 102.35, "sources": ["pivot_low"]}
    assert "score" not in a["level"]                                    # a place, not a rating
    # a support the low did not reach, and a resistance (never the place of a bullish pattern)
    far = {**lv, "supports": [zone(101.0, 102.2)]}
    assert located(rows, "cs_bullish_engulfing", far) == {**a, "at_level": False, "level": None}
    # a pattern that closed below the zone has broken it, not formed at it
    through = {**lv, "supports": [zone(104.5, 105.5)]}
    assert located(rows, "cs_bullish_engulfing", through)["at_level"] is False


def test_a_bearish_pattern_is_at_a_level_when_its_high_reached_a_resistance_zone():
    rows = advance() + [[96.2, 97.2, 96.1, 97.0], [97.1, 97.3, 95.8, 96.0]]              # high 97.3, close 96.0
    lv = {"available": True, "atr": 1.0, "supports": [zone(96.5, 97.5)],
          "resistances": [zone(99.0, 100.0), zone(97.35, 98.0)], "current_zone": None}
    a = located(rows, "cs_bearish_engulfing", lv)
    assert a["at_level"] is True and a["level"]["label"] == "R2" and a["level"]["side"] == "resistance"
    assert located(rows, "cs_bearish_engulfing", {**lv, "resistances": [zone(99.0, 100.0)]})["at_level"] is False


def test_the_zone_the_close_is_in_is_a_place_for_either_bias_and_the_nearest_zone_wins():
    rows = decline() + [[103.6, 103.7, 102.6, 102.8], [102.7, 104.0, 102.5, 103.9]]
    cur = zone(102.0, 104.5)
    lv = {"available": True, "atr": 1.0, "supports": [zone(101.0, 102.45)], "resistances": [], "current_zone": cur}
    a = located(rows, "cs_bullish_engulfing", lv)
    assert a["level"]["label"] == "Z" and a["level"]["side"] == "current"       # the low is inside it; S1 is 0.05 away
    rows = advance() + [[96.2, 97.2, 96.1, 97.0], [97.1, 97.3, 95.8, 96.0]]
    assert located(rows, "cs_bearish_engulfing", {**lv, "current_zone": zone(95.5, 97.5)})["level"]["label"] == "Z"


def test_a_multi_bar_pattern_uses_the_extreme_of_all_its_bars():
    spec = CS._BY_ID["cs_morning_star"]
    assert spec["bars"] == 3
    rows = decline() + [[103.0, 103.1, 101.4, 101.6], [101.2, 101.4, 100.2, 101.0], [101.3, 102.9, 101.2, 102.8]]
    lv = {"available": True, "atr": 1.0, "supports": [zone(99.5, 100.2)], "resistances": [], "current_zone": None}
    assert located(rows, "cs_morning_star", lv)["at_level"] is True              # the middle bar's low of 100.2
    assert located(rows, "cs_hammer", lv)["at_level"] is False                    # one bar: the last one, low 101.2


def test_without_key_levels_the_place_is_unknown():
    rows = decline() + [[103.6, 103.7, 102.6, 102.8], [102.7, 104.0, 102.5, 103.9]]
    for lv in (None, {"available": False, "reason": "no ATR"}):
        bars, _ = frame(rows)
        cs = {"available": True, "notes": ["n1"], "active": [{"id": "cs_hammer", "fired_date": "2024-01-11"}]}
        out = CS.locate({"bars": bars}, cs, lv)
        assert out["active"] == [{"id": "cs_hammer", "fired_date": "2024-01-11", "at_level": None, "level": None}]
        assert out["notes"] == ["n1", "key levels unavailable: the patterns' place (at_level) is unknown"]
        assert cs["notes"] == ["n1"] and "at_level" not in cs["active"][0]         # the input is not changed
    unavailable = {"available": False, "reason": "short history"}
    assert CS.locate({"bars": None}, unavailable, None) is unavailable


def test_locate_changes_nothing_but_the_place(make_bars):
    import levels as L
    bars = make_bars(n=600, seed=3)
    ctx = {"ticker": "TEST", "as_of": bars.index[-1].strftime("%Y-%m-%d"), "horizon": "swing",
           "hz": C.HORIZONS["swing"], "bars": bars, "ind": I.compute_all(bars), "is_etf": False, "earnings": None,
           "skipped": {}, "notes": []}
    cs = CS.build_candlesticks(ctx)
    out = CS.locate(ctx, cs, L.build_levels(ctx))
    strip = lambda a: {k: v for k, v in a.items() if k not in ("at_level", "level")}  # noqa: E731
    assert [strip(a) for a in out["active"]] == cs["active"]
    assert {k: v for k, v in out.items() if k not in ("active", "at_level_rule")} == \
        {k: v for k, v in cs.items() if k != "active"}
    assert all(isinstance(a["at_level"], bool) and (a["level"] is not None) == a["at_level"] for a in out["active"])
