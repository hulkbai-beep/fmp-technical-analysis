"""End-to-end runs of analyze.run_analysis / analyze.main on a fake FMP (no network)."""
import json
import os
import re
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

import analyze
import config as C
import fetch
import fmp_client
import indicators
from fakes import (ADJ, DIVIDENDS, ECON, FULL, HOLIDAYS, MACRO_EVENTS, NEXT_EX_DATE, NOW, SPY_NEXT_EX_DATE,
                   FakeClient, earnings_history, macro_rows, market, profile_row, series)

TOP_KEYS = ["ticker", "as_of", "data_source", "horizon", "snapshot", "data_quality", "market_context",
            "regime", "trend_momentum", "relative_strength", "mean_reversion", "volume_flow", "volatility",
            "levels", "multi_timeframe", "event_risk", "dividends", "macro_events", "signals", "price_action",
            "candlesticks", "scorecard", "scenarios", "risk", "meta"]
MODULES = TOP_KEYS[6:-1]
META_KEYS = {"network_calls", "cache_hits", "restricted", "errors", "skipped", "notes", "connection",
             "config_snapshot"}
# technical analysis only: no fundamentals (EPS, revenue) and none of the removed research blocks
OFF_TOPIC_KEY = re.compile(r"(^|_)eps(_|$)|revenue|ladder|node_zone|backtest", re.IGNORECASE)


def all_keys(obj):
    """Every dict key in a nested JSON-like object."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_keys(v)


def stock_market(**kw):
    """30 years of ACME (Technology -> XLK) with five years of quarterly earnings and an upcoming report."""
    return market(earnings=earnings_history("ACME"), **kw)


def etf_market(symbol, start, sector="Technology"):
    return market(stock=symbol, stock_start=start, profile=profile_row(symbol, sector, start, etf=True))


def run(client, ticker="ACME", horizon="swing"):
    metrics, chart_data = analyze.run_analysis(ticker, horizon, client=client, now=NOW)
    check_shape(metrics, chart_data)
    return metrics, chart_data


def check_shape(metrics, chart_data):
    """The invariants every run must satisfy."""
    assert list(metrics) == TOP_KEYS
    text = json.dumps(metrics, allow_nan=False)            # strict JSON: no NaN / inf survived
    assert json.loads(text) == metrics                     # and nothing that json cannot round-trip
    json.dumps(chart_data, allow_nan=False)
    for name in MODULES:
        assert isinstance(metrics[name], dict), name
        assert isinstance(metrics[name].get("available"), bool), name
        if not metrics[name]["available"]:
            assert metrics[name].get("reason"), name
    assert META_KEYS <= set(metrics["meta"])
    assert metrics["meta"]["errors"] == [], metrics["meta"]["errors"]   # no module may fail silently
    assert chart_data["meta"]["data_source"] == metrics["data_source"]
    assert chart_data["meta"]["as_of"] == metrics["as_of"]
    off_topic = sorted({k for k in [*all_keys(metrics), *all_keys(chart_data)] if OFF_TOPIC_KEY.search(k)})
    assert off_topic == [], off_topic


def note_text(metrics, *modules):
    return " | ".join(n for m in modules for n in metrics[m].get("notes") or [])


# ---------------------------------------------------------------------------- stock
def test_stock_swing_run():
    fake = stock_market()
    m, chart_data = run(fake)
    meta = m["meta"]
    assert meta["network_calls"] == 13 and meta["cache_hits"] == 0      # the pinned call budget
    assert meta["skipped"] == {} and meta["restricted"] == [] and meta["modules_unavailable"] == {}
    assert all(m[k]["available"] for k in MODULES)
    assert m["ticker"] == "ACME" and m["horizon"] == "swing" and m["as_of"] == "2026-10-16"
    assert m["data_source"] == "FMP stable API (official)"

    snap = m["snapshot"]
    last = series("1996-10-01", seed=3)["close"].iloc[-1]
    assert snap["price"] == pytest.approx(last, rel=1e-5)
    assert snap["sector"] == "Technology" and snap["sector_etf"] == "XLK" and snap["is_etf"] is False
    assert snap["quote_time"].startswith("2026-10-16T16:00:00")

    assert m["regime"]["type"] in C.REGIME_WEIGHTS and m["regime"]["label"]
    assert m["relative_strength"]["vs_sector"]["benchmark"] == "XLK"
    assert m["event_risk"]["next_earnings"]["date"] == "2026-10-29"
    assert m["event_risk"]["reactions"]["n"] == C.EARNINGS_REACTIONS_MAX
    assert any(a["anchor"] == "last_earnings" for a in m["volume_flow"]["anchored_vwaps"])
    assert m["signals"]["stat_horizons"] == [5, 10, 20] and m["signals"]["primary_horizon"] == 10
    for a in m["signals"]["active"]:
        assert set(a["horizons"]) == {"5", "10", "20"}     # int keys become strings in JSON

    sc = m["scorecard"]
    assert list(sc["pillars"]) == list(C.PILLARS)
    assert sc["bias"] in [label for _, label in C.BIAS_STEPS]
    assert sum(sc["weights"].values()) == pytest.approx(1.0)
    assert m["scenarios"]["horizon_days"] == 10 and m["scenarios"]["bull"]["text"].startswith("If ACME")
    assert m["risk"]["long_side"]["position_size_example"]["risk_dollars"] == pytest.approx(1000.0)

    cfg = meta["config_snapshot"]
    assert cfg["horizon"]["name"] == "swing" and cfg["signals"]["confidence_n_normal"] == C.CONFIDENCE_N_NORMAL
    assert cfg["risk"]["vol_stop_k"] == C.VOL_STOP_K
    assert m["levels"]["evidence"] == {"note": C.LEVELS_EVIDENCE_NOTE}
    assert set(m["event_risk"]["next_earnings"]) == {"date", "calendar_days", "trading_days_est"}
    assert "excluding NYSE holidays" in m["event_risk"]["trading_days_note"]
    assert m["data_quality"]["calendar"] == {"source": "fmp+rules", "exchange": "NYSE", "closed_days": 355,
                                             "early_closes": 88, "problem": None}
    assert m["data_quality"]["stale"]["basis"] == "NYSE sessions"

    # ex-dividend date and macro events: context next to the scorecard
    dv = m["dividends"]
    assert dv["pays_dividends"] is True and dv["horizon_days"] == 10
    assert dv["next_ex_dividend"]["date"] == NEXT_EX_DATE and dv["next_ex_dividend"]["trading_days_to"] == 6
    assert dv["within_horizon"] is True and dv["within_stat_horizons"] == {"5": False, "10": True, "20": True}
    assert dv["ex_dates_in_history"] == dv["matched_to_bars"] > 60
    assert m["levels"]["gap_adjustment"]["ex_dividend_bars"] >= 1
    me = m["macro_events"]
    assert me["window"] == {"from": "2026-10-16", "to": "2027-01-12"} and me["horizon_days"] == 10
    assert [(e["id"], e["date"], e["time_ny"], e["timing"], e["trading_days_to"]) for e in me["events"]] == [
        ("cpi", "2026-10-20", "08:30", "pre_open", 2), ("fomc", "2026-10-28", "14:00", "intraday", 8),
        ("jobs", "2026-11-06", "08:30", "pre_open", 15)]
    assert [e["id"] for e in me["within_horizon"]] == ["cpi", "fomc"]
    assert me["within_horizon"][0] == {"id": "cpi", "label": "CPI", "tier": 1, "date": "2026-10-20", "time_ny": "08:30",
                                       "timing": "pre_open", "session": "2026-10-20", "trading_days_to": 2}
    assert me["counts"] == {"tier1": 3, "tier2": 0, "tier3": 0, "excluded": 1, "unclassified": 2}
    assert any(n.startswith(f"Ex-dividend date {NEXT_EX_DATE} falls inside the horizon") for n in m["risk"]["notes"])
    assert not any("dividend" in c["type"] or "macro" in c["type"] for c in sc["conflicts"])

    assert chart_data["default_timeframe"] == "daily"
    assert len(chart_data["candles"]) == C.HORIZONS["swing"]["chart_bars"]
    assert chart_data["rs_spy"] and chart_data["rs_spy"][0][1] == pytest.approx(100.0)


def test_stock_position_run():
    m, chart_data = run(stock_market(), horizon="position")
    assert m["horizon"] == "position"
    assert m["signals"]["stat_horizons"] == [20, 60] and m["signals"]["primary_horizon"] == 60
    assert m["levels"]["lookback_bars"] == C.HORIZONS["position"]["levels_lookback"]
    assert m["scenarios"]["horizon_days"] == 60
    assert m["scorecard"]["weight_basis"]["position_shift"] is True
    assert "weekly_direction" in m["scorecard"]["pillars"]["trend"]["votes"]
    assert chart_data["default_timeframe"] == "weekly"
    assert m["dividends"]["horizon_days"] == 60 and m["dividends"]["within_stat_horizons"] == {"20": True, "60": True}
    me = m["macro_events"]
    assert [e["id"] for e in me["within_horizon"]] == ["cpi", "fomc", "jobs"]
    assert me["coverage"]["complete"] is False and me["coverage"]["listed_through"] == "2026-11-06"
    assert any("2026-11-06" in n and "60 sessions" in n for n in me["notes"])


def test_metrics_floats_are_rounded_to_six_significant_digits():
    m, _ = run(stock_market())
    price = m["snapshot"]["price"]
    assert price == float(f"{price:.6g}")
    assert analyze.json_clean({"a": np.float64(1 / 3), "b": np.int64(7), "c": np.bool_(True),
                               "d": float("nan"), "e": pd.Timestamp("2026-01-02 15:30"), "f": (1, -np.inf),
                               "g": pd.NaT, 3: "x"}) == \
        {"a": 0.333333, "b": 7, "c": True, "d": None, "e": "2026-01-02", "f": [1, None], "g": None, "3": "x"}


def test_proxy_data_source_is_named():
    m, chart_data = run(stock_market(base="https://54.236.92.205"))
    assert m["data_source"] == "FMP stable API via third-party proxy 54.236.92.205"
    assert chart_data["meta"]["data_source"] == m["data_source"]


# ---------------------------------------------------------------------------- ETF paths
def test_spy_skips_earnings_sector_and_rs_vs_spy():
    fake = etf_market("SPY", "1993-01-29", sector="Financial Services")
    m, chart_data = run(fake, "SPY")
    assert m["meta"]["network_calls"] == 10
    assert set(m["meta"]["skipped"]) == {"earnings", "sector"}
    dv = m["dividends"]
    assert dv["available"] and dv["pays_dividends"] and dv["next_ex_dividend"]["date"] == SPY_NEXT_EX_DATE
    assert dv["next_ex_dividend"]["trading_days_to"] == 44 and dv["within_horizon"] is False   # swing: 10 days
    assert m["macro_events"]["available"] and m["macro_events"]["within_horizon"]
    assert not [e for e, _ in fake.requests if e == "earnings"]
    assert m["snapshot"]["is_etf"] is True and m["snapshot"]["sector"] is None
    assert m["event_risk"]["available"] is False and "ETF" in m["event_risk"]["reason"]
    rs = m["relative_strength"]
    assert rs["available"] is False and rs["vs_spy"]["available"] is False and rs["beta_1y"] is None
    assert "benchmark" in rs["vs_spy"]["reason"]
    assert m["market_context"]["sector"]["available"] is False
    assert m["scorecard"]["pillars"]["relative_strength"]["reason"].startswith("not applicable")
    assert not any(a["anchor"] == "last_earnings" for a in m["volume_flow"]["anchored_vwaps"])
    assert chart_data["rs_spy"] is None and chart_data["earnings_dates"] == []


def test_other_etf_keeps_rs_vs_spy_and_skips_sector():
    fake = etf_market("QQQ", "1999-03-10")
    m, _ = run(fake, "QQQ")
    assert m["meta"]["network_calls"] == 11                # no earnings, no sector ETF
    assert m["dividends"]["available"] and m["dividends"]["pays_dividends"] is True     # ETFs pay dividends too
    assert m["macro_events"]["available"]
    assert not fake.calls_to(ADJ, "XLK")
    rs = m["relative_strength"]
    assert rs["vs_spy"]["available"] is True and rs["vs_sector"]["available"] is False
    assert rs["beta_1y"] is not None
    assert m["event_risk"]["available"] is False


# ---------------------------------------------------------------------------- degraded data
def test_short_history_skips_what_cannot_be_computed():
    start = pd.bdate_range(end="2026-10-16", periods=150)[0].strftime("%Y-%m-%d")
    fake = market(stock_start=start, profile=profile_row("ACME", ipo=start), earnings=[])
    m, chart_data = run(fake)
    assert m["snapshot"]["history"]["bars"] == 150
    tm = m["trend_momentum"]
    assert tm["mom_12_1"] is None and tm["sma"]["sma200"] is None and tm["returns"]["12m"] is None
    assert "12-1 momentum unavailable" in note_text(m, "trend_momentum")
    assert "SMA200" in note_text(m, "trend_momentum")
    assert m["regime"]["direction"]["votes"]["close_vs_sma200"] is None
    assert "direction inputs unavailable" in note_text(m, "regime")
    assert m["regime"]["volatility"]["label"] is None
    assert m["multi_timeframe"]["weekly"]["sign"] is None
    assert m["relative_strength"]["beta_1y"] is None and "beta" in note_text(m, "relative_strength")
    assert any("fewer than" in n or "only" in n for n in m["levels"]["notes"])
    assert m["event_risk"]["available"] is False and m["meta"]["skipped"]["earnings"] == "no data returned"
    assert m["scorecard"]["pillars"]["trend"]["votes"]["close_vs_sma200"] is None
    assert len(chart_data["candles"]) == 150 and chart_data["sma200"] == []


def test_restricted_dividend_adjusted_endpoint_falls_back_to_price_returns():
    fake = stock_market(blocked={(ADJ, "ACME")})
    m, _ = run(fake)
    assert m["data_quality"]["adj_coverage"] == 0.0
    assert ADJ in m["meta"]["restricted"]
    assert any("price return only" in n for n in m["meta"]["notes"])
    assert m["signals"]["history"]["adj_coverage"] == 0.0
    assert any("split-adjusted" in n for n in m["signals"]["notes"])
    assert m["trend_momentum"]["returns"]["3m"] is not None   # computed from split-adjusted closes
    assert m["relative_strength"]["vs_spy"]["available"] is True


def test_restricted_earnings_endpoint_skips_event_risk():
    fake = stock_market(blocked={"earnings"})
    m, chart_data = run(fake)
    assert m["meta"]["skipped"]["earnings"].startswith("restricted:")
    assert m["event_risk"]["available"] is False and m["event_risk"]["reason"].startswith("restricted:")
    assert "earnings" in m["meta"]["restricted"]
    assert not any(a["anchor"] == "last_earnings" for a in m["volume_flow"]["anchored_vwaps"])
    assert "last_earnings anchor unavailable" in note_text(m, "volume_flow")
    assert not any(c["type"] == "earnings_in_horizon" for c in m["scorecard"]["conflicts"])
    assert chart_data["earnings_dates"] == []


def test_restricted_calendar_dividends_and_macro_leave_their_modules_unavailable():
    fake = stock_market(blocked={HOLIDAYS, DIVIDENDS, ECON})
    m, _ = run(fake)
    meta = m["meta"]
    assert meta["network_calls"] == 13 and set(meta["restricted"]) == {HOLIDAYS, DIVIDENDS, ECON}
    assert set(meta["skipped"]) == {"dividends", "macro_events"}
    for name in ("dividends", "macro_events"):
        assert m[name]["available"] is False and m[name]["reason"].startswith("restricted:"), name
        assert meta["modules_unavailable"][name] == m[name]["reason"]
    cal = m["data_quality"]["calendar"]
    assert cal["source"] == "rules" and cal["problem"].startswith("restricted:")
    assert m["data_quality"]["stale"]["basis"] == "NYSE sessions"         # the rules still give NYSE sessions
    assert any("dividend data unavailable" in n for n in m["levels"]["notes"])
    assert m["levels"]["gap_adjustment"]["ex_dividend_bars"] == 0
    assert not any("Ex-dividend" in n for n in m["risk"]["notes"])
    assert all(m[k]["available"] for k in MODULES if k not in ("dividends", "macro_events"))


def test_failing_calendar_dividends_and_macro_are_errors_not_crashes():
    fake = stock_market()
    respond = fake.respond

    def flaky(endpoint, params):
        if endpoint in (HOLIDAYS, DIVIDENDS, ECON):
            raise fmp_client.FMPError(f"HTTP 500 on {endpoint} (rate limit or server error)")
        return respond(endpoint, params)
    fake.respond = flaky
    m, _ = analyze.run_analysis("ACME", client=fake, now=NOW)
    assert m["meta"]["errors"] == [HOLIDAYS, DIVIDENDS, ECON] and m["meta"]["restricted"] == []
    assert m["dividends"]["reason"].startswith("error:") and m["macro_events"]["reason"].startswith("error:")
    assert m["data_quality"]["calendar"]["source"] == "rules"
    assert m["scorecard"]["available"] and m["levels"]["available"] and m["signals"]["available"]


def test_macro_events_never_change_the_scorecard():
    with_macro, _ = run(stock_market())
    without, _ = run(stock_market(blocked={ECON}))
    assert with_macro["macro_events"]["available"] and not without["macro_events"]["available"]
    for k in ("scorecard", "scenarios", "levels", "signals", "risk", "dividends"):
        assert with_macro[k] == without[k], k


def short_market():
    """150 bars: the SMA100 exists, the SMA200 does not yet."""
    start = pd.bdate_range(end="2026-10-16", periods=150)[0].strftime("%Y-%m-%d")
    return market(stock_start=start, profile=profile_row("ACME", ipo=start), earnings=[])


@pytest.mark.parametrize("horizon, make", [("swing", stock_market), ("position", stock_market),
                                           ("swing", short_market)])
def test_sma100_never_changes_the_regime_scorecard_or_signals(monkeypatch, horizon, make):
    """SMA100 is context only: with absurd SMA100 values in the indicator frame, the regime, the scorecard,
    the signals, the SMA20/50/200 stack, everything weekly and the price-action EMA stay exactly as they were.
    Only the SMA100 readings and, through its low-weight level candidate, the levels and what is built from
    them may move: the scenarios, and the risk block's reference-only structural stop and level target (with
    their distances, touch rates, reward/risk and by_stop.structural size; contract stops_ref.*.structural).
    The volatility-band stop and target, the ATR stop and the headline position-size example do not use the
    levels, so they stay too."""
    normal, _ = run(make(), horizon=horizon)
    compute_all = indicators.compute_all

    def absurd(bars):
        ind = compute_all(bars)
        return ind.assign(sma100=ind["sma100"] + 1e6, sma100_slope=-1e6)
    monkeypatch.setattr(indicators, "compute_all", absurd)
    weird, _ = run(make(), horizon=horizon)

    tm_n, tm_w = normal["trend_momentum"]["sma"], weird["trend_momentum"]["sma"]
    assert tm_w["sma100"] > 1e6 and tm_w["slopes"]["sma100"] == -1e6        # the patch reached the run (6 sig. digits)
    assert any(c["source"] == "sma100" and c["price"] > 1e6 for c in weird["levels"]["candidates"])
    for k in ("sma20", "sma50", "sma200", "stack", "stack_state", "stack_periods"):
        assert tm_w[k] == tm_n[k], k
    assert weird["regime"] == normal["regime"]
    sc_n, sc_w = normal["scorecard"], weird["scorecard"]
    assert {p: v["score"] for p, v in sc_w["pillars"].items()} == {p: v["score"] for p, v in sc_n["pillars"].items()}
    assert (sc_w["weighted_score"], sc_w["bias"], sc_w["confidence"]) == \
        (sc_n["weighted_score"], sc_n["bias"], sc_n["confidence"])
    assert sc_w == sc_n
    assert weird["signals"] == normal["signals"]
    for k in ("market_context", "multi_timeframe", "mean_reversion", "price_action", "candlesticks"):
        assert weird[k] == normal[k], k
    # risk: only the reference-only structural stop / level target built from the zones may follow them
    for side in ("long_side", "short_side"):
        r_n, r_w = normal["risk"][side], weird["risk"][side]
        assert r_n["vol_target"] is not None                               # so first_target is not the level target
        for k in ("entry_ref", "sigma_h_pct", "vol_stop", "vol_target", "atr_stop", "first_target",
                  "first_target_sigma", "first_target_touch_rate", "vol_pair_odds"):
            assert r_w[k] == r_n[k], (side, k)
        for block in ("stop_distance_atr", "stop_distance_pct", "stop_distance_sigma", "stop_touch_rate",
                      "reward_risk"):
            assert {s: r_w[block][s] for s in ("vol", "atr")} == {s: r_n[block][s] for s in ("vol", "atr")}, block
        ex_n, ex_w = r_n["position_size_example"], r_w["position_size_example"]
        assert {k: v for k, v in ex_w.items() if k != "by_stop"} == {k: v for k, v in ex_n.items() if k != "by_stop"}
        assert {s: ex_w["by_stop"][s] for s in ("vol", "atr")} == {s: ex_n["by_stop"][s] for s in ("vol", "atr")}


def test_a_release_after_the_as_of_close_is_listed():
    # 16:30 New York on as_of 2026-10-16 is still 2026-10-16 in UTC: inside the window, which starts on as_of;
    # a release before that day's open is already in the as_of bar and is dropped
    extra = (("2026-10-16 20:30:00", "US", "Fed Chair Powell Speech", "High"),
             ("2026-10-16 12:30:00", "US", "Retail Sales MoM (Sep)", "High"))
    m, _ = run(stock_market(macro=macro_rows(MACRO_EVENTS + extra)))
    me = m["macro_events"]
    assert me["window"]["from"] == m["as_of"] == "2026-10-16"
    first = me["events"][0]
    assert {k: first[k] for k in ("id", "label", "tier", "date", "time_ny", "timing", "session", "trading_days_to",
                                  "calendar_days")} == \
        {"id": "other", "label": "Fed Chair Powell Speech", "tier": 3, "date": "2026-10-16", "time_ny": "16:30",
         "timing": "after_close", "session": "2026-10-19", "trading_days_to": 1, "calendar_days": 0}
    assert me["within_horizon"][0]["label"] == "Fed Chair Powell Speech"
    assert "retail_sales" not in [e["id"] for e in me["events"]]
    assert me["counts"] == {"tier1": 3, "tier2": 0, "tier3": 1, "excluded": 1, "unclassified": 2}


def test_partial_bar_is_dropped_during_the_session():
    m, _ = analyze.run_analysis("ACME", client=stock_market(), now=pd.Timestamp("2026-10-16 11:00", tz=NOW.tz))
    assert m["as_of"] == "2026-10-15"
    assert m["data_quality"]["partial_bar_excluded"]["date"] == "2026-10-16"
    assert m["levels"]["as_of"] == "2026-10-15" and m["signals"]["history"]["last_date"] == "2026-10-15"
    # the window starts on that as_of, whose 08:30 CPI is in the 2026-10-15 bar: not an upcoming event
    assert m["macro_events"]["window"]["from"] == "2026-10-15"
    assert [(e["id"], e["date"]) for e in m["macro_events"]["events"]][0] == ("cpi", "2026-10-20")


def test_a_failing_module_is_reported_and_the_rest_still_run(monkeypatch):
    def broken(ctx):
        raise KeyError("boom")
    monkeypatch.setattr(analyze, "BUILDERS", tuple((n, broken if n == "levels" else f) for n, f in analyze.BUILDERS))
    m, chart_data = analyze.run_analysis("ACME", client=stock_market(), now=NOW)
    assert m["levels"] == {"available": False, "reason": m["levels"]["reason"], "notes": []}
    assert m["levels"]["reason"].startswith("internal error: KeyError")
    assert m["meta"]["errors"] and m["meta"]["errors"][0].startswith("levels: KeyError")
    assert m["meta"]["modules_unavailable"]["levels"].startswith("internal error")
    assert m["scorecard"]["available"] and m["scenarios"]["available"] and m["signals"]["available"]
    assert chart_data is not None and chart_data["levels"]["available"] is False


def test_unknown_ticker_and_horizon_raise_value_errors():
    with pytest.raises(ValueError, match="no price data for NOPE"):
        analyze.run_analysis("nope", client=stock_market(), now=NOW)
    with pytest.raises(ValueError, match="unknown horizon"):
        analyze.run_analysis("ACME", "intraday", client=stock_market(), now=NOW)


# ---------------------------------------------------------------------------- real client + cache
@pytest.fixture
def real_client(tmp_path, monkeypatch):
    """A real FMPClient (official base, temp cache) whose HTTP layer is served by the fake market."""
    for var in ("FMP_BASE_URL", "FMP_PATH_PREFIX", "FMP_AUTH_STYLE", "FMP_TLS_VERIFY", "FMP_MAX_PER_MIN",
                "FMP_TIMEOUT", "FMP_CACHE_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(fmp_client.time, "sleep", lambda s: None)
    fake = stock_market()

    def make():
        c = fmp_client.FMPClient(api_key="TEST-KEY-XYZ", cache_dir=tmp_path)
        c._http_get = fake.http_get
        c._now_ny = lambda: NOW.to_pydatetime() + timedelta(hours=3)    # same evening
        return c
    return make, tmp_path


def test_same_day_rerun_with_the_real_client_makes_no_calls(real_client):
    make, cache = real_client
    first, _ = analyze.run_analysis("ACME", client=make(), now=NOW)
    assert first["meta"]["network_calls"] == 13 and first["meta"]["errors"] == []
    assert first["dividends"]["available"] and first["macro_events"]["available"]
    assert first["data_quality"]["calendar"]["source"] == "fmp+rules"
    stamp = NOW.timestamp()                       # the cache was written right after the close
    for f in cache.glob("*.json"):
        os.utime(f, (stamp, stamp))
    second, _ = analyze.run_analysis("ACME", client=make(), now=NOW)
    assert second["meta"]["network_calls"] == 0 and second["meta"]["cache_hits"] == 13
    drop = ("meta",)
    assert {k: v for k, v in first.items() if k not in drop} == {k: v for k, v in second.items() if k not in drop}
    assert not any("TEST-KEY-XYZ" in f.read_text(encoding="utf-8") or "TEST-KEY-XYZ" in f.name
                   for f in cache.iterdir() if f.is_file())


# ---------------------------------------------------------------------------- CLI
@pytest.fixture
def cli(monkeypatch):
    fake = stock_market()
    monkeypatch.setattr(analyze.fmp_client, "FMPClient", lambda refresh=False: fake)
    monkeypatch.setattr(fetch, "market_now", lambda: NOW)
    return fake


def test_cli_writes_metrics_and_chart(cli, tmp_path, capsys):
    out = tmp_path / "run"
    assert analyze.main(["acme", "--horizon", "swing", "--out", str(out)]) == 0
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert list(metrics) == TOP_KEYS and metrics["ticker"] == "ACME"
    html = (out / "chart.html").read_text(encoding="utf-8")
    assert 'id="ta-data"' in html and "TAChart.boot" in html
    text = capsys.readouterr().out
    for piece in ("ACME  swing  as of 2026-10-16", "metrics.json", "chart.html", "regime", "bias", "API calls    : 13",
                  f"ex-dividend  : {NEXT_EX_DATE} (6 trading days), $0.5",
                  "macro events : CPI 2026-10-20, FOMC rate decision 2026-10-28 (tier 1, inside the 10-day horizon)"):
        assert piece in text


def test_cli_default_out_dir_and_position(cli, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert analyze.main(["ACME", "--horizon", "position"]) == 0
    folder = tmp_path / "ta-output" / "ACME_2026-10-16"
    assert (folder / "metrics.json").is_file() and (folder / "chart.html").is_file()
    assert json.loads((folder / "metrics.json").read_text(encoding="utf-8"))["horizon"] == "position"


def test_cli_unknown_ticker_exits_1(cli, tmp_path, capsys):
    assert analyze.main(["NOPE", "--out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "Cannot analyse NOPE" in err and "no price data" in err
    assert not (tmp_path / "metrics.json").exists()


@pytest.mark.parametrize("setting, value", [("SMA_PERIODS", (20, 50, 100)), ("SMA_PERIODS", (20, 100, 200)),
                                            ("SMA_CONTEXT_PERIODS", (150,))])
def test_cli_refuses_an_edited_sma_layout(cli, tmp_path, monkeypatch, capsys, setting, value):
    """A replaced trend period used to run to the end without an error, with the 100-day mean as the regime's
    "SMA200" and twice as a level; another context period broke the chart. Both now stop before any module."""
    monkeypatch.setattr(C, setting, value)
    assert analyze.main(["ACME", "--out", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert f"Cannot analyse ACME: config.{setting} must be" in err
    assert "Restore that setting in scripts/config.py." in err and "Check the symbol" not in err
    assert not (tmp_path / "out").exists()


def test_cli_missing_key_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setenv("FMP_CACHE_DIR", str(tmp_path / "cache"))
    assert analyze.main(["ACME", "--out", str(tmp_path / "out")]) == 2
    assert "FMP_API_KEY is not set" in capsys.readouterr().err


def test_cli_fmp_error_exits_1_without_the_key(monkeypatch, tmp_path, capsys):
    key = "SECRET-KEY-987"
    monkeypatch.setenv("FMP_API_KEY", key)

    class Down(FakeClient):
        def get(self, endpoint, ttl=None, **params):
            raise fmp_client.FMPError(f"cannot reach host: https://x/stable/{endpoint}?apikey={key}")

    monkeypatch.setattr(analyze.fmp_client, "FMPClient", lambda refresh=False: Down({}))
    monkeypatch.setattr(fetch, "market_now", lambda: NOW)
    assert analyze.main(["ACME", "--out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "Cannot analyse ACME" in err and "fmp_client.py check" in err
    assert key not in err and "apikey=***" in err


def test_full_history_uses_two_chunks_per_price_endpoint():
    fake = stock_market()
    run(fake)
    assert len(fake.calls_to(FULL, "ACME")) == 2 and len(fake.calls_to(ADJ, "ACME")) == 2


def test_spy_market_context_and_regime_use_one_price_basis():
    """SPY analysed as the stock: with dividends the adjusted and split-adjusted SMA200 differ, and a
    close just below the split-adjusted SMA200 must read "below" in both sections."""
    from conftest import synthetic_bars
    n = 1500
    idx = pd.bdate_range(end="2026-10-16", periods=n, name="date")
    df = synthetic_bars(n, seed=8, drift=0.0, vol=0.01)
    df.index = idx
    scale = df["close"].rolling(200).mean().iloc[-1] * 0.998 / df["close"].iloc[-1]
    for col in ("open", "high", "low", "close"):
        df.iloc[-1, df.columns.get_loc(col)] *= scale
    div = np.zeros(n)
    div[::63] = 0.0035                                           # 0.35% quarterly dividends
    factor = np.r_[np.cumprod((1 - div)[::-1])[::-1][1:], 1.0]
    for col in ("open", "high", "low", "close"):
        df["adj_" + col] = df[col] * factor
    fake = etf_market("SPY", str(idx[0].date()), sector="Financial Services")
    fake.prices["SPY"] = df
    m, _ = run(fake, "SPY")
    spy, direction = m["market_context"]["spy"], m["regime"]["direction"]
    assert spy["close"] == direction["close"] and spy["sma200"] == pytest.approx(direction["sma200"])
    assert spy["above_sma200"] is False and direction["votes"]["close_vs_sma200"] == -1
    assert spy["price_basis"].startswith("split-adjusted")


def test_cli_history_start_cuts_history(cli, tmp_path, capsys):
    out = tmp_path / "run"
    assert analyze.main(["ACME", "--history-start", "2015-01-01", "--out", str(out)]) == 0
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["data_quality"]["history_start_override"] == "2015-01-01"
    assert metrics["data_quality"]["first_date"] >= "2015-01-01"


def test_cli_bad_history_start_exits_1(cli, tmp_path, capsys):
    assert analyze.main(["ACME", "--history-start", "not-a-date", "--out", str(tmp_path)]) == 1
    assert "not a date" in capsys.readouterr().err


def test_cli_prints_the_spac_warning(monkeypatch, tmp_path, capsys):
    df = series("2021-01-04", seed=11).copy()
    for c in ("open", "high", "low", "close"):
        df.iloc[:130, df.columns.get_loc(c)] = 10.4
        df["adj_" + c] = df[c] * 0.98
    fake = market(stock="IONQ", prices={"IONQ": df}, profile=profile_row("IONQ", ipo="2021-01-04"))
    monkeypatch.setattr(analyze.fmp_client, "FMPClient", lambda refresh=False: fake)
    monkeypatch.setattr(fetch, "market_now", lambda: NOW)
    assert analyze.main(["IONQ", "--out", str(tmp_path / "run")]) == 0
    assert "looks like a pre-merger SPAC shell" in capsys.readouterr().out
