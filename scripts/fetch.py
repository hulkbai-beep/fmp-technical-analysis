"""
Data layer: fetch daily bars and reference data from FMP, then validate them.

load_all() makes about thirteen requests for an uncached stock (profile, the NYSE holiday list,
quote, earnings, two chunks each of /full and /dividend-adjusted history, the symbol's dividends, SPY,
the sector ETF, ^VIX and the economic calendar; eleven for an ETF, ten for SPY); a same-day re-run is
served from the client's cache. Missing or plan-restricted data is recorded in `skipped` / `notes`
and never filled in. A benchmark without dividend-adjusted prices falls back to split-adjusted /full
prices (price return), and says so.

The NYSE calendar (market_calendar.py: FMP's holiday list united with the exchange rules, the rules
alone when FMP's list cannot be loaded) drives validation: the partial-bar rule, the stale check and
data-gap flags count NYSE sessions. It also sets the client's cache cutoff (client.complete_from), so
a copy cached before today's bar is complete (13:15 on an early close) is not served as complete. The
stock's bars carry a "dividend" column: the split-adjusted dividend going ex on each bar
(dividends.py), 0 elsewhere and everywhere when the dividends could not be loaded.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import dividends as D
import fmp_client
import market_calendar as MC
from symbols import normalize_symbol

FULL = "historical-price-eod/full"
ADJUSTED = "historical-price-eod/dividend-adjusted"
HOLIDAYS = "holidays-by-exchange"
DIVIDENDS = "dividends"
ECONOMIC_CALENDAR = "economic-calendar"

LIST_MAX = 10  # dates kept per data-quality flag (most recent)

PRICE = ["open", "high", "low", "close"]
COLUMNS = PRICE + ["volume"] + ["adj_" + c for c in PRICE]
FULL_FIELDS = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
ADJ_FIELDS = {"adjOpen": "adj_open", "adjHigh": "adj_high", "adjLow": "adj_low", "adjClose": "adj_close"}
BENCH_FIELDS = {"adjOpen": "open", "adjHigh": "high", "adjLow": "low", "adjClose": "close", "volume": "volume"}
BASIS = {ADJUSTED: "dividend-adjusted", FULL: "split-adjusted"}   # price basis of a benchmark frame


# ---------------------------------------------------------------------------- time
def market_now() -> pd.Timestamp:
    """Current time in New York."""
    return pd.Timestamp.now(tz=C.MARKET_TZ)


def to_ny(now=None) -> pd.Timestamp:
    """`now` as a New York timestamp; naive inputs are taken as New York time, None = now."""
    if now is None:
        return market_now()
    ts = pd.Timestamp(now)
    return ts.tz_localize(C.MARKET_TZ) if ts.tzinfo is None else ts.tz_convert(C.MARKET_TZ)


def _after_close(now_ny: pd.Timestamp) -> bool:
    h, m = C.MARKET_CLOSE_BUFFER.split(":")
    return now_ny.hour * 60 + now_ny.minute >= int(h) * 60 + int(m)


def _day(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def expected_session(now_ny: pd.Timestamp, calendar=None) -> pd.Timestamp:
    """Date of the last session that should be complete at `now_ny`: calendar.expected_session with
    an exchange calendar (market_calendar.MarketCalendar), else weekdays (exchange holidays not
    modelled)."""
    if calendar is not None:
        return calendar.expected_session(now_ny)
    today = pd.Timestamp(now_ny.date())
    if today.weekday() < 5 and _after_close(now_ny):
        return today
    return today - pd.offsets.BDay(1)


def _complete_from(calendar, day) -> str:
    """"HH:MM" New York time from which a bar dated `day` counts as complete: the session's close +
    C.CLOSE_BUFFER_MINUTES with a calendar (13:15 on a 13:00 early close), else C.MARKET_CLOSE_BUFFER."""
    if calendar is None:
        return C.MARKET_CLOSE_BUFFER
    h, m = calendar.close_time(day).split(":")
    due = int(h) * 60 + int(m) + C.CLOSE_BUFFER_MINUTES
    return f"{due // 60:02d}:{due % 60:02d}"


def _count_basis(calendar) -> str:
    """What the stale check counts: NYSE sessions with an exchange calendar, else weekdays."""
    if calendar is None:
        return "weekdays (no exchange calendar)"
    if calendar.source == "weekdays":
        return "weekdays (calendar without exchange holidays)"
    return f"{C.CALENDAR_EXCHANGE} sessions"


# ---------------------------------------------------------------------------- reference data
def sector_etf_for(profile) -> str | None:
    """SPDR sector ETF for a stock's FMP sector; None for ETFs/funds, a blank or an unmapped sector."""
    if not profile or profile.get("isEtf") or profile.get("isFund"):
        return None
    return C.SECTOR_ETF.get(str(profile.get("sector") or "").strip())


def _first(data):
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, dict) and data else None


def _rows(data) -> list:
    if isinstance(data, dict):  # some proxies wrap rows in "historical"
        data = data.get("historical", [])
    return [r for r in data or [] if isinstance(r, dict) and r.get("date")]


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _earnings(data) -> list[dict]:
    """FMP earnings rows -> ascending list of {date, reported}. Only the report dates are kept; `reported`
    says whether FMP already lists actual results for that date (a report dated today after the close is not
    reported yet). No reported or estimated figure is kept."""
    by_date = {}
    for r in _rows(data):
        d = str(r["date"])[:10]
        by_date[d] = {"date": d,
                      "reported": _num(r.get("epsActual")) is not None or _num(r.get("revenueActual")) is not None}
    return [by_date[d] for d in sorted(by_date)]


def history_start(profile, override=None) -> pd.Timestamp:
    """Earliest date worth requesting: C.HISTORY_START, the IPO date or the user's override,
    whichever is latest."""
    start = pd.Timestamp(C.HISTORY_START)
    ipo = pd.to_datetime(str((profile or {}).get("ipoDate") or "")[:10], format="%Y-%m-%d", errors="coerce")
    if pd.notna(ipo):
        start = max(start, ipo)
    return max(start, pd.Timestamp(override)) if override is not None else start


def spac_like_start(bars: pd.DataFrame) -> dict | None:
    """The first C.SPAC_CHECK_BARS closes centre on $10 with most of them near it: the history probably
    begins with a pre-merger SPAC shell (FMP dates such listings from the shell's first trading day).
    Median and share-in-band rather than min/max, so a deal-announcement spike does not hide it."""
    head = bars["close"].iloc[:C.SPAC_CHECK_BARS]
    if len(head) < C.SPAC_MIN_BARS:
        return None
    median = float(head.median())
    lo, hi = C.SPAC_PRICE_BAND
    share = float(head.between(lo, hi).mean())
    m_lo, m_hi = C.SPAC_MEDIAN_BAND
    if m_lo <= median <= m_hi and share >= C.SPAC_MIN_SHARE_IN_BAND:
        return {"first_date": _day(head.index[0]), "checked_until": _day(head.index[-1]),
                "median_close": median, "share_in_band": share}
    return None


# ---------------------------------------------------------------------------- history
def _fits_cap(start, to) -> bool:
    sessions = (to - start).days * C.TRADING_DAYS_PER_YEAR / 365.25
    return sessions <= C.EOD_ROW_CAP * C.CHUNK_FIT_SHARE


def fetch_history(client, endpoint, symbol, start, end):
    """Daily rows for start..end in newest-first date chunks. Returns (rows, problem).

    FMP returns at most C.EOD_ROW_CAP rows per request, the NEWEST ones within from..to.
    Chunks span C.CHUNK_YEARS (or the whole remainder when it fits under the cap). The fetch
    stops when a chunk is empty, when a short chunk starts well after its `from` (history
    exhausted), when `from` reaches `start`, or after C.MAX_CHUNKS requests. A chunk that hits
    the cap continues from the day before its earliest row. `problem` explains missing history."""
    start, to = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    rows = []
    for _ in range(C.MAX_CHUNKS):
        frm = start if _fits_cap(start, to) else \
            max(start, to - pd.DateOffset(years=C.CHUNK_YEARS) + pd.Timedelta(days=1))
        data, problem = fmp_client.get_with_reason(
            client, endpoint, symbol=symbol, **{"from": _day(frm), "to": _day(to)})
        if problem:
            return rows, (problem if not rows else f"history up to {_day(to)} not loaded ({problem})")
        chunk = _rows(data)
        if not chunk:
            break
        rows.extend(chunk)
        earliest = min(pd.Timestamp(str(r["date"])[:10]) for r in chunk)
        if len(chunk) >= C.EOD_ROW_CAP:
            to = earliest - pd.Timedelta(days=1)
        elif frm <= start or (earliest - frm).days > C.HISTORY_EXHAUSTED_DAYS:
            break
        else:
            to = frm - pd.Timedelta(days=1)
        if to < start:
            break
    else:
        return rows, f"stopped after {C.MAX_CHUNKS} requests; history up to {_day(to)} not loaded"
    return rows, (None if rows else "no data returned")


def _frame(rows, fields) -> pd.DataFrame:
    """FMP rows -> float frame with the renamed `fields`, indexed by date (unsorted, as returned)."""
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in fields.values()},
                            index=pd.DatetimeIndex([], name="date"))
    raw = pd.DataFrame(rows)
    dates = pd.to_datetime(raw["date"].astype(str).str[:10], format="%Y-%m-%d", errors="coerce")
    out = pd.DataFrame({new: pd.to_numeric(raw[old], errors="coerce") if old in raw else np.nan
                        for old, new in fields.items()}, index=raw.index).astype("float64")
    out.index = pd.DatetimeIndex(dates, name="date")
    return out[out.index.notna()]


def _with_adj_copy(df: pd.DataFrame) -> pd.DataFrame:
    """Benchmark convention: adj_* equal the (already adjusted, or unadjustable) price columns."""
    return df.assign(**{"adj_" + c: df[c] for c in PRICE})[COLUMNS]


def stock_frame(full_rows, adj_rows) -> pd.DataFrame:
    """/full bars with the /dividend-adjusted OHLC left-joined on date."""
    full = _frame(full_rows, FULL_FIELDS)
    adj = _frame(adj_rows, ADJ_FIELDS).sort_index(kind="mergesort")
    adj = adj[~adj.index.duplicated(keep="last")]
    return full.join(adj, how="left")[COLUMNS]


# ---------------------------------------------------------------------------- validation
def validate(bars, now_ny=None, symbol=None, check_volume=True, calendar=None):
    """Sort, de-duplicate and sanity-check daily bars. Returns (clean_bars, data_quality).

    Drops: duplicate dates (keeps the last), rows with missing or non-positive OHLC (and,
    when check_volume, missing or negative volume), and today's bar while the session is not
    yet over (partial). Flags only: inconsistent OHLC, zero volume, data gaps, staleness,
    dividend-adjusted coverage and liquidity. Set check_volume=False for ^VIX (no volume).

    With an exchange calendar (market_calendar.MarketCalendar), today's bar is partial until the
    session's close + C.CLOSE_BUFFER_MINUTES (16:15, 13:15 on an early close), staleness counts
    NYSE sessions after the last bar up to calendar.expected_session (stale.business_days_behind
    keeps its name) and a data gap is at least C.GAP_MISSING_SESSIONS sessions without a bar
    between consecutive bars, so exchange holidays and closures (9/11, Hurricane Sandy) are not
    gaps. Without one: C.MARKET_CLOSE_BUFFER, weekdays, and bars more than C.GAP_CALENDAR_DAYS
    calendar days apart. stale.basis and gaps.basis say which rule applied."""
    now = to_ny(now_ny)
    tag = f"{symbol}: " if symbol else ""
    warnings = []

    df = bars.sort_index(kind="mergesort")
    dup = df.index.duplicated(keep="last")
    df = df[~dup]
    px = df[PRICE]
    bad = px.isna().any(axis=1) | (px <= 0).any(axis=1)
    if check_volume:
        bad |= df["volume"].isna() | (df["volume"] < 0)
    df = df[~bad]
    if not check_volume:
        df = df.assign(volume=df["volume"].fillna(0.0))
    if dup.sum():
        warnings.append(f"{tag}{int(dup.sum())} duplicate dates dropped")
    if bad.sum():
        what = "missing or non-positive prices" + (", or missing/negative volume" if check_volume else "")
        warnings.append(f"{tag}{int(bad.sum())} invalid rows dropped ({what})")

    partial = None
    today = pd.Timestamp(now.date())
    after_close = _after_close(now) if calendar is None else calendar.after_close(now)
    if len(df) and df.index[-1].normalize() == today and not after_close:
        last = df.iloc[-1]
        early = (f", the {calendar.close_time(today)} early close + {C.CLOSE_BUFFER_MINUTES} minutes"
                 if calendar is not None and today in calendar.early else "")
        reason = (f"bar dated {_day(today)} is partial (run at {now:%H:%M} New York time, before "
                  f"{_complete_from(calendar, today)}{early}); excluded from the analysis")
        partial = {"date": _day(today), **{c: float(last[c]) for c in PRICE + ["volume"]}, "reason": reason}
        df = df.iloc[:-1]
        warnings.append(f"{tag}{reason}")

    inconsistent = (df["high"] < df["low"]) | (df["close"] > df["high"]) | (df["close"] < df["low"])
    if inconsistent.any():
        warnings.append(f"{tag}{int(inconsistent.sum())} bars with high < low or close outside the "
                        f"high-low range (kept)")

    zero_volume = {"count": None, "dates": [], "checked": False}
    if check_volume:
        zv = df.index[df["volume"] == 0]
        zero_volume = {"count": int(len(zv)), "dates": [_day(d) for d in zv[-LIST_MAX:]], "checked": True}
        if len(zv):
            warnings.append(f"{tag}{len(zv)} zero-volume bars (latest {_day(zv[-1])})")

    gaps = _data_gaps(df, calendar)
    if gaps["count"]:
        g = gaps["items"][-1]
        if calendar is None:
            warnings.append(f"{tag}{gaps['count']} gaps longer than {C.GAP_CALENDAR_DAYS} calendar days "
                            f"(latest {g['after']} -> {g['before']})")
        else:
            warnings.append(f"{tag}{gaps['count']} data gaps: no bar on {C.CALENDAR_EXCHANGE} sessions between "
                            f"consecutive bars (latest {g['after']} -> {g['before']}, {g['missing_sessions']} "
                            f"session(s) missing)")

    expected = expected_session(now, calendar)
    stale = {"is_stale": False, "last_bar": None, "expected_session": _day(expected),
             "business_days_behind": None, "basis": _count_basis(calendar)}
    if len(df):
        last_bar = df.index[-1].normalize()
        if calendar is None:
            behind = len(pd.bdate_range(last_bar + pd.Timedelta(days=1), expected)) if expected > last_bar else 0
        else:
            behind = calendar.sessions_between(last_bar, expected)
        stale.update(last_bar=_day(last_bar), business_days_behind=behind, is_stale=behind > C.STALE_BDAYS)
        if stale["is_stale"]:
            unit = "business days" if calendar is None else stale["basis"]
            warnings.append(f"{tag}last bar {_day(last_bar)} is {behind} {unit} behind the "
                            f"expected session {_day(expected)} (stale data)")

    adj_coverage, adj_missing = None, None
    if "adj_close" in df and len(df):
        adj_coverage = float(df["adj_close"].notna().mean())
        miss = df.index[df["adj_close"].isna()]
        adj_missing = {"count": int(len(miss)), "latest": _day(miss[-1]) if len(miss) else None,
                       "dates": [_day(d) for d in miss[-LIST_MAX:]]}
        first_adj = df["adj_close"].first_valid_index()
        if first_adj is None:
            warnings.append(f"{tag}no dividend-adjusted prices: return statistics use split-adjusted "
                            f"closes (price return only)")
        elif len(miss) and miss[-1] < first_adj:
            warnings.append(f"{tag}dividend-adjusted prices start {_day(first_adj)}: returns on the "
                            f"{len(miss)} earlier bars are price returns")
        elif len(miss):
            warnings.append(f"{tag}{len(miss)} of {len(df)} bars have no dividend-adjusted prices "
                            f"(latest {_day(miss[-1])}): returns on those bars are price returns")

    liquidity = _liquidity(df) if check_volume else \
        {"avg_volume": None, "avg_dollar_volume": None, "bars": 0, "illiquid": False,
         "reason": "not checked (no meaningful volume)"}
    if liquidity["illiquid"]:
        warnings.append(f"{tag}illiquid: {liquidity['reason']}")

    dq = {
        "symbol": symbol,
        "rows": int(len(df)),
        "first_date": _day(df.index[0]) if len(df) else None,
        "last_date": _day(df.index[-1]) if len(df) else None,
        "duplicates_dropped": int(dup.sum()),
        "invalid_rows_dropped": int(bad.sum()),
        "inconsistent_ohlc": {"count": int(inconsistent.sum()),
                              "dates": [_day(d) for d in df.index[inconsistent][-LIST_MAX:]]},
        "zero_volume": zero_volume,
        "gaps": gaps,
        "partial_bar_excluded": partial,
        "stale": stale,
        "adj_coverage": adj_coverage,
        "adj_missing": adj_missing,
        "liquidity": liquidity,
        "warnings": warnings,
    }
    return df, dq


def _data_gaps(df: pd.DataFrame, calendar=None) -> dict:
    """{count, items (the latest LIST_MAX), basis}: consecutive bars with at least C.GAP_MISSING_SESSIONS
    sessions of `calendar` between them (items carry missing_sessions), or, without a calendar, more
    than C.GAP_CALENDAR_DAYS calendar days apart."""
    idx = df.index
    days = pd.Series(idx, index=idx).diff().dt.days
    if calendar is None:
        at = np.flatnonzero((days > C.GAP_CALENDAR_DAYS).to_numpy())
        missing = None
        basis = f"consecutive bars more than {C.GAP_CALENDAR_DAYS} calendar days apart (no exchange calendar)"
    else:
        missing = np.zeros(len(idx), dtype=np.int64)          # missing[i]: sessions between bar i-1 and bar i
        if len(idx) > 1:
            missing[1:] = calendar.missing_sessions_between(idx[:-1], idx[1:])
        at = np.flatnonzero(missing >= C.GAP_MISSING_SESSIONS)
        basis = (f"{C.GAP_MISSING_SESSIONS} or more {_count_basis(calendar)} without a bar between consecutive "
                 f"bars (exchange holidays and closures are not sessions, so not gaps)")
    items = []
    for i in at[-LIST_MAX:]:
        item = {"after": _day(idx[i - 1]), "before": _day(idx[i]), "calendar_days": int(days.iloc[i])}
        if missing is not None:
            item["missing_sessions"] = int(missing[i])
        items.append(item)
    return {"count": int(len(at)), "items": items, "basis": basis}


def _liquidity(df):
    """Average shares and dollar volume (split-adjusted close x volume) over the last C.LIQUIDITY_BARS bars."""
    tail = df.tail(C.LIQUIDITY_BARS)
    if tail.empty:
        return {"avg_volume": None, "avg_dollar_volume": None, "bars": 0, "illiquid": False,
                "reason": "no bars"}
    shares = float(tail["volume"].mean())
    dollars = float((tail["close"] * tail["volume"]).mean())
    why = []
    if shares < C.ILLIQUID_MIN_AVG_SHARES:
        why.append(f"average {len(tail)}-day volume {shares:,.0f} shares < {C.ILLIQUID_MIN_AVG_SHARES:,}")
    if dollars < C.ILLIQUID_MIN_AVG_DOLLARS:
        why.append(f"average {len(tail)}-day dollar volume ${dollars:,.0f} < ${C.ILLIQUID_MIN_AVG_DOLLARS:,}")
    return {"avg_volume": shares, "avg_dollar_volume": dollars, "bars": int(len(tail)),
            "illiquid": bool(why), "reason": "; ".join(why) or None}


# ---------------------------------------------------------------------------- orchestration
def _reference(client, endpoint, symbol):
    """One-row reference endpoint -> (dict|None, reason|None); empty responses count as no data."""
    data, problem = fmp_client.get_with_reason(client, endpoint, symbol=symbol)
    row = _first(data)
    return row, (None if row else problem or "no data returned")


def _benchmark(client, symbol, endpoints, now, start, calendar=None):
    """Benchmark bars (benchmark column convention) from the first of `endpoints` that returns
    valid rows -> (bars|None, data_quality|None, problems). data_quality["basis"] names the price
    basis used; `problems` says why each earlier endpoint (or every one) gave nothing. The bars
    are validated against `calendar` like the analysed symbol's."""
    problems = []
    for endpoint in endpoints:
        rows, problem = fetch_history(client, endpoint, symbol, start, pd.Timestamp(now.date()))
        if not rows:
            problems.append(problem)
            continue
        fields = BENCH_FIELDS if endpoint == ADJUSTED else FULL_FIELDS
        bars, dq = validate(_with_adj_copy(_frame(rows, fields)), now, symbol, check_volume=symbol != C.VIX,
                            calendar=calendar)
        if bars.empty:
            problems.append(f"{endpoint}: no valid rows")
            continue
        return bars, {**dq, "basis": BASIS[endpoint]}, problems
    return None, None, problems


def load_calendar(client, today) -> tuple[MC.MarketCalendar, str | None]:
    """The NYSE calendar from ONE holidays-by-exchange request (C.HISTORY_START through Dec 31 of the
    year after `today`, so the parameters change once a year and the client's C.CALENDAR_TTL_SECONDS
    cache nearly always answers) united with the exchange rules. Returns (calendar, problem): when
    FMP's list is restricted, fails or holds no usable row, the calendar comes from the rules alone
    (source "rules") and `problem` says why; it is None when FMP's list was used."""
    year = pd.Timestamp(today).year
    rows, problem = fmp_client.get_with_reason(
        client, HOLIDAYS, exchange=C.CALENDAR_EXCHANGE,
        **{"from": C.HISTORY_START, "to": f"{year + C.CALENDAR_FUTURE_YEARS}-12-31"})
    calendar = MC.build_calendar(None if problem else rows, problem,
                                 end_year=year + C.CALENDAR_FUTURE_YEARS + 1)
    return calendar, calendar.problem


def fetch_dividends(client, symbol) -> tuple[list[dict] | None, int, str | None]:
    """The symbol's ex-dividend dates from ONE dividends request -> (entries, dropped, problem).

    entries is dividends.parse_dividends' list (ascending by ex-date, amounts split-adjusted; []
    for a symbol that pays none) and dropped the rows it could not use; entries is None, with the
    reason in `problem`, when the request was restricted or failed."""
    data, problem = fmp_client.get_with_reason(client, DIVIDENDS, symbol=symbol)
    if problem:
        return None, 0, problem
    entries, dropped = D.parse_dividends(data)
    return entries, dropped, None


def macro_window(as_of) -> dict:
    """The economic-calendar window for a run whose last bar is `as_of`: C.MACRO_WINDOW_DAYS days
    starting on as_of itself (FMP silently keeps only ~90 days per request, counted back from `to`).
    FMP filters by UTC date, and a release after the as_of close is still dated as_of in UTC (until
    20:00 New York in summer, 19:00 in winter), so the window starts on as_of; macro.py drops every
    release at or before the as_of close. It does not depend on the horizon, so the other horizon's
    run is served from the cache."""
    start = pd.Timestamp(as_of).normalize()
    return {"from": _day(start), "to": _day(start + pd.Timedelta(days=C.MACRO_WINDOW_DAYS - 1))}


def _has_country_rows(data, country=C.MACRO_COUNTRY) -> bool:
    """True when `data` is a list holding at least one row of `country` (macro.py's test: any case,
    surrounding spaces ignored)."""
    return isinstance(data, list) and any(
        isinstance(r, dict) and str(r.get("country") or "").strip().upper() == country for r in data)


def fetch_macro(client, as_of) -> tuple[list | None, dict, str | None]:
    """ONE economic-calendar request over macro_window(as_of) -> (rows, window, problem): FMP's rows
    as returned (every country; macro.py keeps C.MACRO_COUNTRY), or None with the reason in
    `problem` when the request was restricted or failed, or returned no C.MACRO_COUNTRY row. An
    89-day window always lists US releases, so an empty answer (FMP answers [] without an error for
    a range it does not serve) is missing data, not a window without events."""
    window = macro_window(as_of)
    data, problem = fmp_client.get_with_reason(client, ECONOMIC_CALENDAR, **window)
    if problem is None and not _has_country_rows(data):
        problem = (f"FMP economic-calendar returned no {C.MACRO_COUNTRY} rows for {window['from']} to "
                   f"{window['to']}")
    return (None if problem else data), window, problem


def load_all(client, ticker, now=None, start=None) -> dict:
    """Fetch and validate everything the analysis needs for `ticker` (see CONTRACT "fetch.py").
    `start` (a date) cuts the history requested for the symbol, e.g. to drop a pre-merger SPAC shell.
    Raises ValueError when the symbol has no usable price history.

    Requests, in order: profile, holidays-by-exchange, quote, earnings (stocks), /full and
    /dividend-adjusted history, dividends, the benchmarks (SPY unless it is the symbol, the sector
    ETF for a stock), ^VIX and the economic calendar. The calendar comes before every request the
    client caches by the session rule, because it sets the client's cutoff (client.complete_from).
    Besides the bars and benchmarks it returns `calendar` (the NYSE MarketCalendar), `dividends`
    (parse_dividends entries; None when they could not be loaded), `macro_rows` (the economic-calendar
    rows; None on failure or without a US row) and `macro_window`."""
    now = to_ny(now)
    symbol = normalize_symbol(ticker)
    today = pd.Timestamp(now.date())
    calls_before = client.stats["network_calls"]
    notes, skipped = [], {}

    def unavailable(module, reason):
        skipped[module] = reason
        notes.append(f"{module}: {reason}")

    profile, why = _reference(client, "profile", symbol)
    if profile is None:
        unavailable("profile", why)
    is_etf = bool(profile and (profile.get("isEtf") or profile.get("isFund")))
    if profile and profile.get("isActivelyTrading") is False:
        notes.append(f"profile reports {symbol} as not actively trading")

    calendar, cal_problem = load_calendar(client, today)
    if cal_problem:
        notes.append(f"exchange calendar: {cal_problem}; {C.CALENDAR_EXCHANGE} holidays and early closes come "
                     f"from the built-in exchange rules")
    # the client's cache keeps a session endpoint's copy (bars, quote, earnings, dividends) only until the bar
    # it holds is complete by validate()'s rule: the session's close + C.CLOSE_BUFFER_MINUTES (13:15 on an early
    # close). With the fixed 16:15 a copy cached at noon would pass for the complete bar from 13:15 to 16:15.
    client.complete_from = lambda day: _complete_from(calendar, day)

    quote, why = _reference(client, "quote", symbol)
    if quote is None:
        unavailable("quote", why)

    earnings = None
    if is_etf:
        skipped["earnings"] = "ETF/fund: earnings module not applicable"
    else:
        data, why = fmp_client.get_with_reason(client, "earnings", symbol=symbol)
        earnings = _earnings(data) or None
        if earnings is None:
            unavailable("earnings", why or "no data returned")

    override = start
    start = history_start(profile, override)
    full_rows, problem = fetch_history(client, FULL, symbol, start, today)
    if not full_rows:
        raise ValueError(f"no price data for {symbol}" + (f" ({problem})" if problem else ""))
    if problem:
        notes.append(f"price history: {problem}")
    first_full = pd.Timestamp(min(str(r["date"])[:10] for r in full_rows))
    adj_rows, adj_problem = fetch_history(client, ADJUSTED, symbol, max(start, first_full), today)
    if adj_problem:
        notes.append(f"dividend-adjusted prices: {adj_problem}")
    bars, dq = validate(stock_frame(full_rows, adj_rows), now, symbol, calendar=calendar)
    if bars.empty:
        raise ValueError(f"no price data for {symbol} (no valid rows after validation)")
    notes.extend(dq["warnings"])
    dq["calendar"] = calendar.summary()
    dq["history_start_override"] = _day(pd.Timestamp(override)) if override is not None else None
    dq["spac_like_start"] = spac_like_start(bars) if override is None else None
    if dq["spac_like_start"]:
        sp = dq["spac_like_start"]
        notes.append(f"history starts {sp['first_date']} with closes centred on $10 (median "
                     f"{sp['median_close']:.2f} through {sp['checked_until']}, {sp['share_in_band']:.0%} "
                     f"between {C.SPAC_PRICE_BAND[0]:.0f} and {C.SPAC_PRICE_BAND[1]:.0f}): this looks like a "
                     f"pre-merger SPAC shell, which long-history statistics include; re-run with "
                     f"--history-start set to the merger date to exclude it")
        dq["warnings"].append(notes[-1])

    # ex-dividend dates as a bar column (0 on other bars, and on every bar when they could not be loaded);
    # an ex-date without a bar goes to the bar of its first NYSE session (a multi-day closure included)
    divs, dropped, why = fetch_dividends(client, symbol)
    if divs is None:
        unavailable("dividends", why)
    bars = bars.assign(dividend=D.dividend_column(bars.index, divs or [], calendar))
    dq["dividends"] = {"ex_dates": None if divs is None else len(divs),
                       "rows_dropped": None if divs is None else dropped, "problem": why}

    # a plan without /dividend-adjusted: benchmarks go straight to /full (no wasted requests)
    adj_restricted = not adj_rows and str(adj_problem or "").startswith("restricted")
    bench_endpoints = (FULL,) if adj_restricted else (ADJUSTED, FULL)
    bench_start = today - pd.DateOffset(years=C.BENCHMARK_YEARS)
    benchmarks, basis = {}, {}

    def benchmark(module, sym):
        frame, bdq, problems = _benchmark(client, sym, bench_endpoints, now, bench_start, calendar)
        if frame is None:
            unavailable(module, f"{sym} data unavailable ({'; '.join(problems)})")
            return None
        benchmarks[sym], basis[sym] = bdq, bdq["basis"]
        if bdq["basis"] == BASIS[FULL]:
            why = "; ".join(problems) or "restricted on this plan"
            mixed = (f"; {symbol}'s own returns are dividend-adjusted, so relative strength vs {sym} "
                     f"is overstated by roughly {sym}'s dividend yield") if dq["adj_coverage"] else ""
            notes.append(f"{sym}: no dividend-adjusted prices ({why}); split-adjusted /full prices used, so "
                         f"its returns and SMA readings are price-only{mixed}")
        return frame

    if symbol == C.SPY:
        # SPY's own bars, on the regime's split-adjusted closes, so the two sections cannot disagree
        spy, basis[C.SPY] = bars, "split-adjusted (the analysed bars)"
    else:
        spy = benchmark("spy", C.SPY)

    sector, sector_etf = None, None
    if is_etf:
        skipped["sector"] = "ETF/fund: sector module not applicable"
    elif profile is None:
        skipped["sector"] = "profile unavailable, sector unknown"
    else:
        etf = sector_etf_for(profile)
        if etf is None:
            unavailable("sector", f"no sector ETF mapped for sector {profile.get('sector') or '(blank)'!r}")
        else:
            sector = benchmark("sector", etf)
            sector_etf = etf if sector is not None else None

    vix, vix_dq, problems = _benchmark(client, C.VIX, (FULL,), now, bench_start, calendar)
    if vix is None:
        unavailable("vix", f"{C.VIX} data unavailable ({'; '.join(problems)})")
    else:
        benchmarks[C.VIX] = vix_dq

    for bdq in filter(None, benchmarks.values()):
        # a benchmark's partial bar needs no extra note when the analysed symbol's was dropped too
        same = dq["partial_bar_excluded"] and bdq["partial_bar_excluded"]
        notes.extend(w for w in bdq["warnings"] if not (same and bdq["partial_bar_excluded"]["reason"] in w))
    dq["benchmarks"] = benchmarks
    dq["benchmark_basis"] = basis

    # scheduled US macro events after the last bar's close (the window starts on its date and does not
    # depend on the horizon)
    macro_rows, window, why = fetch_macro(client, bars.index[-1])
    if macro_rows is None:
        unavailable("macro_events", why)

    return {
        "bars": bars, "spy": spy, "sector": sector, "sector_etf": sector_etf, "vix": vix,
        "profile": profile, "quote": quote, "earnings": earnings, "is_etf": is_etf,
        "calendar": calendar, "dividends": divs, "macro_rows": macro_rows, "macro_window": window,
        "data_quality": dq, "notes": notes, "skipped": skipped,
        "calls": client.stats["network_calls"] - calls_before,
        "connection": client.describe(),
        "fetched_at": now.isoformat(timespec="seconds"),
    }
