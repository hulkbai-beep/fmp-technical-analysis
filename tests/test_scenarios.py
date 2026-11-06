"""Tests for scenarios.py: conflicts, scenarios and risk arithmetic, on hand-built module dicts (no network)."""
import copy
import json
import math
import re

import pytest

import config as C
import scenarios as S

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


def signal(sid="uptrend_pullback", bias=1, conf="normal", n=40, edge_median=0.01, edge_hit=0.08, h=10):
    return {"id": sid, "name": sid, "bias": bias, "confidence": conf,
            "horizons": {h: {"n": n, "confidence": conf, "edge": {"median": edge_median, "hit_rate": edge_hit}}}}


def conflicts(m, horizon="swing"):
    return S.build_conflicts(ctx_for(horizon), m)["items"]


SCORE_KEY = re.compile(r"score|pillar|weight|rating|grade|bias_sign|confidence|agreement|leaning", re.IGNORECASE)


def all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_keys(v)


# ---------------------------------------------------------------------------- conflicts
def test_specific_conflict_patterns():
    m = modules_for(1)
    m["multi_timeframe"].update(weekly={"sign": -1}, alignment="contradicts", note="Weekly downtrend vs daily uptrend")
    m["market_context"]["spy"].update(above_sma200=False, pct_vs_sma200=-0.04)
    m["event_risk"].update(within_horizon=True, next_earnings={"date": "2026-10-28", "trading_days_est": 8})
    types = {c["type"]: c for c in conflicts(m)}
    assert {"daily_vs_weekly", "rs_vs_market", "earnings_in_horizon"} <= set(types)
    assert "counter-trend move" in types["daily_vs_weekly"]["typical_meaning"]
    assert "risk-off tape" in types["rs_vs_market"]["typical_meaning"]
    assert "2026-10-28" in types["earnings_in_horizon"]["description"]
    assert "5.0%" in types["earnings_in_horizon"]["description"]
    for c in types.values():
        assert c["description"] and c["typical_meaning"] and c["involved"]


def test_rs_vs_market_uses_relative_returns():
    m = modules_for(1)
    m["market_context"]["spy"].update(above_sma200=False, pct_vs_sma200=-0.04)
    (c,) = [c for c in conflicts(m) if c["type"] == "rs_vs_market"]
    assert "outperforming SPY over 3m (+3.0%) and 6m (+5.0%)" in c["description"] and "(-4.0%)" in c["description"]
    m["relative_strength"]["vs_spy"]["rel_return_6m"] = -0.01             # not outperforming on both windows
    assert not any(c["type"] == "rs_vs_market" for c in conflicts(m))


def test_conflicts_scenarios_and_risk_carry_no_scores():
    m = modules_for(1)
    m["signals"]["active"] = [signal()]
    m["event_risk"]["within_horizon"] = True
    m["market_context"]["spy"].update(above_sma200=False)
    m["mean_reversion"].update(rsi14=75.0, rsi2=96.0, z20=2.4)
    cf = S.build_conflicts(ctx_for(), m)
    sc = S.build_scenarios(ctx_for(), m)
    assert cf["items"] and not [k for k in all_keys([cf, sc, S.build_risk(ctx_for(), m, sc)]) if SCORE_KEY.search(k)]
    assert not any(c["type"] == "pillar_pair" for c in cf["items"])


def test_squeeze_without_direction_conflict():
    m = modules_for(1)
    m["volatility"]["squeeze"] = {"on": True, "bars": 9, "last_release": None}
    m["regime"]["direction_sign"] = 0
    sq = [c for c in conflicts(m) if c["type"] == "squeeze_no_direction"]
    assert sq and "Break direction unknown" in sq[0]["typical_meaning"] and "9 bars" in sq[0]["description"]


def test_bullish_signals_in_bearish_regime_conflict():
    m = modules_for(-1)
    m["signals"]["active"] = [signal("downtrend_oversold", conf="low", n=20),
                              signal("death_cross", bias=-1)]
    found = conflicts(m)
    vs_regime = [c for c in found if c["type"] == "signals_vs_regime"]
    assert len(vs_regime) == 1 and vs_regime[0]["signals"] == ["downtrend_oversold"]
    assert any(c["type"] == "signals_both_ways" for c in found)


# ---------------------------------------------------------------------------- scenarios
def test_scenarios_with_levels():
    m = modules_for(1)
    sc = S.build_scenarios(ctx_for(), m)
    assert sc["available"] and sc["horizon_days"] == 10
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
    base = S.build_scenarios(ctx_for(), m)["base"]
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
    pos = S.build_scenarios(ctx_for("position"), m)
    assert pos["horizon_days"] == 60
    assert pos["base"]["expected_move_1sd_pct"] == pytest.approx(0.30 * math.sqrt(60 / 252))
    assert pos["base"]["coverage"]["horizon_days"] == 60 and pos["base"]["coverage"]["within_95_band"] == 0.949


def test_base_case_earnings_caveat():
    m = modules_for(1)
    m["event_risk"].update(within_horizon=True, next_earnings={"date": "2026-10-28", "trading_days_est": 8})
    base = S.build_scenarios(ctx_for(), m)["base"]
    assert "2026-10-28" in base["earnings_caveat"] and "5.0%" in base["earnings_caveat"]
    assert base["earnings_caveat"] in base["text"]


def test_earnings_caveat_and_meaning_follow_the_numbers():
    # NVDA 2026-11-04: median two-day move 3.7% against a 10-day 1-sigma of 6.7%, 4 of 12 beyond it; the fixed
    # text said "so the realized range can be much wider" and "reactions often exceed the 1-sigma range"
    m = modules_for(1)
    moves = [-0.034, 0.131, 0.088, -0.083, -0.002, -0.051, 0.027, -0.009, -0.004, -0.041, -0.005, 0.070]
    m["event_risk"].update(within_horizon=True, next_earnings={"date": "2026-11-17", "trading_days_est": 9},
                           reactions={"n": 12, "median_abs_move": 0.0374,
                                      "items": [{"date": "x", "move": v} for v in moves]})
    sd = 0.30 * math.sqrt(10 / 252)                                   # 5.98%
    caveat = S.build_scenarios(ctx_for(), m)["base"]["earnings_caveat"]
    assert caveat == ("Earnings on 2026-11-17 fall inside the 10-day horizon and add a jump to the ordinary range "
                      "the band describes: over the last 12 reports the median absolute two-day move was 3.7%, "
                      f"and 4 of 12 moved more than the 10-day 1-sigma move of {sd * 100:.1f}% (the largest 13.1%).")
    assert "much wider" not in caveat
    (c,) = [c for c in conflicts(m) if c["type"] == "earnings_in_horizon"]
    assert "often exceed" not in c["typical_meaning"] and "adds a jump to the ordinary range" in c["typical_meaning"]
    m["event_risk"]["reactions"] = {"n": 0, "median_abs_move": None}
    assert S.build_scenarios(ctx_for(), m)["base"]["earnings_caveat"] == (
        "Earnings on 2026-11-17 fall inside the 10-day horizon and add a jump to the ordinary range the band "
        "describes.")


def test_scenarios_at_all_time_high():
    m = modules_for(1)
    m["levels"].update(resistances=[], high_52w=CLOSE,
                       extremes={"at_high_52w": True, "at_low_52w": False, "at_all_time_high": True,
                                 "at_all_time_low": False})
    bull = S.build_scenarios(ctx_for(), m)["bull"]
    assert bull["trigger"]["level"] == pytest.approx(CLOSE + ATR)
    assert "last close + 1 ATR" in bull["trigger"]["basis"] and "all-time high" in bull["trigger"]["basis"]
    assert [t["price"] for t in bull["targets"]] == pytest.approx([104.0, 106.0])
    assert all(t["kind"] == "projection" and "no overhead level" in t["label"] for t in bull["targets"])
    assert bull["invalidation"]["level"] == pytest.approx(101.0)


def test_scenarios_no_resistance_below_52w_high():
    m = modules_for(1)
    m["levels"].update(resistances=[], high_52w=103.0)
    bull = S.build_scenarios(ctx_for(), m)["bull"]
    assert bull["trigger"]["level"] == 103.0 and bull["trigger"]["basis"].startswith("52-week high")
    assert [t["price"] for t in bull["targets"]] == pytest.approx([105.0, 107.0])


def test_scenarios_without_levels_module():
    m = modules_for(1)
    m["levels"] = {"available": False, "reason": "close or ATR unavailable"}
    sc = S.build_scenarios(ctx_for(), m)
    assert sc["available"] and any("levels unavailable" in n for n in sc["notes"])
    assert not any("reference only" in n for n in sc["notes"])
    assert any("reference only" in n for n in S.build_scenarios(ctx_for(), modules_for(1))["notes"])
    assert sc["bear"]["trigger"]["level"] == pytest.approx(CLOSE - ATR)


def test_scenarios_unavailable_without_atr():
    m = modules_for(1)
    m["volatility"]["atr14"] = None
    m["levels"]["atr"] = None
    assert S.build_scenarios(ctx_for(), m)["available"] is False
    assert S.build_risk(ctx_for(), m, None)["available"] is False


def test_no_advice_words():
    m = modules_for(1)
    m["event_risk"]["within_horizon"] = True
    cf = S.build_conflicts(ctx_for(), m)
    sc = S.build_scenarios(ctx_for(), m)
    texts = json.dumps([cf, sc, S.build_risk(ctx_for(), m, sc)])
    assert not re.search(r"\b(buy|sell|hold)\b", texts, flags=re.IGNORECASE)


# ---------------------------------------------------------------------------- risk
def test_risk_arithmetic_exact():
    m = modules_for(1)
    sc = S.build_scenarios(ctx_for(), m)
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
    assert "ladder_ref" not in risk and not any("back-tested" in n for n in risk["notes"])


def test_ex_dividend_inside_the_horizon_adds_a_risk_note_and_changes_nothing_else():
    m = modules_for(1)
    dv = {"available": True, "pays_dividends": True, "within_horizon": True,
          "next_ex_dividend": {"date": "2026-11-09", "amount": 0.53, "amount_pct": 0.0053, "amount_atr": 0.25,
                               "trading_days_to": 4}}
    me = {"available": True, "within_horizon": [{"id": "cpi", "label": "CPI", "tier": 1, "date": "2026-11-10",
                                                 "trading_days_to": 5}]}
    events = {**m, "dividends": dv, "macro_events": me}
    assert S.build_conflicts(ctx_for(), events) == S.build_conflicts(ctx_for(), m)    # context only
    sc = S.build_scenarios(ctx_for(), m)
    assert S.build_scenarios(ctx_for(), events) == sc
    plain, risk = S.build_risk(ctx_for(), m, sc), S.build_risk(ctx_for(), events, sc)
    (extra,) = [n for n in risk["notes"] if n not in plain["notes"]]
    assert risk["notes"].index(extra) == 1 and extra.startswith("Ex-dividend date 2026-11-09 falls inside the horizon")
    assert "$0.53 (0.53% of the close, 0.25 ATR)" in extra and "touched by the ex-dividend drop alone" in extra
    assert not re.search(r"\b(buy|sell|hold)\b", extra, flags=re.IGNORECASE)
    assert {k: v for k, v in risk.items() if k != "notes"} == {k: v for k, v in plain.items() if k != "notes"}
    for other in ({**dv, "within_horizon": False}, {**dv, "within_horizon": None, "next_ex_dividend": None},
                  {"available": False, "reason": "restricted: dividends"}):
        assert S.build_risk(ctx_for(), {**m, "dividends": other}, sc)["notes"] == plain["notes"]
    m["event_risk"]["within_horizon"] = True                          # earnings first, then the ex-dividend date
    notes = S.build_risk(ctx_for(), {**m, "dividends": dv}, sc)["notes"]
    assert notes[1].startswith("Earnings on") and notes[2].startswith("Ex-dividend date")


def test_projected_ex_dividend_dates_add_a_risk_note_and_change_nothing_else():
    # HYG 2026-11-04 (position): monthly, none announced, about 3 ex-dates inside 60 sessions, 1.6% in all
    m = modules_for(1)
    dv = {"available": True, "pays_dividends": True, "within_horizon": None, "next_ex_dividend": None,
          "last_ex_dividend": {"date": "2026-11-02", "amount": 0.40, "amount_pct": 0.0052},
          "projected_in_horizon": {"ex_dates": 3, "amount_pct": 0.0155}}
    sc = S.build_scenarios(ctx_for("position"), m)
    plain = S.build_risk(ctx_for("position"), m, sc)
    risk = S.build_risk(ctx_for("position"), {**m, "dividends": dv}, sc)
    assert S.build_conflicts(ctx_for("position"), {**m, "dividends": dv}) == S.build_conflicts(ctx_for("position"), m)
    (extra,) = [n for n in risk["notes"] if n not in plain["notes"]]
    assert extra.startswith("No ex-dividend date is announced yet, but at the spacing of the last 12 months about 3 "
                            "would fall inside the horizon, about 1.6% of the close in all at the last amount")
    assert "touched by these drops alone" in extra and extra.endswith("so they leave these drops out.")
    assert {k: v for k, v in risk.items() if k != "notes"} == {k: v for k, v in plain.items() if k != "notes"}
    one = S.build_risk(ctx_for("position"), {**m, "dividends": {**dv, "projected_in_horizon":
                                                                {"ex_dates": 1, "amount_pct": 0.015}}}, sc)
    (single,) = [n for n in one["notes"] if n.startswith("No ex-dividend date is announced yet")]
    assert "about 1 would fall inside the horizon, about 1.5% of the close at the last amount" in single
    assert "on it for mechanical reasons" in single and single.endswith("so they leave this drop out.")
    for proj in ({"ex_dates": 0, "amount_pct": 0.0}, None):         # none projected, or too few ex-dates to tell
        assert S.build_risk(ctx_for("position"), {**m, "dividends": {**dv, "projected_in_horizon": proj}},
                            sc)["notes"] == plain["notes"]
    announced = {**dv, "within_horizon": True,
                 "next_ex_dividend": {"date": "2026-12-01", "amount": 0.40, "amount_pct": 0.0052, "amount_atr": 0.9}}
    (note,) = [n for n in S.build_risk(ctx_for("position"), {**m, "dividends": announced}, sc)["notes"]
               if n.startswith("Ex-dividend date 2026-12-01")]
    assert "At the spacing of the last 12 months about 3 ex-dates would fall inside the horizon, about 1.6% of " \
           "the close in all." in note and note.endswith("so they leave these drops out.")


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
    sc = S.build_scenarios(ctx_for(), m)
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
    sc = S.build_scenarios(ctx_for("position"), m)
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
    cf = S.build_conflicts(ctx, modules)
    sc = S.build_scenarios(ctx, modules)
    risk = S.build_risk(ctx, modules, sc)
    assert cf["available"] and isinstance(cf["items"], list)
    assert sc["available"] and risk["available"]
    assert sc["bull"]["trigger"]["level"] > bars["close"].iloc[-1] > sc["bear"]["trigger"]["level"]
    json.dumps([cf, sc, risk])                               # plain Python types only


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
