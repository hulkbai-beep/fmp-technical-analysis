"""Data layer tests: chunked fetch, validation, module skips and the client's cache rules (no network)."""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

import config as C
import dividends as D
import fetch
import fmp_client
import market_calendar as MC
from conftest import synthetic_bars
from fakes import (ADJ, DIVIDENDS, ECON, FMP_ROW_CAP, FULL, HOLIDAYS, MACRO_EVENTS, NEXT_EX_DATE, NOW, NY,
                   FakeClient, day, dividend_rows, macro_rows, market, nyse_holiday_rows, profile_row, series)


# ---------------------------------------------------------------------------- chunked history
def test_thirty_years_take_two_full_calls_and_merge_cleanly():
    fake = market()
    out = fetch.load_all(fake, "ACME", now=NOW)
    full = fake.calls_to(FULL, "ACME")
    assert len(full) == 2
    assert pd.Timestamp(full[1]["to"]) == pd.Timestamp(full[0]["from"]) - pd.Timedelta(days=1)
    bars, src = out["bars"], series("1996-10-01", seed=3)
    assert bars.index.is_unique and bars.index.is_monotonic_increasing
    assert list(bars.columns) == fetch.COLUMNS + ["dividend"]
    assert list(bars.index.strftime("%Y-%m-%d")) == list(src.index.strftime("%Y-%m-%d"))
    np.testing.assert_allclose(bars["close"].to_numpy(), src["close"].to_numpy())
    assert out["data_quality"]["duplicates_dropped"] == 0


def test_normal_stock_uses_thirteen_calls():
    fake = market()
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert out["calls"] == 13 == fake.stats["network_calls"]
    assert [e for e, _ in fake.requests] == ["profile", "quote", HOLIDAYS, "earnings", FULL, FULL, ADJ, ADJ,
                                             DIVIDENDS, ADJ, ADJ, FULL, ECON]
    assert out["sector_etf"] == "XLK" and out["sector"] is not None and out["spy"] is not None
    assert out["skipped"] == {}
    assert out["connection"] == fake.describe() and out["connection"]["route"] == "official FMP API"


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
    missing = out["data_quality"]["adj_missing"]
    assert missing["count"] == len(bars.loc[:"2010-01-03"]) and missing["latest"] == "2010-01-01"
    assert any("dividend-adjusted prices start 2010-01-04" in n for n in out["notes"])


def test_missing_latest_adjusted_bars_are_counted_not_rounded_away():
    adj = series("1996-10-01", seed=3).iloc[:-3]                 # /dividend-adjusted lags /full by 3 days
    out = fetch.load_all(market(adjusted={"ACME": adj}), "ACME", now=NOW)
    dq = out["data_quality"]
    assert dq["adj_missing"]["count"] == 3 and dq["adj_missing"]["latest"] == "2026-10-16"
    assert any(f"3 of {dq['rows']} bars have no dividend-adjusted prices (latest 2026-10-16)" in n
               for n in out["notes"])
    assert not any("100.0%" in n for n in out["notes"])


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
    # profile, quote, holidays, QQQ's history, its dividends, SPY, ^VIX, the economic calendar
    assert out["calls"] == 3 + len(fake.calls_to(FULL, "QQQ")) + len(fake.calls_to(ADJ, "QQQ")) + 4 == 11
    assert fake.calls_to(DIVIDENDS, "QQQ") and not fake.calls_to(DIVIDENDS, "SPY")     # ETFs pay dividends too


def test_spy_as_ticker_reuses_its_own_bars_as_benchmark():
    fake = market(stock="SPY", stock_start="1993-01-29",
                  profile=profile_row("SPY", "Financial Services", "1993-01-22", etf=True))
    out = fetch.load_all(fake, "SPY", now=NOW)
    bench_from = day(NOW.tz_localize(None).normalize() - pd.DateOffset(years=C.BENCHMARK_YEARS))
    assert not [p for p in fake.calls_to(ADJ, "SPY") if p["from"] == bench_from]
    assert out["calls"] == 10     # profile, quote, holidays, 2 x /full, 2 x /dividend-adjusted, dividends, ^VIX, macro
    assert len(fake.calls_to(DIVIDENDS, "SPY")) == 1
    # the very bars the regime uses (split-adjusted close; adj_close for returns)
    pd.testing.assert_frame_equal(out["spy"], out["bars"])
    assert out["data_quality"]["benchmark_basis"]["SPY"].startswith("split-adjusted")


def test_vix_uses_caret_symbol_on_full_and_skips_volume_checks():
    fake = market()
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert len(fake.calls_to(FULL, "^VIX")) == 1
    assert not fake.calls_to(FULL, "VIX") and not fake.calls_to(ADJ, "^VIX")
    vix_dq = out["data_quality"]["benchmarks"]["^VIX"]
    assert vix_dq["zero_volume"]["checked"] is False and vix_dq["liquidity"]["illiquid"] is False
    assert not any(n.startswith("^VIX") for n in out["notes"])
    assert out["vix"].index[0] >= pd.Timestamp("2021-10-16")


def test_restricted_sector_etf_falls_back_to_split_adjusted_prices():
    fake = market(blocked={(ADJ, "XLK")})
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert out["sector_etf"] == "XLK" and "sector" not in out["skipped"]
    np.testing.assert_allclose(out["sector"]["close"].to_numpy(), out["sector"]["adj_close"].to_numpy())
    xlk = series("1998-12-22", seed=7)
    assert out["sector"]["close"].iloc[-1] == pytest.approx(xlk["close"].iloc[-1])   # /full, not adjClose
    assert out["data_quality"]["benchmark_basis"] == {"SPY": "dividend-adjusted", "XLK": "split-adjusted"}
    assert any(n.startswith("XLK: no dividend-adjusted prices (restricted:") and "price-only" in n
               and "overstated" in n for n in out["notes"])
    assert fake.restricted == [ADJ] and len(fake.calls_to(FULL, "XLK")) == 1


def test_sector_etf_missing_on_both_endpoints_is_recorded_not_fabricated():
    fake = market(blocked={(ADJ, "XLK"), (FULL, "XLK")})
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert out["sector"] is None and out["sector_etf"] is None
    assert out["skipped"]["sector"].startswith("XLK data unavailable (restricted:")
    assert any(n.startswith("sector: XLK data unavailable") for n in out["notes"])
    assert out["spy"] is not None and out["vix"] is not None


def test_plan_without_dividend_adjusted_keeps_every_benchmark_on_full_prices():
    fake = market(blocked={ADJ})
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert out["spy"] is not None and out["sector"] is not None and out["sector_etf"] == "XLK"
    assert set(out["skipped"]) == set()
    assert out["data_quality"]["benchmark_basis"] == {"SPY": "split-adjusted", "XLK": "split-adjusted"}
    assert not fake.calls_to(ADJ, "SPY") and not fake.calls_to(ADJ, "XLK")   # known restricted: not retried
    assert out["calls"] == 12        # profile, quote, holidays, earnings, 2 x /full, 1 x /dividend-adjusted,
                                     # dividends, SPY, XLK, ^VIX, economic calendar
    assert not any("overstated" in n for n in out["notes"])                 # stock is price-only too


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


def test_earnings_rows_keep_only_dates_and_whether_reported():
    out = fetch.load_all(market(), "ACME", now=NOW)
    rows = out["earnings"]
    assert [r["date"] for r in rows] == ["2026-05-01", "2026-07-30", "2026-11-02"]
    assert all(set(r) == {"date", "reported"} for r in rows)          # no EPS or revenue figures are kept
    assert [r["reported"] for r in rows] == [True, True, False]


def test_earnings_reported_flag_reads_either_actual():
    rows = fetch._earnings([{"date": "2026-01-02", "epsActual": None, "revenueActual": 5.0e9},
                            {"date": "2026-04-02", "epsActual": None, "revenueActual": None},
                            {"date": "2025-10-02", "epsActual": 0.0, "revenueActual": None}])
    assert rows == [{"date": "2025-10-02", "reported": True}, {"date": "2026-01-02", "reported": True},
                    {"date": "2026-04-02", "reported": False}]


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
                     "business_days_behind": 5, "basis": "NYSE sessions"}
    assert any("stale" in n for n in out["notes"])


def test_expected_session_before_close_and_on_weekends():
    assert day(fetch.expected_session(pd.Timestamp("2026-10-19 10:00", tz=NY))) == "2026-10-16"  # Monday
    assert day(fetch.expected_session(pd.Timestamp("2026-10-17 20:00", tz=NY))) == "2026-10-16"  # Saturday
    assert day(fetch.expected_session(pd.Timestamp("2026-10-16 16:15", tz=NY))) == "2026-10-16"


# ---------------------------------------------------------------------------- NYSE calendar in validate()
CAL = MC.build_calendar(list(nyse_holiday_rows()), end_year=2028)


def _bars_on(days):
    """Synthetic bars on exactly these dates."""
    idx = pd.DatetimeIndex(days, name="date")
    df = synthetic_bars(n=len(idx), seed=11, start="2000-01-03")
    df.index = idx
    return df


def test_stale_check_counts_nyse_sessions():
    bars = _bars_on(CAL.sessions("2026-09-01", "2026-11-25"))       # through the Wednesday before Thanksgiving
    now = pd.Timestamp("2026-12-01 10:00", tz=NY)                   # Tuesday, before the close
    _, plain = fetch.validate(bars, now_ny=now)
    assert plain["stale"]["business_days_behind"] == 3 and plain["stale"]["is_stale"] is True   # Thanksgiving counted
    assert plain["stale"]["basis"] == "weekdays (no exchange calendar)"
    _, cal = fetch.validate(bars, now_ny=now, calendar=CAL)
    assert cal["stale"] == {"is_stale": False, "last_bar": "2026-11-25", "expected_session": "2026-11-30",
                            "business_days_behind": 2, "basis": "NYSE sessions"}
    assert not any("stale" in w for w in cal["warnings"])

    bars = _bars_on(CAL.sessions("2026-01-02", "2026-04-02"))       # through the Thursday before Good Friday
    monday = pd.Timestamp("2026-04-06 10:00", tz=NY)
    _, plain = fetch.validate(bars, now_ny=monday)
    _, cal = fetch.validate(bars, now_ny=monday, calendar=CAL)
    assert (plain["stale"]["expected_session"], plain["stale"]["business_days_behind"]) == ("2026-04-03", 1)
    assert (cal["stale"]["expected_session"], cal["stale"]["business_days_behind"]) == ("2026-04-02", 0)
    assert day(fetch.expected_session(monday, CAL)) == "2026-04-02" and day(fetch.expected_session(monday)) == "2026-04-03"


def test_one_missing_session_is_a_data_gap_with_the_calendar():
    days = CAL.sessions("2026-06-01", "2026-07-31")                 # Juneteenth and July 3 are holidays
    bars = _bars_on(days.drop(pd.Timestamp("2026-07-15")))          # a Wednesday without a bar
    _, plain = fetch.validate(bars, now_ny=NOW)
    assert plain["gaps"]["count"] == 0 and "calendar days" in plain["gaps"]["basis"]   # 2 days: below the old rule
    _, cal = fetch.validate(bars, now_ny=NOW, calendar=CAL)
    assert cal["gaps"]["count"] == 1 and "NYSE sessions" in cal["gaps"]["basis"]
    assert cal["gaps"]["items"] == [{"after": "2026-07-14", "before": "2026-07-16", "calendar_days": 2,
                                     "missing_sessions": 1}]
    assert any("1 data gaps" in w and "2026-07-14 -> 2026-07-16" in w for w in cal["warnings"])


def test_exchange_closures_are_not_data_gaps():
    days = CAL.sessions("2001-08-01", "2001-10-31").append(CAL.sessions("2012-10-01", "2012-11-30"))
    bars = _bars_on(days)                                           # 9/11 and Hurricane Sandy closures, a real hole
    _, plain = fetch.validate(bars, now_ny=NOW)
    assert [(g["after"], g["before"]) for g in plain["gaps"]["items"]] == \
        [("2001-09-10", "2001-09-17"), ("2001-10-31", "2012-10-01"), ("2012-10-26", "2012-10-31")]
    _, cal = fetch.validate(bars, now_ny=NOW, calendar=CAL)
    (gap,) = cal["gaps"]["items"]
    assert (gap["after"], gap["before"]) == ("2001-10-31", "2012-10-01")
    assert gap["missing_sessions"] == len(CAL.sessions("2001-11-01", "2012-09-30"))


def test_early_close_bar_is_complete_fifteen_minutes_after_one():
    bars = _bars_on(CAL.sessions("2026-09-01", "2026-11-27"))       # the day after Thanksgiving closes at 13:00
    after = pd.Timestamp("2026-11-27 13:20", tz=NY)
    clean, cal = fetch.validate(bars, now_ny=after, calendar=CAL)
    assert cal["partial_bar_excluded"] is None and day(clean.index[-1]) == "2026-11-27"
    assert cal["stale"]["expected_session"] == "2026-11-27" and cal["stale"]["business_days_behind"] == 0
    _, plain = fetch.validate(bars, now_ny=after)
    assert plain["partial_bar_excluded"]["date"] == "2026-11-27"   # 16:15 without the calendar

    clean, cal = fetch.validate(bars, now_ny=pd.Timestamp("2026-11-27 13:10", tz=NY), calendar=CAL)
    reason = cal["partial_bar_excluded"]["reason"]
    assert day(clean.index[-1]) == "2026-11-25" and "before 13:15" in reason and "13:00 early close" in reason
    assert cal["stale"]["business_days_behind"] == 0                # Thanksgiving is no session


def test_benchmarks_are_validated_with_the_calendar():
    spy = series("1993-01-29", seed=5)
    fake = market(prices={"SPY": spy.drop(pd.Timestamp("2026-09-16"))})
    out = fetch.load_all(fake, "ACME", now=NOW)
    gaps = out["data_quality"]["benchmarks"]["SPY"]["gaps"]
    assert gaps["count"] == 1 and gaps["items"][0]["missing_sessions"] == 1 and "NYSE" in gaps["basis"]
    assert any(n.startswith("SPY: 1 data gaps") for n in out["notes"])


# ---------------------------------------------------------------------------- calendar, dividends, macro requests
def test_load_calendar_is_one_request_through_next_year():
    fake = market()
    cal, problem = fetch.load_calendar(fake, pd.Timestamp("2026-10-16"))
    assert fake.requests == [(HOLIDAYS, {"exchange": "NYSE", "from": "1990-01-01", "to": "2027-12-31"})]
    assert problem is None and cal.source == "fmp+rules" and cal.problem is None
    assert not cal.is_session("2026-11-26") and cal.close_time("2026-11-27") == "13:00"
    assert cal.is_session("2001-09-10") and not cal.is_session("2001-09-11")
    assert max(cal.closed).year == 2028                              # the rules run a year past the request

    cal, problem = fetch.load_calendar(FakeClient({}, holidays=[]), pd.Timestamp("2026-10-16"))
    assert cal.source == "rules" and problem == "FMP holidays-by-exchange returned no usable NYSE rows"


def test_restricted_holiday_list_falls_back_to_the_rules():
    fake = market(blocked={HOLIDAYS})
    out = fetch.load_all(fake, "ACME", now=NOW)
    cal = out["data_quality"]["calendar"]
    assert cal["source"] == "rules" and cal["exchange"] == "NYSE" and cal["problem"].startswith("restricted:")
    assert cal["closed_days"] > 300 and not out["calendar"].is_session("2026-11-26")
    assert any(n.startswith("exchange calendar: restricted:") and "built-in exchange rules" in n for n in out["notes"])
    assert HOLIDAYS in fake.restricted and out["skipped"] == {} and out["calls"] == 13
    assert out["data_quality"]["stale"]["basis"] == "NYSE sessions"


def _failing(fake, *endpoints):
    """Make `endpoints` fail on `fake` with a server error (not a plan restriction)."""
    respond = fake.respond

    def flaky(endpoint, params):
        if endpoint in endpoints:
            raise fmp_client.FMPError(f"HTTP 500 on {endpoint} (rate limit or server error)")
        return respond(endpoint, params)
    fake.respond = flaky
    return fake


def test_failing_new_endpoints_are_recorded_and_the_run_goes_on():
    fake = _failing(market(), HOLIDAYS, DIVIDENDS, ECON)
    out = fetch.load_all(fake, "ACME", now=NOW)
    cal = out["data_quality"]["calendar"]
    assert cal["source"] == "rules" and cal["problem"].startswith("error:")
    assert out["skipped"]["dividends"].startswith("error:") and out["skipped"]["macro_events"].startswith("error:")
    assert out["dividends"] is None and out["macro_rows"] is None and not out["bars"]["dividend"].any()
    assert fake.errors == [HOLIDAYS, DIVIDENDS, ECON] and fake.restricted == []
    assert len(out["bars"]) == len(series("1996-10-01", seed=3)) and out["spy"] is not None and out["vix"] is not None


def test_dividends_become_the_bars_dividend_column():
    fake = market()
    out = fetch.load_all(fake, "ACME", now=NOW)
    div = out["bars"]["dividend"]
    past = [r["date"] for r in dividend_rows("ACME") if r["date"] <= "2026-10-16"]
    assert sorted(day(d) for d in div.index[div > 0]) == sorted(past) and set(div[div > 0]) == {0.5}
    assert len(fake.calls_to(DIVIDENDS, "ACME")) == 1 and not fake.calls_to(DIVIDENDS, "SPY")
    assert out["dividends"][-1]["ex_date"] == NEXT_EX_DATE            # declared, after the last bar
    assert out["data_quality"]["dividends"] == {"ex_dates": len(past) + 1, "rows_dropped": 0, "problem": None}
    assert "dividend" not in out["spy"].columns                       # benchmarks are dividend-adjusted

    weekend = [{"date": "2026-09-05", "adjDividend": 0.3, "dividend": 0.3, "frequency": "Quarterly"},   # a Saturday
               {"date": "2026-08-03", "adjDividend": 0.0, "dividend": 0.0, "frequency": "Quarterly"}]   # unusable
    out = fetch.load_all(market(dividends={"ACME": weekend}), "ACME", now=NOW)
    div = out["bars"]["dividend"]
    # the Saturday before Labor Day (09-07): the drop shows on Tuesday 09-08, the first NYSE session (the
    # fake history, on every weekday, even has a bar on the holiday)
    assert [day(d) for d in div.index[div > 0]] == ["2026-09-08"] and div.sum() == pytest.approx(0.3)
    assert out["data_quality"]["dividends"] == {"ex_dates": 1, "rows_dropped": 1, "problem": None}


def test_an_ex_date_inside_a_multi_day_closure_finds_its_bar():
    # KO-like: ex-date 2001-09-12, while the NYSE was closed 09-11..09-14; the next bar is 09-17, 5 days later
    closure = pd.bdate_range("2001-09-11", "2001-09-14")
    rows = [{"date": "2001-09-12", "adjDividend": 0.18, "dividend": 0.18, "frequency": "Quarterly"}]
    fake = market(prices={"ACME": series("1996-10-01", seed=3).drop(closure)}, dividends={"ACME": rows})
    out = fetch.load_all(fake, "ACME", now=NOW)
    div = out["bars"]["dividend"]
    assert [day(d) for d in div.index[div > 0]] == ["2001-09-17"] and div.sum() == pytest.approx(0.18)
    assert out["data_quality"]["gaps"]["count"] == 0                   # the closure is no data gap either
    # the weekday rule alone (no exchange calendar) would leave it unmatched: 5 > DIVIDEND_MATCH_DAYS days
    assert not D.dividend_column(out["bars"].index, out["dividends"]).any()


def test_restricted_dividends_leave_zeros_and_a_reason():
    fake = market(blocked={DIVIDENDS})
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert out["dividends"] is None and out["skipped"]["dividends"].startswith("restricted:")
    assert not out["bars"]["dividend"].any() and DIVIDENDS in fake.restricted
    assert out["data_quality"]["dividends"]["problem"].startswith("restricted:")
    assert any(n.startswith("dividends: restricted:") for n in out["notes"])
    assert out["calls"] == 13 and out["macro_rows"] is not None       # the rest of the run goes on

    out = fetch.load_all(market(dividends={}), "ACME", now=NOW)        # a symbol that pays none
    assert out["dividends"] == [] and not out["bars"]["dividend"].any() and "dividends" not in out["skipped"]


def test_economic_calendar_window_follows_the_last_bar():
    # the window starts on as_of itself: FMP filters by UTC date, and 16:30 New York on as_of is still as_of
    late = ("2026-10-16 20:30:00", "US", "Fed Chair Powell Speech", "High")
    fake = market(macro=macro_rows(MACRO_EVENTS + (late,)))
    out = fetch.load_all(fake, "ACME", now=NOW)
    (params,) = [p for e, p in fake.requests if e == ECON]
    assert params == {"from": "2026-10-16", "to": "2027-01-12"} == out["macro_window"] == fetch.macro_window(NOW.date())
    assert (pd.Timestamp(params["to"]) - pd.Timestamp(params["from"])).days == C.MACRO_WINDOW_DAYS - 1
    assert len(out["macro_rows"]) == 12 and min(r["date"] for r in out["macro_rows"]) == "2026-10-16 20:30:00"
    assert {r["country"] for r in out["macro_rows"]} == {"US", "EU"}   # raw rows: macro.py keeps the US ones

    out = fetch.load_all(market(), "ACME", now=pd.Timestamp("2026-10-16 11:00", tz=NY))   # partial bar dropped
    assert out["macro_window"] == {"from": "2026-10-15", "to": "2027-01-11"}
    assert "2026-10-15 12:30:00" in [r["date"] for r in out["macro_rows"]]   # CPI before that as_of's open


def test_restricted_economic_calendar_is_recorded():
    fake = market(blocked={ECON})
    out = fetch.load_all(fake, "ACME", now=NOW)
    assert out["macro_rows"] is None and out["skipped"]["macro_events"].startswith("restricted:")
    assert any(n.startswith("macro_events: restricted:") for n in out["notes"]) and ECON in fake.restricted
    assert out["macro_window"] == {"from": "2026-10-16", "to": "2027-01-12"}


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
    assert liq["illiquid"] is True and liq["bars"] == C.LIQUIDITY_BARS
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


def test_quote_follows_the_session_rule(client):
    params = {"symbol": "ACME"}
    assert _get_at(client, "2026-10-16T10:00", "2026-10-16T14:00", "quote", **params) == 1   # same session
    client._now_ny = lambda: _ny("2026-10-16T16:30")                                       # close has passed
    client.get("quote", **params)
    assert client.stats["network_calls"] == 2


@pytest.mark.parametrize("endpoint, params, written, now, calls", [
    (HOLIDAYS, {"exchange": "NYSE", "from": "1990-01-01", "to": "2027-12-31"},
     "2026-10-10T10:00", "2026-10-16T09:00", 1),                  # 6 days: within the week
    (HOLIDAYS, {"exchange": "NYSE", "from": "1990-01-01", "to": "2027-12-31"},
     "2026-10-08T10:00", "2026-10-16T11:00", 2),                  # 8 days
    (DIVIDENDS, {"symbol": "ACME"}, "2026-10-15T20:00", "2026-10-16T19:00", 1),   # 23 hours
    (DIVIDENDS, {"symbol": "ACME"}, "2026-10-15T10:00", "2026-10-16T11:00", 2),   # 25 hours
    (ECON, {"from": "2026-10-16", "to": "2027-01-12"}, "2026-10-16T06:00", "2026-10-16T17:00", 1),   # 11 hours
    (ECON, {"from": "2026-10-16", "to": "2027-01-12"}, "2026-10-16T06:00", "2026-10-16T19:00", 2),   # 13 hours
])
def test_calendar_dividends_and_macro_use_fixed_ttls(client, endpoint, params, written, now, calls):
    assert fmp_client.TTL_BY_ENDPOINT[HOLIDAYS] == C.CALENDAR_TTL_SECONDS == 7 * 86400
    assert fmp_client.TTL_BY_ENDPOINT[DIVIDENDS] == C.DIVIDEND_TTL_SECONDS
    assert fmp_client.TTL_BY_ENDPOINT[ECON] == C.MACRO_TTL_SECONDS
    assert _get_at(client, written, now, endpoint, **params) == calls


def test_check_probes_the_new_endpoints():
    probes = fmp_client._probes("ACME", "2026-09-16", "2026-10-16")
    params = {ep: p for ep, p, _ in probes}
    assert params[HOLIDAYS] == {"exchange": "NYSE", "from": "2025-01-01", "to": "2027-12-31"}
    assert params[DIVIDENDS] == {"symbol": "ACME"}
    assert params[ECON] == {"from": "2026-10-16", "to": "2026-11-15"}


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


def test_earnings_follow_the_session_rule(client):
    params = {"symbol": "ACME"}
    assert _get_at(client, "2026-10-16T03:00", "2026-10-16T15:30", "earnings", **params) == 1   # same day
    client._now_ny = lambda: _ny("2026-10-16T16:30")             # after the close: actuals may be new
    client.get("earnings", **params)
    assert client.stats["network_calls"] == 2


def test_unreachable_host_fails_fast_for_the_rest_of_the_run(client, monkeypatch, capsys):
    import urllib.error
    slept, attempts = [], []
    monkeypatch.setattr(fmp_client.time, "sleep", slept.append)

    def down(url, style=None):
        attempts.append(url)
        raise urllib.error.URLError(TimeoutError("timed out"))

    client._http_get = down
    with pytest.raises(fmp_client.Unreachable, match="cannot reach .*tried twice"):
        fmp_client.get_with_reason(client, "profile", symbol="ACME")      # optional data still raises
    assert len(attempts) == fmp_client.NETWORK_ATTEMPTS and sum(slept) <= 1
    assert "retrying once" in capsys.readouterr().err
    with pytest.raises(fmp_client.Unreachable):
        client.get("quote", symbol="ACME")
    assert len(attempts) == fmp_client.NETWORK_ATTEMPTS                    # no further request was sent
    with pytest.raises(fmp_client.Unreachable):
        fetch.load_all(client, "ACME", now=NOW)


def test_a_single_dropped_connection_is_retried(client, monkeypatch):
    import urllib.error
    replies = [urllib.error.URLError(ConnectionResetError("reset")), (200, "[]")]

    def flaky(url, style=None):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    client._http_get = flaky
    assert client.get("profile", symbol="ACME") == [] and client.unreachable is None


def test_tls_verification_cannot_be_turned_off_for_the_official_host(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FMP_TLS_VERIFY", "0")
    monkeypatch.delenv("FMP_BASE_URL", raising=False)
    official = fmp_client.FMPClient(api_key=KEY, cache_dir=tmp_path)
    assert official.tls_verify is True and official._ssl_ctx is None
    assert "ignored" in capsys.readouterr().err
    monkeypatch.setenv("FMP_BASE_URL", "https://54.236.92.205")
    monkeypatch.setenv("FMP_PATH_PREFIX", "stable")
    monkeypatch.setenv("FMP_AUTH_STYLE", "header")
    proxy = fmp_client.FMPClient(api_key=KEY, cache_dir=tmp_path)
    assert proxy.tls_verify is False and proxy._ssl_ctx is not None


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
    assert sma["n"] == rsi["n"] == C.CROSSCHECK_WINDOW
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


# ---------------------------------------------------------------------------- SPAC shell / history start
def _spac_prices():
    df = series("2021-01-04", seed=11).copy()
    shell = np.linspace(10.0, 10.8, 130)
    shell[40:60] = np.linspace(12.5, 14.6, 20)              # deal-announcement spike, as IONQ had
    for c in ("open", "high", "low", "close"):
        df.iloc[:130, df.columns.get_loc(c)] = shell * {"high": 1.004, "low": 0.996}.get(c, 1.0)
        df["adj_" + c] = df[c] * 0.98
    return df


def test_spac_like_start_is_flagged_but_kept():
    fake = market(stock="IONQ", prices={"IONQ": _spac_prices()}, profile=profile_row("IONQ", ipo="2021-01-04"))
    out = fetch.load_all(fake, "IONQ", now=NOW)
    sp = out["data_quality"]["spac_like_start"]
    assert sp and sp["first_date"] == "2021-01-04"
    assert any("pre-merger SPAC" in n for n in out["notes"])
    assert any("pre-merger SPAC" in w for w in out["data_quality"]["warnings"])
    assert day(out["bars"].index[0]) == "2021-01-04"          # flagged, never silently dropped


def test_history_start_override_cuts_the_request_and_skips_the_spac_check():
    fake = market(stock="IONQ", prices={"IONQ": _spac_prices()}, profile=profile_row("IONQ", ipo="2021-01-04"))
    out = fetch.load_all(fake, "IONQ", now=NOW, start="2021-10-01")
    assert all(pd.Timestamp(c["from"]) >= pd.Timestamp("2021-10-01") for c in fake.calls_to(FULL, "IONQ"))
    assert out["bars"].index[0] >= pd.Timestamp("2021-10-01")
    assert out["data_quality"]["history_start_override"] == "2021-10-01"
    assert out["data_quality"]["spac_like_start"] is None


def test_normal_history_is_not_flagged_as_spac():
    out = fetch.load_all(market(), "ACME", now=NOW)
    assert out["data_quality"]["spac_like_start"] is None


def test_a_ten_dollar_stock_that_trends_is_not_flagged():
    df = series("2021-01-04", seed=11).copy()
    path = np.linspace(9.0, 16.0, 130)                      # passes $10 on the way up
    for c in ("open", "high", "low", "close"):
        df.iloc[:130, df.columns.get_loc(c)] = path
    assert fetch.spac_like_start(df) is None
