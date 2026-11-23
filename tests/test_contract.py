"""The ta-3.4 contract (scripts/contract.py, contracts/ta-3.4.schema.json) on a fake FMP (no network).

Every contract must match the schema, and its numbers must be the metrics' numbers. A small JSON Schema checker
lives here so the tests need nothing beyond pandas, numpy and pytest.
"""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest

import analyze
import config as C
import contract
import fetch
import scenarios as SC
from fakes import (DIVIDENDS, ECON, HOLIDAYS, MACRO_EVENTS, NEXT_EX_DATE, NOW, SPY_NEXT_EX_DATE, earnings_history,
                   macro_rows, market, profile_row)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "contracts" / f"{contract.CONTRACT}.schema.json").read_text(encoding="utf-8"))
TYPES = {"null": lambda x: x is None, "boolean": lambda x: isinstance(x, bool),
         "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
         "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
         "string": lambda x: isinstance(x, str), "array": lambda x: isinstance(x, list),
         "object": lambda x: isinstance(x, dict)}


def validate(inst, schema, root=SCHEMA, path="$"):
    """Errors of inst against the JSON Schema subset the contract schemas use."""
    errs = []
    if "$ref" in schema:
        errs += validate(inst, root["$defs"][schema["$ref"].rsplit("/", 1)[-1]], root, path)
    if "const" in schema and inst != schema["const"]:
        errs.append(f"{path}: {inst!r} is not {schema['const']!r}")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} not in {schema['enum']}")
    if "anyOf" in schema:
        alts = [validate(inst, alt, root, path) for alt in schema["anyOf"]]
        if all(alts):
            errs += min(alts, key=len)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(TYPES[t](inst) for t in types):
            return errs + [f"{path}: {type(inst).__name__} is not {types}"]
    if isinstance(inst, dict):
        props = schema.get("properties", {})
        errs += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in inst]
        for k, v in inst.items():
            extra = schema.get("additionalProperties", True)
            if k in props:
                errs += validate(v, props[k], root, f"{path}.{k}")
            elif extra is False:
                errs.append(f"{path}: unexpected {k}")
            elif isinstance(extra, dict):
                errs += validate(v, extra, root, f"{path}.{k}")
    if isinstance(inst, list):
        if len(inst) < schema.get("minItems", 0) or len(inst) > schema.get("maxItems", len(inst)):
            errs.append(f"{path}: {len(inst)} items")
        if "items" in schema:
            for i, v in enumerate(inst):
                errs += validate(v, schema["items"], root, f"{path}[{i}]")
    return errs


def both(client, ticker="ACME", **kw):
    swing, _ = analyze.run_analysis(ticker, "swing", client=client, now=NOW, **kw)
    position, _ = analyze.run_analysis(ticker, "position", client=client, now=NOW, **kw)
    return swing, position


@pytest.fixture(scope="module")
def stock():
    swing, position = both(market(earnings=earnings_history("ACME")))
    return swing, position, contract.build(swing, position)


# ---------------------------------------------------------------------------- schema
def test_schema_version_matches_the_module():
    assert SCHEMA["properties"]["contract"]["const"] == contract.CONTRACT == SCHEMA["$id"]
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"])


def test_the_checker_catches_drift():
    assert validate({"contract": "ta-1.1"}, SCHEMA)
    assert validate({"state": "sideways", "zone": None}, SCHEMA["$defs"]["horizon"]["properties"]["price_location"])
    assert validate(True, SCHEMA["$defs"]["num"])
    assert validate(2, SCHEMA["$defs"]["sign"])
    assert not validate(None, SCHEMA["$defs"]["scenario"])
    brief = {"id": "cpi", "label": "CPI", "tier": 1, "date": "2026-10-20", "trading_days_to": 2}
    assert validate(brief, SCHEMA["$defs"]["macro_brief"]) == ["$: missing time_ny", "$: missing timing"]
    assert not validate({**brief, "time_ny": None, "timing": "date_only"}, SCHEMA["$defs"]["macro_brief"])
    assert validate({**brief, "time_ny": "08:30", "timing": "soon"}, SCHEMA["$defs"]["macro_brief"])


# ---------------------------------------------------------------------------- stock
def test_stock_contract_matches_the_schema(stock):
    _, _, c = stock
    assert validate(c, SCHEMA) == []
    json.dumps(c, allow_nan=False)


def test_numbers_are_the_metrics(stock):
    swing, position, c = stock
    assert (c["ticker"], c["as_of"], c["last_close"]) == ("ACME", swing["as_of"], swing["snapshot"]["price"])
    assert c["atr14"] == swing["volatility"]["atr14"]
    assert c["regime"]["type"] == swing["regime"]["type"]
    assert c["extension"]["z20"] == swing["mean_reversion"]["z20"]
    assert c["event"]["next_earnings"] == swing["event_risk"]["next_earnings"]["date"]
    assert "eps_estimated" not in c["event"]                       # 2.0: the contract carries no fundamentals
    for name, m in (("swing", swing), ("position", position)):
        h = c["horizons"][name]
        assert h["horizon_days"] == m["signals"]["primary_horizon"]
        # 3.0: no scores of any kind (no bias, pillars or weights); the conflicts are listed as they are
        assert not {"bias", "pillars", "weights_used"} & set(h)
        assert [x["type"] for x in h["conflicts"]] == [x["type"] for x in m["conflicts"]["items"]]
        assert all("pillar" not in s for s in h["signals"] + h["patterns"])
        assert [(z["low"], z["high"]) for z in h["supports"]] == \
            [(z["zone_low"], z["zone_high"]) for z in m["levels"]["supports"]]
        # 3.2: the zone the close is in (None when it is in none) and each zone's record since its last break
        cz = m["levels"]["current_zone"]
        assert (h["current_zone"] is None) == (cz is None)
        assert (h["price_location"]["state"] == "inside_zone") == (cz is not None)
        assert [(z["last_break"], z["holds_since_break"]) for z in h["supports"] + h["resistances"]] == \
            [(z["last_break"], z["holds_since_break"]) for z in m["levels"]["supports"] + m["levels"]["resistances"]]
        assert h["levels_window"]["lookback_bars"] == m["levels"]["lookback_bars"]
        assert h["bull"]["trigger"] == m["scenarios"]["bull"]["trigger"]["level"]
        assert h["stops_ref"]["long"]["structural"] == m["risk"]["long_side"]["structural_stop"]
        assert h["stops_ref"]["short"]["vol"] == m["risk"]["short_side"]["vol_stop"]
        assert h["stops_ref"]["long"]["touch_rate"]["vol"] == m["risk"]["long_side"]["stop_touch_rate"]["vol"]
        assert h["stops_ref"]["long"]["primary"] == "vol"
        assert h["range_95"]["high"] == m["scenarios"]["base"]["range_95_high"] and h["range_95"]["k"] == 2.4
        assert h["levels_note"] == m["levels"]["evidence"]["note"] and "reference only" in h["levels_note"]
        assert h["earnings_within_horizon"] == m["event_risk"]["within_horizon"]
        primary = m["signals"]["primary_horizon"]
        assert [(s["id"], s["confirmed"]) for s in h["signals"]] == \
            [(s["id"], SC._signal_view(s, primary)["confirmed"]) for s in m["signals"]["active"]]
    assert (c["horizons"]["swing"]["horizon_days"], c["horizons"]["position"]["horizon_days"]) == (10, 60)


def test_trend_sma100_is_the_metrics_context_sma(stock):
    swing, position, c = stock
    sma, t = swing["trend_momentum"]["sma"], c["trend"]
    assert isinstance(t["sma100"], float) and t["sma100"] == sma["sma100"]
    assert t["slopes"]["sma100"] == sma["slopes"]["sma100"] and t["close_vs"]["sma100"] == sma["close_vs"]["sma100"]
    assert (t["sma20"], t["sma50"], t["sma200"]) == (sma["sma20"], sma["sma50"], sma["sma200"])
    assert t["stack"] == sma["stack"] and "100" not in t["stack"] and sma["stack_periods"] == [20, 50, 200]
    spec = SCHEMA["properties"]["trend"]
    assert "sma100" in spec["required"] and "context only" in spec["properties"]["sma100"]["description"]
    assert "trend.sma100" in SCHEMA["description"]
    # a run without the 100-day mean (short history, or an older metrics file) gives null, still valid
    old = [copy.deepcopy(m) for m in (swing, position)]
    for m in old:
        del m["trend_momentum"]["sma"]["sma100"]
    c_old = contract.build(*old)
    assert c_old["trend"]["sma100"] is None and validate(c_old, SCHEMA) == []


def test_squeeze_last_release_is_the_metrics_release(stock):
    swing, position, c = stock
    rel, out = swing["volatility"]["squeeze"]["last_release"], c["volatility"]["squeeze_last_release"]
    assert rel is not None, "the fake history should switch a squeeze off at least once"
    assert out == {k: rel[k] for k in ("date", "bars_ago", "direction", "squeeze_bars_before")}
    assert out["bars_ago"] >= 0 and out["squeeze_bars_before"] >= 1 and out["direction"] in ("up", "down", "flat")
    assert "volatility.squeeze_last_release" in SCHEMA["description"]
    # no release in the history (or an older metrics file without the field) gives null, still valid
    for drop in (False, True):
        old = [copy.deepcopy(m) for m in (swing, position)]
        for m in old:
            if drop:
                del m["volatility"]["squeeze"]["last_release"]
            else:
                m["volatility"]["squeeze"]["last_release"] = None
        c_old = contract.build(*old)
        assert c_old["volatility"]["squeeze_last_release"] is None and validate(c_old, SCHEMA) == []
    bad = copy.deepcopy(c)
    bad["volatility"]["squeeze_last_release"]["direction"] = "sideways"
    assert validate(bad, SCHEMA)


def test_swing_structure_is_the_metrics_structure(stock):
    """3.3: trend.structure (daily swings) and weekly.structure (weekly swings)."""
    swing, position, c = stock
    pairs = ((c["trend"]["structure"], swing["trend_momentum"]["structure"]),
             (c["weekly"]["structure"], swing["multi_timeframe"]["weekly"]["structure"]))
    for got, m in pairs:
        assert m["available"] is True, "the fake history should have two swing highs and two swing lows"
        assert (got["state"], got["highs"], got["lows"]) == (m["state"], m["highs"], m["lows"])
        assert got["state"] in ("uptrend", "downtrend", "mixed")
        for k in ("last_high", "prev_high", "last_low", "prev_low"):
            assert got[k] == {x: m[k][x] for x in ("price", "date", "provisional")}
        assert (got["invalidation"] is None) == (m["state"] == "mixed")
        if got["invalidation"]:
            assert got["invalidation"] == {x: m["invalidation"][x]
                                           for x in ("price", "date", "kind", "distance_pct", "distance_atr")}
        assert set(got) == set(SCHEMA["$defs"]["structure"]["required"])          # no rule text, no swing list
    # the structure does not depend on the horizon
    assert position["trend_momentum"]["structure"] == swing["trend_momentum"]["structure"]
    # a structure that could not be read (or an older metrics file without it) is null, still valid
    for drop in (False, True):
        old = [copy.deepcopy(m) for m in (swing, position)]
        for m in old:
            if drop:
                del m["trend_momentum"]["structure"], m["multi_timeframe"]["weekly"]["structure"]
            else:
                m["trend_momentum"]["structure"] = {"available": False, "reason": "needs two confirmed swing highs"}
                m["multi_timeframe"]["weekly"]["structure"] = {"available": False, "reason": "no weekly pivots"}
        c_old = contract.build(*old)
        assert c_old["trend"]["structure"] is None and c_old["weekly"]["structure"] is None
        assert validate(c_old, SCHEMA) == []
    bad = copy.deepcopy(c)
    bad["trend"]["structure"]["state"] = "sideways"
    assert validate(bad, SCHEMA)
    assert "structure" in SCHEMA["properties"]["trend"]["required"] and "trend.structure" in SCHEMA["description"]


def test_trendlines_are_the_metrics_trendlines(stock):
    """3.3: horizons.*.trendlines, the up and the down line of that horizon's levels window."""
    swing, position, c = stock
    seen = channels = 0
    for name, m in (("swing", swing), ("position", position)):
        got, lines = c["horizons"][name]["trendlines"], m["levels"]["trendlines"]
        assert set(got) == {"up", "down"}
        for side in ("up", "down"):
            line, out = lines[side], got[side]
            assert (out is None) == (line is None)
            if line is None:
                continue
            seen += 1
            assert (out["anchor_date"], out["anchor_price"]) == (line["anchor"]["date"], line["anchor"]["price"])
            assert (out["second_date"], out["second_price"]) == (line["second"]["date"], line["second"]["price"])
            for k in ("slope_per_bar", "price", "distance_pct", "distance_atr", "touches", "confirmed", "state",
                      "broken_on", "close_side"):
                assert out[k] == line[k], k
            assert (out["state"] == "broken") == (out["broken_on"] is not None)
            assert out["anchor_date"] < out["second_date"] <= m["as_of"]
            # 3.4: the channel line of the same line, from price_action.trend_channels
            pa_line = m["price_action"]["trend_channels"][side]
            assert pa_line["anchor"] == line["anchor"]
            cl, got_cl = pa_line["channel_line"], out["channel_line"]
            assert (got_cl is None) == (cl is None)
            if cl:
                channels += 1
                assert (got_cl["through_date"], got_cl["through_price"]) == (cl["through"]["date"], cl["through"]["price"])
                for k in ("price", "distance_pct", "distance_atr", "position", "overshoots", "last_overshoot", "close_side"):
                    assert got_cl[k] == cl[k], k
                assert out["anchor_date"] < got_cl["through_date"] < out["second_date"]
    assert seen, "the fake history should draw at least one trendline"
    assert channels, "... and at least one channel line"
    assert "trendlines" in SCHEMA["$defs"]["horizon"]["required"]
    # an older metrics file without trendlines gives null, still valid; a broken line is caught
    old = [copy.deepcopy(m) for m in (swing, position)]
    for m in old:
        del m["levels"]["trendlines"]
    c_old = contract.build(*old)
    assert c_old["horizons"]["swing"]["trendlines"] is None and validate(c_old, SCHEMA) == []
    h = copy.deepcopy(c["horizons"]["swing"])
    h["trendlines"] = {"up": {"price": 1.0}, "down": None}
    assert validate(h, SCHEMA["$defs"]["horizon"])


def test_zones_say_when_they_are_round_number_fills(stock):
    """3.3: zone sources may name weekly pivots and round numbers; round_fill marks a zone listed as a fill."""
    swing, position, c = stock
    for name, m in (("swing", swing), ("position", position)):
        h = c["horizons"][name]
        for side in ("supports", "resistances"):
            assert [z["round_fill"] for z in h[side]] == [z["round_fill"] for z in m["levels"][side]]
            assert [z["sources"] for z in h[side]] == [z["sources"] for z in m["levels"][side]]
            assert all(not z["round_fill"] or "round_number" in z["sources"] for z in h[side])
    # the weekly swings are candidates for both horizons
    for m in (swing, position):
        assert m["levels"]["weekly_pivots"]["available"] is True
        assert any(x["source"].startswith("weekly_pivot_") for x in m["levels"]["candidates"])
    zone = {"price": 110.0, "zone_low": 110.0, "zone_high": 110.0, "score": 0.25, "touches": 0, "holds": 0, "breaks": 0,
            "flips": 0, "fresh": True, "last_test": None, "last_break": None, "holds_since_break": 0,
            "reaction_score": 0.0, "sources": ["round_number"], "round_fill": True, "distance_pct": 0.05,
            "distance_atr": 2.5}
    out = contract._zone(zone)
    assert out["round_fill"] is True and validate(out, SCHEMA["$defs"]["zone"]) == []
    assert contract._zone({k: v for k, v in zone.items() if k != "round_fill"})["round_fill"] is False
    assert validate({**out, "round_fill": None}, SCHEMA["$defs"]["zone"])


def test_candlestick_patterns_say_where_they_formed(stock):
    """3.3: at_level / level on candlestick patterns (3.4: on price-action setups too), without the zone's score."""
    swing, _, _ = stock
    spec = SCHEMA["$defs"]["evidence"]
    at = {"id": "cs_hammer", "name": "Hammer", "bias": 1, "fired_date": swing["as_of"], "horizons": {},
          "at_level": True, "level": {"label": "S1", "side": "support", "price": 96.0, "zone_low": 95.0,
                                      "zone_high": 96.5, "sources": ["pivot_low"], "score": 3.0}}
    m = copy.deepcopy(swing)
    m["candlesticks"]["active"] = [at, {**at, "id": "cs_shooting_star", "bias": -1, "at_level": False, "level": None},
                                   {**at, "id": "cs_doji_star", "at_level": None, "level": None}]
    m["price_action"]["active"] = [{**at, "id": "pa_h2"}]
    rows = contract._horizon(m)["patterns"]
    by = {r["id"]: r for r in rows}
    assert by["cs_hammer"]["at_level"] is True
    assert by["cs_hammer"]["level"] == {"label": "S1", "side": "support", "price": 96.0, "low": 95.0, "high": 96.5}
    assert (by["cs_shooting_star"]["at_level"], by["cs_shooting_star"]["level"]) == (False, None)
    assert (by["cs_doji_star"]["at_level"], by["cs_doji_star"]["level"]) == (None, None)
    assert by["pa_h2"]["at_level"] is True and by["pa_h2"]["level"] == by["cs_hammer"]["level"]      # 3.4
    assert all(validate(r, spec) == [] for r in rows)
    assert validate({**by["cs_hammer"], "level": {**by["cs_hammer"]["level"], "score": 3.0}}, spec)
    # the real run: every active pattern has a place
    for r in contract._horizon(swing)["patterns"]:
        assert isinstance(r["at_level"], bool) and (r["level"] is not None) == r["at_level"]


def test_price_action_patterns_carry_the_stop_entry_and_where_they_formed(stock):
    """3.4: entry, signal_bar, variant, range_third and line on price-action patterns only."""
    swing, _, c = stock
    spec = SCHEMA["$defs"]["evidence"]
    row = {"id": "pa_double_bottom", "name": "Double bottom", "bias": 1, "fired_date": swing["as_of"], "bars_ago": 0,
           "horizons": {}, "variant": "flag", "at_level": False, "level": None,
           "entry": {"order": "buy_stop", "trigger_price": 101.5, "status": "pending", "entry_date": None,
                     "entry_price": None},
           "signal_bar": {"date": swing["as_of"], "high": 101.5, "low": 99.0, "close_position": 0.9, "range_atr": 1.1,
                          "strong": True, "weak_because": []},
           "location": {"ref_price": 98.2, "ref_date": "2026-10-12", "range_third": "bottom",
                        "line": {"kind": "up", "line": "trendline", "price": 98.3, "touches": 3, "state": "intact"}}}
    m = copy.deepcopy(swing)
    m["price_action"]["active"] = [row, {**row, "id": "pa_h2_ema", "variant": None,
                                         "entry": {**row["entry"], "status": "triggered", "entry_date": swing["as_of"],
                                                   "entry_price": 101.8},
                                         "location": {**row["location"], "range_third": None, "line": None}}]
    m["candlesticks"]["active"] = [{"id": "cs_hammer", "name": "Hammer", "bias": 1, "fired_date": swing["as_of"],
                                    "horizons": {}, "at_level": False, "level": None}]
    by = {r["id"]: r for r in contract._horizon(m)["patterns"]}
    d = by["pa_double_bottom"]
    assert d["entry"] == {"status": "pending", "order": "buy_stop", "trigger_price": 101.5, "entry_date": None,
                          "entry_price": None}
    assert d["signal_bar"] == {"date": swing["as_of"], "high": 101.5, "low": 99.0, "strong": True}
    assert (d["variant"], d["range_third"]) == ("flag", "bottom")
    assert d["line"] == {"kind": "up", "line": "trendline", "price": 98.3}
    h2 = by["pa_h2_ema"]
    assert (h2["entry"]["status"], h2["entry"]["entry_price"], h2["variant"], h2["range_third"], h2["line"]) == \
        ("triggered", 101.8, None, None, None)
    assert not {"entry", "signal_bar", "variant", "range_third", "line"} & set(by["cs_hammer"])
    assert all(validate(r, spec) == [] for r in by.values())
    assert validate({**d, "entry": {**d["entry"], "status": "filled"}}, spec)        # not a status the schema knows
    # the real run: every price-action pattern is pending on the last bar or was triggered after it
    for hz in ("swing", "position"):
        for r in c["horizons"][hz]["patterns"]:
            if r["source"] == "price_action":
                assert r["entry"]["status"] == ("pending" if r["bars_ago"] == 0 else "triggered")
                assert r["signal_bar"]["date"] == r["fired_date"]
                assert (r["entry"]["entry_price"] is None) == (r["entry"]["status"] == "pending")


def test_context_carries_the_price_action_reading(stock):
    """3.4: the pending state, climax, breakout mode, the last breakout / wedge / double / reversal and the
    setups that did not trigger."""
    swing, _, c = stock
    ctx, pa = c["context_only"], swing["price_action"]
    assert ctx["pa_state"] == pa["state"]["label"] and ctx["pa_state_since"] == pa["state"]["since"]
    pend = pa["state"]["pending"]
    assert ctx["pa_state_pending"] == (None if pend is None else {"label": pend["label"], "bars": pend["bars"]})
    assert ctx["climax"]["active"] == pa["climax"]["active"] and ctx["climax"]["side"] == pa["climax"]["side"]
    assert "rule" not in ctx["climax"]
    for key, fields in (("breakout_mode", ("pattern", "date", "broke")), ("last_breakout", ("side", "date", "level", "follow_through", "holding")),
                        ("last_wedge", ("side", "confirmed_date")), ("last_double", ("side", "variant", "confirmed_date")),
                        ("last_mtr", ("side", "variant", "signal_date"))):
        if pa[key] is None:
            assert ctx[key] is None, key
        else:
            assert {k: ctx[key][k] for k in fields} == {k: pa[key][k] for k in fields}, key
    assert [x["id"] for x in ctx["lapsed_setups"]] == [x["id"] for x in pa["lapsed"]]
    m = copy.deepcopy(swing)
    m["price_action"]["lapsed"] = [{"id": "pa_mtr_top", "name": "x", "bias": -1, "fired_date": "2026-10-15",
                                    "bars_ago": 1, "trigger_price": 99.5}]
    m["price_action"]["state"]["pending"] = {"label": "trading_range", "bars": 2}
    out = contract._context(m["price_action"], m["candlesticks"])
    assert out["lapsed_setups"] == [{"id": "pa_mtr_top", "bias": -1, "fired_date": "2026-10-15", "bars_ago": 1,
                                     "trigger_price": 99.5}]
    assert out["pa_state_pending"] == {"label": "trading_range", "bars": 2}
    assert validate(out, SCHEMA["properties"]["context_only"]) == []


def test_shared_blocks_are_the_same_in_both_runs(stock):
    swing, position, c = stock
    for k in contract.SHARED:
        assert swing[k] == position[k], k
    assert not [w for w in c["warnings"] if "differs" in w]


def test_a_shared_block_that_differs_is_flagged(stock):
    swing, position, _ = stock
    other = copy.deepcopy(position)
    other["regime"]["label"] = "something else"
    assert any(w.startswith("regime differs") for w in contract.build(swing, other)["warnings"])


def test_inputs_must_pair_up(stock):
    swing, position, _ = stock
    with pytest.raises(ValueError, match="swing input"):
        contract.build(position, position)
    later = dict(position, as_of="2026-10-19")
    with pytest.raises(ValueError, match="as_of"):
        contract.build(swing, later)


def test_last_earnings_is_the_latest_report_before_the_last_bar(stock):
    swing, _, c = stock
    ev = c["event"]
    reports = sorted(r["date"] for r in earnings_history("ACME") if r["date"] <= c["as_of"])
    assert ev["last_earnings"] == swing["event_risk"]["last_earnings"]["date"] == reports[-1]
    assert ev["days_since_last_earnings"] == (pd.Timestamp(c["as_of"]) - pd.Timestamp(reports[-1])).days
    assert ev["next_earnings"] == "2026-10-29" and ev["last_earnings"] < c["as_of"] < ev["next_earnings"]
    assert isinstance(ev["last_earnings_move"], float)


def test_targets_say_whether_they_are_levels_or_projections(stock):
    _, _, c = stock
    kinds = [t["kind"] for h in c["horizons"].values() for side in ("bull", "bear") if h[side]
             for t in h[side]["targets"]]
    assert kinds and set(kinds) <= {"level", "projection"}
    for h in c["horizons"].values():
        for side in ("bull", "bear"):
            for t in (h[side] or {}).get("targets", []):
                assert (t["kind"] == "projection") == t["label"].startswith("projection")


def test_position_sizing_stays_out(stock):
    text = json.dumps(stock[2])
    assert "position_size" not in text and "shares" not in text


def test_dividend_macro_and_calendar_fields_are_the_metrics(stock):
    swing, position, c = stock
    dv, me = swing["dividends"], swing["macro_events"]
    nx, la = dv["next_ex_dividend"], dv["last_ex_dividend"]
    assert c["dividend"] == {"available": True, "reason": None, "pays_dividends": True, "next_ex_date": nx["date"],
                             "next_amount": nx["amount"], "next_amount_pct": nx["amount_pct"],
                             "next_trading_days_to": nx["trading_days_to"], "last_ex_date": la["date"],
                             "last_amount": la["amount"]}
    assert (c["dividend"]["next_ex_date"], c["dividend"]["next_trading_days_to"]) == (NEXT_EX_DATE, 6)
    assert c["macro"]["available"] is True and c["macro"]["reason"] is None
    assert (c["macro"]["window_from"], c["macro"]["window_to"]) == (me["window"]["from"], me["window"]["to"])
    assert c["macro"]["listed_through"] == me["coverage"]["listed_through"] == "2026-11-06"
    assert c["macro"]["events"] == [{k: e[k] for k in contract.MACRO_EVENT_KEYS} for e in me["events"]]
    assert [(e["id"], e["session"], e["trading_days_to"]) for e in c["macro"]["events"]] == \
        [("cpi", "2026-10-20", 2), ("fomc", "2026-10-28", 8), ("jobs", "2026-11-06", 15)]
    assert c["data_quality"]["calendar_source"] == swing["data_quality"]["calendar"]["source"] == "fmp+rules"
    assert c["event"]["trading_days_to"] == swing["event_risk"]["next_earnings"]["trading_days_est"]
    for name, m in (("swing", swing), ("position", position)):
        h = c["horizons"][name]
        assert h["ex_dividend_within_horizon"] is m["dividends"]["within_horizon"] is True
        assert h["macro_within_horizon"] == [{k: e[k] for k in contract.MACRO_BRIEF_KEYS}
                                             for e in m["macro_events"]["within_horizon"]]
        assert h["macro_complete"] is m["macro_events"]["coverage"]["complete"]
    assert [e["id"] for e in c["horizons"]["swing"]["macro_within_horizon"]] == ["cpi", "fomc"]
    assert [e["id"] for e in c["horizons"]["position"]["macro_within_horizon"]] == ["cpi", "fomc", "jobs"]
    # each brief entry carries its New York time and timing, so a caller needs no lookup in macro.events
    assert c["horizons"]["swing"]["macro_within_horizon"][1] == {
        "id": "fomc", "label": "FOMC rate decision", "tier": 1, "date": "2026-10-28", "time_ny": "14:00",
        "timing": "intraday", "trading_days_to": 8}
    assert set(contract.MACRO_BRIEF_KEYS) <= set(contract.MACRO_EVENT_KEYS)
    briefs = {(e["id"], e["date"]): e for e in c["horizons"]["position"]["macro_within_horizon"]}
    for e in c["macro"]["events"]:
        if (e["id"], e["date"]) in briefs:
            assert briefs[(e["id"], e["date"])] == {k: e[k] for k in contract.MACRO_BRIEF_KEYS}
    assert (c["horizons"]["swing"]["macro_complete"], c["horizons"]["position"]["macro_complete"]) == (True, False)


def test_the_contract_lists_macro_tiers_one_and_two_only():
    extra = (("2026-10-27 12:30:00", "US", "Durable Goods Orders MoM (Sep)", "High"),    # tier 3: FMP rates it High
             ("2026-11-02 15:00:00", "US", "ISM Manufacturing PMI (Oct)", "Medium"))     # tier 2 whatever the label
    swing, position = both(market(earnings=earnings_history("ACME"), macro=macro_rows(MACRO_EVENTS + extra)))
    c = contract.build(swing, position)
    assert validate(c, SCHEMA) == []
    assert [(e["id"], e["tier"]) for e in swing["macro_events"]["events"]] == \
        [("cpi", 1), ("other", 3), ("fomc", 1), ("ism_manufacturing", 2), ("jobs", 1)]
    assert [e["id"] for e in c["macro"]["events"]] == ["cpi", "fomc", "ism_manufacturing", "jobs"]
    assert [e["id"] for e in swing["macro_events"]["within_horizon"]] == ["cpi", "other", "fomc"]
    assert [e["id"] for e in c["horizons"]["swing"]["macro_within_horizon"]] == ["cpi", "fomc"]
    assert all(e["tier"] <= C.MACRO_CONTRACT_MAX_TIER for e in c["macro"]["events"])


def test_contract_without_calendar_dividends_and_macro():
    fake = market(earnings=earnings_history("ACME"), blocked={HOLIDAYS, DIVIDENDS, ECON})
    c = contract.build(*both(fake))
    assert validate(c, SCHEMA) == []
    assert c["data_quality"]["calendar_source"] == "rules"
    d, mc = c["dividend"], c["macro"]
    assert d["available"] is False and d["reason"].startswith("restricted:") and d["pays_dividends"] is None
    assert d["next_ex_date"] is None and d["last_ex_date"] is None
    assert mc["available"] is False and mc["reason"].startswith("restricted:") and mc["events"] is None
    assert (mc["window_from"], mc["window_to"]) == ("2026-10-16", "2027-01-12")
    for h in c["horizons"].values():
        assert h["ex_dividend_within_horizon"] is None and h["macro_within_horizon"] is None
        assert h["macro_complete"] is None
    assert set(c["skipped"]) == {"dividends", "macro_events"}
    assert {"dividends", "macro_events"} <= set(c["modules_unavailable"])


def test_an_empty_economic_calendar_is_missing_not_a_quiet_horizon():
    # FMP answering [] for the window: the contract must not read as "no FOMC or CPI inside the horizon"
    c = contract.build(*both(market(earnings=earnings_history("ACME"), macro=[])))
    assert validate(c, SCHEMA) == []
    why = "FMP economic-calendar returned no US rows for 2026-10-16 to 2027-01-12"
    assert c["macro"]["available"] is False and c["macro"]["reason"] == why and c["macro"]["events"] is None
    for h in c["horizons"].values():
        assert h["macro_within_horizon"] is None and h["macro_complete"] is None
    assert c["skipped"]["macro_events"] == why and c["modules_unavailable"]["macro_events"] == why


def test_a_symbol_without_dividends_has_none_inside_any_horizon():
    c = contract.build(*both(market(earnings=earnings_history("ACME"), dividends={})))
    assert validate(c, SCHEMA) == []
    assert c["dividend"]["available"] is True and c["dividend"]["pays_dividends"] is False
    assert c["dividend"]["next_ex_date"] is None and c["dividend"]["last_ex_date"] is None
    assert all(h["ex_dividend_within_horizon"] is False for h in c["horizons"].values())


def test_an_older_metrics_file_without_the_new_modules_still_builds(stock):
    swing, position, _ = stock
    old = [{k: v for k, v in m.items() if k not in ("dividends", "macro_events")} for m in (swing, position)]
    c = contract.build(*old)
    assert validate(c, SCHEMA) == []
    assert c["dividend"]["available"] is False and c["macro"]["available"] is False
    assert c["dividend"]["reason"] and c["horizons"]["swing"]["macro_within_horizon"] is None


# ---------------------------------------------------------------------------- derived facts
@pytest.mark.parametrize("close, state, zone", [
    (100.0, "inside_support", 0), (110.5, "inside_resistance", 0), (105.0, "between", None), (95.5, "inside_support", 1),
])
def test_price_location(close, state, zone):
    sup = [{"low": 98.0, "high": 100.0}, {"low": 95.0, "high": 96.0}]
    res = [{"low": 110.0, "high": 111.0}]
    assert contract.price_location(close, sup, res) == {"state": state, "zone": zone}


def test_price_location_in_the_current_zone():
    sup, res = [{"low": 95.0, "high": 96.0}], [{"low": 110.0, "high": 111.0}]
    cur = {"low": 99.0, "high": 101.0}
    assert contract.price_location(100.0, sup, res, cur) == {"state": "inside_zone", "zone": None}
    assert contract.price_location(100.0, [], [], cur) == {"state": "inside_zone", "zone": None}
    assert contract.price_location(100.0, sup, res, None) == {"state": "between", "zone": None}


def test_current_zone_is_the_metrics_zone(stock):
    swing, _, _ = stock
    zone = {"price": 100.2, "zone_low": 99.0, "zone_high": 101.5, "score": 2.5, "touches": 3, "holds": 2, "breaks": 1,
            "flips": 1, "fresh": False, "last_test": "2026-09-01", "last_break": "2026-06-01", "holds_since_break": 1,
            "reaction_score": 1.5, "sources": ["pivot_low", "sma50"], "distance_pct": 0.002, "distance_atr": 0.1,
            "close_position": 0.4}
    m = {**swing, "levels": {**swing["levels"], "current_zone": zone}}
    h = contract._horizon(m)
    assert validate(h, SCHEMA["$defs"]["horizon"]) == []
    cur = h["current_zone"]
    assert (cur["low"], cur["high"], cur["close_position"]) == (99.0, 101.5, 0.4)
    assert (cur["last_break"], cur["holds_since_break"], cur["sources"]) == ("2026-06-01", 1, ["pivot_low", "sma50"])
    assert h["price_location"] == {"state": "inside_zone", "zone": None}
    # a broken current zone is caught by the checker
    assert validate({**h, "current_zone": {"low": 99.0}}, SCHEMA["$defs"]["horizon"])
    assert validate({**h, "current_zone": None}, SCHEMA["$defs"]["horizon"]) == []


def test_price_location_edges():
    z = [{"low": 98.0, "high": 100.0}]
    assert contract.price_location(120.0, z, []) == {"state": "above_all", "zone": None}
    assert contract.price_location(90.0, [], z) == {"state": "below_all", "zone": None}
    assert contract.price_location(90.0, [], []) is None
    assert contract.price_location(None, z, z) is None


@pytest.mark.parametrize("rtype, sign, state", [
    ("trend", 1, "uptrend"), ("trend", -1, "downtrend"), ("trend", 0, "transitional"),
    ("range", 1, "range"), ("transitional", -1, "transitional"), ("vol_extreme", 1, "vol_extreme"), ("odd", 0, None),
])
def test_regime_state(rtype, sign, state):
    assert contract.regime_state({"type": rtype, "direction_sign": sign}) == state


# ---------------------------------------------------------------------------- degraded runs
def test_spy_contract():
    fake = market(stock="SPY", stock_start="1993-01-29",
                  profile=profile_row("SPY", "Financial Services", "1993-01-29", etf=True))
    c = contract.build(*both(fake, "SPY"))
    assert validate(c, SCHEMA) == []
    assert c["company"]["is_etf"] is True
    assert c["event"]["available"] is False and "ETF" in c["event"]["reason"]
    assert c["rs"]["vs_spy"] is None and c["market"]["sector"] is None
    assert c["horizons"]["swing"]["earnings_within_horizon"] is None
    assert c["event"]["last_earnings"] is None and c["event"]["days_since_last_earnings"] is None
    assert set(c["skipped"]) == {"earnings", "sector"}
    assert c["dividend"]["available"] and c["dividend"]["next_ex_date"] == SPY_NEXT_EX_DATE
    assert c["dividend"]["next_trading_days_to"] == 44
    assert (c["horizons"]["swing"]["ex_dividend_within_horizon"], c["horizons"]["position"]["ex_dividend_within_horizon"]) \
        == (False, True)
    assert c["macro"]["available"] and c["horizons"]["swing"]["macro_within_horizon"]


def test_short_history_contract():
    start = pd.bdate_range(end="2026-10-16", periods=150)[0].strftime("%Y-%m-%d")
    c = contract.build(*both(market(stock_start=start, profile=profile_row("ACME", ipo=start), earnings=[])))
    assert validate(c, SCHEMA) == []
    assert c["trend"]["sma200"] is None and c["regime"]["vol"] is None
    assert c["trend"]["sma100"] is not None                                # 150 bars: the 100-day mean exists
    assert c["event"]["available"] is False


def test_failing_module_contract(monkeypatch):
    def broken(ctx):
        raise KeyError("boom")
    monkeypatch.setattr(analyze, "BUILDERS", tuple((n, broken if n == "levels" else f) for n, f in analyze.BUILDERS))
    c = contract.build(*both(market(earnings=earnings_history("ACME"))))
    assert validate(c, SCHEMA) == []
    h = c["horizons"]["swing"]
    assert h["supports"] == [] and h["price_location"] is None and h["levels_window"] is None
    assert c["modules_unavailable"]["levels"].startswith("internal error")
    assert c["errors"] and c["errors"][0].startswith("levels: KeyError")


# ---------------------------------------------------------------------------- CLI
@pytest.fixture
def cli(monkeypatch):
    fake = market(earnings=earnings_history("ACME"))
    monkeypatch.setattr(analyze.fmp_client, "FMPClient", lambda refresh=False: fake)
    monkeypatch.setattr(fetch, "market_now", lambda: NOW)
    return fake


def test_cli_writes_the_contract(cli, tmp_path, capsys):
    path = tmp_path / "c" / "acme.json"
    assert analyze.main(["ACME", "--out", str(tmp_path / "run"), "--contract", str(path)]) == 0
    c = json.loads(path.read_text(encoding="utf-8"))
    assert validate(c, SCHEMA) == []
    assert c["horizons"]["position"]["horizon_days"] == 60
    assert (tmp_path / "run" / "metrics.json").is_file()
    assert "contract     :" in capsys.readouterr().out


def test_cli_normalises_the_ticker(cli, tmp_path, capsys):
    path = tmp_path / "acme.json"
    assert analyze.main(["$acme", "--out", str(tmp_path / "run"), "--contract", str(path)]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["ticker"] == "ACME"
    assert analyze.main(["SHOP.TO", "--out", str(tmp_path / "x")]) == 1
    assert "non-US listing" in capsys.readouterr().err
    assert not (tmp_path / "x").exists()                                # refused before any work


def test_contract_script_matches_build(stock, tmp_path, capsys):
    swing, position, c = stock
    paths = []
    for name, m in (("swing", swing), ("position", position)):
        paths.append(str(analyze.write_outputs(m, None, tmp_path / name)["metrics"]))
    out = tmp_path / "c.json"
    assert contract.main([*paths, "-o", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == c
    assert contract.main([paths[1], paths[0]]) == 1                    # swapped horizons
    assert "Cannot build the contract" in capsys.readouterr().err


def test_docs_mention_the_contract():
    for name in ("SKILL.md", "README.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "--contract" in text and contract.CONTRACT in text, name
