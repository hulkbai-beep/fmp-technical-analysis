"""chart.py (chart data + self-contained chart.html) and ui/server.py. No network beyond 127.0.0.1."""
import http.client
import importlib.util
import json
import re
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import chart
import config as C
import indicators as I
from conftest import synthetic_bars

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "ui"


# ---------------------------------------------------------------------------- fixtures
def _ctx(ticker="TEST", horizon="swing", n=600, is_etf=False, **extra):
    bars = synthetic_bars(n=n, seed=3)
    spy = synthetic_bars(n=n, seed=9, drift=0.0002, vol=0.01, start_price=400.0)
    dates = bars.index
    earnings = [{"date": str(dates[i].date()), "eps_actual": 1.0, "eps_estimated": 0.9,
                 "revenue_actual": None, "revenue_estimated": None} for i in range(30, n, 63)]
    next_date = dates[-1] + pd.offsets.BDay(8)
    earnings.append({"date": str(next_date.date()), "eps_actual": None, "eps_estimated": 1.2,
                     "revenue_actual": None, "revenue_estimated": None})
    ctx = dict(ticker=ticker, as_of=str(dates[-1].date()), horizon=horizon, hz=C.HORIZONS[horizon],
               bars=bars, ind=I.compute_all(bars), spy=spy, sector=None, sector_etf=None, vix=None,
               profile={"companyName": "Test Corp", "currency": "USD", "exchange": "NASDAQ"}, quote=None,
               earnings=None if is_etf else earnings, is_etf=is_etf,
               data_quality={"partial_bar_excluded": None, "stale": {"is_stale": False}, "warnings": [],
                             "liquidity": {"illiquid": False}},
               notes=[], skipped={}, connection={"route": "official FMP API"})
    ctx.update(extra)
    return ctx


def _modules(ctx):
    """Minimal module outputs in the CONTRACT shapes (independent of the other builders)."""
    bars = ctx["bars"]
    close = float(bars["close"].iloc[-1])
    lo, hi = float(bars["low"].iloc[-126:].min()), float(bars["high"].iloc[-126:].max())
    edges = np.linspace(lo, hi, 11)
    bins = [{"low": a, "high": b, "volume": float(1000 + 100 * i)} for i, (a, b) in enumerate(zip(edges[:-1], edges[1:]))]
    level = lambda p, src: {"price": p, "zone_low": p * 0.99, "zone_high": p * 1.01, "sources": src,  # noqa: E731
                            "touches": 5, "volume_share": 0.1, "score": 1.5,
                            "distance_pct": p / close - 1, "distance_atr": (p - close) / 2.0}
    anchor = str(bars.index[-40].date())
    return {
        "regime": {"available": True, "label": "Strong uptrend / normal vol", "type": "trend", "direction_sign": 1,
                   "direction": {"label": "Strong uptrend"}, "strength": {"adx": 31.2, "label": "trending"},
                   "volatility": {"label": "normal", "vol_pctl": 48.0, "rv20": 0.22, "compression": False}},
        "multi_timeframe": {"available": True, "weekly": {"direction": "Uptrend"}, "alignment": "confirms",
                            "note": "Weekly uptrend confirms the daily one."},
        "volume_flow": {"available": True, "anchored_vwaps": [{"anchor": "ytd_start", "anchor_date": anchor, "vwap": close}]},
        "levels": {"available": True, "supports": [level(close * 0.97, ["pivot_low", "poc"])],
                   "resistances": [level(close * 1.04, ["pivot_high", "avwap_high_52w"])],
                   "high_52w": hi, "low_52w": lo, "lookback_bars": 126, "window_start": str(bars.index[-126].date()),
                   "volume_profile": {"available": True, "poc": (edges[9] + edges[10]) / 2, "value_area_low": edges[3],
                                      "value_area_high": edges[10], "bins": bins, "hvn": [], "lvn": [], "lookback_bars": 126}},
        "signals": {"available": True, "primary_horizon": 10, "active": [{
            "id": "uptrend_pullback", "name": "Uptrend pullback", "bias": 1, "pillar": "mean_reversion",
            "fired_date": str(bars.index[-1].date()), "bars_ago": 0, "regime_used": "above_200", "confidence": "normal",
            "horizons": {10: {"n": 41, "hit_rate": 0.61, "median": 0.012, "p25": -0.01, "p75": 0.03, "median_mae": -0.02,
                              "confidence": "normal", "same_regime": {"median": 0.006, "hit_rate": 0.55},
                              "edge": {"median": 0.006, "hit_rate": 0.06}}}}]},
        "scorecard": {"pillars": {p: {"score": s, "reason": f"{p} reason"} for p, s in
                                  zip(C.PILLARS, (2, 1, 1, 0, -1, -2))},
                      "weights": C.REGIME_WEIGHTS["trend"], "weighted_score": 0.65, "bias": "Moderately bullish",
                      "confidence": {"level": "medium", "reasons": ["4 of 6 pillars agree"]},
                      "conflicts": [{"description": "Trend up but extended", "typical_meaning": "..."}]},
        "event_risk": {"available": False, "reason": "test: no event module"},
        "scenarios": {"horizon_days": 10, "base": {"expected_move_1sd_pct": 0.05}},
    }


def _data(**kw):
    ctx = _ctx(**kw)
    return ctx, chart.build_chart_data(ctx, _modules(ctx))


# ---------------------------------------------------------------------------- build_chart_data
def test_chart_data_is_json_safe_and_sized_to_the_window():
    ctx, d = _data()
    json.dumps(d, allow_nan=False)
    n = C.HORIZONS["swing"]["chart_bars"]
    win = ctx["bars"].iloc[-n:]
    assert len(d["candles"]) == n
    assert d["candles"][0][0] == str(win.index[0].date()) and d["candles"][-1][0] == ctx["as_of"]
    # split-adjusted OHLCV (not the dividend-adjusted columns), prices rounded to 4 decimals
    assert d["candles"][-1][4] == round(float(win["close"].iloc[-1]), 4)
    assert d["candles"][-1][5] == int(round(win["volume"].iloc[-1]))
    for key in ("sma20", "sma50", "sma200", "rsi14", "rvol"):
        assert 0 < len(d[key]) <= n
        assert d[key][-1][0] == ctx["as_of"]
    assert d["sma200"][-1][1] == pytest.approx(ctx["ind"]["sma200"].iloc[-1], abs=1e-4)
    assert d["weekly"][-1][0] == ctx["as_of"] and d["weekly"][0][0] >= d["candles"][0][0]
    assert d["default_timeframe"] == "daily"
    assert d["meta"]["ticker"] == "TEST" and d["meta"]["chart_bars"] == n


def test_rs_line_is_normalized_and_absent_for_spy():
    _, d = _data()
    assert d["rs_spy"][0][1] == pytest.approx(100.0)
    assert d["rs_spy"][0][0] == d["candles"][0][0]
    _, spy = _data(ticker="SPY", is_etf=True)
    assert spy["rs_spy"] is None and "benchmark" in spy["rs_spy_note"]
    assert spy["earnings_dates"] == []


def test_earnings_markers_cover_the_window_and_the_next_report():
    ctx, d = _data()
    past = [e for e in d["earnings_dates"] if e["kind"] == "past"]
    nxt = [e for e in d["earnings_dates"] if e["kind"] == "next"]
    assert past and all(d["candles"][0][0] <= e["date"] <= ctx["as_of"] for e in past)
    assert len(nxt) == 1 and nxt[0]["date"] > ctx["as_of"]
    assert d["key_numbers"]["next_earnings"]["date"] == nxt[0]["date"]


def test_summary_panels_follow_the_module_outputs():
    _, d = _data()
    assert d["regime"]["label"] == "Strong uptrend / normal vol" and d["regime"]["alignment"] == "confirms"
    assert [p["name"] for p in d["scorecard"]["pillars"]] == list(C.PILLARS)
    assert d["scorecard"]["bias"] == "Moderately bullish"
    row = d["signals"]["active"][0]
    assert (row["n"], row["hit_rate"], row["edge_median"], row["confidence"]) == (41, 0.61, 0.006, "normal")
    assert d["levels"]["supports"][0]["sources"] == ["pivot_low", "poc"]
    vp = d["volume_profile"]
    assert vp["available"] and len(vp["bins"]) == 10 and vp["val"] < vp["poc"] <= vp["vah"]
    assert {"module": "event_risk", "reason": "test: no event module"} in d["data_notes"]["skipped"]
    assert len(d["data_notes"]["approximations"]) == 2


def test_avwap_series_fall_back_to_volume_flow_anchors(monkeypatch):
    monkeypatch.setitem(sys.modules, "modules", None)   # import modules -> ImportError
    ctx, d = _data()
    expected = I.anchored_vwap(ctx["bars"], pd.Timestamp(d["avwap_anchors"]["ytd_start"]))
    assert list(d["avwaps"]) == ["ytd_start"]
    assert d["avwaps"]["ytd_start"][-1][1] == pytest.approx(expected.iloc[-1], abs=1e-4)
    assert any("anchored VWAP" in s for s in d["data_notes"]["notes"])


def test_short_history_and_position_mode():
    ctx, d = _data(n=150, horizon="position")
    json.dumps(d, allow_nan=False)
    assert len(d["candles"]) == 150 and d["sma200"] == []
    assert d["default_timeframe"] == "weekly"


def test_bars_after_as_of_are_not_charted():
    ctx = _ctx()
    ctx["as_of"] = str(ctx["bars"].index[-6].date())
    d = chart.build_chart_data(ctx, _modules(ctx))
    assert d["candles"][-1][0] == ctx["as_of"]


# ---------------------------------------------------------------------------- write_chart_html
def _embedded(html: str):
    m = re.search(r'<script type="application/json" id="ta-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_chart_html_is_self_contained(tmp_path):
    _, d = _data()
    path = chart.write_chart_html(d, tmp_path / "out" / "chart.html")
    html = path.read_text(encoding="utf-8")
    assert _embedded(html) == chart.json_safe(d)
    assert "TAChart.boot(" in html and "function draw(st, W)" in html and "TA_I18N" in html
    assert "--sma20" in html  # inlined CSS
    assert not re.search(r'(src|href)\s*=\s*["\']?\s*(https?:)?//', html, re.I)
    assert "<link" not in html and "@import" not in html
    assert html.count("<script") == html.count("</script>") == 4


def test_chart_html_escapes_script_breakouts(tmp_path):
    _, d = _data()
    evil = '</script><script>alert(1)</script><!-- & "quotes"'
    d["data_notes"]["notes"].append(evil)
    html = chart.write_chart_html(d, tmp_path / "chart.html").read_text(encoding="utf-8")
    assert html.count("</script>") == 4 and "alert(1)</script>" not in html
    assert evil in _embedded(html)["data_notes"]["notes"]


def test_ui_sources_cannot_break_out_of_inline_tags():
    for name in ("chart.js", "i18n.js", "chart.css"):
        text = (UI / name).read_text(encoding="utf-8").lower()
        assert "</script" not in text and "</style" not in text and "<!--" not in text


# ---------------------------------------------------------------------------- ui/server.py
@pytest.fixture
def server(monkeypatch):
    spec = importlib.util.spec_from_file_location("ta_ui_server", UI / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []

    def run_analysis(ticker, horizon="swing", **kw):
        calls.append((ticker, horizon, kw))
        if ticker == "BOOM":
            raise RuntimeError(f"upstream failed for https://x/?apikey={key}&symbol=BOOM")
        if ticker == "NODATA":
            raise ValueError("no price data for NODATA")
        return {"ticker": ticker, "horizon": horizon, "x": float("nan")}, {"meta": {"ticker": ticker}}

    key = "SECRETKEY123456"
    monkeypatch.setenv("FMP_API_KEY", key)
    monkeypatch.setitem(sys.modules, "analyze", types.SimpleNamespace(run_analysis=run_analysis))
    srv = mod.make_server(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield types.SimpleNamespace(port=srv.server_address[1], calls=calls, key=key, mod=mod)
    srv.shutdown()
    srv.server_close()


def _get(port, path, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"Host": host} if host else {})
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res.status, res.getheader("Content-Type"), body


def test_server_serves_the_app_and_static_files(server):
    code, ctype, body = _get(server.port, "/")
    assert code == 200 and ctype.startswith("text/html") and b"/api/analyze" in body
    for name, kind in (("chart.js", "javascript"), ("i18n.js", "javascript"), ("chart.css", "css")):
        code, ctype, _ = _get(server.port, "/" + name)
        assert code == 200 and kind in ctype


def test_server_analyze_returns_metrics_and_chart(server):
    code, ctype, body = _get(server.port, "/api/analyze?symbol=brk.b&horizon=position")
    data = json.loads(body)
    assert code == 200 and ctype.startswith("application/json")
    assert data["metrics"]["ticker"] == "BRK.B" and data["metrics"]["x"] is None
    assert data["chart"]["meta"]["ticker"] == "BRK.B"
    assert server.calls[-1] == ("BRK.B", "position", {})
    _get(server.port, "/api/analyze?symbol=%5EVIX&refresh=1")
    assert server.calls[-1] == ("^VIX", "swing", {"refresh": True})


@pytest.mark.parametrize("query", ["symbol=", "symbol=AAPL;rm", "symbol=TOOLONGSYMBOL", "symbol=A%20B",
                                   "symbol=AAPL&horizon=daily", "symbol=%3Cscript%3E"])
def test_server_rejects_bad_input(server, query):
    code, _, body = _get(server.port, "/api/analyze?" + query)
    assert code == 400 and "error" in json.loads(body)
    assert not server.calls


def test_server_errors_are_json_and_never_echo_the_key(server):
    code, _, body = _get(server.port, "/api/analyze?symbol=BOOM")
    assert code == 500 and server.key.encode() not in body and "RuntimeError" in json.loads(body)["error"]
    code, _, body = _get(server.port, "/api/analyze?symbol=NODATA")
    assert code == 400 and json.loads(body) == {"error": "no price data for NODATA"}


@pytest.mark.parametrize("path", ["/../scripts/config.py", "/%2e%2e/scripts/config.py", "/..%2fscripts%2fconfig.py",
                                  "/%2e%2e%5cscripts%5cconfig.py", "/server.py", "/.hidden.js", "/nope.js",
                                  "//etc/passwd", "/C:/Windows/win.ini"])
def test_server_blocks_traversal_and_unknown_files(server, path):
    code, _, body = _get(server.port, path)
    assert code == 404 and b"FMP" not in body


def test_server_rejects_foreign_host_headers(server):
    assert _get(server.port, "/", host="evil.example:80")[0] == 403
    assert _get(server.port, "/", host=f"localhost:{server.port}")[0] == 200


def test_proxy_tls_rule(server, monkeypatch):
    mod = server.mod
    monkeypatch.delenv("FMP_TLS_VERIFY", raising=False)
    monkeypatch.setenv("FMP_BASE_URL", "https://example.com")
    assert not mod.apply_proxy_tls_rule() and "FMP_TLS_VERIFY" not in __import__("os").environ
    monkeypatch.setenv("FMP_BASE_URL", "54.236.92.205")
    assert mod.apply_proxy_tls_rule() and __import__("os").environ["FMP_TLS_VERIFY"] == "0"
    monkeypatch.setenv("FMP_TLS_VERIFY", "1")
    assert not mod.apply_proxy_tls_rule() and __import__("os").environ["FMP_TLS_VERIFY"] == "1"
