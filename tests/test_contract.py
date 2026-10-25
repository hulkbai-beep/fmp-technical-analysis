"""The ta-1.1 contract (scripts/contract.py, contracts/ta-1.1.schema.json) on a fake FMP (no network).

Every contract must match the schema, and its numbers must be the metrics' numbers. A small JSON Schema checker
lives here so the tests need nothing beyond pandas, numpy and pytest.
"""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest

import analyze
import contract
import fetch
import scorecard as SC
from fakes import NOW, earnings_history, market, profile_row

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
    for name, m in (("swing", swing), ("position", position)):
        h, sc = c["horizons"][name], m["scorecard"]
        assert h["horizon_days"] == m["signals"]["primary_horizon"]
        assert (h["bias"]["score"], h["bias"]["label"], h["bias"]["bias_confidence"]) == \
            (sc["weighted_score"], sc["bias"], sc["confidence"]["level"])
        assert [(z["low"], z["high"]) for z in h["supports"]] == \
            [(z["zone_low"], z["zone_high"]) for z in m["levels"]["supports"]]
        assert h["levels_window"]["lookback_bars"] == m["levels"]["lookback_bars"]
        assert h["bull"]["trigger"] == m["scenarios"]["bull"]["trigger"]["level"]
        assert h["stops_ref"]["long"]["structural"] == m["risk"]["long_side"]["structural_stop"]
        assert h["earnings_within_horizon"] == m["event_risk"]["within_horizon"]
        primary = m["signals"]["primary_horizon"]
        assert [(s["id"], s["confirmed"]) for s in h["signals"]] == \
            [(s["id"], SC._signal_view(s, primary)["confirmed"]) for s in m["signals"]["active"]]
    assert (c["horizons"]["swing"]["horizon_days"], c["horizons"]["position"]["horizon_days"]) == (10, 60)


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


def test_short_history_contract():
    start = pd.bdate_range(end="2026-10-16", periods=150)[0].strftime("%Y-%m-%d")
    c = contract.build(*both(market(stock_start=start, profile=profile_row("ACME", ipo=start), earnings=[])))
    assert validate(c, SCHEMA) == []
    assert c["trend"]["sma200"] is None and c["regime"]["vol"] is None
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
