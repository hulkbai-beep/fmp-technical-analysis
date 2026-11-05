"""NYSE exchange calendar (scripts/market_calendar.py): the rules against FMP's holiday list for 1990-2028
(tests/data/nyse_holidays_fmp.json), the FMP/rules union and every session helper (no network)."""
import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config as C
import market_calendar as M

FIXTURE = Path(__file__).resolve().parent / "data" / "nyse_holidays_fmp.json"


def ts(d):
    return pd.Timestamp(d)


def ny(t):
    return pd.Timestamp(t, tz=C.MARKET_TZ)


@pytest.fixture(scope="module")
def rows():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)["rows"]


@pytest.fixture(scope="module")
def cal(rows):
    return M.build_calendar(rows, end_year=2028)


@pytest.fixture(scope="module")
def rules_cal():
    return M.build_calendar(None, end_year=2028)


# ---------------------------------------------------------------------------- rules vs FMP
def test_rule_closures_equal_fmp_for_1991_2028_and_add_new_year_1990(rows):
    rules, _ = M.rules_closed_and_early(1990, 2028)
    fmp, _ = M.from_fmp_rows(rows)
    assert len(fmp) == 354 and len(rules) == 355
    assert rules - fmp == {ts("1990-01-01")}                 # FMP's list starts in February 1990
    assert fmp - rules == set()
    assert {d for d in rules if d.year >= 1991} == {d for d in fmp if d.year >= 1991}
    assert all(d.weekday() < 5 and d == d.normalize() and d.tzinfo is None for d in rules)


def test_special_closures_are_weekdays_inside_the_rules(rules_cal):
    assert len(M.SPECIAL_CLOSURES) == 11
    for day in M.SPECIAL_CLOSURES:
        assert ts(day).weekday() < 5 and not rules_cal.is_session(day)
    _, early = M.rules_closed_and_early(2001, 2001)
    closed, _ = M.rules_closed_and_early(2001, 2001)
    assert {ts(f"2001-09-{d}") for d in (11, 12, 13, 14)} <= closed and not set(early) & closed


@pytest.mark.parametrize("day, is_session", [
    ("2023-01-02", False), ("2021-12-31", True),           # Sunday New Year -> Monday; Saturday: not observed
    ("1997-01-20", True), ("1998-01-19", False),           # MLK day from 1998
    ("2021-06-18", True), ("2022-06-20", False), ("2027-06-18", False),   # Juneteenth from 2022, nearest weekday
    ("2026-07-03", False), ("2027-07-05", False),          # Independence Day on a Saturday / Sunday
    ("2027-12-24", False), ("2022-12-26", False),          # Christmas on a Saturday / Sunday
    ("2026-02-16", False), ("2026-04-03", False), ("2026-05-25", False), ("2026-09-07", False),
    ("2026-11-26", False), ("2026-12-25", False), ("2027-01-01", False),
    ("2026-10-12", True), ("2026-11-11", True),            # Columbus / Veterans Day: NYSE is open
])
def test_rule_holidays_follow_nyse_observance(rules_cal, day, is_session):
    assert rules_cal.is_session(day) is is_session


def test_rule_early_closes_against_fmp_2013_2028(rows):
    _, rules = M.rules_closed_and_early(2013, 2028)
    _, fmp = M.from_fmp_rows(rows)
    fmp = {d: t for d, t in fmp.items() if 2013 <= d.year <= 2028}
    assert len(rules) == 34 and len(fmp) == 32
    assert set(fmp) <= set(rules)                            # every FMP early close is a rule early close
    assert set(rules) - set(fmp) == {ts("2028-07-03"), ts("2028-11-24")}   # FMP has not listed 2028's yet
    assert [d for d in fmp if fmp[d] != rules[d]] == [ts("2015-11-27")]
    assert fmp[ts("2015-11-27")] == "13:30" and set(rules.values()) == {C.EARLY_CLOSE_DEFAULT}


@pytest.mark.parametrize("day, early", [
    ("2025-07-03", True), ("2028-07-03", True), ("2026-07-02", False),   # July 3 Mon-Thu; 2026-07-03 is closed
    ("2026-11-27", True), ("2027-11-26", True),                          # day after Thanksgiving
    ("2026-12-24", True), ("2024-12-24", True), ("2027-12-23", False),   # Dec 24 Mon-Thu; 2027-12-24 is closed
    ("2026-12-31", False),
])
def test_rule_early_close_dates(day, early):
    closed, rules = M.rules_closed_and_early(2024, 2028)
    assert (ts(day) in rules) is early
    assert not set(rules) & closed


def test_rule_years_are_validated():
    closed, early = M.rules_closed_and_early(2026, 2026)
    assert len(closed) == 10 and len(early) == 2            # 2026: ten weekday holidays, two early closes
    with pytest.raises(ValueError, match="before"):
        M.rules_closed_and_early(2027, 2026)


# ---------------------------------------------------------------------------- union
def test_union_keeps_rule_days_fmp_lacks_and_adds_fmp_only_days(rows):
    trimmed = [r for r in rows if r["date"] not in ("2026-11-26", "2026-12-25")]
    extra = [{"date": "2026-12-31", "name": "National Day of Mourning", "isClosed": True, "adjCloseTime": None},
             {"date": "2026-12-25", "name": "Christmas", "isClosed": None, "adjCloseTime": "13:00"}]
    assert M.from_fmp_rows(trimmed + extra)[1][ts("2026-12-25")] == "13:00"   # FMP alone: an early close
    cal = M.build_calendar(trimmed + extra, end_year=2028)
    assert cal.source == "fmp+rules" and cal.problem is None
    assert not cal.is_session("2026-11-26")                  # the rules still close Thanksgiving
    assert not cal.is_session("2026-12-31")                  # an FMP-only closure is kept
    assert not cal.is_session("1990-01-01")                  # a rules-only closure is kept
    # a rule closure beats an FMP early close on the same day
    assert not cal.is_session("2026-12-25") and ts("2026-12-25") not in cal.early
    assert cal.close_time("2026-12-25") == C.SESSION_CLOSE
    assert cal.close_time("2015-11-27") == "13:30"           # FMP's time wins over the rule's 13:00
    assert cal.close_time("1993-12-23") == "13:00"           # FMP-only early close
    assert cal.close_time("2028-11-24") == "13:00"           # rules-only early close
    assert len(cal.closed) == 356


def test_union_counts_on_the_fixture(cal):
    assert len(cal.closed) == 355                            # 354 FMP days + 1990-01-01
    assert len(cal.early) == 88                              # 83 rule + 51 FMP early closes, 46 in both
    assert cal.summary() == {"source": "fmp+rules", "exchange": "NYSE", "closed_days": 355,
                             "early_closes": 88, "problem": None}


def test_fmp_days_beyond_the_rule_years_still_count(rows):
    cal = M.build_calendar(rows, start_year=2020, end_year=2026)
    assert not cal.is_session("2028-12-25") and not cal.is_session("1995-12-25")   # FMP rows outside the rules
    assert cal.is_session("1990-01-01")                      # only the rules know it, and 1990 is not covered


# ---------------------------------------------------------------------------- single days
def test_thanksgiving_2026(cal):
    assert not cal.is_session("2026-11-26")
    assert cal.is_session("2026-11-27") and cal.close_time("2026-11-27") == "13:00"
    assert cal.is_session("2026-11-25") and cal.close_time("2026-11-25") == C.SESSION_CLOSE == "16:00"
    assert not cal.is_session("2026-11-28")
    assert cal.early[ts("2026-11-27")] == "13:00"


def test_good_friday_week_ends_on_thursday(cal):
    assert cal.week_last_session("2026-03-30") == ts("2026-04-02")
    for day in pd.date_range("2026-03-28", "2026-04-03"):   # Saturday..Friday is one W-FRI week
        assert cal.week_last_session(day) == ts("2026-04-02")
    assert cal.week_last_session("2026-04-04") == ts("2026-04-10")   # Saturday opens the next week
    assert cal.week_last_session("2026-11-23") == ts("2026-11-27")   # an early close still ends its week
    assert cal.week_last_session("2001-09-12") == ts("2001-09-10")   # 9/11 week: Monday was the only session
    assert M.weekday_calendar().week_last_session("2026-03-30") == ts("2026-04-03")


def test_week_without_a_session_has_no_last_session():
    cal = M.MarketCalendar(closed=pd.bdate_range("2030-01-07", "2030-01-11"))
    assert cal.week_last_session("2030-01-09") is None
    assert cal.week_last_session("2030-01-14") == ts("2030-01-18")


@pytest.mark.parametrize("now, expected", [
    ("2026-11-26 10:00", "2026-11-25"),     # Thanksgiving morning
    ("2026-11-26 18:00", "2026-11-25"),     # Thanksgiving evening: still no session today
    ("2026-11-27 13:10", "2026-11-25"),     # early close, before 13:00 + 15 min: previous session
    ("2026-11-27 13:20", "2026-11-27"),     # early close, after 13:15: today
    ("2026-11-28 11:00", "2026-11-27"),     # Saturday
    ("2026-11-29 22:00", "2026-11-27"),     # Sunday
    ("2026-11-30 09:00", "2026-11-27"),     # Monday before the open
    ("2026-11-30 16:14", "2026-11-27"),
    ("2026-11-30 16:15", "2026-11-30"),
    ("2026-04-03 17:00", "2026-04-02"),     # Good Friday
    ("2026-01-02 08:00", "2025-12-31"),     # the morning after New Year's Day
    ("2001-09-17 08:00", "2001-09-10"),     # the reopening after 9/11
])
def test_expected_session(cal, now, expected):
    assert cal.expected_session(ny(now)) == ts(expected)


def test_after_close_uses_the_session_close_in_new_york_time(cal):
    assert not cal.after_close(ny("2026-11-27 13:14")) and cal.after_close(ny("2026-11-27 13:15"))
    assert not cal.after_close(ny("2026-11-25 16:14")) and cal.after_close(ny("2026-11-25 16:15"))
    # UTC input is converted: 20:15Z is 16:15 EDT after the March switch, 21:15Z is 16:15 EST in November
    assert cal.after_close(pd.Timestamp("2026-03-09 20:15", tz="UTC"))
    assert not cal.after_close(pd.Timestamp("2026-03-09 20:14", tz="UTC"))
    assert cal.after_close(pd.Timestamp("2026-11-30 21:15", tz="UTC"))
    assert not cal.after_close(pd.Timestamp("2026-11-30 21:14", tz="UTC"))
    assert cal.after_close(pd.Timestamp("2026-11-27 13:15"))           # naive input: New York time
    assert cal.after_close(dt.datetime(2026, 11, 27, 18, 15, tzinfo=dt.timezone.utc))
    # 01:00 UTC on the 28th is 20:00 on the 27th in New York: the 27th is the expected session
    assert cal.expected_session(pd.Timestamp("2026-11-28 01:00", tz="UTC")) == ts("2026-11-27")
    # naive times that are ambiguous or skipped at a DST switch (Sunday nights) do not raise
    assert cal.expected_session("2026-11-01 01:30") == ts("2026-10-30")
    assert cal.expected_session("2026-03-08 02:30") == ts("2026-03-06")
    with pytest.raises(ValueError):
        cal.after_close(None)


# ---------------------------------------------------------------------------- counting
def test_sessions_between_across_thanksgiving(cal):
    assert cal.sessions_between("2026-11-25", "2026-11-30") == 2      # 11-27 and 11-30
    assert cal.sessions_between("2026-11-25", "2026-11-27") == 1
    assert cal.sessions_between("2026-11-26", "2026-11-27") == 1
    assert cal.sessions_between("2026-11-20", "2026-12-01") == 6
    assert cal.sessions_between("2026-11-25", "2026-11-25") == 0
    assert cal.sessions_between("2026-11-30", "2026-11-25") == 0
    assert M.weekday_calendar().sessions_between("2026-11-25", "2026-11-30") == 3
    assert list(cal.sessions("2026-11-24", "2026-11-30").strftime("%Y-%m-%d")) == \
        ["2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"]
    assert len(cal.sessions("2026-01-01", "2026-12-31")) == 251
    assert len(cal.sessions("2026-12-01", "2026-11-01")) == 0


def test_missing_between(cal):
    assert cal.missing_between("2001-09-10", "2001-09-17") == 0      # 9/11 closures are not data holes
    assert M.weekday_calendar().missing_between("2001-09-10", "2001-09-17") == 4
    assert cal.missing_between("2026-11-24", "2026-11-27") == 1      # 11-25 has no bar; 11-26 was closed
    assert cal.missing_between("2026-11-25", "2026-11-27") == 0
    assert cal.missing_between("2026-03-31", "2026-04-06") == 2      # 04-01, 04-02; Good Friday closed
    assert cal.missing_between("2026-11-27", "2026-11-30") == 0
    assert cal.missing_between("2026-11-27", "2026-11-25") == 0      # reversed pair
    assert cal.missing_between("2026-11-25", "2026-11-25") == 0


def _brute_missing(cal, a, b):
    days = pd.date_range(a + pd.Timedelta(days=1), b - pd.Timedelta(days=1)) if b - a > pd.Timedelta(days=1) else []
    return sum(1 for d in days if d.weekday() < 5 and d not in cal.closed)


def test_missing_sessions_between_matches_the_scalar_version(cal):
    rng = np.random.default_rng(11)
    a = pd.DatetimeIndex(ts("1990-01-01") + pd.to_timedelta(rng.integers(0, 14_000, 400), unit="D"))
    b = a + pd.to_timedelta(rng.integers(-3, 40, 400), unit="D")       # includes b <= a pairs
    vec = cal.missing_sessions_between(a, b)
    assert vec.dtype == np.int64 and vec.shape == (400,)
    assert list(vec) == [cal.missing_between(x, y) for x, y in zip(a, b)]
    assert list(vec[:100]) == [_brute_missing(cal, x, y) for x, y in zip(a[:100], b[:100])]
    assert vec.min() == 0 and vec.max() > 20
    # the same pairs as strings, Series and tz-aware indexes
    assert list(cal.missing_sessions_between(list(a.strftime("%Y-%m-%d")), pd.Series(b))) == list(vec)
    aware = cal.missing_sessions_between(a.tz_localize(C.MARKET_TZ), b.tz_localize(C.MARKET_TZ))
    assert list(aware) == list(vec)
    assert list(cal.missing_sessions_between("2026-11-24", "2026-11-27")) == [1]


def test_missing_sessions_between_on_a_long_history_is_fast(cal):
    index = cal.sessions("1990-01-01", "2026-11-04")[:9001]
    holes = [index[100], index[5000], index[5001], index[8999]]
    bars = index.difference(pd.DatetimeIndex(holes))
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        missing = cal.missing_sessions_between(bars[:-1], bars[1:])
        best = min(best, time.perf_counter() - t0)
    assert best < 0.5
    assert len(missing) == len(bars) - 1 >= 8_990
    assert missing.sum() == 4 and sorted(missing[missing > 0]) == [1, 1, 2]
    assert M.weekday_calendar().missing_sessions_between(bars[:-1], bars[1:]).sum() > 300   # holidays as holes


def test_holidays_array_feeds_numpy_busday_count(cal):
    arr = cal.holidays_array
    assert arr.dtype == np.dtype("datetime64[D]") and len(arr) == len(cal.closed)
    assert (np.diff(arr.astype("int64")) > 0).all()
    assert set(pd.DatetimeIndex(arr)) == set(cal.closed)
    assert not arr.flags.writeable
    assert np.busday_count("2026-11-25", "2026-12-01", holidays=arr) == 3 == \
        len(cal.sessions("2026-11-25", "2026-11-30"))


# ---------------------------------------------------------------------------- stepping
def test_next_previous_and_add_sessions(cal):
    assert cal.next_session("2026-11-25") == ts("2026-11-27")
    assert cal.next_session("2026-11-26") == ts("2026-11-27")
    assert cal.next_session("2026-11-26", inclusive=True) == ts("2026-11-27")
    assert cal.next_session("2026-11-27", inclusive=True) == ts("2026-11-27")
    assert cal.next_session("2026-11-27") == ts("2026-11-30")
    assert cal.next_session("2026-11-28") == ts("2026-11-30")
    assert cal.next_session("2001-09-10") == ts("2001-09-17")
    assert cal.previous_session("2026-11-27") == ts("2026-11-25")
    assert cal.previous_session("2026-11-26") == ts("2026-11-25")
    assert cal.previous_session("2026-11-26", inclusive=True) == ts("2026-11-25")
    assert cal.previous_session("2026-11-25", inclusive=True) == ts("2026-11-25")
    assert cal.previous_session("2026-11-29") == ts("2026-11-27")
    assert cal.previous_session("2001-09-17") == ts("2001-09-10")
    assert cal.add_sessions("2026-11-25", 1) == ts("2026-11-27") == cal.next_session("2026-11-25")
    assert cal.add_sessions("2026-11-25", 3) == ts("2026-12-01")
    assert cal.add_sessions("2026-11-26", 1) == ts("2026-11-27")
    assert cal.add_sessions("2026-12-23", 2) == ts("2026-12-28")     # 12-24 is a (short) session, 12-25 closed
    assert cal.add_sessions("2026-11-30", -2) == ts("2026-11-25")
    for n in (1, 5, 20, 60):
        day = cal.add_sessions("2026-11-05", n)
        assert cal.is_session(day) and cal.sessions_between("2026-11-05", day) == n
    out = cal.add_sessions("2026-11-25", np.int64(1))
    assert isinstance(out, pd.Timestamp) and out.tzinfo is None and out == out.normalize()
    with pytest.raises(ValueError):
        cal.add_sessions("2026-11-25", 0)
    with pytest.raises(TypeError):
        cal.add_sessions("2026-11-25", 1.5)


def test_dates_of_any_form_are_accepted(cal):
    for day in ("2026-11-26", dt.date(2026, 11, 26), dt.datetime(2026, 11, 26, 15, 0), np.datetime64("2026-11-26"),
                pd.Timestamp("2026-11-26").as_unit("s"), ny("2026-11-26 23:00")):
        assert not cal.is_session(day)
    assert cal.is_session(ny("2026-11-25 23:59"))                     # a tz-aware time keeps its own date
    assert pd.Timestamp("2026-11-26").as_unit("ns") in cal.closed
    with pytest.raises(ValueError):
        cal.is_session(None)


# ---------------------------------------------------------------------------- FMP rows and fallbacks
def test_from_fmp_rows_tolerates_garbage():
    for junk in (None, {}, {"Error Message": "Premium Query Parameter"}, "text", 42, b"[]"):
        assert M.from_fmp_rows(junk) == (set(), {})
    rows = [
        None, 1, "x", [], {}, {"date": None, "isClosed": True}, {"date": 20261126, "isClosed": True},
        {"date": "not a date", "isClosed": True}, {"date": "2026-13-45", "isClosed": True},
        {"date": "2026-11-28", "isClosed": True},                       # Saturday: ignored
        {"date": "2026-11-29", "adjCloseTime": "13:00"},                # Sunday: ignored
        {"date": "2026-11-26T00:00:00", "isClosed": True},              # time suffix: fine
        {"date": "2026-12-25", "isClosed": "true"},
        {"date": "2026-01-19", "isClosed": 1},
        {"date": "2026-01-01", "isClosed": "false"},                    # a string "false" is not closed
        {"date": "2026-02-16", "isClosed": 0.0},
        {"date": "2026-04-03", "isClosed": float("nan")},
        {"date": "2026-11-27", "isClosed": None, "adjCloseTime": "13:00:00"},
        {"date": "2026-12-24", "isClosed": None, "adjCloseTime": "13:00"},
        {"date": "2026-12-24", "isClosed": None, "adjCloseTime": "13:30"},   # repeated date: later time
        {"date": "2026-07-02", "adjCloseTime": "1pm"}, {"date": "2026-07-06", "adjCloseTime": "25:00"},
        {"date": "2026-07-07", "adjCloseTime": "16:00"}, {"date": "2026-07-08", "adjCloseTime": "08:00"},
        {"date": "2026-07-09", "adjCloseTime": 1300},
        {"date": "2026-05-25", "isClosed": True, "adjCloseTime": "13:00"},   # closed wins
    ]
    closed, early = M.from_fmp_rows(rows)
    assert closed == {ts("2026-11-26"), ts("2026-12-25"), ts("2026-01-19"), ts("2026-05-25")}
    assert early == {ts("2026-11-27"): "13:00", ts("2026-12-24"): "13:30"}


def test_build_calendar_falls_back_to_rules_when_fmp_is_restricted(rules_cal):
    problem = "restricted: holidays-by-exchange is not available on your plan"
    cal = M.build_calendar(None, problem, end_year=2028)
    assert cal.source == "rules" and cal.problem == problem
    assert cal.closed == rules_cal.closed and cal.early == rules_cal.early
    assert not cal.is_session("2026-11-26") and cal.close_time("2026-11-27") == "13:00"
    assert cal.close_time("2015-11-27") == "13:00"                     # no FMP: the rule's time
    assert cal.summary()["problem"] == problem and cal.summary()["exchange"] == "NYSE"
    for rows in ([], [{"date": "garbage"}], {"Error Message": "x"}):
        empty = M.build_calendar(rows, end_year=2028)
        assert empty.source == "rules" and "no usable" in empty.problem and empty.closed == rules_cal.closed
    assert M.build_calendar(None, end_year=2028).problem is None        # FMP not asked: nothing to report


def test_build_calendar_defaults_reach_past_next_year():
    cal = M.build_calendar()
    year = pd.Timestamp.now(tz=C.MARKET_TZ).year + C.CALENDAR_FUTURE_YEARS + 1
    thanksgiving = pd.Timestamp(year=year, month=11, day=1)
    thanksgiving += pd.Timedelta(days=(3 - thanksgiving.weekday()) % 7 + 21)
    assert not cal.is_session(thanksgiving) and cal.close_time(thanksgiving + pd.Timedelta(days=1)) == "13:00"
    assert not cal.is_session("1990-01-01") and cal.source == "rules"
    assert min(cal.closed).year == int(C.HISTORY_START[:4]) and max(cal.closed).year == year


def test_weekday_calendar_has_no_holidays():
    w = M.weekday_calendar()
    assert w.closed == frozenset() and w.early == {} and w.source == "weekdays" and w.problem is None
    assert w.holidays_array.size == 0 and w.holidays_array.dtype == np.dtype("datetime64[D]")
    assert w.is_session("2026-11-26") and w.is_session("2026-12-25") and not w.is_session("2026-11-28")
    assert w.close_time("2026-11-27") == "16:00"
    assert w.expected_session(ny("2026-11-27 13:20")) == ts("2026-11-26")   # no early close: still in session
    assert w.expected_session(ny("2026-11-28 12:00")) == ts("2026-11-27")
    assert len(w.sessions("2026-01-01", "2026-12-31")) == 261
    assert w.summary() == {"source": "weekdays", "exchange": None, "closed_days": 0, "early_closes": 0,
                           "problem": None}


def test_constructor_keeps_weekday_closures_and_early_closes_on_sessions_only():
    cal = M.MarketCalendar(closed=["2026-11-28", "2026-11-26"],
                           early={"2026-11-26": "13:00", "2026-11-29": "13:00", "2026-11-27": "13:00:00"})
    assert cal.closed == frozenset({ts("2026-11-26")})
    assert cal.early == {ts("2026-11-27"): "13:00"}
    assert cal.source == "rules" and "closed_days=1" in repr(cal)
    with pytest.raises(ValueError, match="HH:MM"):
        M.MarketCalendar(early={"2026-11-27": "1pm"})
