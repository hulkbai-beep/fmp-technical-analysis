"""End-to-end runs of analyze.run_analysis / analyze.main on a fake FMP (no network)."""
import json
import os
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

import analyze
import config as C
import fetch
import fmp_client
from fakes import ADJ, FULL, NOW, FakeClient, earnings_history, market, profile_row, series

TOP_KEYS = ["ticker", "as_of", "data_source", "horizon", "snapshot", "data_quality", "market_context",
            "regime", "trend_momentum", "relative_strength", "mean_reversion", "volume_flow", "volatility",
            "levels", "multi_timeframe", "event_risk", "signals", "scorecard", "scenarios", "risk", "meta"]
MODULES = TOP_KEYS[6:-1]
META_KEYS = {"network_calls", "cache_hits", "restricted", "errors", "skipped", "notes", "connection",
             "config_snapshot"}


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


def note_text(metrics, *modules):
    return " | ".join(n for m in modules for n in metrics[m].get("notes") or [])


# ---------------------------------------------------------------------------- stock
def test_stock_swing_run():
    fake = stock_market()
    m, chart_data = run(fake)
    meta = m["meta"]
    assert meta["network_calls"] == 10 and meta["cache_hits"] == 0      # the pinned call budget
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
    assert m["meta"]["network_calls"] == 7
    assert set(m["meta"]["skipped"]) == {"earnings", "sector"}
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
    assert m["meta"]["network_calls"] == 8                 # no earnings, no sector ETF
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


def test_partial_bar_is_dropped_during_the_session():
    m, _ = analyze.run_analysis("ACME", client=stock_market(), now=pd.Timestamp("2026-10-16 11:00", tz=NOW.tz))
    assert m["as_of"] == "2026-10-15"
    assert m["data_quality"]["partial_bar_excluded"]["date"] == "2026-10-16"
    assert m["levels"]["as_of"] == "2026-10-15" and m["signals"]["history"]["last_date"] == "2026-10-15"


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
    assert first["meta"]["network_calls"] == 10 and first["meta"]["errors"] == []
    stamp = NOW.timestamp()                       # the cache was written right after the close
    for f in cache.glob("*.json"):
        os.utime(f, (stamp, stamp))
    second, _ = analyze.run_analysis("ACME", client=make(), now=NOW)
    assert second["meta"]["network_calls"] == 0 and second["meta"]["cache_hits"] == 10
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
    for piece in ("ACME  swing  as of 2026-10-16", "metrics.json", "chart.html", "regime", "bias", "API calls    : 10"):
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
