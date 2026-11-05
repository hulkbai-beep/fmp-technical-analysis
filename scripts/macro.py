"""
Macro events (`macro_events`): scheduled US economic releases between the last bar and the end of
the economic-calendar window, from FMP's economic calendar.

FMP economic-calendar rows ({date, country, event, impact, previous, estimate, actual, ...}) arrive
newest first, with times in UTC ("2026-11-25 13:30:00" is 08:30 New York). The module

- keeps the C.MACRO_COUNTRY rows and converts their times to New York time. A row at exactly
  00:00:00 UTC is a date-only listing (holidays, elections, the Jackson Hole symposium): it keeps
  its own date and gets no time, since converting it would move it to the evening before;
- names each row by its event name without the "(period)" suffix and classifies it by that name,
  not by FMP's impact label, which changes between releases of one event: C.MACRO_EXCLUDE first
  (weekly jobless claims), then the anchored regexes of C.MACRO_GROUPS (tier 1: FOMC decision,
  CPI, jobs report, PCE, PPI, GDP, retail sales; tier 2: FOMC minutes, Fed policy events, ISM,
  JOLTS, consumer sentiment), then tier 3 ("other", labelled with its own name) for any other
  event FMP rates "High";
- merges the rows of one tier 1-2 group on one New York date into one event at the earliest row's
  time (the CPI MoM / YoY / core rows -> one "cpi"; the FOMC decision at 14:00 and the press
  conference at 14:30 -> one "fomc"); tier 3 is one event per name and date;
- maps each event to the session it can first move (`timing`): pre_open (before C.SESSION_OPEN),
  intraday (before that day's close, 13:00 on an early close) and date_only (no time given) ->
  that session; after_close and non_session (weekend or exchange holiday) -> the next session;
- keeps the events whose session comes after as_of (anything before the as_of close is already in
  the last bar) and counts NYSE sessions to them: trading_days_to = sessions in (as_of, session].

Events are context: they never change the scorecard, and the module gives dates and times only,
never an expected outcome. See references/methodology.md.
"""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd

import config as C
import market_calendar as MC
import regime as R
from regime import iso

SOURCE = f"FMP economic-calendar, country {C.MACRO_COUNTRY}; FMP times are UTC, shown in New York time"
IMPACTS = ("None", "Low", "Medium", "High")          # FMP's impact labels, lowest first
OTHER = "other"                                      # id of tier-3 events
TIER1_GROUPS = tuple(gid for gid, _, tier, _ in C.MACRO_GROUPS if tier == 1)

_SUFFIX = re.compile(r"\s*\([^()]*\)$")
_GROUPS = tuple((gid, label, tier, tuple(re.compile(p, re.IGNORECASE) for p in patterns))
                for gid, label, tier, patterns in C.MACRO_GROUPS)
_EXCLUDE = tuple(re.compile(p, re.IGNORECASE) for p in C.MACRO_EXCLUDE)
_HORIZON_KEYS = ("id", "label", "tier", "date", "trading_days_to")


# ---------------------------------------------------------------------------- small helpers
def _day(d) -> pd.Timestamp:
    """`d` as a tz-naive midnight Timestamp (a tz-aware input keeps its own wall-clock date)."""
    ts = pd.Timestamp(d)
    if pd.isna(ts):
        raise ValueError(f"not a date: {d!r}")
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def _iso_or_none(d) -> str | None:
    try:
        return iso(_day(d))
    except (ValueError, TypeError, OverflowError):
        return None


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _impact(value) -> str | None:
    """FMP's impact as one of IMPACTS (any case); anything else -> None."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return next((label for label in IMPACTS if label.lower() == value), None)


def _highest(impacts) -> str | None:
    known = [i for i in impacts if i in IMPACTS]
    return max(known, key=IMPACTS.index) if known else None


def _utc(value) -> pd.Timestamp | None:
    """FMP's `date` ("YYYY-MM-DD HH:MM:SS", UTC) as a tz-aware UTC Timestamp; None if unusable."""
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    elif not isinstance(value, (dt.date, np.datetime64)):
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if pd.isna(ts):
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


# ---------------------------------------------------------------------------- names
def clean_name(event) -> str:
    """The event name without its trailing "(period)" suffix, whitespace collapsed:
    "Non Farm Payrolls (Oct)" -> "Non Farm Payrolls", "Initial Jobless Claims (Oct/31)" ->
    "Initial Jobless Claims". Anything that is not a string -> ""."""
    if not isinstance(event, str):
        return ""
    return _SUFFIX.sub("", " ".join(event.split()))


def is_excluded(name) -> bool:
    """True when the event name (with or without its suffix) matches C.MACRO_EXCLUDE."""
    name = clean_name(name)
    return bool(name) and any(p.search(name) for p in _EXCLUDE)


def classify(name, impact=None) -> tuple[str, str, int] | None:
    """(group id, label, tier) of an event name, with or without its "(period)" suffix.

    None for a C.MACRO_EXCLUDE name; else the first C.MACRO_GROUPS group (tiers 1-2) with a matching
    regex, whatever FMP's impact label; else ("other", the clean name, 3) when FMP rates the row
    "High"; else None."""
    name = clean_name(name)
    if not name or any(p.search(name) for p in _EXCLUDE):
        return None
    for gid, label, tier, patterns in _GROUPS:
        if any(p.search(name) for p in patterns):
            return gid, label, tier
    if _impact(impact) == "High":
        return OTHER, name, 3
    return None


# ---------------------------------------------------------------------------- rows
def _prepare(rows, country) -> tuple[list[dict], int]:
    """prepare_rows and the number of rows skipped as unusable."""
    if not isinstance(rows, (list, tuple)):
        return [], 0
    want = None if country is None else str(country).strip().upper()
    out, skipped = [], 0
    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        if want is not None and str(row.get("country") or "").strip().upper() != want:
            continue
        event = " ".join(row["event"].split()) if isinstance(row.get("event"), str) else ""
        name, utc = clean_name(event), _utc(row.get("date"))
        if not name or utc is None:
            skipped += 1
            continue
        date_only = utc == utc.normalize()
        if date_only:                                    # 00:00:00 UTC: a date without a time
            day = utc.tz_localize(None).normalize()
            ts_ny = day.tz_localize(C.MARKET_TZ)
        else:
            ts_ny = utc.tz_convert(C.MARKET_TZ)
            day = ts_ny.tz_localize(None).normalize()
        out.append({"event": event, "name": name, "impact": _impact(row.get("impact")),
                    "ts_utc": utc, "ts_ny": ts_ny, "date": day,
                    "time_ny": None if date_only else ts_ny.strftime("%H:%M"), "date_only": date_only})
    out.sort(key=lambda r: (r["ts_ny"], r["name"]))
    return out, skipped


def prepare_rows(rows, country: str | None = C.MACRO_COUNTRY) -> list[dict]:
    """FMP economic-calendar rows of `country` (None: every country) in time order, each as
    {event (original name), name (clean name), impact (one of IMPACTS or None), ts_utc, ts_ny
    (tz-aware New York; midnight New York for a date-only row), date (New York date, tz-naive),
    time_ny ("HH:MM", None for a date-only row), date_only}.

    A row at exactly 00:00:00 UTC (or with a date only) is date-only: it keeps its own date. Rows
    that are not dicts or lack a usable date or event name are skipped (build_macro_events counts
    them); input that is not a list gives []."""
    return _prepare(rows, country)[0]


# ---------------------------------------------------------------------------- timing and events
def session_timing(day, time_ny: str | None, calendar) -> tuple[str, pd.Timestamp]:
    """(timing, session) for an event on New York date `day` at "HH:MM" `time_ny` (None: no time),
    session being the first session the event can move:
    non_session (weekend or holiday) -> the next session; date_only -> that day;
    pre_open (before C.SESSION_OPEN) and intraday (before the day's close) -> that day;
    after_close (at or after the close, e.g. 13:00 on an early close) -> the next session."""
    day = _day(day)
    if not calendar.is_session(day):
        return "non_session", calendar.next_session(day)
    if time_ny is None:
        return "date_only", day
    t = _minutes(time_ny)
    if t < _minutes(C.SESSION_OPEN):
        return "pre_open", day
    if t < _minutes(calendar.close_time(day)):
        return "intraday", day
    return "after_close", calendar.next_session(day)


def _group(rows, calendar, as_of) -> tuple[list[dict], dict]:
    """(events whose session is after as_of, sorted; {"excluded", "unclassified"} row counts after as_of)."""
    calendar = MC.weekday_calendar() if calendar is None else calendar
    as_of = _day(as_of)
    counts = {"excluded": 0, "unclassified": 0}
    buckets: dict[tuple, dict] = {}
    for row in rows:
        cls = classify(row["name"], row["impact"])
        if cls is None:
            if session_timing(row["date"], row["time_ny"], calendar)[1] > as_of:
                counts["excluded" if is_excluded(row["name"]) else "unclassified"] += 1
            continue
        gid, label, tier = cls                   # tier 3: label = the clean name, so one event per name
        buckets.setdefault((gid, label, row["date"]), {"tier": tier, "rows": []})["rows"].append(row)

    events = []
    for (gid, label, day), bucket in buckets.items():
        members = bucket["rows"]
        times = [r["time_ny"] for r in members if r["time_ny"] is not None]
        time_ny = min(times) if times else None
        timing, session = session_timing(day, time_ny, calendar)
        if session <= as_of:                     # already in the as_of bar (or earlier)
            continue
        events.append({
            "id": gid, "label": label, "tier": bucket["tier"], "date": iso(day), "time_ny": time_ny,
            "timing": timing, "session": iso(session),
            "trading_days_to": calendar.sessions_between(as_of, session),
            "calendar_days": int((day - as_of).days),
            "releases": sorted({r["event"] for r in members}),
            "impact": _highest(r["impact"] for r in members),
        })
    events.sort(key=lambda e: (e["session"], e["date"], e["time_ny"] or "", e["tier"], e["id"], e["label"]))
    return events, counts


def group_events(rows, calendar, as_of) -> list[dict]:
    """Events from prepare_rows output whose session comes after as_of, sorted by session, then
    date, time and tier: {id, label, tier, date, time_ny, timing, session, trading_days_to,
    calendar_days, releases, impact}.

    Tier 1-2 rows of one group on one New York date form one event: time_ny = the earliest row's
    time, releases = the original names (sorted, unique), impact = the highest FMP label. Tier 3
    is one event per clean name and date. calendar None -> market_calendar.weekday_calendar()."""
    return _group(rows, calendar, as_of)[0]


# ---------------------------------------------------------------------------- module
def _window(w) -> dict:
    w = w if isinstance(w, dict) else {}
    return {"from": _iso_or_none(w.get("from")), "to": _iso_or_none(w.get("to"))}


def _span(window: dict) -> str:
    return f" ({window['from']} to {window['to']})" if window["from"] and window["to"] else ""


def _coverage(rows, calendar, as_of, hz, window) -> tuple[dict, str | None]:
    """How far the listing is complete. FMP lists FOMC dates (C.MACRO_FAR_AHEAD_GROUPS) months ahead
    but every other release only ~6-8 weeks ahead, so the last other US row (`listed_through`), or
    the window's end if earlier, bounds the complete part. Horizons ending later count only listed
    events: complete_stat_horizons says which, and a note names them."""
    horizons = sorted({*hz["stat_horizons"], hz["primary"]})
    ends = {h: calendar.add_sessions(as_of, h) for h in horizons}
    regular = [r["date"] for r in rows
               if (classify(r["name"], r["impact"]) or (None,))[0] not in C.MACRO_FAR_AHEAD_GROUPS]
    listed = max(regular) if regular else None
    out = {"listed_through": None if listed is None else iso(listed),
           "horizon_end": iso(ends[hz["primary"]]), "complete": None,
           "complete_stat_horizons": {str(h): None for h in hz["stat_horizons"]}}
    if listed is None:
        return out, None
    window_to = None if window["to"] is None else _day(window["to"])
    through = listed if window_to is None else min(listed, window_to)
    out["complete"] = bool(ends[hz["primary"]] <= through)
    out["complete_stat_horizons"] = {str(h): bool(ends[h] <= through) for h in hz["stat_horizons"]}
    late = [h for h in horizons if ends[h] > through]
    if not late:
        return out, None
    if window_to is not None and window_to <= listed:
        why = (f"the economic-calendar window ends on {iso(window_to)} (FMP returns at most ~90 days per "
               f"request), so later events are missing")
    else:
        why = (f"FMP lists {C.MACRO_COUNTRY} releases other than FOMC dates only through {iso(listed)}, so later "
               f"ones are missing")
    spans = " and ".join(f"{h} sessions (to {iso(ends[h])})" for h in late)
    return out, f"{why}; the event list and counts for {spans} cover only the part through {iso(through)}"


def build_macro_events(ctx) -> dict:
    """macro_events: US macro events after as_of in the economic-calendar window, tiers 1-3.

    ctx keys: macro_rows (FMP economic-calendar rows; None when the fetch failed), macro_window
    ({"from", "to"}), calendar (market_calendar.MarketCalendar; None -> weekdays, with a note),
    skipped (the fetch's reason under "macro_events"), hz (or horizon) and as_of.

    within_horizon lists every event within the primary horizon (trading_days_to <= hz["primary"]);
    within_stat_horizons counts the tier-1 events within each stat horizon; next holds the first
    event of each tier-1 group (null when the window has none); counts = events per tier plus the
    US rows after as_of that were excluded (C.MACRO_EXCLUDE) or unclassified; coverage says whether
    FMP's listing reaches the end of each horizon. Context only: nothing here enters the scorecard."""
    hz = ctx.get("hz") or C.HORIZONS[ctx.get("horizon", C.DEFAULT_HORIZON)]
    window = _window(ctx.get("macro_window"))
    out = {"available": True, "source": SOURCE, "window": window, "horizon_days": hz["primary"]}
    rows = ctx.get("macro_rows")
    if not isinstance(rows, (list, tuple)):
        default = ("economic calendar unavailable" if rows is None else
                   f"economic calendar returned {type(rows).__name__}, not a list of rows")
        return {**out, "available": False,
                "reason": R.skip_reason(ctx, ("macro_events", "economic-calendar", "macro"), default),
                "events": None, "within_horizon": None, "within_stat_horizons": None, "next": None,
                "counts": None, "coverage": None, "notes": []}

    notes: list[str] = []
    calendar = ctx.get("calendar")
    if calendar is None:
        calendar = MC.weekday_calendar()
        notes.append("no exchange calendar: sessions are weekdays, exchange holidays not excluded")
    as_of = _day(R.as_of(ctx))
    prepared, skipped = _prepare(rows, C.MACRO_COUNTRY)
    if not rows:
        notes.append(f"FMP economic-calendar returned no events for the window{_span(window)}")
    elif not prepared:
        notes.append(f"FMP economic-calendar returned no usable {C.MACRO_COUNTRY} events for the "
                     f"window{_span(window)}")
    if skipped:
        notes.append(f"{skipped} economic-calendar row(s) without a usable date or event name skipped")

    events, row_counts = _group(prepared, calendar, as_of)
    undated = sum(e["time_ny"] is None for e in events)
    if undated:
        notes.append(f"{undated} event(s) have a date but no time in FMP (listed at 00:00 UTC): time_ny is "
                     f"null and the session is that day, or the next session after a weekend or holiday")
    coverage, note = _coverage(prepared, calendar, as_of, hz, window)
    if note:
        notes.append(note)

    tiers = [e["tier"] for e in events]
    first = {}
    for e in events:
        first.setdefault(e["id"], e)
    return {
        **out,
        "events": events,
        "within_horizon": [{k: e[k] for k in _HORIZON_KEYS} for e in events
                           if e["trading_days_to"] <= hz["primary"]],
        "within_stat_horizons": {str(h): sum(e["tier"] == 1 and e["trading_days_to"] <= h for e in events)
                                 for h in hz["stat_horizons"]},
        "next": {g: ({**first[g], "releases": list(first[g]["releases"])} if g in first else None)
                 for g in TIER1_GROUPS},
        "counts": {"tier1": tiers.count(1), "tier2": tiers.count(2), "tier3": tiers.count(3), **row_counts},
        "coverage": coverage,
        "notes": notes,
    }
