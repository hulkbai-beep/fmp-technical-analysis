"""Macro events (scripts/macro.py): classification by event name, UTC -> New York times across both 2026
DST switches, same-day releases merged into one event, timing / session on the NYSE calendar, the as_of
filter, horizon counts, listing coverage and the empty / failed-fetch paths. Offline: the rows below are
verbatim FMP economic-calendar rows (the window of as_of 2026-11-04: 2026-11-04 .. 2027-01-31, fetched 2026-11-05)."""
import json
import random
from pathlib import Path

import pandas as pd
import pytest

import config as C
import macro as MA
import market_calendar as MC

FIXTURE = Path(__file__).resolve().parent / "data" / "nyse_holidays_fmp.json"
SWING, POSITION = C.HORIZONS["swing"], C.HORIZONS["position"]
WINDOW = {"from": "2026-11-04", "to": "2027-01-31"}

# (date UTC, event, impact, country): newest first, as FMP sends them; a subset of the real window, with every row
# FMP lists under several names for one tier-3 release (11-12, 11-25) and rows on as_of itself, before its close
REAL = [
    ("2027-01-27 19:30:00", "Press Conference", "High", "US"),
    ("2027-01-27 19:00:00", "Fed Interest Rate Decision", "High", "US"),
    ("2026-12-31 14:45:00", "Chicago PMI (Dec)", "Medium", "US"),
    ("2026-12-31 13:30:00", "Initial Jobless Claims (Dec/26)", "Medium", "US"),
    ("2026-12-30 19:00:00", "FOMC Minutes", "High", "US"),
    ("2026-12-23 13:30:00", "Core PCE Price Index MoM (Nov)", "High", "US"),
    ("2026-12-23 13:30:00", "Durable Goods Orders MoM (Nov)", "High", "US"),
    ("2026-12-17 13:30:00", "Housing Starts (Nov)", "High", "US"),
    ("2026-12-17 13:15:00", "Interest Rate Decision", "High", "EU"),
    ("2026-12-17 12:00:00", "BoE Interest Rate Decision", "High", "UK"),
    ("2026-12-16 13:30:00", "Retail Sales MoM (Nov)", "High", "US"),
    ("2026-12-15 13:30:00", "Producer Price Index MoM (Nov)", "High", "US"),
    ("2026-12-10 13:30:00", "Inflation Rate YoY (Nov)", "High", "US"),
    ("2026-12-10 13:30:00", "Core Inflation Rate MoM (Nov)", "High", "US"),
    ("2026-12-09 19:30:00", "Press Conference", "High", "US"),
    ("2026-12-09 19:00:00", "Fed Interest Rate Decision", "High", "US"),
    ("2026-12-09 19:00:00", "FOMC Economic Projections", "High", "US"),
    ("2026-12-09 19:00:00", "Interest Rate Projection - Longer", "Low", "US"),
    ("2026-12-09 14:45:00", "BoC Interest Rate Decision", "High", "CA"),
    ("2026-12-04 15:00:00", "Michigan Consumer Sentiment (Dec)", "High", "US"),
    ("2026-12-04 13:30:00", "Non Farm Payrolls (Nov)", "High", "US"),
    ("2026-12-04 13:30:00", "Unemployment Rate (Nov)", "High", "US"),
    ("2026-12-03 15:00:00", "ISM Services PMI (Nov)", "High", "US"),
    ("2026-12-01 15:00:00", "ISM Manufacturing PMI (Nov)", "High", "US"),
    ("2026-12-01 15:00:00", "JOLTs Job Openings (Oct)", "High", "US"),
    ("2026-11-27 13:30:00", "Retail Inventories Ex Autos MoM (Oct)", "Medium", "US"),
    ("2026-11-26 00:00:00", "Thanksgiving Day", "None", "US"),
    ("2026-11-25 19:00:00", "Beige Book", "Medium", "US"),
    ("2026-11-25 15:00:00", "New Home Sales (Oct)", "High", "US"),
    ("2026-11-25 15:00:00", "New Home Sales MoM (Oct)", "High", "US"),
    ("2026-11-25 13:30:00", "Core PCE Price Index MoM (Oct)", "High", "US"),
    ("2026-11-25 13:30:00", "Personal Income MoM (Oct)", "High", "US"),
    ("2026-11-25 13:30:00", "Durable Goods Orders Ex Defense MoM (Oct)", "High", "US"),
    ("2026-11-25 13:30:00", "Durable Goods Orders MoM (Oct)", "High", "US"),
    ("2026-11-25 13:30:00", "Durable Goods Orders Ex Transp MoM (Oct)", "High", "US"),
    ("2026-11-25 13:30:00", "Initial Jobless Claims (Nov/21)", "Medium", "US"),
    ("2026-11-24 15:00:00", "CB Consumer Confidence (Nov)", "High", "US"),
    ("2026-11-18 19:00:00", "FOMC Minutes", "High", "US"),
    ("2026-11-17 13:30:00", "Retail Sales MoM (Oct)", "High", "US"),
    ("2026-11-17 13:30:00", "Retail Sales Control Group MoM (Oct)", "Medium", "US"),
    ("2026-11-16 13:30:00", "NY Empire State Manufacturing Index (Nov)", "High", "US"),
    ("2026-11-13 13:30:00", "Producer Price Index MoM (Oct)", "High", "US"),
    ("2026-11-13 13:30:00", "Core PPI MoM (Oct)", "Medium", "US"),
    ("2026-11-12 15:00:00", "Existing Home Sales MoM (Oct)", "High", "US"),
    ("2026-11-12 15:00:00", "Existing Home Sales (Oct)", "High", "US"),
    ("2026-11-10 15:00:00", "Cleveland CPI MoM (Oct)", "Low", "US"),
    ("2026-11-10 13:30:00", "Inflation Rate YoY (Oct)", "High", "US"),
    ("2026-11-10 13:30:00", "Core Inflation Rate MoM (Oct)", "High", "US"),
    ("2026-11-10 13:30:00", "CPI s.a (Oct)", "Medium", "US"),
    ("2026-11-10 13:30:00", "CPI n.s.a MoM (Oct)", "Low", "US"),
    ("2026-11-09 16:00:00", "Consumer Inflation Expectation (Oct)", "Low", "US"),
    ("2026-11-06 15:00:00", "Michigan Consumer Sentiment (Nov)", "High", "US"),
    ("2026-11-06 15:00:00", "Michigan 5 Year Inflation Expectations (Nov)", "Low", "US"),
    ("2026-11-06 13:30:00", "Non Farm Payrolls (Oct)", "High", "US"),
    ("2026-11-06 13:30:00", "Unemployment Rate (Oct)", "High", "US"),
    ("2026-11-06 13:30:00", "Average Hourly Earnings MoM (Oct)", "Medium", "US"),
    ("2026-11-06 13:30:00", "Nonfarm Payrolls Private (Oct)", "High", "US"),
    ("2026-11-05 13:50:00", "Fed Hammack Speech", "Medium", "US"),
    ("2026-11-05 13:30:00", "Initial Jobless Claims (Oct/31)", "High", "US"),
    ("2026-11-05 13:30:00", "Continuing Jobless Claims (Oct/24)", "High", "US"),
    # on as_of 2026-11-04 before its close: already in the last bar, so neither events nor counted
    ("2026-11-04 19:00:00", "Total Vehicle Sales (Oct)", "Low", "US"),
    ("2026-11-04 17:00:00", "Atlanta Fed GDPNow (Q4)", "Medium", "US"),
    ("2026-11-04 15:30:00", "EIA Crude Oil Stocks Change (Oct/30)", "Medium", "US"),
    ("2026-11-04 15:00:00", "ISM Services PMI (Oct)", "High", "US"),
    ("2026-11-04 13:15:00", "ADP Employment Change (Oct)", "High", "US"),
    ("2026-11-04 12:00:00", "MBA 30-Year Mortgage Rate (Oct/30)", "Medium", "US"),
]
# what build_macro_events makes of REAL as of 2026-11-04: (id, date, trading_days_to), in output order
REAL_EVENTS = [
    ("jobs", "2026-11-06", 2), ("consumer_sentiment", "2026-11-06", 2), ("cpi", "2026-11-10", 4),
    ("other", "2026-11-12", 6), ("ppi", "2026-11-13", 7), ("other", "2026-11-16", 8),
    ("retail_sales", "2026-11-17", 9), ("fomc_minutes", "2026-11-18", 10), ("consumer_sentiment", "2026-11-24", 14),
    ("pce", "2026-11-25", 15), ("other", "2026-11-25", 15), ("other", "2026-11-25", 15),   # durable goods, new home
                                                                      # sales; Thanksgiving (11-26) is not counted
    ("ism_manufacturing", "2026-12-01", 18), ("jolts", "2026-12-01", 18), ("ism_services", "2026-12-03", 20),
    ("jobs", "2026-12-04", 21), ("consumer_sentiment", "2026-12-04", 21), ("fomc", "2026-12-09", 24),
    ("cpi", "2026-12-10", 25), ("ppi", "2026-12-15", 28), ("retail_sales", "2026-12-16", 29),
    ("other", "2026-12-17", 30), ("pce", "2026-12-23", 34), ("other", "2026-12-23", 34),
    ("fomc_minutes", "2026-12-30", 38), ("fomc", "2027-01-27", 56),  # Jan 1 and MLK Day are not counted
]


@pytest.fixture(scope="module")
def cal():
    with open(FIXTURE, encoding="utf-8") as f:
        return MC.build_calendar(json.load(f)["rows"], end_year=2028)


def row(date, event, impact="High", country="US"):
    """One FMP economic-calendar row; `date` is UTC, as FMP sends it."""
    return {"date": date, "country": country, "event": event, "currency": "USD", "previous": None,
            "estimate": None, "actual": None, "change": None, "impact": impact, "changePercentage": 0,
            "unit": None}


def real_rows():
    return [row(d, e, i, c) for d, e, i, c in REAL]


def ctx(rows, as_of="2026-11-04", calendar=None, hz=SWING, window=WINDOW, skipped=None):
    return {"macro_rows": rows, "macro_window": window, "calendar": calendar, "skipped": skipped or {},
            "hz": hz, "as_of": as_of}


def events_of(rows, cal, as_of):
    return MA.group_events(MA.prepare_rows(rows), cal, as_of)


# ---------------------------------------------------------------------------- names and classification
@pytest.mark.parametrize("raw, clean", [
    ("Non Farm Payrolls (Oct)", "Non Farm Payrolls"),
    ("Initial Jobless Claims (Oct/31)", "Initial Jobless Claims"),
    ("GDP Growth Rate QoQ (Q3)", "GDP Growth Rate QoQ"),
    ("Fed Interest Rate Decision", "Fed Interest Rate Decision"),
    ("  CPI   s.a  (Oct) ", "CPI s.a"),
    ("Interest Rate Projection - 1st Yr", "Interest Rate Projection - 1st Yr"),
    (None, ""), (12, ""),
])
def test_clean_name(raw, clean):
    assert MA.clean_name(raw) == clean


TIER_1_2 = {
    "fomc": ["Fed Interest Rate Decision", "FOMC Statement", "FOMC Economic Projections", "Press Conference",
             "Interest Rate Projection - 1st Yr", "Interest Rate Projection - Longer"],
    "cpi": ["Inflation Rate (Oct)", "Inflation Rate MoM (Oct)", "Inflation Rate YoY (Oct)", "Core Inflation Rate (Oct)",
            "Core Inflation Rate MoM (Oct)", "Core Inflation Rate YoY (Oct)", "CPI s.a (Oct)", "CPI n.s.a MoM (Oct)"],
    "jobs": ["Non Farm Payrolls (Oct)", "Nonfarm Payrolls Private (Oct)", "Unemployment Rate (Oct)",
             "U-6 Unemployment Rate (Oct)", "Average Hourly Earnings MoM (Oct)", "Average Hourly Earnings YoY (Oct)",
             "Average Weekly Hours (Oct)", "Government Payrolls (Oct)", "Manufacturing Payrolls (Oct)",
             "Participation Rate (Oct)"],
    "pce": ["PCE Price Index MoM (Oct)", "PCE Price Index YoY (Oct)", "Core PCE Price Index MoM (Oct)",
            "Core PCE Price Index YoY (Oct)", "Personal Income MoM (Oct)", "Personal Spending MoM (Oct)"],
    "ppi": ["Producer Price Index (Oct)", "Producer Price Index MoM (Oct)", "Producer Price Index YoY (Oct)",
            "Core PPI MoM (Oct)", "Core PPI YoY (Oct)", "PPI Ex Food, Energy and Trade MoM (Oct)",
            "PPI Ex Food, Energy and Trade YoY (Oct)"],
    "gdp": ["GDP Growth Rate QoQ (Q3)", "GDP Growth Rate QoQ Adv (Q3)", "GDP Growth Rate QoQ 2nd Est (Q3)",
            "GDP Price Index QoQ (Q3)", "GDP Sales QoQ (Q3)", "PCE Prices QoQ (Q3)", "Core PCE Prices QoQ (Q3)",
            "Real Consumer Spending QoQ (Q3)"],
    "retail_sales": ["Retail Sales MoM (Oct)", "Retail Sales YoY (Oct)", "Retail Sales Ex Autos MoM (Oct)",
                     "Retail Sales Ex Gas/Autos MoM (Oct)", "Retail Sales Control Group MoM (Oct)"],
    "fomc_minutes": ["FOMC Minutes"],
    "fed_policy": ["Jackson Hole Symposium", "Monetary Policy Report"],
    "ism_manufacturing": ["ISM Manufacturing PMI (Nov)"],
    "ism_services": ["ISM Services PMI (Nov)", "ISM Non-Manufacturing PMI (Nov)"],
    "jolts": ["JOLTs Job Openings (Oct)", "JOLTS Job Openings (Oct)"],
    "consumer_sentiment": ["Michigan Consumer Sentiment (Nov)", "Michigan Consumer Sentiment Prel (Nov)",
                           "CB Consumer Confidence (Nov)"],
}
TIERS = {gid: tier for gid, _, tier, _ in C.MACRO_GROUPS}


@pytest.mark.parametrize("gid, name", [(g, n) for g, names in TIER_1_2.items() for n in names])
def test_tier_1_2_groups_by_name_whatever_the_impact(gid, name):
    label = next(lbl for g, lbl, _, _ in C.MACRO_GROUPS if g == gid)
    for impact in ("High", "Medium", "Low", "None", None):
        assert MA.classify(name, impact) == (gid, label, TIERS[gid])


def test_every_group_is_covered_and_tier_1_is_the_headline_set():
    assert set(TIER_1_2) == set(TIERS)
    assert MA.TIER1_GROUPS == ("fomc", "cpi", "jobs", "pce", "ppi", "gdp", "retail_sales")


LOOK_ALIKES = [
    "Cleveland CPI MoM (Oct)", "Michigan 5 Year Inflation Expectations (Nov)",
    "Michigan 1 Year Inflation Expectations (Nov)", "Consumer Inflation Expectation (Oct)", "Atlanta Fed GDPNow (Q4)",
    "Retail Inventories Ex Autos MoM (Oct)", "Fed Waller Speech", "Fed Chair Powell Speech", "Fed Bowman Speech",
    "Interest Rate Decision", "BoE Interest Rate Decision", "ADP Employment Change (Nov)", "Challenger Job Cuts (Nov)",
    "ISM Manufacturing Prices (Nov)", "JOLTs Job Quits (Oct)", "Michigan Consumer Expectations (Nov)", "Beige Book",
    "Real Earnings MoM (Oct)", "Employment Cost Index QoQ (Q3)", "Nonfarm Productivity QoQ (Q3)",
]


@pytest.mark.parametrize("name", LOOK_ALIKES)
def test_look_alikes_stay_out_of_the_groups(name):
    assert MA.classify(name, "Medium") is None and MA.classify(name, "Low") is None
    assert MA.classify(name, "High") == ("other", MA.release_name(name), 3)   # only FMP's "High" makes it tier 3


@pytest.mark.parametrize("raw, release", [
    ("Durable Goods Orders MoM (Oct)", "Durable Goods Orders"),
    ("Durable Goods Orders Ex Transp MoM (Oct)", "Durable Goods Orders"),
    ("Durable Goods Orders Ex Defense MoM (Oct)", "Durable Goods Orders"),
    ("Existing Home Sales MoM (Oct)", "Existing Home Sales"),
    ("Existing Home Sales (Oct)", "Existing Home Sales"),                 # "Ex" opening a word is no qualifier
    ("New Home Sales MoM (Oct)", "New Home Sales"),
    ("Pending Home Sales YoY (Oct)", "Pending Home Sales"),
    ("Employment Cost Index QoQ (Q3)", "Employment Cost Index"),
    ("Factory Orders ex Transportation (Sep)", "Factory Orders"),
    ("Export Prices MoM (Oct)", "Export Prices"),
    ("Michigan 5 Year Inflation Expectations (Nov)", "Michigan 5 Year Inflation Expectations"),
    ("NY Empire State Manufacturing Index (Nov)", "NY Empire State Manufacturing Index"),
    ("Midterm Elections", "Midterm Elections"),
    ("MoM", "MoM"), (None, ""),
])
def test_release_name_drops_the_qualifiers_fmp_splits_one_release_by(raw, release):
    assert MA.release_name(raw) == release


@pytest.mark.parametrize("name", ["Initial Jobless Claims (Oct/31)", "Continuing Jobless Claims (Oct/24)",
                                  "Jobless Claims 4-Week Average (Oct/31)"])
def test_weekly_claims_are_excluded_even_when_high(name):
    assert MA.is_excluded(name)
    assert MA.classify(name, "High") is None and MA.classify(name, "Medium") is None


def test_tier_3_needs_fmp_high_in_any_case():
    for impact in ("High", "high", " HIGH "):
        assert MA.classify("Durable Goods Orders MoM (Oct)", impact) == ("other", "Durable Goods Orders", 3)
    for impact in ("Medium", "Low", "None", None, "", 3):
        assert MA.classify("Durable Goods Orders MoM (Oct)", impact) is None
    assert MA.classify("Midterm Elections", "High") == ("other", "Midterm Elections", 3)


def test_classify_tolerates_garbage():
    for name in (None, "", "   ", "(Oct)", 42):
        assert MA.classify(name, "High") is None and not MA.is_excluded(name)


def test_names_are_matched_case_insensitively():
    assert MA.classify("NONFARM PAYROLLS PRIVATE (Oct)")[0] == "jobs"
    assert MA.classify("fed interest rate decision")[0] == "fomc"


# ---------------------------------------------------------------------------- rows: UTC -> New York
@pytest.mark.parametrize("utc, local, offset_h", [
    ("2026-03-06 13:30:00", "2026-03-06 08:30", -5),     # before the spring switch (Sunday 2026-03-08)
    ("2026-03-08 06:59:00", "2026-03-08 01:59", -5),
    ("2026-03-08 07:00:00", "2026-03-08 03:00", -4),     # 02:00 EST jumps to 03:00 EDT
    ("2026-03-11 12:30:00", "2026-03-11 08:30", -4),     # CPI after the switch: 12:30 UTC
    ("2026-07-29 18:00:00", "2026-07-29 14:00", -4),     # summer FOMC
    ("2026-10-30 12:30:00", "2026-10-30 08:30", -4),     # before the fall switch (Sunday 2026-11-01)
    ("2026-11-01 05:30:00", "2026-11-01 01:30", -4),     # the repeated hour, first pass (EDT) ...
    ("2026-11-01 06:30:00", "2026-11-01 01:30", -5),     # ... and second pass (EST)
    ("2026-11-02 13:30:00", "2026-11-02 08:30", -5),
    ("2026-12-09 19:00:00", "2026-12-09 14:00", -5),     # winter FOMC
    ("2026-04-02 01:00:00", "2026-04-01 21:00", -4),     # the New York date is the day before
])
def test_utc_to_new_york_across_both_2026_dst_switches(utc, local, offset_h):
    (r,) = MA.prepare_rows([row(utc, "Retail Sales MoM (Oct)")])
    assert r["ts_ny"].tz_localize(None) == pd.Timestamp(local)
    assert r["ts_ny"].utcoffset() == pd.Timedelta(hours=offset_h)
    assert r["ts_ny"] == pd.Timestamp(utc, tz="UTC")                       # the same instant
    assert r["time_ny"] == local[11:] and r["date"] == pd.Timestamp(local[:10]) and not r["date_only"]


def test_midnight_utc_is_a_date_only_listing():
    (thanks, election) = MA.prepare_rows([row("2026-11-26 00:00:00", "Thanksgiving Day", "None"),
                                          row("2026-11-03", "Midterm Elections")])[::-1]
    assert thanks["date"] == pd.Timestamp("2026-11-26") and thanks["time_ny"] is None and thanks["date_only"]
    assert election["date"] == pd.Timestamp("2026-11-03") and election["time_ny"] is None
    assert election["ts_ny"] == pd.Timestamp("2026-11-03", tz=C.MARKET_TZ)


def test_prepare_rows_keeps_us_rows_in_time_order_and_counts_garbage():
    rows = [row("2026-11-10 13:30:00", "Inflation Rate YoY (Oct)"),
            row("2026-11-06 13:30:00", "Non Farm Payrolls (Oct)"),
            row("2026-12-17 13:15:00", "Interest Rate Decision", country="EU"),
            {**row("2026-11-12 15:00:00", "Existing Home Sales (Oct)"), "country": " us "},
            row("2026-11-13 13:30:00", "Producer Price Index MoM (Oct)", country=None),   # no country: not US
            row("garbage", "Retail Sales MoM (Oct)"), row(None, "Retail Sales MoM (Oct)"),
            row("2026-11-17 13:30:00", None), row("2026-11-17 13:30:00", "(Oct)"), "not a row", None]
    prepared, skipped = MA._prepare(rows, "US")
    assert [r["name"] for r in prepared] == ["Non Farm Payrolls", "Inflation Rate YoY", "Existing Home Sales"]
    assert skipped == 6
    assert prepared[0]["event"] == "Non Farm Payrolls (Oct)" and prepared[0]["impact"] == "High"
    assert MA.prepare_rows(rows) == prepared
    assert len(MA.prepare_rows(rows, country=None)) == 5                   # every country


@pytest.mark.parametrize("rows", [None, "rows", {"date": "2026-11-10"}, 5, []])
def test_prepare_rows_tolerates_non_lists(rows):
    assert MA.prepare_rows(rows) == [] and MA._prepare(rows, "US") == ([], 0)


# ---------------------------------------------------------------------------- timing / session
@pytest.mark.parametrize("day, time_ny, timing, session", [
    ("2026-11-10", "08:30", "pre_open", "2026-11-10"),
    ("2026-11-10", "09:29", "pre_open", "2026-11-10"),
    ("2026-11-10", "09:30", "intraday", "2026-11-10"),
    ("2026-11-10", "15:59", "intraday", "2026-11-10"),
    ("2026-11-10", "16:00", "after_close", "2026-11-11"),     # Veterans Day: the NYSE is open
    ("2026-11-25", "16:30", "after_close", "2026-11-27"),     # Thanksgiving (11-26) skipped
    ("2026-11-26", "08:30", "non_session", "2026-11-27"),     # Thanksgiving
    ("2026-11-27", "12:59", "intraday", "2026-11-27"),        # 13:00 early close
    ("2026-11-27", "13:00", "after_close", "2026-11-30"),
    ("2026-11-28", "10:00", "non_session", "2026-11-30"),     # Saturday
    ("2026-12-24", "13:30", "after_close", "2026-12-28"),     # early close, then Christmas and a weekend
    ("2026-04-03", "08:30", "non_session", "2026-04-06"),     # NFP on Good Friday 2026
    ("2026-11-03", None, "date_only", "2026-11-03"),
    ("2026-11-26", None, "non_session", "2026-11-27"),
])
def test_session_timing_on_the_nyse_calendar(cal, day, time_ny, timing, session):
    assert MA.session_timing(day, time_ny, cal) == (timing, pd.Timestamp(session))


def test_weekday_calendar_treats_holidays_as_sessions():
    wk = MC.weekday_calendar()
    assert MA.session_timing("2026-11-26", "08:30", wk) == ("pre_open", pd.Timestamp("2026-11-26"))
    assert MA.session_timing("2026-11-27", "13:30", wk) == ("intraday", pd.Timestamp("2026-11-27"))


# ---------------------------------------------------------------------------- grouping
@pytest.mark.parametrize("day, decision_utc, presser_utc", [
    ("2026-12-09", "2026-12-09 19:00:00", "2026-12-09 19:30:00"),     # EST
    ("2026-07-29", "2026-07-29 18:00:00", "2026-07-29 18:30:00"),     # EDT
])
def test_fomc_decision_and_press_conference_are_one_event(cal, day, decision_utc, presser_utc):
    rows = [row(presser_utc, "Press Conference"), row(decision_utc, "Fed Interest Rate Decision"),
            row(decision_utc, "FOMC Economic Projections"), row(decision_utc, "Interest Rate Projection - Longer", "Low")]
    (ev,) = events_of(rows, cal, "2026-07-01")
    assert ev == {"id": "fomc", "label": "FOMC rate decision", "tier": 1, "date": day, "time_ny": "14:00",
                  "timing": "intraday", "session": day, "trading_days_to": ev["trading_days_to"],
                  "calendar_days": (pd.Timestamp(day) - pd.Timestamp("2026-07-01")).days,
                  "releases": ["FOMC Economic Projections", "Fed Interest Rate Decision",
                               "Interest Rate Projection - Longer", "Press Conference"],
                  "impact": "High"}


def test_cpi_rows_form_one_event_and_look_alikes_stay_apart(cal):
    rows = [row("2026-11-10 15:00:00", "Cleveland CPI MoM (Oct)", "High"),
            row("2026-11-10 13:30:00", "Inflation Rate YoY (Oct)", "Medium"),
            row("2026-11-10 13:30:00", "CPI n.s.a MoM (Oct)", "Low"),
            row("2026-11-10 13:30:00", "Core Inflation Rate MoM (Oct)", "High"),
            row("2026-11-10 13:30:00", "CPI s.a (Oct)", "Medium"),
            row("2026-11-09 16:00:00", "Consumer Inflation Expectation (Oct)", "High")]
    cpi, other1, other2 = sorted(events_of(rows, cal, "2026-11-04"), key=lambda e: e["tier"])
    assert (cpi["id"], cpi["date"], cpi["time_ny"], cpi["timing"], cpi["impact"]) == \
        ("cpi", "2026-11-10", "08:30", "pre_open", "High")
    assert cpi["releases"] == ["CPI n.s.a MoM (Oct)", "CPI s.a (Oct)", "Core Inflation Rate MoM (Oct)",
                               "Inflation Rate YoY (Oct)"]
    assert {other1["label"], other2["label"]} == {"Cleveland CPI", "Consumer Inflation Expectation"}


def test_one_group_on_two_dates_is_two_events_and_tier_3_is_per_release(cal):
    # FMP lists one release under several names, each of them rated "High" (the real rows of 2026-11-25): one
    # event per release and date, as for tiers 1-2, so the tier-3 count the report gives counts releases
    rows = [row("2026-11-06 15:00:00", "Michigan Consumer Sentiment (Nov)"),
            row("2026-11-24 15:00:00", "CB Consumer Confidence (Nov)"),
            row("2026-11-25 13:30:00", "Durable Goods Orders MoM (Oct)"),
            row("2026-11-25 13:30:00", "Durable Goods Orders Ex Transp MoM (Oct)"),
            row("2026-11-25 13:30:00", "Durable Goods Orders Ex Defense MoM (Oct)"),
            row("2026-11-25 15:00:00", "New Home Sales MoM (Oct)"),
            row("2026-11-25 15:00:00", "New Home Sales (Oct)"),
            row("2026-11-25 15:00:00", "New Home Sales (Oct)"),          # a repeated row adds nothing
            row("2026-12-23 13:30:00", "Durable Goods Orders MoM (Nov)")]
    evs = events_of(rows, cal, "2026-11-04")
    assert [(e["id"], e["label"], e["date"], e["time_ny"]) for e in evs] == [
        ("consumer_sentiment", "Consumer sentiment", "2026-11-06", "10:00"),
        ("consumer_sentiment", "Consumer sentiment", "2026-11-24", "10:00"),
        ("other", "Durable Goods Orders", "2026-11-25", "08:30"), ("other", "New Home Sales", "2026-11-25", "10:00"),
        ("other", "Durable Goods Orders", "2026-12-23", "08:30")]
    assert evs[2]["releases"] == ["Durable Goods Orders Ex Defense MoM (Oct)",
                                  "Durable Goods Orders Ex Transp MoM (Oct)", "Durable Goods Orders MoM (Oct)"]
    assert evs[3]["releases"] == ["New Home Sales (Oct)", "New Home Sales MoM (Oct)"] and evs[3]["tier"] == 3


def test_row_order_does_not_matter(cal):
    rows = real_rows()
    expected = events_of(rows, cal, "2026-11-04")
    shuffled = rows[:]
    random.Random(3).shuffle(shuffled)
    assert events_of(shuffled, cal, "2026-11-04") == expected == events_of(rows[::-1], cal, "2026-11-04")


def test_after_close_weekend_and_thanksgiving_events_move_to_the_next_session(cal):
    rows = [row("2026-11-25 21:30:00", "Late Release", "High"),                # Wed 16:30 New York
            row("2026-11-26 13:30:00", "Holiday Release", "High"),             # Thanksgiving 08:30
            row("2026-11-27 18:30:00", "Black Friday Release", "High"),        # 13:30, after the 13:00 close
            row("2026-11-28 15:00:00", "Saturday Release", "High"),
            row("2026-11-29 00:00:00", "Sunday Listing", "High")]              # date only
    got = {e["label"]: (e["timing"], e["session"], e["trading_days_to"], e["calendar_days"])
           for e in events_of(rows, cal, "2026-11-24")}
    assert got == {"Late Release": ("after_close", "2026-11-27", 2, 1),
                   "Holiday Release": ("non_session", "2026-11-27", 2, 2),
                   "Black Friday Release": ("after_close", "2026-11-30", 3, 3),
                   "Saturday Release": ("non_session", "2026-11-30", 3, 4),
                   "Sunday Listing": ("non_session", "2026-11-30", 3, 5)}


# ---------------------------------------------------------------------------- as_of filter
def test_as_of_keeps_only_events_after_its_close(cal):
    rows = [row("2026-11-10 13:30:00", "Inflation Rate YoY (Oct)"),      # 08:30 on as_of: in the last bar
            row("2026-11-10 19:00:00", "FOMC Minutes"),                  # 14:00 on as_of: in the last bar
            row("2026-11-10 21:30:00", "Late Speech", "High"),           # 16:30 on as_of: next session
            row("2026-11-11 01:00:00", "Evening Speech", "High"),        # 20:00 on as_of (UTC date is 11-11)
            row("2026-11-09 13:30:00", "Retail Sales MoM (Oct)"),        # before as_of
            row("2026-11-11 13:30:00", "Producer Price Index MoM (Oct)")]
    evs = events_of(rows, cal, "2026-11-10")
    assert [(e["label"], e["date"], e["timing"], e["session"], e["trading_days_to"], e["calendar_days"]) for e in evs] == [
        ("Late Speech", "2026-11-10", "after_close", "2026-11-11", 1, 0),
        ("Evening Speech", "2026-11-10", "after_close", "2026-11-11", 1, 0),
        ("PPI", "2026-11-11", "pre_open", "2026-11-11", 1, 1)]


def test_weekend_events_before_a_monday_as_of_are_in_its_bar(cal):
    rows = [row("2026-11-07 15:00:00", "Saturday Release", "High"),
            row("2026-11-06 21:30:00", "Friday Late Release", "High"),
            row("2026-11-09 21:00:00", "Monday Close Release", "High")]   # 16:00 on as_of -> after close
    assert [e["label"] for e in events_of(rows, cal, "2026-11-09")] == ["Monday Close Release"]


# ---------------------------------------------------------------------------- build_macro_events
def test_real_window_swing(cal):
    m = MA.build_macro_events(ctx(real_rows(), calendar=cal, hz=SWING))
    assert m["available"] and m["source"] == MA.SOURCE and m["window"] == WINDOW and m["horizon_days"] == 10
    assert [(e["id"], e["date"], e["trading_days_to"]) for e in m["events"]] == REAL_EVENTS
    # tier 3: one per release (11-12 existing home sales, 11-16 Empire State, 11-25 durable goods and new home
    # sales, 12-17 housing starts, 12-23 durable goods); counts skip the rows on as_of before its close
    assert m["counts"] == {"tier1": 12, "tier2": 8, "tier3": 6, "excluded": 4, "unclassified": 8}
    assert [(e["id"], e["date"], e["trading_days_to"]) for e in m["within_horizon"]] == REAL_EVENTS[:8]
    assert m["within_horizon"][3] == {"id": "other", "label": "Existing Home Sales", "tier": 3,
                                      "date": "2026-11-12", "time_ny": "10:00", "timing": "intraday",
                                      "session": "2026-11-12", "trading_days_to": 6}
    keys = ("id", "label", "tier", "date", "time_ny", "timing", "session", "trading_days_to")
    assert m["within_horizon"] == [{k: e[k] for k in keys} for e in m["events"][:8]]
    assert m["within_stat_horizons"] == {"5": 2, "10": 4, "20": 5}          # tier 1 only
    assert {g: e and e["date"] for g, e in m["next"].items()} == {
        "fomc": "2026-12-09", "cpi": "2026-11-10", "jobs": "2026-11-06", "pce": "2026-11-25", "ppi": "2026-11-13",
        "gdp": None, "retail_sales": "2026-11-17"}
    assert m["next"]["fomc"] == next(e for e in m["events"] if e["id"] == "fomc")
    assert m["coverage"] == {"listed_through": "2026-12-31", "horizon_end": "2026-11-18", "complete": True,
                             "complete_stat_horizons": {"5": True, "10": True, "20": True}}
    assert m["notes"] == []
    fomc = m["next"]["fomc"]
    assert (fomc["time_ny"], fomc["timing"], fomc["session"], fomc["calendar_days"], len(fomc["releases"])) == \
        ("14:00", "intraday", "2026-12-09", 35, 4)
    json.dumps(m, allow_nan=False)                                          # plain JSON types only


def test_real_window_position_flags_the_unlisted_tail(cal):
    m = MA.build_macro_events(ctx(real_rows(), calendar=cal, hz=POSITION))
    assert m["horizon_days"] == 60
    assert len(m["within_horizon"]) == len(REAL_EVENTS)                    # FOMC 2027-01-27 is session 56
    assert m["within_stat_horizons"] == {"20": 5, "60": 12}
    assert m["coverage"] == {"listed_through": "2026-12-31", "horizon_end": "2027-02-02", "complete": False,
                             "complete_stat_horizons": {"20": True, "60": False}}
    (note,) = m["notes"]
    assert "only through 2026-12-31" in note and "60 sessions (to 2027-02-02)" in note
    # the list holds the FOMC decision of 2027-01-27: FOMC dates reach the request window's end, other releases
    # stop at listed_through, so the note says how far each goes and never that the list stops at 2026-12-31
    assert "FOMC dates through 2027-01-31, the request window's end" in note
    assert "complete only through 2026-12-31" in note and "cover only" not in note
    assert any(e["id"] == "fomc" and e["date"] == "2027-01-27" for e in m["within_horizon"])


def test_the_window_end_can_limit_coverage(cal):
    m = MA.build_macro_events(ctx(real_rows(), calendar=cal, hz=SWING,
                                  window={"from": "2026-11-04", "to": "2026-11-16"}))
    assert m["coverage"]["complete"] is False and m["coverage"]["complete_stat_horizons"] == \
        {"5": True, "10": False, "20": False}
    assert "window ends on 2026-11-16" in m["notes"][-1] and "10 sessions (to 2026-11-18)" in m["notes"][-1]


def test_next_is_a_copy(cal):
    m = MA.build_macro_events(ctx(real_rows(), calendar=cal))
    m["next"]["cpi"]["releases"].append("x")
    assert "x" not in next(e for e in m["events"] if e["id"] == "cpi")["releases"]


def test_date_only_events_get_a_note(cal):
    m = MA.build_macro_events(ctx([row("2026-11-26 00:00:00", "Thanksgiving Day", "None"),
                                   row("2026-11-17 00:00:00", "Midterm Elections")], calendar=cal))
    (ev,) = m["events"]
    assert (ev["label"], ev["time_ny"], ev["timing"], ev["session"]) == \
        ("Midterm Elections", None, "date_only", "2026-11-17")
    assert any("no time in FMP" in n for n in m["notes"])


UNAVAILABLE = {"events": None, "within_horizon": None, "within_stat_horizons": None, "next": None, "counts": None,
               "coverage": None}


def test_an_empty_listing_is_missing_data(cal):
    # an 89-day window always lists US releases: [] is missing data (null + reason), never "no events"
    m = MA.build_macro_events(ctx([], calendar=cal))
    assert m["available"] is False and {k: m[k] for k in UNAVAILABLE} == UNAVAILABLE
    assert m["reason"] == "FMP economic-calendar returned no events for the window (2026-11-04 to 2027-01-31)"
    assert m["window"] == WINDOW and m["horizon_days"] == 10 and m["notes"] == []


def test_a_listing_without_usable_us_rows_is_missing_data(cal):
    m = MA.build_macro_events(ctx([row("2026-12-17 13:15:00", "Interest Rate Decision", country="EU")], calendar=cal))
    assert m["available"] is False and {k: m[k] for k in UNAVAILABLE} == UNAVAILABLE
    assert m["reason"] == "FMP economic-calendar returned no usable US events for the window (2026-11-04 to 2027-01-31)"
    m = MA.build_macro_events(ctx([row("garbage", "Retail Sales MoM (Oct)"), row(None, "CPI s.a (Oct)")], calendar=cal))
    assert m["available"] is False and "no usable US events" in m["reason"]
    assert m["notes"] == ["2 economic-calendar row(s) without a usable date or event name skipped"]


def test_us_rows_without_an_event_after_as_of_leave_the_module_available(cal):
    # the listing is there; nothing is scheduled after the as_of close inside it
    m = MA.build_macro_events(ctx([row("2026-11-04 13:30:00", "Inflation Rate YoY (Oct)")], calendar=cal))
    assert m["available"] is True and m["events"] == [] and m["within_horizon"] == []
    assert m["counts"] == {"tier1": 0, "tier2": 0, "tier3": 0, "excluded": 0, "unclassified": 0}


# ---------------------------------------------------------------------------- boundaries
def test_counts_skip_rows_at_or_before_the_as_of_close(cal):
    # as_of Thursday 2026-11-05: the rows of that day up to its close (and the evening before) are in its bar
    rows = [row("2026-11-05 00:30:00", "Late Release", "Low"),                       # 19:30 on 11-04
            row("2026-11-05 13:30:00", "Initial Jobless Claims (Oct/31)"),           # 08:30: excluded, in the bar
            row("2026-11-05 15:30:00", "EIA Natural Gas Stocks Change (Oct/30)", "Low"),   # 10:30, in the bar
            row("2026-11-05 21:30:00", "API Crude Oil Stock Change (Oct/30)", "Low"),      # 16:30: next session
            row("2026-11-12 13:30:00", "Initial Jobless Claims (Nov/07)")]
    m = MA.build_macro_events(ctx(rows, as_of="2026-11-05", calendar=cal,
                                  window={"from": "2026-11-05", "to": "2027-02-01"}))
    assert m["events"] == []
    assert m["counts"] == {"tier1": 0, "tier2": 0, "tier3": 0, "excluded": 1, "unclassified": 1}


def test_stat_horizons_count_an_event_exactly_h_sessions_out(cal):
    # as of 2026-11-03 the first CPI is 5 sessions out and retail sales 10: both count (trading_days_to <= h)
    m = MA.build_macro_events(ctx(real_rows(), as_of="2026-11-03", calendar=cal))
    first = {}
    for e in m["events"]:
        first.setdefault(e["id"], e["trading_days_to"])
    assert (first["cpi"], first["retail_sales"]) == (5, 10)
    assert m["within_stat_horizons"] == {"5": 2, "10": 4, "20": 5}


@pytest.mark.parametrize("as_of, to, complete, stat", [
    ("2026-12-16", "2027-01-31", True, {"5": True, "10": True, "20": False}),    # horizon ends on listed_through
    ("2026-11-04", "2026-11-18", True, {"5": True, "10": True, "20": False}),    # ... on the window's last day
    ("2026-11-04", "2026-11-17", False, {"5": True, "10": False, "20": False}),
])
def test_coverage_holds_a_horizon_that_ends_on_the_last_listed_day(cal, as_of, to, complete, stat):
    m = MA.build_macro_events(ctx(real_rows(), as_of=as_of, calendar=cal, window={"from": as_of, "to": to}))
    cov = m["coverage"]
    assert (cov["complete"], cov["complete_stat_horizons"]) == (complete, stat)
    assert cov["horizon_end"] == {"2026-12-16": "2026-12-31", "2026-11-04": "2026-11-18"}[as_of]
    (note,) = m["notes"]
    assert "20 sessions" in note and ("10 sessions" in note) is (not complete)


def test_unusable_rows_are_counted(cal):
    m = MA.build_macro_events(ctx(real_rows() + [row("bad", "CPI s.a (Oct)"), "junk"], calendar=cal))
    assert m["counts"]["tier1"] == 12
    assert "2 economic-calendar row(s) without a usable date or event name skipped" in m["notes"]


@pytest.mark.parametrize("skipped, reason", [
    ({"macro_events": "restricted: economic-calendar is not in this plan"},
     "restricted: economic-calendar is not in this plan"),
    ({}, "economic calendar unavailable"),
])
def test_failed_fetch_is_unavailable_with_its_reason(cal, skipped, reason):
    m = MA.build_macro_events(ctx(None, calendar=cal, skipped=skipped))
    assert m["available"] is False and m["reason"] == reason
    assert m["events"] is None and m["within_horizon"] is None and m["next"] is None and m["counts"] is None
    assert m["window"] == WINDOW and m["horizon_days"] == 10 and m["notes"] == []


def test_a_payload_that_is_not_a_list_is_unavailable(cal):
    m = MA.build_macro_events(ctx({"Error Message": "Limit reached"}, calendar=cal))
    assert m["available"] is False and "not a list" in m["reason"]


def test_without_a_calendar_sessions_are_weekdays():
    rows = [row("2026-11-27 13:30:00", "Goods Trade Balance (Oct)"), row("2026-11-26 13:30:00", "Holiday Release")]
    m = MA.build_macro_events(ctx(rows, as_of="2026-11-24", calendar=None, window=None))
    assert m["notes"][0].startswith("no exchange calendar")
    assert [(e["label"], e["session"], e["trading_days_to"]) for e in m["events"]] == [
        ("Holiday Release", "2026-11-26", 2), ("Goods Trade Balance", "2026-11-27", 3)]
    assert m["window"] == {"from": None, "to": None}


def test_horizon_defaults_from_the_horizon_name(cal):
    c = ctx(real_rows(), calendar=cal, hz=None)
    c["horizon"] = "position"
    assert MA.build_macro_events(c)["horizon_days"] == 60
