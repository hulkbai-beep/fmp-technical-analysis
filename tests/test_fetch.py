"""Data layer tests: chunked fetch, validation, module skips and the client's cache rules (no network)."""
import json
import os
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

import config as C
import fetch
import fmp_client
from conftest import synthetic_bars

NY = "America/New_York"
NOW = pd.Timestamp("2026-10-16 18:00", tz=NY)          # Friday, after the close buffer
FMP_ROW_CAP = 5000                                      # what the real endpoint returns at most
FULL, ADJ = fetch.FULL, fetch.ADJUSTED


# ---------------------------------------------------------------------------- synthetic FMP
@lru_cache(maxsize=None)
def series(start, end="2026-10-16", seed=1, volume=None):
    """Synthetic daily bars on business days start..end (adj_* = 0.98 x split-adjusted)."""
    n = len(pd.bdate_range(start, end))
    df = synthetic_bars(n=n, seed=seed, start=start)
    return df if volume is None else df.assign(volume=float(volume))


def profile_row(symbol, sector="Technology", ipo="1980-12-12", etf=False):
    return {
        "symbol": symbol, "price": 100.0, "marketCap": 1e11, "beta": 1.1, "lastDividend": 1.0,
        "range": "80-120", "change": 0.5, "changePercentage": 0.5, "volume": 1_000_000,
        "averageVolume": 1_000_000, "companyName": f"{symbol} Inc.", "currency": "USD",
        "cik": "0000000001", "isin": "US0000000001", "cusip": "000000001",
        "exchangeFullName": "NASDAQ Global Select", "exchange": "NASDAQ", "industry": "Software",
        "website": "https://example.com", "description": "Synthetic.", "ceo": "A. Person",
        "sector": sector, "country": "US", "fullTimeEmployees": "1000", "phone": "000",
        "address": "1 Main St", "city": "Town", "state": "CA", "zip": "00000", "image": "",
        "ipoDate": ipo, "defaultImage": False, "isEtf": etf, "isActivelyTrading": True,
        "isAdr": False, "isFund": False,
    }


def quote_row(symbol):
    return {"symbol": symbol, "name": symbol, "price": 100.0, "changePercentage": 0.5, "change": 0.5,
            "volume": 1_000_000, "dayLow": 99.0, "dayHigh": 101.0, "yearHigh": 120.0, "yearLow": 80.0,
            "marketCap": 1e11, "priceAvg50": 98.0, "priceAvg200": 95.0, "exchange": "NASDAQ",
            "open": 99.5, "previousClose": 99.5, "timestamp": 1792180800}


def earnings_rows(symbol):
    """Newest first, with an upcoming report (epsActual null)."""
    return [
        {"symbol": symbol, "date": "2026-11-02", "epsActual": None, "epsEstimated": 1.6,
         "revenueActual": None, "revenueEstimated": 1.0e11, "lastUpdated": "2026-10-16"},
        {"symbol": symbol, "date": "2026-07-30", "epsActual": 1.5, "epsEstimated": 1.4,
         "revenueActual": 9.5e10, "revenueEstimated": 9.4e10, "lastUpdated": "2026-07-31"},
        {"symbol": symbol, "date": "2026-05-01", "epsActual": 1.3, "epsEstimated": 1.35,
         "revenueActual": 9.0e10, "revenueEstimated": 9.1e10, "lastUpdated": "2026-05-02"},
    ]


class FakeClient:
    """Stands in for fmp_client.FMPClient: serves FMP-shaped JSON (EOD rows newest first, the
    newest FMP_ROW_CAP rows within from..to) and counts every request as a network call."""

    def __init__(self, prices, profiles=None, quotes=None, earnings=None, adjusted=None,
                 blocked=(), indicator_rows=None):
        self.prices, self.adjusted = prices, adjusted or {}
        self.profiles, self.quotes, self.earnings = profiles or {}, quotes or {}, earnings or {}
        self.blocked = set(blocked)            # endpoint or (endpoint, symbol) -> 403-style restriction
        self.indicator_rows = indicator_rows or {}
        self.stats = {"network_calls": 0, "cache_hits": 0}
        self.restricted, self.errors, self.requests = [], [], []

    def get(self, endpoint, ttl=None, **params):
        self.stats["network_calls"] += 1
        self.requests.append((endpoint, dict(params)))
        sym = params.get("symbol")
        if endpoint in self.blocked or (endpoint, sym) in self.blocked:
            raise fmp_client.RestrictedEndpoint(f"{endpoint}: not included in your plan (Premium endpoint)")
        if endpoint == "profile":
            return [self.profiles[sym]] if sym in self.profiles else []
        if endpoint == "quote":
            return [self.quotes[sym]] if sym in self.quotes else []
        if endpoint == "earnings":
            return self.earnings.get(sym, [])
        if endpoint.startswith("technical-indicators/"):
            return self.indicator_rows.get((endpoint, sym), [])
        if endpoint in (FULL, ADJ):
            return self._eod(endpoint, sym, params)
        raise fmp_client.FMPError(f"HTTP 404 on {endpoint}")

    def try_get(self, endpoint, **params):
        return fmp_client.get_with_reason(self, endpoint, **params)[0]

    def record_failure(self, endpoint, exc, params):
        (self.restricted if isinstance(exc, fmp_client.RestrictedEndpoint) else self.errors).append(endpoint)

    def describe(self):
        return {"route": "fake", "base": "fake://", "prefix": "stable", "auth_style": "header",
                "source": "test", "tls_verify": True}

    def _eod(self, endpoint, sym, params):
        df = (self.adjusted.get(sym) if endpoint == ADJ else None)
        df = self.prices.get(sym) if df is None else df
        if df is None:
            return []
        lo, hi = pd.Timestamp(params["from"]), pd.Timestamp(params["to"])
        win = df[(df.index >= lo) & (df.index <= hi)].iloc[::-1].head(FMP_ROW_CAP)
        dates = win.index.strftime("%Y-%m-%d")
        if endpoint == FULL:
            change = (win["close"] - win["open"]).to_numpy()
            vwap = win[["open", "high", "low", "close"]].mean(axis=1).to_numpy()
            return [{"symbol": sym, "date": d, "open": o, "high": h, "low": l, "close": c, "volume": v,
                     "change": ch, "changePercent": ch / o * 100, "vwap": vw}
                    for d, o, h, l, c, v, ch, vw in zip(dates, win["open"], win["high"], win["low"],
                                                        win["close"], win["volume"], change, vwap)]
        return [{"symbol": sym, "date": d, "adjOpen": o, "adjHigh": h, "adjLow": l, "adjClose": c, "volume": v}
                for d, o, h, l, c, v in zip(dates, win["adj_open"], win["adj_high"], win["adj_low"],
                                            win["adj_close"], win["volume"])]

    def calls_to(self, endpoint, symbol):
        return [p for e, p in self.requests if e == endpoint and p.get("symbol") == symbol]


def market(stock="ACME", stock_start="1996-10-01", end="2026-10-16", profile=None, **kw):
    """A fake FMP with one stock (30 years by default), SPY, XLK and ^VIX."""
    prices = {
        stock: series(stock_start, end, seed=3),
        "SPY": series("1993-01-29", end, seed=5),
        "XLK": series("1998-12-22", end, seed=7),
        C.VIX: series("2000-01-03", end, seed=9, volume=0),
    }
    prices.update(kw.pop("prices", {}))
    profiles = {stock: profile or profile_row(stock), "SPY": profile_row("SPY", "Financial Services", "1993-01-22", etf=True)}
    return FakeClient(prices, profiles=profiles, quotes={stock: quote_row(stock), "SPY": quote_row("SPY")},
                      earnings={stock: earnings_rows(stock)}, **kw)


def day(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------- chunked history
def test_thirty_years_take_two_full_calls_and_merge_cleanly():
    fake = market()
    out = fetch.load_all(fake, "ACME", now=NOW)
    full = fake.calls_to(FULL, "ACME")
    assert len(full) == 2
    assert pd.Timestamp(full[1]["to"]) == pd.Timestamp(full[0]["from"]) - pd.Timedelta(days=1)
    bars, src = out["bars"], series("1996-10-01", seed=3)
    assert bars.index.is_unique and bars.index.is_monotonic_increasing
    assert list(bars.columns) == fetch.COLUMNS
    assert list(bars.index.strftime("%Y-%m-%d")) == list(src.index.strftime("%Y-%m-%d"))
    np.testing.assert_allclose(bars["close"].to_numpy(), src["close"].to_numpy())
    assert out["data_quality"]["duplicates_dropped"] == 0


def test_normal_stock_uses_ten_calls():
    fake = market()
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert out["calls"] == 10 == fake.stats["network_calls"]
    assert [e for e, _ in fake.requests] == ["profile", "quote", "earnings", FULL, FULL, ADJ, ADJ, ADJ, ADJ, FULL]
    assert out["sector_etf"] == "XLK" and out["sector"] is not None and out["spy"] is not None
    assert out["skipped"] == {}
    assert out["connection"]["route"] == "fake"


def test_ipo_date_short_circuits_the_older_chunk():
    start = "2008-10-22"   # a few days after the first chunk's `from` (2008-10-17)
    with_ipo = market(stock_start=start, profile=profile_row("ACME", ipo=start))
    fetch.load_all(with_ipo, "ACME", now=NOW)
    assert len(with_ipo.calls_to(FULL, "ACME")) == 1

    without = market(stock_start=start, profile=profile_row("ACME", ipo=None))
    out = fetch.load_all(without, "ACME", now=NOW)
    assert len(without.calls_to(FULL, "ACME")) == 2          # the older chunk comes back empty
    assert day(out["bars"].index[0]) == start


def test_row_cap_continues_from_the_earliest_returned_date(monkeypatch):
    monkeypatch.setattr(C, "CHUNK_YEARS", 25)                # first request spans > 5000 sessions
    fake = market()
    out = fetch.load_all(fake, "ACME", now=NOW)
    full = fake.calls_to(FULL, "ACME")
    first = fake._eod(FULL, "ACME", full[0])
    assert len(first) == FMP_ROW_CAP
    assert pd.Timestamp(full[1]["to"]) == pd.Timestamp(first[-1]["date"]) - pd.Timedelta(days=1)
    assert len(out["bars"]) == len(series("1996-10-01", seed=3))
    assert out["bars"].index.is_unique


def test_fetch_history_reports_a_failed_older_chunk():
    class FlakyOlder(FakeClient):
        def get(self, endpoint, ttl=None, **params):
            if self.requests:
                self.stats["network_calls"] += 1
                self.requests.append((endpoint, dict(params)))
                raise fmp_client.FMPError("HTTP 500 on historical-price-eod/full (rate limit or server error)")
            return super().get(endpoint, ttl, **params)

    fake = FlakyOlder({"ACME": series("1996-10-01", seed=3)})
    rows, problem = fetch.fetch_history(fake, FULL, "ACME", pd.Timestamp("1990-01-01"), pd.Timestamp("2026-10-16"))
    assert len(rows) > 4000
    assert problem.startswith("history up to") and "error:" in problem
    assert fake.errors == [FULL]


# ---------------------------------------------------------------------------- adjusted prices
def test_dividend_adjusted_prices_are_merged_on_full_dates():
    out = fetch.load_all(market(), "ACME", now=NOW)
    bars = out["bars"]
    np.testing.assert_allclose(bars["adj_close"].to_numpy(), 0.98 * bars["close"].to_numpy())
    np.testing.assert_allclose(bars["adj_low"].to_numpy(), 0.98 * bars["low"].to_numpy())
    assert out["data_quality"]["adj_coverage"] == 1.0


def test_shorter_adjusted_history_leaves_nan_and_is_reported():
    adj = series("1996-10-01", seed=3).loc["2010-01-04":]
    out = fetch.load_all(market(adjusted={"ACME": adj}), "ACME", now=NOW)
    bars = out["bars"]
    assert bars.loc[:"2009-12-31", "adj_close"].isna().all()
    assert bars.loc["2010-01-04":, "adj_close"].notna().all()
    assert 0 < out["data_quality"]["adj_coverage"] < 1
    assert any("cover" in n and "2010-01-04" in n for n in out["notes"])


def test_restricted_adjusted_endpoint_falls_back_to_price_returns():
    fake = market(blocked={(ADJ, "ACME")})
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert out["bars"]["adj_close"].isna().all()
    assert out["data_quality"]["adj_coverage"] == 0.0
    assert any("price return only" in n for n in out["notes"])
    assert any(n.startswith("dividend-adjusted prices: restricted") for n in out["notes"])


# ---------------------------------------------------------------------------- module paths
def test_etf_skips_earnings_and_sector():
    fake = market(stock="QQQ", stock_start="1999-03-10", profile=profile_row("QQQ", "Technology", "1999-03-10", etf=True))
    out = fetch.load_all(fake, "QQQ", now=NOW)
    assert out["is_etf"] is True
    assert not [e for e, _ in fake.requests if e == "earnings"]
    assert not fake.calls_to(ADJ, "XLK")
    assert out["earnings"] is None and out["sector"] is None and out["sector_etf"] is None
    assert set(out["skipped"]) == {"earnings", "sector"}
    assert out["spy"] is not None
    assert out["calls"] == 2 + len(fake.calls_to(FULL, "QQQ")) + len(fake.calls_to(ADJ, "QQQ")) + 2


def test_spy_as_ticker_reuses_its_own_bars_as_benchmark():
    fake = market(stock="SPY", stock_start="1993-01-29",
                  profile=profile_row("SPY", "Financial Services", "1993-01-22", etf=True))
    out = fetch.load_all(fake, "SPY", now=NOW)
    bench_from = day(NOW.tz_localize(None).normalize() - pd.DateOffset(years=C.BENCHMARK_YEARS))
    assert not [p for p in fake.calls_to(ADJ, "SPY") if p["from"] == bench_from]
    assert out["calls"] == 7      # profile, quote, 2 x /full, 2 x /dividend-adjusted, ^VIX
    np.testing.assert_allclose(out["spy"]["close"].to_numpy(), out["bars"]["adj_close"].to_numpy())
    np.testing.assert_allclose(out["spy"]["adj_close"].to_numpy(), out["spy"]["close"].to_numpy())


def test_vix_uses_caret_symbol_on_full_and_skips_volume_checks():
    fake = market()
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert len(fake.calls_to(FULL, "^VIX")) == 1
    assert not fake.calls_to(FULL, "VIX") and not fake.calls_to(ADJ, "^VIX")
    vix_dq = out["data_quality"]["benchmarks"]["^VIX"]
    assert vix_dq["zero_volume"]["checked"] is False and vix_dq["liquidity"]["illiquid"] is False
    assert not any(n.startswith("^VIX") for n in out["notes"])
    assert out["vix"].index[0] >= pd.Timestamp("2021-10-16")


def test_restricted_sector_etf_is_recorded_not_fabricated():
    fake = market(blocked={(ADJ, "XLK")})
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert out["sector"] is None and out["sector_etf"] is None
    assert out["skipped"]["sector"].startswith("XLK data unavailable (restricted:")
    assert any(n.startswith("sector: XLK data unavailable") for n in out["notes"])
    assert fake.restricted == [ADJ]
    assert out["spy"] is not None and out["vix"] is not None


def test_unmapped_sector_and_empty_earnings_are_skipped():
    fake = market(profile=profile_row("ACME", sector="Conglomerates"))
    fake.earnings = {}
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert "Conglomerates" in out["skipped"]["sector"]
    assert out["skipped"]["earnings"] == "no data returned"
    assert out["earnings"] is None


def test_missing_price_history_raises():
    fake = market()
    with pytest.raises(ValueError, match="no price data for NOPE"):
        fetch.load_all(fake, "nope", now=NOW)


def test_earnings_rows_are_ascending_with_contract_keys():
    out = fetch.load_all(market(), "ACME", now=NOW)
    rows = out["earnings"]
    assert [r["date"] for r in rows] == ["2026-05-01", "2026-07-30", "2026-11-02"]
    assert set(rows[0]) == {"date", "eps_actual", "eps_estimated", "revenue_actual", "revenue_estimated"}
    assert rows[-1]["eps_actual"] is None and rows[-1]["eps_estimated"] == 1.6


def test_sector_etf_mapping():
    assert fetch.sector_etf_for({"sector": " Technology "}) == "XLK"
    assert fetch.sector_etf_for({"sector": "Communication Services"}) == "XLC"
    assert fetch.sector_etf_for({"sector": "Conglomerates"}) is None
    assert fetch.sector_etf_for({"sector": ""}) is None
    assert fetch.sector_etf_for({"sector": "Financial Services", "isEtf": True}) is None
    assert fetch.sector_etf_for(None) is None


# ---------------------------------------------------------------------------- partial / stale
def test_todays_bar_is_partial_during_the_session():
    now = pd.Timestamp("2026-10-16 11:00", tz=NY)
    out = fetch.load_all(market(), "ACME", now=now)
    dq = out["data_quality"]
    assert day(out["bars"].index[-1]) == "2026-10-15"
    partial = dq["partial_bar_excluded"]
    assert partial["date"] == "2026-10-16"
    assert partial["close"] == pytest.approx(series("1996-10-01", seed=3)["close"].iloc[-1])
    assert "partial" in partial["reason"] and "11:00" in partial["reason"]
    assert dq["stale"]["is_stale"] is False and dq["stale"]["business_days_behind"] == 0
    assert day(out["spy"].index[-1]) == "2026-10-15"


def test_todays_bar_is_complete_after_the_close_buffer():
    now = pd.Timestamp("2026-10-16 16:20", tz=NY)
    out = fetch.load_all(market(), "ACME", now=now)
    assert day(out["bars"].index[-1]) == "2026-10-16"
    assert out["data_quality"]["partial_bar_excluded"] is None


def test_stale_last_bar_is_flagged():
    fake = market(end="2026-10-09")
    out = fetch.load_all(fake, "ACME", now=NOW)
    stale = out["data_quality"]["stale"]
    assert stale == {"is_stale": True, "last_bar": "2026-10-09", "expected_session": "2026-10-16",
                     "business_days_behind": 5}
    assert any("stale" in n for n in out["notes"])


def test_expected_session_before_close_and_on_weekends():
    assert day(fetch.expected_session(pd.Timestamp("2026-10-19 10:00", tz=NY))) == "2026-10-16"  # Monday
    assert day(fetch.expected_session(pd.Timestamp("2026-10-17 20:00", tz=NY))) == "2026-10-16"  # Saturday
    assert day(fetch.expected_session(pd.Timestamp("2026-10-16 16:15", tz=NY))) == "2026-10-16"


# ---------------------------------------------------------------------------- validate()
def _bars(n=120, start="2026-04-01", volume=1_000_000.0, price=50.0):
    df = synthetic_bars(n=n, seed=11, start=start, start_price=price)
    return df.assign(volume=volume)


def test_duplicate_dates_are_dropped_keeping_the_last():
    df = _bars()
    dup = df.iloc[[10]].assign(close=df["close"].iloc[10] * 1.001)
    clean, dq = fetch.validate(pd.concat([df.iloc[::-1], dup]), now_ny=NOW)   # unsorted, dup arrives last
    assert dq["duplicates_dropped"] == 1 and len(clean) == len(df)
    assert clean.index.is_unique and clean.index.is_monotonic_increasing
    assert clean["close"].iloc[10] == dup["close"].iloc[0]


def test_invalid_rows_dropped_and_inconsistent_rows_flagged():
    df = _bars()
    idx = df.index
    df.loc[idx[5], "close"] = np.nan
    df.loc[idx[6], "low"] = 0.0
    df.loc[idx[7], "high"] = -1.0
    df.loc[idx[20], "close"] = df.loc[idx[20], "high"] * 1.05      # close above high: flagged, kept
    clean, dq = fetch.validate(df, now_ny=NOW)
    assert dq["invalid_rows_dropped"] == 3 and len(clean) == len(df) - 3
    assert dq["inconsistent_ohlc"] == {"count": 1, "dates": [day(idx[20])]}


def test_zero_volume_and_gaps_are_flagged():
    df = _bars()
    df.loc[df.index[[30, 31]], "volume"] = 0.0
    df = df.drop(df.index[60:64])                                  # a 6-calendar-day hole
    clean, dq = fetch.validate(df, now_ny=NOW)
    assert dq["zero_volume"]["count"] == 2 and dq["zero_volume"]["dates"] == [day(df.index[30]), day(df.index[31])]
    gap = dq["gaps"]["items"][0]
    assert dq["gaps"]["count"] == 1 and gap["calendar_days"] > C.GAP_CALENDAR_DAYS
    assert gap["after"] == day(df.index[59]) and gap["before"] == day(df.index[60])
    assert len(clean) == len(df)
    assert any("zero-volume" in w for w in dq["warnings"]) and any("gaps" in w for w in dq["warnings"])


def test_volume_check_can_be_disabled():
    _, dq = fetch.validate(_bars(volume=0.0), now_ny=NOW, symbol="^VIX", check_volume=False)
    assert dq["zero_volume"]["count"] is None and not dq["liquidity"]["illiquid"]


def test_illiquid_names_are_flagged():
    _, dq = fetch.validate(_bars(volume=20_000.0, price=5.0), now_ny=NOW, symbol="TINY")
    liq = dq["liquidity"]
    assert liq["illiquid"] is True and liq["bars"] == fetch.LIQUIDITY_BARS
    assert liq["avg_volume"] == pytest.approx(20_000.0)
    assert "shares" in liq["reason"] and "dollar volume" in liq["reason"]
    assert any(w.startswith("TINY: illiquid") for w in dq["warnings"])

    _, dq = fetch.validate(_bars(), now_ny=NOW)
    assert dq["liquidity"]["illiquid"] is False and dq["liquidity"]["reason"] is None


def test_validate_handles_all_rows_invalid():
    clean, dq = fetch.validate(_bars().assign(close=np.nan), now_ny=NOW)
    assert clean.empty and dq["rows"] == 0 and dq["last_date"] is None


# ---------------------------------------------------------------------------- fmp_client cache rule
KEY = "SECRET-TEST-KEY-123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    for var in ("FMP_BASE_URL", "FMP_PATH_PREFIX", "FMP_AUTH_STYLE", "FMP_TLS_VERIFY",
                "FMP_MAX_PER_MIN", "FMP_TIMEOUT", "FMP_CACHE_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(fmp_client.time, "sleep", lambda s: None)
    c = fmp_client.FMPClient(api_key=KEY, cache_dir=tmp_path)
    c.urls, c.replies = [], []

    def http_get(url, style=None):
        c.urls.append(url)
        return c.replies.pop(0) if c.replies else (200, json.dumps([{"date": "2026-10-16", "close": 1.0}]))

    c._http_get = http_get
    return c


def _ny(text):
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(NY))


def _get_at(client, written, now, endpoint=FULL, **params):
    """Fetch at NY time `written` (file mtime set to it), then again at `now`; return network calls."""
    params = params or {"symbol": "ACME", "from": "2008-10-17", "to": written[:10]}
    client._now_ny = lambda: _ny(written)
    client.get(endpoint, **params)
    path = client._cache_path(endpoint, params)
    os.utime(path, (_ny(written).timestamp(), _ny(written).timestamp()))
    client._now_ny = lambda: _ny(now)
    client.get(endpoint, **params)
    return client.stats["network_calls"]


@pytest.mark.parametrize("written, now, calls", [
    ("2026-10-16T10:00", "2026-10-16T11:00", 1),   # same session, still intraday
    ("2026-10-16T10:00", "2026-10-16T16:30", 2),   # intraday copy is stale after the close buffer
    ("2026-10-16T16:20", "2026-10-16T23:00", 1),   # written after the close: valid all evening
    ("2026-10-15T17:00", "2026-10-16T09:00", 2),   # a new New York date
    ("2026-10-17T10:00", "2026-10-17T18:00", 1),   # Saturday: no session, same date is enough
])
def test_price_cache_follows_the_session_rule(client, written, now, calls):
    assert _get_at(client, written, now) == calls
    assert client.stats["cache_hits"] == 2 - calls


def test_technical_indicators_follow_the_session_rule(client):
    params = {"symbol": "ACME", "periodLength": 50, "timeframe": "1day"}
    assert _get_at(client, "2026-10-16T10:00", "2026-10-16T17:00", "technical-indicators/sma", **params) == 2


def test_quote_uses_a_five_minute_ttl(client):
    params = {"symbol": "ACME"}
    assert _get_at(client, "2026-10-16T10:00", "2026-10-16T10:04", "quote", **params) == 1
    client._now_ny = lambda: _ny("2026-10-16T10:06")
    client.get("quote", **params)
    assert client.stats["network_calls"] == 2


def test_key_never_reaches_cache_files_or_names(client, tmp_path):
    client.auth_style = "query"
    client.get(FULL, symbol="^VIX", **{"from": "2026-01-01", "to": "2026-10-16"})
    assert KEY in client.urls[0]                      # sent to the server, as asked
    files = list(tmp_path.iterdir())
    assert files and all(KEY not in f.name and KEY not in f.read_text(encoding="utf-8") for f in files)
    assert KEY not in json.dumps(client.describe())


def test_missing_key_explains_setx(monkeypatch, tmp_path):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    with pytest.raises(fmp_client.InvalidKey, match="setx FMP_API_KEY"):
        fmp_client.FMPClient(cache_dir=tmp_path)


def test_restricted_and_retry_behaviour(client):
    client.replies = [(403, json.dumps({"Error Message": "Premium endpoint"}))]
    data, why = fmp_client.get_with_reason(client, ADJ, symbol="XLK", **{"from": "2021-10-16", "to": "2026-10-16"})
    assert data is None and why.startswith("restricted:")
    assert client.restricted == [ADJ]

    client.replies = [(200, json.dumps({"Error Message": "This endpoint requires an upgrade of your subscription"}))]
    assert client.try_get("earnings", symbol="ACME") is None
    assert client.restricted == [ADJ, "earnings"]

    client.replies = [(429, ""), (502, ""), (200, "[]")]
    before = client.stats["network_calls"]
    assert client.get("profile", symbol="ACME") == []
    assert client.stats["network_calls"] - before == 3

    client.replies = [(404, f"no route for /stable/quote?symbol=ACME&apikey={KEY}")]
    data, why = fmp_client.get_with_reason(client, "quote", symbol="ACME")
    assert data is None and why.startswith("error:") and KEY not in why and "***" in why

    client.replies = [(401, json.dumps({"Error Message": "Invalid API KEY."}))]
    with pytest.raises(fmp_client.InvalidKey):
        fmp_client.get_with_reason(client, "quote", symbol="ACME")


# ---------------------------------------------------------------------------- crosscheck
def _indicator_rows(df, field, values):
    """FMP technical-indicator shape: newest first, date with a time part."""
    v = values.dropna()
    sub = df.loc[v.index]
    return [{"date": f"{d:%Y-%m-%d} 00:00:00", "open": o, "high": h, "low": l, "close": c, "volume": vol, field: x}
            for d, o, h, l, c, vol, x in zip(sub.index, sub["open"], sub["high"], sub["low"], sub["close"],
                                             sub["volume"], v)][::-1]


def test_compare_aligns_by_date_and_applies_warmup():
    crosscheck = pytest.importorskip("crosscheck")
    idx = pd.bdate_range("2024-01-01", periods=600)
    ours = pd.Series(np.linspace(40, 60, 600), index=idx)
    theirs = (ours + 0.1).iloc[100:]
    theirs.iloc[-1] += 1.0
    res = crosscheck.compare(ours, theirs, 0.5, skip_ours=250, skip_theirs=250, window=250)
    assert res["n"] == 250 and res["last_date"] == day(idx[-1])
    assert res["max_abs_diff"] == pytest.approx(1.1) and res["worst_date"] == day(idx[-1])
    assert res["share_within_tol"] == pytest.approx(249 / 250)
    assert crosscheck.compare(ours, theirs.iloc[:0], 0.5)["n"] == 0


def test_crosscheck_matches_fmp_shaped_indicators():
    crosscheck = pytest.importorskip("crosscheck")
    ind = pytest.importorskip("indicators")
    df = series("1996-10-01", seed=3)
    recent = df.loc["2021-10-18":]                     # FMP-like: about five years, its own RSI seed
    rows = {
        ("technical-indicators/sma", "ACME"): _indicator_rows(df, "sma", df["close"].rolling(50).mean()),
        ("technical-indicators/rsi", "ACME"): _indicator_rows(recent, "rsi", ind.rsi(recent["close"], 14)),
    }
    res = crosscheck.crosscheck(market(indicator_rows=rows), "ACME", now=NOW)
    assert res["status"] == "ok" and res["as_of"] == "2026-10-16"
    sma, rsi = res["indicators"]["sma50"], res["indicators"]["rsi14"]
    assert sma["n"] == rsi["n"] == crosscheck.WINDOW
    assert sma["share_within_tol"] == 1.0 and sma["max_abs_diff"] < 1e-9
    assert rsi["share_within_tol"] == 1.0 and rsi["max_abs_diff"] < 0.01
    assert "path-dependent" in " ".join(res["notes"])
    assert "SMA(50)" in crosscheck.format_report(res)


def test_crosscheck_reports_a_restricted_plan():
    crosscheck = pytest.importorskip("crosscheck")
    fake = market(blocked={"technical-indicators/sma", "technical-indicators/rsi"})
    res = crosscheck.crosscheck(fake, "ACME", now=NOW)
    assert res["status"] == "restricted" and res["calls"] == 2
    assert all(v["reason"].startswith("restricted") for v in res["indicators"].values())
    assert "not included in this plan" in crosscheck.format_report(res)
