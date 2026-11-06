"""The ta-3.0 contract (scripts/contract.py, contracts/ta-3.0.schema.json) on a fake FMP (no network).

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
