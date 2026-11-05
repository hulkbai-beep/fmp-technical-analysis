"""
NYSE exchange calendar: which weekdays are sessions, and when each session closes.

One FMP request (holidays-by-exchange, exchange=NYSE) is merged with rules that reproduce the
NYSE schedule:
- full closures = FMP's closed days united with the rule holidays and SPECIAL_CLOSURES;
- early closes = FMP's early closes united with the rule early closes, FMP's time winning.

The rules match FMP's closed days for 1991-2028 exactly and add 1990-01-01, which FMP's list
lacks. The union keeps the calendar complete when FMP is restricted or unreachable (rules only,
`source` "rules", `problem` says why) and picks up a closure FMP lists that no rule knows (a
national day of mourning announced at short notice). Early closes only decide whether today's bar
is complete; FMP's early-close list is incomplete before about 2013, and the rules fill it in.

With a calendar, the data layer counts NYSE sessions instead of weekdays: the partial-bar rule
(the session's close + C.CLOSE_BUFFER_MINUTES), the stale check, data-gap flags, trading days to
an event and the weekly "partial" flag. weekday_calendar() keeps the old weekday-only behaviour.

Dates are tz-naive, normalized pd.Timestamps; a tz-aware date is read as its wall-clock date in
its own time zone. Times of day ("HH:MM") are New York time.
"""
from __future__ import annotations

import datetime as dt
import numbers
import operator
import re

import numpy as np
import pandas as pd
from pandas.tseries.holiday import (GoodFriday, Holiday, USLaborDay, USMartinLutherKingJr, USMemorialDay,
                                    USPresidentsDay, USThanksgivingDay, nearest_workday, sunday_to_monday)

import config as C

WEEKMASK = "1111100"   # Monday-Friday

# Regular NYSE holidays. A Saturday New Year's Day is not observed on the Friday before; Juneteenth,
# Independence Day and Christmas move to the nearest weekday.
HOLIDAY_RULES = (
    Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),
    Holiday("Martin Luther King Jr. Day", month=1, day=1, offset=USMartinLutherKingJr.offset,
            start_date="1998-01-01"),                     # an NYSE holiday since 1998
    USPresidentsDay,                                      # Washington's Birthday, third Monday of February
    GoodFriday,
    USMemorialDay,
    Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
    Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
    USLaborDay,
    USThanksgivingDay,
    Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
)

# Unscheduled full-day closures since 1990 that no rule produces (FMP lists them under unrelated
# holiday names, e.g. 2001-09-11 as "Labor Day").
SPECIAL_CLOSURES = {
    "1994-04-27": "national day of mourning, Richard Nixon",
    "2001-09-11": "September 11 attacks",
    "2001-09-12": "September 11 attacks",
    "2001-09-13": "September 11 attacks",
    "2001-09-14": "September 11 attacks",
    "2004-06-11": "national day of mourning, Ronald Reagan",
    "2007-01-02": "national day of mourning, Gerald Ford",
    "2012-10-29": "Hurricane Sandy",
    "2012-10-30": "Hurricane Sandy",
    "2018-12-05": "national day of mourning, George H. W. Bush",
    "2025-01-09": "national day of mourning, Jimmy Carter",
}

_HHMM = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$")
_TRUE_STRINGS = ("true", "1", "yes")


# ---------------------------------------------------------------------------- small helpers
def _date(d) -> pd.Timestamp:
    """`d` as a tz-naive midnight Timestamp (a tz-aware input keeps its own wall-clock date)."""
    ts = pd.Timestamp(d)
    if ts is pd.NaT:
        raise ValueError(f"not a date: {d!r}")
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def _np_day(d) -> np.datetime64:
    return np.datetime64(_date(d).date(), "D")


def _days(values) -> np.ndarray:
    """Array-like (or one) date(s) as datetime64[D], tz-aware values read as their wall-clock dates."""
    if isinstance(values, (str, dt.date, np.datetime64)):
        values = [values]
    idx = pd.DatetimeIndex(values)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize().to_numpy().astype("datetime64[D]")


def _ny(now) -> pd.Timestamp:
    """`now` as a New York timestamp; a naive input is taken as New York time."""
    ts = pd.Timestamp(now)
    if ts is pd.NaT:
        raise ValueError(f"not a time: {now!r}")
    if ts.tzinfo is None:
        return ts.tz_localize(C.MARKET_TZ, ambiguous=True, nonexistent="shift_forward")
    return ts.tz_convert(C.MARKET_TZ)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hhmm(value) -> str | None:
    """'13:00', '13:00:00' or ' 9:30 ' -> 'HH:MM'; anything else -> None."""
    if not isinstance(value, str):
        return None
    m = _HHMM.match(value)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return f"{h:02d}:{mi:02d}" if h <= 23 and mi <= 59 else None


def _flag(value) -> bool:
    """FMP's isClosed: true for True, a non-zero number or 'true' / '1' / 'yes'; None, False, 'false' -> False."""
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, numbers.Real):
        return value == value and value != 0          # NaN -> False
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return False


def _row_day(value) -> pd.Timestamp | None:
    """FMP's `date` ('YYYY-MM-DD', possibly with a time) as a midnight Timestamp; garbage -> None."""
    if isinstance(value, str):
        day = pd.to_datetime(value.strip()[:10], format="%Y-%m-%d", errors="coerce")
    elif isinstance(value, (dt.date, np.datetime64)):
        day = pd.Timestamp(value)
    else:
        return None
    return None if pd.isna(day) else _date(day)


def _is_early_time(hhmm: str) -> bool:
    return _minutes(C.SESSION_OPEN) < _minutes(hhmm) < _minutes(C.SESSION_CLOSE)


# ---------------------------------------------------------------------------- the calendar
class MarketCalendar:
    """NYSE sessions: weekdays that are not full closures, each closing at C.SESSION_CLOSE or earlier.

    closed          frozenset of weekday full closures (tz-naive, normalized)
    early           {date: "HH:MM"} early closes, on sessions only
    source          "fmp+rules", "rules" or "weekdays"
    problem         why FMP's list was not used (None when it was, or was not asked for)
    holidays_array  the closed days as sorted datetime64[D], for np.busday_count(..., holidays=...)
    busdaycal       np.busdaycalendar of the same days (Monday-Friday week)
    """

    def __init__(self, closed=(), early=None, source: str = "rules", problem: str | None = None):
        self.closed = frozenset(d for d in map(_date, closed) if d.weekday() < 5)
        merged = {}
        for d, t in (early or {}).items():
            day, hhmm = _date(d), _hhmm(t)
            if hhmm is None:
                raise ValueError(f"early close for {day:%Y-%m-%d} is not HH:MM: {t!r}")
            if day.weekday() < 5 and day not in self.closed:
                merged[day] = hhmm
        self.early = dict(sorted(merged.items()))
        self.source = source
        self.problem = problem
        days = pd.DatetimeIndex(sorted(self.closed))
        self.holidays_array = days.to_numpy().astype("datetime64[D]")
        self.holidays_array.setflags(write=False)
        self.busdaycal = np.busdaycalendar(weekmask=WEEKMASK, holidays=self.holidays_array)
        self._closed_index = days

    def __repr__(self) -> str:
        return (f"MarketCalendar(source={self.source!r}, closed_days={len(self.closed)}, "
                f"early_closes={len(self.early)}, problem={self.problem!r})")

    def summary(self) -> dict:
        """Where the calendar came from and what it holds (for data_quality)."""
        return {"source": self.source,
                "exchange": None if self.source == "weekdays" else C.CALENDAR_EXCHANGE,
                "closed_days": len(self.closed), "early_closes": len(self.early), "problem": self.problem}

    # ---------------------------------------------------------------- single days
    def is_session(self, d) -> bool:
        """A weekday that is not a full closure."""
        day = _date(d)
        return day.weekday() < 5 and day not in self.closed

    def close_time(self, d) -> str:
        """"HH:MM" New York close of the session on `d`: the early-close time or C.SESSION_CLOSE
        (also C.SESSION_CLOSE for a day without a session)."""
        return self.early.get(_date(d), C.SESSION_CLOSE)

    def after_close(self, now_ny) -> bool:
        """True once `now_ny` is at least C.CLOSE_BUFFER_MINUTES past the close of its New York date
        (16:15, or 13:15 on a 13:00 early close). A naive `now_ny` is taken as New York time."""
        now = _ny(now_ny)
        due = _minutes(self.close_time(now)) + C.CLOSE_BUFFER_MINUTES
        return now.hour * 60 + now.minute >= due

    def expected_session(self, now_ny) -> pd.Timestamp:
        """The last session whose bar should be complete at `now_ny`: today once the close buffer has
        passed on a session day, otherwise the previous session."""
        now = _ny(now_ny)
        today = _date(now)
        if self.is_session(today) and self.after_close(now):
            return today
        return self.previous_session(today)

    # ---------------------------------------------------------------- stepping
    def _offset(self, d, n: int, roll: str) -> pd.Timestamp:
        return pd.Timestamp(np.busday_offset(_np_day(d), n, roll=roll, busdaycal=self.busdaycal))

    def next_session(self, d, inclusive: bool = False) -> pd.Timestamp:
        """First session after `d` (on or after it when inclusive)."""
        return self._offset(d, 0, "forward") if inclusive else self._offset(d, 1, "backward")

    def previous_session(self, d, inclusive: bool = False) -> pd.Timestamp:
        """Last session before `d` (on or before it when inclusive)."""
        return self._offset(d, 0, "backward") if inclusive else self._offset(d, -1, "forward")

    def add_sessions(self, d, n: int) -> pd.Timestamp:
        """The n-th session after `d` (n >= 1; add_sessions(d, 1) == next_session(d)). A negative n
        counts back: the |n|-th session before `d`."""
        n = operator.index(n)
        if n == 0:
            raise ValueError("add_sessions needs n >= 1 (or n <= -1 to count back)")
        return self._offset(d, n, "backward" if n > 0 else "forward")

    def week_last_session(self, d) -> pd.Timestamp | None:
        """Last session of the C.WEEKLY_RULE week (W-FRI: Saturday-Friday) that contains `d`; None if
        that week has no session."""
        week = pd.Period(_date(d), freq=C.WEEKLY_RULE)
        start, end = _date(week.start_time), _date(week.end_time)
        last = self.previous_session(end, inclusive=True)
        return last if last >= start else None

    # ---------------------------------------------------------------- counting
    def sessions(self, start, end) -> pd.DatetimeIndex:
        """Sessions in [start, end] (empty when end < start)."""
        days = pd.bdate_range(_date(start), _date(end))
        return days[~days.isin(self._closed_index)]

    def sessions_between(self, a, b) -> int:
        """Sessions in (a, b]: trading days from `a` to `b`; 0 when b <= a."""
        a, b = _date(a), _date(b)
        return 0 if b <= a else len(self.sessions(a + pd.Timedelta(days=1), b))

    def missing_between(self, a, b) -> int:
        """Sessions strictly between `a` and `b` (bars on both dates: how many sessions have no bar);
        0 when b <= a."""
        a, b = _date(a), _date(b)
        if b - a <= pd.Timedelta(days=1):
            return 0
        return len(self.sessions(a + pd.Timedelta(days=1), b - pd.Timedelta(days=1)))

    def missing_sessions_between(self, a_arr, b_arr) -> np.ndarray:
        """missing_between for many pairs at once, e.g. consecutive bars (a_arr = index[:-1], b_arr =
        index[1:]): an int64 array of the sessions strictly between a_arr[i] and b_arr[i], 0 where
        b_arr[i] <= a_arr[i]. Vectorized with np.busday_count (a 9,000-bar history takes ~2 ms)."""
        start = _days(a_arr) + np.timedelta64(1, "D")
        count = np.busday_count(start, _days(b_arr), busdaycal=self.busdaycal)
        return np.maximum(count, 0).astype(np.int64)


# ---------------------------------------------------------------------------- sources
def rules_closed_and_early(start_year: int, end_year: int) -> tuple[set, dict]:
    """NYSE full closures (HOLIDAY_RULES + SPECIAL_CLOSURES, weekdays only) and rule early closes for
    the calendar years start_year..end_year. Early closes at C.EARLY_CLOSE_DEFAULT: July 3 when it is
    a Monday-Thursday, the day after Thanksgiving, and December 24 when it is a Monday-Thursday."""
    start_year, end_year = int(start_year), int(end_year)
    if end_year < start_year:
        raise ValueError(f"end_year {end_year} is before start_year {start_year}")
    start, end = pd.Timestamp(year=start_year, month=1, day=1), pd.Timestamp(year=end_year, month=12, day=31)

    days = set()
    for rule in HOLIDAY_RULES:
        days.update(rule.dates(start, end))
    days.update(pd.Timestamp(d) for d in SPECIAL_CLOSURES)
    closed = {d for d in map(_date, days) if start <= d <= end and d.weekday() < 5}

    early = [_date(d) + pd.Timedelta(days=1) for d in USThanksgivingDay.dates(start, end)]
    for year in range(start_year, end_year + 1):
        early += [d for d in (pd.Timestamp(year=year, month=7, day=3), pd.Timestamp(year=year, month=12, day=24))
                  if d.weekday() <= 3]               # Monday-Thursday (on a Friday it is the observed holiday)
    return closed, {d: C.EARLY_CLOSE_DEFAULT for d in sorted(early) if d not in closed}


def from_fmp_rows(rows) -> tuple[set, dict]:
    """Closed days and early closes from FMP holidays-by-exchange rows ({date, name, isClosed,
    adjCloseTime}): isClosed true -> closed; otherwise an adjCloseTime "HH:MM" between the open and
    the regular close -> early close (the later time if a date repeats). Weekend dates, rows without
    a usable date and anything that is not a list of rows are ignored."""
    closed, early = set(), {}
    if not isinstance(rows, (list, tuple)):
        return closed, early
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _row_day(row.get("date"))
        if day is None or day.weekday() >= 5:
            continue
        if _flag(row.get("isClosed")):
            closed.add(day)
            continue
        hhmm = _hhmm(row.get("adjCloseTime"))
        if hhmm is not None and _is_early_time(hhmm):
            early[day] = max(early.get(day, hhmm), hhmm)
    return closed, {d: t for d, t in sorted(early.items()) if d not in closed}


def build_calendar(fmp_rows=None, problem: str | None = None, start_year: int = int(C.HISTORY_START[:4]),
                   end_year: int | None = None) -> MarketCalendar:
    """The NYSE calendar: FMP rows united with the rules for start_year..end_year (default: the current
    New York year + C.CALENDAR_FUTURE_YEARS + 1, one year past the FMP request).

    source is "fmp+rules" when FMP gave at least one usable row, else "rules". `problem` (why FMP's
    list is missing, e.g. "restricted: ...") is kept for reporting; rows that were fetched but held
    nothing usable get a problem of their own."""
    if end_year is None:
        end_year = pd.Timestamp.now(tz=C.MARKET_TZ).year + C.CALENDAR_FUTURE_YEARS + 1
    closed, early = rules_closed_and_early(start_year, end_year)
    fmp_closed, fmp_early = from_fmp_rows(fmp_rows)
    used_fmp = bool(fmp_closed or fmp_early)
    closed |= fmp_closed
    early.update(fmp_early)                                   # FMP's time wins
    if fmp_rows is not None and not used_fmp and problem is None:
        problem = f"FMP holidays-by-exchange returned no usable {C.CALENDAR_EXCHANGE} rows"
    return MarketCalendar(closed, early, source="fmp+rules" if used_fmp else "rules", problem=problem)


def weekday_calendar() -> MarketCalendar:
    """Every weekday is a session closing at C.SESSION_CLOSE: the behaviour before the exchange calendar."""
    return MarketCalendar((), {}, source="weekdays")
