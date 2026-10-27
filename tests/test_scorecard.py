"""Tests for scorecard.py: pillar scoring, weights, bias steps, confidence, conflicts, scenarios and
risk arithmetic, on hand-built module dicts (no network)."""
import copy
import json
import math
import re

import pytest

import config as C
import scorecard as S

CLOSE, ATR = 100.0, 2.0

LEVELS = {
    "available": True, "last_close": CLOSE, "atr": ATR, "high_52w": 116.5, "low_52w": 89.5,
    "extremes": {"at_high_52w": False, "at_low_52w": False, "at_all_time_high": False, "at_all_time_low": False},
    "supports": [
        {"price": 96.0, "zone_low": 95.0, "zone_high": 96.5, "sources": ["pivot_low", "poc"]},
        {"price": 90.0, "zone_low": 89.5, "zone_high": 90.5, "sources": ["low_52w"]},
    ],
    "resistances": [
        {"price": 110.0, "zone_low": 109.0, "zone_high": 111.0, "sources": ["pivot_high"]},
        {"price": 116.0, "zone_low": 115.5, "zone_high": 116.5, "sources": ["high_52w"]},
    ],
}


def ctx_for(horizon="swing", ticker="TEST", is_etf=False):
    return {"ticker": ticker, "horizon": horizon, "hz": C.HORIZONS[horizon], "is_etf": is_etf,
            "sector_etf": None if is_etf else "XLK"}


def modules_for(s=1):
    """A complete, internally consistent module set: s=+1 bullish, s=-1 bearish."""
    return {
        "regime": {"available": True, "type": "trend", "label": "Strong uptrend / normal vol", "direction_sign": s,
                   "direction": {"close": CLOSE, "sma50": CLOSE - 5 * s, "sma200": CLOSE - 10 * s,
                                 "sma200_slope": 0.02 * s},
                   "volatility": {"label": "normal", "compression": False, "rv20": 0.30, "vol_pctl": 50.0,
                                  "bb_width_pctl": 50.0}},
        "trend_momentum": {"available": True, "close": CLOSE, "mom_12_1": 0.20 * s,
                           "returns": {"1m": 0.02 * s, "3m": 0.06 * s, "6m": 0.12 * s, "12m": 0.25 * s},
                           "sma": {"sma20": CLOSE - 2 * s, "sma50": CLOSE - 5 * s, "sma200": CLOSE - 10 * s,
                                   "slopes": {"sma20": 0.01 * s, "sma50": 0.01 * s, "sma200": 0.02 * s}},
                           "macd": {"macd": 1.0 * s, "signal": 0.6 * s, "hist": 0.4 * s}},
        "relative_strength": {"available": True,
                              "vs_spy": {"available": True, "rel_return_3m": 0.03 * s, "rel_return_6m": 0.05 * s,
                                         "rs_new_high": s > 0, "rs_new_low": s < 0},
                              "vs_sector": {"available": True, "etf": "XLK", "rel_return_3m": 0.01 * s,
                                            "rel_return_6m": 0.02 * s, "rs_new_high": False, "rs_new_low": False}},
        "mean_reversion": {"available": True, "rsi14": 55.0, "rsi2": 50.0, "z20": 0.5, "bb_pct_b": 0.62,
                           "dist_sma50_atr": 1.0 * s},
        "volume_flow": {"available": True, "updown_volume_ratio_50d": 1.0 + 0.4 * s,
                        "obv": {"divergence": "none", "slope_20d": 0.10 * s},
                        "ad": {"divergence": "none", "slope_20d": 0.05 * s},
                        "anchored_vwaps": [{"anchor": a, "price_vs_vwap_pct": 0.03 * s}
                                           for a in ("high_52w", "low_52w", "ytd_start")]
                                          + [{"anchor": "largest_gap_6m", "price_vs_vwap_pct": -0.01 * s}]},
        "volatility": {"available": True, "atr14": ATR, "rv20": 0.30, "bb_width_pctl": 50.0,
                       "squeeze": {"on": False, "bars": 0, "last_release": None}},
        "levels": copy.deepcopy(LEVELS),
        "multi_timeframe": {"available": True, "weekly": {"sign": s, "direction": "Uptrend" if s > 0 else "Downtrend"},
                            "daily_sign": s, "alignment": "confirms", "note": "weekly confirms daily"},
        "event_risk": {"available": True, "within_horizon": False,
                       "next_earnings": {"date": "2026-11-20", "trading_days_est": 24},
                       "reactions": {"n": 12, "median_abs_move": 0.05}},
        "signals": {"available": True, "primary_horizon": 10, "active": []},
        "market_context": {"available": True, "spy": {"available": True, "above_sma200": True, "pct_vs_sma200": 0.05}},
    }


def signal(sid="uptrend_pullback", bias=1, pillar="mean_reversion", conf="normal", n=40,
           edge_median=0.01, edge_hit=0.08, h=10):
    return {"id": sid, "name": sid, "bias": bias, "pillar": pillar, "confidence": conf,
            "horizons": {h: {"n": n, "confidence": conf, "edge": {"median": edge_median, "hit_rate": edge_hit}}}}


def scores(card):
    return {p: v["score"] for p, v in card["pillars"].items()}


# ---------------------------------------------------------------------------- helpers
@pytest.mark.parametrize("x, want", [(0.5, 1), (-0.5, -1), (1.5, 2), (-1.5, -2), (0.49, 0), (1.2, 1), (0.0, 0)])
def test_round_half_away(x, want):
    assert S.round_half_away(x) == want


@pytest.mark.parametrize("score, label", [
    (0.0, "Neutral"), (0.4, "Neutral"), (-0.4, "Neutral"), (0.4001, "Moderately bullish"),
    (-0.4001, "Moderately bearish"), (1.2, "Moderately bullish"), (-1.2, "Moderately bearish"),
    (1.2001, "Bullish"), (-1.2001, "Bearish"), (2.0, "Bullish"), (-2.0, "Bearish"),
])
def test_bias_steps_boundaries_symmetric(score, label):
    got, sign = S.bias_label(score)
    assert got == label
    assert sign == (0 if label == "Neutral" else 1 if "bullish" in label.lower() else -1)


def test_bias_float_noise_at_boundary():
    assert S.bias_label(0.25 + 0.15)[0] == "Neutral"            # 0.4 up to float noise


# ---------------------------------------------------------------------------- weights
@pytest.mark.parametrize("rtype", list(C.REGIME_WEIGHTS))
def test_weights_renormalized(rtype):
    for hz in ("swing", "position"):
        w = S.pillar_weights(rtype, hz)
        assert set(w) == set(C.PILLARS)
        assert sum(w.values()) == pytest.approx(1.0)
        assert min(w.values()) >= 0


def test_weights_regime_and_position_shift():
    trend, rng = S.pillar_weights("trend"), S.pillar_weights("range")
    assert trend["trend"] > rng["trend"] and rng["mean_reversion"] > trend["mean_reversion"]
    pos = S.pillar_weights("trend", "position")
    assert pos["trend"] == pytest.approx(0.30) and pos["mean_reversion"] == pytest.approx(0.0)
    assert S.pillar_weights("nonsense") == S.pillar_weights("transitional")


def test_card_weights_follow_regime_type():
    m = modules_for(1)
    m["regime"]["type"] = "range"
    card = S.build_scorecard(ctx_for(), m)
    assert card["weights"] == pytest.approx(S.pillar_weights("range"))
    assert card["weighted_score"] == pytest.approx(round(sum(card["weights"][p] * s for p, s in scores(card).items()), 4))
    del m["regime"]
    card = S.build_scorecard(ctx_for(), m)
    assert card["weight_basis"]["weights_used"] == "transitional"
    assert any("transitional weights" in n for n in card["notes"])


# ---------------------------------------------------------------------------- pillars
def test_bullish_pillars_and_bias():
    card = S.build_scorecard(ctx_for(), modules_for(1))
    assert scores(card) == {"trend": 2, "momentum": 2, "relative_strength": 2, "volume_flow": 2,
                            "volatility_setup": 0, "mean_reversion": 0}
    assert card["weighted_score"] == pytest.approx(1.7)
    assert card["bias"] == "Bullish" and card["bias_sign"] == 1
    assert card["confidence"]["level"] == "high"
    assert card["confidence"]["agreement"] == pytest.approx(0.85)
    assert card["conflicts"] == []
    trend = card["pillars"]["trend"]
    assert "close above SMA50 (+5.3%)" in trend["reason"] and "net +4 of 4 votes" in trend["reason"]
    assert trend["inputs"]["sma200"] == 90.0


def test_bearish_pillars_mirror():
    card = S.build_scorecard(ctx_for(), modules_for(-1))
    assert scores(card) == {"trend": -2, "momentum": -2, "relative_strength": -2, "volume_flow": -2,
                            "volatility_setup": 0, "mean_reversion": 0}
    assert card["bias"] == "Bearish" and card["confidence"]["level"] == "high"


def test_relative_strength_scaling():
    m = modules_for(1)
    m["relative_strength"]["vs_spy"]["rs_new_high"] = False      # 4 of 6 votes -> 1.33 -> 1
    assert S.build_scorecard(ctx_for(), m)["pillars"]["relative_strength"]["score"] == 1


def test_mixed_overbought_uptrend_conflict():
    m = modules_for(1)
    m["mean_reversion"].update(rsi14=75.0, rsi2=96.0, z20=2.4, bb_pct_b=1.1, dist_sma50_atr=2.5)
    card = S.build_scorecard(ctx_for(), m)
    mr = card["pillars"]["mean_reversion"]
    assert mr["score"] == -2 and mr["raw_score"] == -2
    assert "RSI14 75.0 overbought (-1)" in mr["reason"] and "net -4" in mr["reason"]
    assert mr["votes"] == {"rsi14": -1, "rsi2": -1, "band": -1, "dist_sma50": -1}
    pairs = [c for c in card["conflicts"] if c["type"] == "pillar_pair"]
    assert {tuple(c["pillars"]) for c in pairs} == {(p, "mean_reversion") for p in
                                                    ("trend", "momentum", "relative_strength", "volume_flow")}
    tm = next(c for c in pairs if c["pillars"] == ["trend", "mean_reversion"])
    assert tm["typical_meaning"].startswith("Overbought in an uptrend")


def test_oversold_counts_band_once():
    m = modules_for(-1)
    m["mean_reversion"].update(rsi14=45.0, rsi2=5.0, z20=-2.5, bb_pct_b=-0.12, dist_sma50_atr=-1.0)
    mr = S.build_scorecard(ctx_for(), m)["pillars"]["mean_reversion"]
    assert mr["votes"] == {"rsi14": 0, "rsi2": 1, "band": 1, "dist_sma50": 0}
    assert mr["score"] == 2


def test_missing_everything_scores_zero():
    card = S.build_scorecard(ctx_for(), {})
    for p in C.PILLARS:
        pil = card["pillars"][p]
        assert pil["score"] == 0 and pil["available"] is False
        assert pil["reason"].startswith("insufficient data")
    assert card["weighted_score"] == 0 and card["bias"] == "Neutral"
    assert card["confidence"]["level"] == "low"            # no pillar can agree with anything


def test_unavailable_module_reason_is_reported():
    m = modules_for(1)
    m["relative_strength"] = {"available": False, "reason": "SPY data unavailable"}
    m["volume_flow"] = None
    card = S.build_scorecard(ctx_for(), m)
    assert "SPY data unavailable" in card["pillars"]["relative_strength"]["reason"]
    assert "volume_flow module missing" in card["pillars"]["volume_flow"]["reason"]


def test_partial_inputs_abstain():
    m = modules_for(1)
    del m["regime"]
    m["trend_momentum"]["sma"].update(sma200=None, slopes={})
    trend = S.build_scorecard(ctx_for(), m)["pillars"]["trend"]
    assert trend["votes"] == {"close_vs_sma50": 1, "close_vs_sma200": None, "sma50_vs_sma200": None,
                              "sma200_slope": None}
    assert trend["score"] == 1                              # 2 * 1 / 4 = 0.5 -> 1
    assert "unavailable" in trend["reason"]


def test_position_horizon_adds_weekly_vote():
    m = modules_for(1)
    m["multi_timeframe"]["weekly"]["sign"] = -1
    trend = S.build_scorecard(ctx_for("position"), m)["pillars"]["trend"]
    assert trend["votes"]["weekly_direction"] == -1
    assert trend["score"] == 1                              # net 3 of 5 -> 1.2 -> 1
    assert "weekly_direction" not in S.build_scorecard(ctx_for("swing"), m)["pillars"]["trend"]["votes"]


def test_rs_not_applicable_for_spy_and_etf():
    m = modules_for(1)
    m["relative_strength"]["vs_spy"] = {"available": False, "reason": "SPY is the benchmark"}
    rs = S.build_scorecard(ctx_for(ticker="SPY", is_etf=True), m)["pillars"]["relative_strength"]
    assert rs["score"] == 0 and rs["reason"].startswith("not applicable")
    m = modules_for(1)
    rs = S.build_scorecard(ctx_for(ticker="QQQ", is_etf=True), m)["pillars"]["relative_strength"]
    assert set(rs["votes"]) == {"spy_3m", "spy_6m", "spy_extreme"} and rs["score"] == 2


@pytest.mark.parametrize("sq, vol, direction, want, phrase", [
    ({"on": True, "bars": 7, "last_release": None}, {}, 1, 1, "uptrend (+1)"),
    ({"on": True, "bars": 7, "last_release": None}, {"compression": True}, -1, -1, "downtrend (-1)"),
    ({"on": False, "bars": 0, "last_release": {"date": "2026-10-16", "bars_ago": 1, "direction": "up"}},
     {"compression": True}, 1, 2, "released 1 bar(s) ago"),
    ({"on": False, "bars": 0, "last_release": {"date": "2026-09-01", "bars_ago": 30, "direction": "up"}},
     {}, 1, 0, "no recent squeeze release"),
    ({"on": False, "bars": 0, "last_release": None}, {"label": "extreme", "vol_pctl": 97.0}, -1, -1, "extreme vol"),
    ({"on": False, "bars": 0, "last_release": None}, {"label": "extreme", "vol_pctl": 97.0}, 1, 0, "fragile"),
    ({"on": True, "bars": 9, "last_release": None}, {}, 0, 0, "break direction unknown"),
])
def test_volatility_setup(sq, vol, direction, want, phrase):
    m = modules_for(1)
    m["volatility"]["squeeze"] = sq
    m["regime"]["volatility"].update(vol)
    m["regime"]["direction_sign"] = direction
    pil = S.build_scorecard(ctx_for(), m)["pillars"]["volatility_setup"]
    assert pil["score"] == want
    assert phrase in pil["reason"]


def test_volume_flow_divergence_and_vwaps():
    m = modules_for(1)
    m["volume_flow"].update(updown_volume_ratio_50d=1.0)
    m["volume_flow"]["obv"]["divergence"] = "bearish"
    m["volume_flow"]["obv"]["detail"] = "2026-10-16: close made a new 20-bar closing high"
    m["volume_flow"]["anchored_vwaps"] = [{"price_vs_vwap_pct": -0.02}] * 3 + [{"price_vs_vwap_pct": 0.01}]
    card = S.build_scorecard(ctx_for(), m)
    vf = card["pillars"]["volume_flow"]
    assert vf["votes"] == {"updown_volume": 0, "divergence": -1, "flow_slope": 1, "avwap": -1}
    assert vf["score"] == -1                                # net -1 of 4 -> -0.5 -> -1
    div = [c for c in card["conflicts"] if c["type"] == "price_flow_divergence"]
    assert len(div) == 1 and "OBV" in div[0]["involved"]
    assert "Participation is not confirming" in div[0]["typical_meaning"]


# ---------------------------------------------------------------------------- signal nudges
@pytest.mark.parametrize("h_key", [10, "10"])
def test_signal_nudge_applied(h_key):
    m = modules_for(1)
    sig = signal()
    sig["horizons"] = {h_key: sig["horizons"][10]}
    m["signals"]["active"] = [sig]
    card = S.build_scorecard(ctx_for(), m)
    mr = card["pillars"]["mean_reversion"]
    assert mr["raw_score"] == 0 and mr["score"] == 1
    assert "signal nudge +1 from uptrend_pullback" in mr["reason"]
    assert card["signal_nudges"][0]["id"] == "uptrend_pullback"


@pytest.mark.parametrize("kw", [
    {"conf": "insufficient", "n": 9},                     # sample too small
    {"edge_median": 0.002},                               # median edge below C.SIGNAL_EDGE_MIN
    {"edge_hit": 0.02},                                   # hit-rate edge below C.SIGNAL_HIT_EDGE_MIN
    {"edge_median": -0.02, "edge_hit": -0.10},           # edge contradicts the pre-registered bias
])
def test_signal_nudge_not_applied(kw):
    m = modules_for(1)
    m["signals"]["active"] = [signal(**kw)]
    card = S.build_scorecard(ctx_for(), m)
    assert card["pillars"]["mean_reversion"]["score"] == 0 and card["signal_nudges"] == []


def test_signal_nudges_capped_and_bounded():
    m = modules_for(1)
    m["signals"]["active"] = [signal("breakout_55d", pillar="momentum"), signal("new_52w_high", pillar="momentum"),
                              signal("golden_cross", pillar="trend")]
    card = S.build_scorecard(ctx_for(), m)
    assert card["pillars"]["momentum"]["score"] == 2          # already +2: stays inside -2..+2
    m["trend_momentum"]["mom_12_1"] = -0.1                    # momentum raw: net +3 of 5 -> 1.2 -> 1
    card = S.build_scorecard(ctx_for(), m)
    mom = card["pillars"]["momentum"]
    assert mom["raw_score"] == 1 and mom["score"] == 2        # two nudges, net capped at +1
    assert "capped at +1" in mom["reason"]


def test_bearish_signal_nudges_down():
    m = modules_for(-1)
    m["signals"]["active"] = [signal("death_cross", bias=-1, pillar="trend", edge_median=-0.02, edge_hit=-0.1)]
    m["trend_momentum"]["sma"]["slopes"]["sma200"] = 0.01     # trend raw: 3 of 4 bearish -> -1
    m["regime"]["direction"]["sma200_slope"] = 0.01
    trend = S.build_scorecard(ctx_for(), m)["pillars"]["trend"]
    assert trend["raw_score"] == -1 and trend["score"] == -2


# ---------------------------------------------------------------------------- confidence
def test_confidence_low_when_weekly_contradicts():
    m = modules_for(1)
    m["multi_timeframe"].update(weekly={"sign": -1}, alignment="contradicts")
    conf = S.build_scorecard(ctx_for(), m)["confidence"]
    assert conf["level"] == "low" and conf["mtf_alignment"] == "contradicts"


def test_confidence_medium_when_weekly_mixed():
    m = modules_for(1)
    m["multi_timeframe"].update(weekly={"sign": 0}, alignment="mixed")
    conf = S.build_scorecard(ctx_for(), m)["confidence"]
    assert conf["level"] == "medium" and conf["mtf_alignment"] == "mixed"


def test_confidence_medium_when_signals_insufficient_only():
    m = modules_for(1)
    m["signals"]["active"] = [signal(conf="insufficient", n=8)]
    conf = S.build_scorecard(ctx_for(), m)["confidence"]
    assert conf["level"] == "medium" and conf["signal_support"]["insufficient_only"] is True


def test_confidence_medium_with_opposing_backed_signal():
    m = modules_for(1)
    m["signals"]["active"] = [signal("extension_2sigma", bias=-1, edge_median=-0.01, edge_hit=-0.08)]
    conf = S.build_scorecard(ctx_for(), m)["confidence"]
    assert conf["signal_support"]["opposing"] == ["extension_2sigma"] and conf["level"] == "medium"


def test_confidence_low_on_poor_agreement():
    m = modules_for(1)
    for k in ("trend_momentum", "relative_strength", "volume_flow"):
        m[k] = None
    card = S.build_scorecard(ctx_for(), m)
    assert card["bias"] == "Moderately bullish"               # trend +2 x 0.25 = 0.5
    assert card["confidence"]["agreement"] == pytest.approx(0.25) and card["confidence"]["level"] == "low"


def flat_modules():
    """Every input sits exactly on its neutral point."""
    m = modules_for(1)
    m["regime"]["direction_sign"] = 0
    m["regime"]["direction"].update(sma50=CLOSE, sma200=CLOSE, sma200_slope=0.0)
    m["trend_momentum"].update(mom_12_1=0.0, returns={"3m": 0.0, "6m": 0.0})
    m["trend_momentum"]["sma"].update(sma50=CLOSE, sma200=CLOSE, slopes={"sma200": 0.0})
    m["trend_momentum"]["macd"].update(macd=0.0, hist=0.0)
    for b in ("vs_spy", "vs_sector"):
        m["relative_strength"][b].update(rel_return_3m=0.0, rel_return_6m=0.0, rs_new_high=False, rs_new_low=False)
    m["volume_flow"].update(updown_volume_ratio_50d=1.0,
                            anchored_vwaps=[{"price_vs_vwap_pct": 0.01}, {"price_vs_vwap_pct": -0.01}])
    m["volume_flow"]["obv"]["slope_20d"] = m["volume_flow"]["ad"]["slope_20d"] = 0.0
    m["multi_timeframe"].update(weekly={"sign": 0}, alignment="mixed")
    return m


def test_confidence_neutral_flat_is_high():
    card = S.build_scorecard(ctx_for(), flat_modules())
    assert set(scores(card).values()) == {0} and card["bias"] == "Neutral"
    assert card["confidence"]["agreement"] == pytest.approx(1.0)
    assert card["confidence"]["mtf_alignment"] == "confirms" and card["confidence"]["level"] == "high"


def test_confidence_neutral_ignores_missing_pillars():
    m = flat_modules()
    m["trend_momentum"] = m["relative_strength"] = m["volume_flow"] = None
    card = S.build_scorecard(ctx_for(), m)
    assert card["bias"] == "Neutral"
    # pillars without data never count as agreeing with a neutral bias
    assert card["confidence"]["agreement"] == pytest.approx(0.25 + 0.10 + 0.05)
    assert card["confidence"]["level"] == "low"


def test_neutral_from_offsetting_pillars_is_low_confidence():
    m = modules_for(1)
    m["trend_momentum"] = m["volume_flow"] = None               # trend +2 (regime direction) remains
    for b in ("vs_spy", "vs_sector"):
        m["relative_strength"][b].update(rel_return_3m=-0.05, rel_return_6m=-0.05, rs_new_high=False, rs_new_low=True)
    card = S.build_scorecard(ctx_for(), m)
    assert card["pillars"]["relative_strength"]["score"] == -2
    assert card["weighted_score"] == pytest.approx(0.25 * 2 - 0.20 * 2) and card["bias"] == "Neutral"
    assert card["confidence"]["level"] == "low"


# ---------------------------------------------------------------------------- conflicts
def test_specific_conflict_patterns():
    m = modules_for(1)
    m["multi_timeframe"].update(weekly={"sign": -1}, alignment="contradicts", note="Weekly downtrend vs daily uptrend")
    m["market_context"]["spy"].update(above_sma200=False, pct_vs_sma200=-0.04)
    m["event_risk"].update(within_horizon=True, next_earnings={"date": "2026-10-28", "trading_days_est": 8})
    types = {c["type"]: c for c in S.build_scorecard(ctx_for(), m)["conflicts"]}
    assert {"daily_vs_weekly", "rs_vs_market", "earnings_in_horizon"} <= set(types)
    assert "counter-trend move" in types["daily_vs_weekly"]["typical_meaning"]
    assert "risk-off tape" in types["rs_vs_market"]["typical_meaning"]
    assert "2026-10-28" in types["earnings_in_horizon"]["description"]
    assert "5.0%" in types["earnings_in_horizon"]["description"]
    for c in types.values():
        assert c["description"] and c["typical_meaning"] and c["involved"]


def test_squeeze_without_direction_conflict():
    m = modules_for(1)
    m["volatility"]["squeeze"] = {"on": True, "bars": 9, "last_release": None}
    m["regime"]["direction_sign"] = 0
    conflicts = S.build_scorecard(ctx_for(), m)["conflicts"]
    sq = [c for c in conflicts if c["type"] == "squeeze_no_direction"]
    assert sq and "Break direction unknown" in sq[0]["typical_meaning"] and "9 bars" in sq[0]["description"]


def test_bullish_signals_in_bearish_regime_conflict():
    m = modules_for(-1)
    m["signals"]["active"] = [signal("downtrend_oversold", conf="low", n=20),
                              signal("death_cross", bias=-1, pillar="trend")]
    conflicts = S.build_scorecard(ctx_for(), m)["conflicts"]
    vs_regime = [c for c in conflicts if c["type"] == "signals_vs_regime"]
    assert len(vs_regime) == 1 and vs_regime[0]["signals"] == ["downtrend_oversold"]
    assert any(c["type"] == "signals_both_ways" for c in conflicts)


# ---------------------------------------------------------------------------- scenarios
def test_scenarios_with_levels():
    m = modules_for(1)
    card = S.build_scorecard(ctx_for(), m)
    sc = S.build_scenarios(ctx_for(), m, card)
    assert sc["available"] and sc["horizon_days"] == 10 and sc["leaning"] == "bull"
    bull, bear = sc["bull"], sc["bear"]
    assert bull["trigger"]["level"] == 111.0 and bull["trigger"]["distance_atr"] == pytest.approx(5.5)
    assert bull["trigger"]["condition"] == "close above" and bull["trigger"]["volume"] == "RVOL >= 1.5"
    assert bull["invalidation"]["level"] == 109.0               # zone low beats trigger - 0.5 ATR (110)
    assert [t["price"] for t in bull["targets"]] == [116.0]
    t = bull["targets"][0]                                     # level price +- 0.25 ATR
    assert (t["zone_low"], t["zone_high"]) == pytest.approx((115.5, 116.5))
    assert (t["level_zone_low"], t["level_zone_high"]) == (115.5, 116.5) and t["sources"] == ["high_52w"]
    assert bear["trigger"]["level"] == 95.0 and bear["trigger"]["condition"] == "close below"
    assert bear["invalidation"]["level"] == 96.5                # zone high beats trigger + 0.5 ATR (96)
    assert [t["price"] for t in bear["targets"]] == [90.0]
    assert (bear["targets"][0]["zone_low"], bear["targets"][0]["zone_high"]) == pytest.approx((89.5, 90.5))
    assert bull["text"] == ("If TEST closes above 111.00 (top of the nearest resistance zone: pivot_high) on "
                            "RVOL >= 1.5, the next zones are 115.50-116.50; the scenario is invalidated by a close "
                            "back below 109.00.")
    assert bear["text"].startswith("If TEST closes below 95.00 (bottom of the nearest support zone: pivot_low, poc)")


def test_base_case_expected_move():
    m = modules_for(1)
    base = S.build_scenarios(ctx_for(), m, None)["base"]
    sd = 0.30 * math.sqrt(10 / 252)
    assert base["expected_move_1sd_pct"] == pytest.approx(sd)
    assert base["expected_move_1sd_price"] == pytest.approx(CLOSE * sd)
    assert base["expected_move_atr"] == pytest.approx(CLOSE * sd / ATR)
    assert (base["range_low"], base["range_high"]) == pytest.approx((CLOSE * (1 - sd), CLOSE * (1 + sd)))
    assert base["band_95_k"] == 2.4 and base["expected_move_95_pct"] == pytest.approx(2.4 * sd)
    assert (base["range_95_low"], base["range_95_high"]) == pytest.approx((CLOSE * (1 - 2.4 * sd), CLOSE * (1 + 2.4 * sd)))
    assert base["coverage"] == {"horizon_days": 10, "within_1sd": 0.682, "within_2sd": 0.926, "within_95_band": 0.958,
                                "basis": C.VOL_CALIBRATION}
    assert "68.2%" in base["text"] and "+-2.4 sigma" in base["text"] and "92.6%" in base["method"]
    assert base["earnings_caveat"] is None
    pos = S.build_scenarios(ctx_for("position"), m, None)
    assert pos["horizon_days"] == 60
    assert pos["base"]["expected_move_1sd_pct"] == pytest.approx(0.30 * math.sqrt(60 / 252))
    assert pos["base"]["coverage"]["horizon_days"] == 60 and pos["base"]["coverage"]["within_95_band"] == 0.949


def test_base_case_earnings_caveat():
    m = modules_for(1)
    m["event_risk"].update(within_horizon=True, next_earnings={"date": "2026-10-28", "trading_days_est": 8})
    base = S.build_scenarios(ctx_for(), m, None)["base"]
    assert "2026-10-28" in base["earnings_caveat"] and "5.0%" in base["earnings_caveat"]
    assert base["earnings_caveat"] in base["text"]


def test_scenarios_at_all_time_high():
    m = modules_for(1)
    m["levels"].update(resistances=[], high_52w=CLOSE,
                       extremes={"at_high_52w": True, "at_low_52w": False, "at_all_time_high": True,
                                 "at_all_time_low": False})
    bull = S.build_scenarios(ctx_for(), m, None)["bull"]
    assert bull["trigger"]["level"] == pytest.approx(CLOSE + ATR)
    assert "last close + 1 ATR" in bull["trigger"]["basis"] and "all-time high" in bull["trigger"]["basis"]
    assert [t["price"] for t in bull["targets"]] == pytest.approx([104.0, 106.0])
    assert all(t["kind"] == "projection" and "no overhead level" in t["label"] for t in bull["targets"])
    assert bull["invalidation"]["level"] == pytest.approx(101.0)


def test_scenarios_no_resistance_below_52w_high():
    m = modules_for(1)
    m["levels"].update(resistances=[], high_52w=103.0)
    bull = S.build_scenarios(ctx_for(), m, None)["bull"]
    assert bull["trigger"]["level"] == 103.0 and bull["trigger"]["basis"].startswith("52-week high")
    assert [t["price"] for t in bull["targets"]] == pytest.approx([105.0, 107.0])


def test_scenarios_without_levels_module():
    m = modules_for(1)
    m["levels"] = {"available": False, "reason": "close or ATR unavailable"}
    sc = S.build_scenarios(ctx_for(), m, None)
    assert sc["available"] and any("levels unavailable" in n for n in sc["notes"])
    assert not any("reference only" in n for n in sc["notes"])
    assert any("reference only" in n for n in S.build_scenarios(ctx_for(), modules_for(1), None)["notes"])
    assert sc["bear"]["trigger"]["level"] == pytest.approx(CLOSE - ATR)


def test_scenarios_unavailable_without_atr():
    m = modules_for(1)
    m["volatility"]["atr14"] = None
    m["levels"]["atr"] = None
    assert S.build_scenarios(ctx_for(), m, None)["available"] is False
    assert S.build_risk(ctx_for(), m, None)["available"] is False


def test_no_advice_words():
    m = modules_for(1)
    m["event_risk"]["within_horizon"] = True
    card = S.build_scorecard(ctx_for(), m)
    sc = S.build_scenarios(ctx_for(), m, card)
    texts = json.dumps([card, sc, S.build_risk(ctx_for(), m, sc)])
    assert not re.search(r"\b(buy|sell|hold)\b", texts, flags=re.IGNORECASE)


# ---------------------------------------------------------------------------- risk
def test_risk_arithmetic_exact():
    m = modules_for(1)
    sc = S.build_scenarios(ctx_for(), m, None)
    risk = S.build_risk(ctx_for(), m, sc)
    lg, sh = risk["long_side"], risk["short_side"]
    sd = 0.30 * math.sqrt(10 / 252)
    assert lg["entry_ref"] == 100.0 and lg["sigma_h_pct"] == pytest.approx(sd) and risk["horizon_days"] == 10
    assert lg["vol_stop"] == pytest.approx(100 * (1 - 2.4 * sd)) and lg["vol_target"] == pytest.approx(100 * (1 + sd))
    assert lg["structural_stop"] == pytest.approx(94.0) and lg["atr_stop"] == pytest.approx(96.0)
    assert "reference only" in lg["structural_stop_basis"] and "95% band" in lg["vol_stop_basis"]
    assert lg["stop_distance_atr"] == pytest.approx({"vol": 120 * sd, "structural": 3.0, "atr": 2.0})
    assert lg["stop_distance_pct"] == pytest.approx({"vol": 2.4 * sd, "structural": 0.06, "atr": 0.04})
    assert lg["stop_distance_sigma"] == pytest.approx({"vol": 2.4, "structural": 0.06 / sd, "atr": 0.04 / sd})
    assert lg["first_target"] == pytest.approx(100 * (1 + sd)) and "1-sigma band edge" in lg["first_target_basis"]
    assert lg["first_target_sigma"] == pytest.approx(1.0)
    assert lg["level_target"] == 110.0 and lg["level_target_basis"] == "nearest resistance (reference only)"
    assert lg["reward_risk"] == pytest.approx({"vol": 1 / 2.4, "structural": 100 * sd / 6, "atr": 100 * sd / 4})
    assert lg["stop_touch_rate"]["vol"] == pytest.approx(C.VOL_TOUCH[10][2.4])
    assert C.VOL_TOUCH[10][1.5] < lg["stop_touch_rate"]["atr"] < C.VOL_TOUCH[10][0.5]
    assert lg["first_target_touch_rate"] == pytest.approx(C.VOL_TOUCH[10][1.0])
    assert lg["vol_pair_odds"] == {"target_first": 0.314, "stop_first": 0.041, "neither": 0.644, "horizon_days": 10}
    ex = lg["position_size_example"]
    assert (ex["account"], ex["risk_pct"], ex["risk_dollars"]) == (100_000, 0.01, 1000.0)
    assert ex["stop_basis"] == "vol" and ex["shares"] == 69 and ex["notional"] == pytest.approx(6_900.0)
    assert ex["notional_pct_account"] == pytest.approx(0.069) and ex["exceeds_account"] is False
    assert ex["by_stop"]["structural"]["shares"] == 166 and ex["by_stop"]["atr"]["shares"] == 250
    assert ex["by_stop"]["atr"]["notional"] == pytest.approx(25_000.0)

    assert sh["vol_stop"] == pytest.approx(100 * (1 + 2.4 * sd)) and sh["first_target"] == pytest.approx(100 * (1 - sd))
    assert sh["structural_stop"] == pytest.approx(112.0) and sh["atr_stop"] == pytest.approx(104.0)
    assert sh["level_target"] == 96.0
    assert sh["reward_risk"] == pytest.approx({"vol": 1 / 2.4, "structural": 100 * sd / 12, "atr": 100 * sd / 4})
    assert sh["position_size_example"]["shares"] == 69
    assert sh["position_size_example"]["by_stop"]["structural"]["shares"] == 83
    assert risk["label"] == S.ILLUSTRATIVE and C.DISCLAIMER in risk["notes"]
    assert any("reference only" in n for n in risk["notes"])


def test_touch_rate_interpolates_the_calibration():
    row = C.VOL_TOUCH[10]
    assert S.touch_rate(1.0, 10) == pytest.approx(row[1.0]) and S.touch_rate(2.4, 10) == pytest.approx(row[2.4])
    mid = S.touch_rate(1.25, 10)
    assert mid == pytest.approx(math.sqrt(row[1.0] * row[1.5]))
    assert S.touch_rate(0.1, 10) is None and S.touch_rate(5.0, 10) is None and S.touch_rate(None, 10) is None
    assert S.touch_rate(1.0, 12) == pytest.approx(row[1.0]) and S.touch_rate(1.0, 45) == pytest.approx(C.VOL_TOUCH[60][1.0])


def test_risk_exact_multiple_is_not_floored_down():
    m = modules_for(1)
    m["levels"]["supports"] = [{"price": 95.4, "zone_low": 95.3, "zone_high": 95.5, "sources": ["pivot_low"]}]
    m["volatility"]["atr14"] = 0.6                          # stop = 95.3 - 0.3 = 95.0 -> 5.0 per share
    ex = S.build_risk(ctx_for(), m, None)["long_side"]["position_size_example"]
    assert ex["by_stop"]["structural"]["shares"] == 200


def test_risk_flags_notional_above_account():
    m = modules_for(1)
    m["volatility"]["atr14"] = 0.1
    m["levels"]["supports"] = [{"price": 99.95, "zone_low": 99.9, "zone_high": 99.95, "sources": ["poc"]}]
    ex = S.build_risk(ctx_for(), m, None)["long_side"]["position_size_example"]["by_stop"]["structural"]
    assert ex["risk_per_share"] == pytest.approx(0.15) and ex["shares"] == 6666
    assert ex["exceeds_account"] is True and ex["flag"] == "illustrative only; would exceed the account"


def test_risk_without_support_or_volatility():
    m = modules_for(1)
    m["levels"].update(supports=[], resistances=[], high_52w=CLOSE)
    sc = S.build_scenarios(ctx_for(), m, None)
    lg = S.build_risk(ctx_for(), m, sc)["long_side"]
    assert lg["structural_stop"] is None and lg["reward_risk"]["structural"] is None
    assert lg["position_size_example"]["stop_basis"] == "vol"
    assert lg["level_target"] == pytest.approx(104.0) and "projection" in lg["level_target_basis"]
    assert any("no structural stop" in n for n in lg["notes"])
    m["volatility"]["rv20"] = m["regime"]["volatility"]["rv20"] = None
    lg = S.build_risk(ctx_for(), m, sc)["long_side"]
    assert lg["vol_stop"] is None and lg["vol_pair_odds"] is None and lg["stop_touch_rate"]["atr"] is None
    assert lg["position_size_example"]["stop_basis"] == "atr" and lg["position_size_example"]["shares"] == 250
    assert lg["first_target"] == pytest.approx(104.0) and "projection" in lg["first_target_basis"]
    assert any("no volatility stop" in n for n in lg["notes"])


def test_bands_that_reach_zero():
    m = modules_for(1)
    m["volatility"]["rv20"] = 2.0                            # 60 days: sigma_h = 0.98, 2.4 sigma > 100%
    sc = S.build_scenarios(ctx_for("position"), m, None)
    assert sc["base"]["range_95_low"] is None and any("reaches zero" in n for n in sc["notes"])
    assert "zero to" in sc["base"]["text"]
    lg = S.build_risk(ctx_for("position"), m, sc)["long_side"]
    assert lg["vol_stop"] is None and lg["position_size_example"]["stop_basis"] == "atr"
    assert any("volatility stop would be at or below zero" in n for n in lg["notes"])


# ---------------------------------------------------------------------------- end to end on synthetic bars
def test_pipeline_on_synthetic_bars():
    I = pytest.importorskip("indicators")
    R = pytest.importorskip("regime")
    M = pytest.importorskip("modules")
    L = pytest.importorskip("levels")
    from conftest import synthetic_bars

    bars, spy = synthetic_bars(n=700, seed=3), synthetic_bars(n=700, seed=11)
    ctx = {**ctx_for(), "as_of": bars.index[-1].strftime("%Y-%m-%d"), "bars": bars, "ind": I.compute_all(bars),
           "spy": spy, "sector": None, "vix": None, "profile": None, "quote": None, "earnings": None,
           "data_quality": {}, "notes": [], "skipped": {}}
    modules = {
        "market_context": R.build_market_context(ctx), "regime": R.build_regime(ctx),
        "multi_timeframe": R.build_multi_timeframe(ctx), "trend_momentum": M.build_trend_momentum(ctx),
        "relative_strength": M.build_relative_strength(ctx), "mean_reversion": M.build_mean_reversion(ctx),
        "volume_flow": M.build_volume_flow(ctx), "volatility": M.build_volatility(ctx),
        "event_risk": M.build_event_risk(ctx), "levels": L.build_levels(ctx),
    }
    try:
        import signal_stats
        modules["signals"] = signal_stats.build_signal_stats(ctx)
    except ImportError:
        modules["signals"] = None
    card = S.build_scorecard(ctx, modules)
    sc = S.build_scenarios(ctx, modules, card)
    risk = S.build_risk(ctx, modules, sc)
    assert set(card["pillars"]) == set(C.PILLARS)
    assert all(-2 <= p["score"] <= 2 and p["reason"] for p in card["pillars"].values())
    assert card["confidence"]["level"] in ("high", "medium", "low")
    assert "insufficient" in card["pillars"]["relative_strength"]["reason"] or \
        card["pillars"]["relative_strength"]["available"]
    assert sc["available"] and risk["available"]
    assert sc["bull"]["trigger"]["level"] > bars["close"].iloc[-1] > sc["bear"]["trigger"]["level"]
    json.dumps([card, sc, risk])                               # plain Python types only


def test_risk_flags_a_stop_wider_than_the_risk_budget():
    m = modules_for(1)
    big = 50_000.0
    m["levels"].update(supports=[{"price": big * 0.95, "zone_low": big * 0.95, "zone_high": big * 0.95,
                                  "sources": ["pivot_low"]}], resistances=[], last_close=big)
    m["volatility"]["atr14"] = 1_000.0
    m["trend_momentum"]["close"] = m["regime"]["direction"]["close"] = big
    lg = S.build_risk(ctx_for(), m, None)["long_side"]
    ex = lg["position_size_example"]
    # structural stop = 47,500 - 500 = 47,000 -> 3,000 per share > the $1,000 budget (the vol stop is wider still)
    st = ex["by_stop"]["structural"]
    assert ex["shares"] == 0 and st["shares"] == 0 and st["fractional_shares"] == pytest.approx(1 / 3)
    assert "exceeds the $1,000 risk budget" in st["flag"] and "0.33 shares" in st["flag"]
    assert any("no whole-share position fits" in n for n in lg["notes"])


@pytest.mark.parametrize("on, compression, phrase, missing", [
    (False, None, "no squeeze (compression unknown", "compression (BB-width percentile)"),
    (None, False, "no compression (squeeze state unknown)", "squeeze state"),
    (False, False, "no squeeze or compression", None),
])
def test_volatility_setup_says_when_squeeze_or_compression_is_unknown(on, compression, phrase, missing):
    m = modules_for(1)
    m["volatility"]["squeeze"] = {"on": on, "bars": 0, "last_release": None}
    m["volatility"]["bb_width_pctl"] = None
    m["regime"]["volatility"].update(compression=compression, bb_width_pctl=None)
    pil = S.build_scorecard(ctx_for(), m)["pillars"]["volatility_setup"]
    assert pil["score"] == 0 and phrase in pil["reason"]
    if missing:
        assert f"unavailable: {missing}" in pil["reason"]
    else:
        assert "unavailable" not in pil["reason"]
