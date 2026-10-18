"""
Data layer: fetch daily bars and reference data from FMP, then validate them.

load_all() makes about ten requests for an uncached stock (profile, quote, earnings, two
chunks each of /full and /dividend-adjusted history, SPY, the sector ETF and ^VIX); a
same-day re-run is served from the client's cache. Missing or plan-restricted data is
recorded in `skipped` / `notes` and never filled in. A benchmark without dividend-adjusted
prices falls back to split-adjusted /full prices (price return), and says so.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import fmp_client

FULL = "historical-price-eod/full"
ADJUSTED = "historical-price-eod/dividend-adjusted"

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


def expected_session(now_ny: pd.Timestamp) -> pd.Timestamp:
    """Date of the last session that should be complete at `now_ny` (exchange holidays not modelled)."""
    today = pd.Timestamp(now_ny.date())
    if today.weekday() < 5 and _after_close(now_ny):
        return today
    return today - pd.offsets.BDay(1)


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
    """FMP earnings rows -> ascending list of {date, eps_actual, eps_estimated, revenue_actual, revenue_estimated}."""
    by_date = {}
    for r in _rows(data):
        d = str(r["date"])[:10]
        by_date[d] = {
            "date": d,
            "eps_actual": _num(r.get("epsActual")),
            "eps_estimated": _num(r.get("epsEstimated")),
            "revenue_actual": _num(r.get("revenueActual")),
            "revenue_estimated": _num(r.get("revenueEstimated")),
        }
    return [by_date[d] for d in sorted(by_date)]


def history_start(profile) -> pd.Timestamp:
    """Earliest date worth requesting: C.HISTORY_START, or the IPO date if later."""
    start = pd.Timestamp(C.HISTORY_START)
    ipo = pd.to_datetime(str((profile or {}).get("ipoDate") or "")[:10], format="%Y-%m-%d", errors="coerce")
    return max(start, ipo) if pd.notna(ipo) else start


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
def validate(bars, now_ny=None, symbol=None, check_volume=True):
    """Sort, de-duplicate and sanity-check daily bars. Returns (clean_bars, data_quality).

    Drops: duplicate dates (keeps the last), rows with missing or non-positive OHLC (and,
    when check_volume, missing or negative volume), and today's bar while the session is not
    yet over (partial). Flags only: inconsistent OHLC, zero volume, calendar gaps, staleness,
    dividend-adjusted coverage and liquidity. Set check_volume=False for ^VIX (no volume)."""
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
    if len(df) and df.index[-1].normalize() == today and not _after_close(now):
        last = df.iloc[-1]
        reason = (f"bar dated {_day(today)} is partial (run at {now:%H:%M} New York time, before "
                  f"{C.MARKET_CLOSE_BUFFER}); excluded from the analysis")
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

    steps = pd.Series(df.index, index=df.index).diff().dt.days
    gap_at = steps.index[steps > C.GAP_CALENDAR_DAYS]
    gap_items = [{"after": _day(df.index[df.index.get_loc(d) - 1]), "before": _day(d),
                  "calendar_days": int(steps[d])} for d in gap_at[-LIST_MAX:]]
    if len(gap_at):
        g = gap_items[-1]
        warnings.append(f"{tag}{len(gap_at)} gaps longer than {C.GAP_CALENDAR_DAYS} calendar days "
                        f"(latest {g['after']} -> {g['before']})")

    expected = expected_session(now)
    stale = {"is_stale": False, "last_bar": None, "expected_session": _day(expected), "business_days_behind": None}
    if len(df):
        last_bar = df.index[-1].normalize()
        behind = len(pd.bdate_range(last_bar + pd.Timedelta(days=1), expected)) if expected > last_bar else 0
        stale.update(last_bar=_day(last_bar), business_days_behind=behind, is_stale=behind > C.STALE_BDAYS)
        if stale["is_stale"]:
            warnings.append(f"{tag}last bar {_day(last_bar)} is {behind} business days behind the "
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
        "gaps": {"count": int(len(gap_at)), "items": gap_items},
        "partial_bar_excluded": partial,
        "stale": stale,
        "adj_coverage": adj_coverage,
        "adj_missing": adj_missing,
        "liquidity": liquidity,
        "warnings": warnings,
    }
    return df, dq


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


def _benchmark(client, symbol, endpoints, now, start):
    """Benchmark bars (benchmark column convention) from the first of `endpoints` that returns
    valid rows -> (bars|None, data_quality|None, problems). data_quality["basis"] names the price
    basis used; `problems` says why each earlier endpoint (or every one) gave nothing."""
    problems = []
    for endpoint in endpoints:
        rows, problem = fetch_history(client, endpoint, symbol, start, pd.Timestamp(now.date()))
        if not rows:
            problems.append(problem)
            continue
        fields = BENCH_FIELDS if endpoint == ADJUSTED else FULL_FIELDS
        bars, dq = validate(_with_adj_copy(_frame(rows, fields)), now, symbol, check_volume=symbol != C.VIX)
        if bars.empty:
            problems.append(f"{endpoint}: no valid rows")
            continue
        return bars, {**dq, "basis": BASIS[endpoint]}, problems
    return None, None, problems


def load_all(client, ticker, now=None) -> dict:
    """Fetch and validate everything the analysis needs for `ticker` (see CONTRACT "fetch.py").
    Raises ValueError when the symbol has no usable price history."""
    now = to_ny(now)
    symbol = ticker.strip().upper()
    today = pd.Timestamp(now.date())
    calls_before = client.stats["network_calls"]
    notes, skipped = [], {}

    def unavailable(module, reason):
        skipped[module] = reason
        notes.append(f"{module}: {reason}")

    profile, why = _reference(client, "profile", symbol)
    if profile is None:
        unavailable("profile", why)
    quote, why = _reference(client, "quote", symbol)
    if quote is None:
        unavailable("quote", why)
    is_etf = bool(profile and (profile.get("isEtf") or profile.get("isFund")))
    if profile and profile.get("isActivelyTrading") is False:
        notes.append(f"profile reports {symbol} as not actively trading")

    earnings = None
    if is_etf:
        skipped["earnings"] = "ETF/fund: earnings module not applicable"
    else:
        data, why = fmp_client.get_with_reason(client, "earnings", symbol=symbol)
        earnings = _earnings(data) or None
        if earnings is None:
            unavailable("earnings", why or "no data returned")

    start = history_start(profile)
    full_rows, problem = fetch_history(client, FULL, symbol, start, today)
    if not full_rows:
        raise ValueError(f"no price data for {symbol}" + (f" ({problem})" if problem else ""))
    if problem:
        notes.append(f"price history: {problem}")
    first_full = pd.Timestamp(min(str(r["date"])[:10] for r in full_rows))
    adj_rows, adj_problem = fetch_history(client, ADJUSTED, symbol, max(start, first_full), today)
    if adj_problem:
        notes.append(f"dividend-adjusted prices: {adj_problem}")
    bars, dq = validate(stock_frame(full_rows, adj_rows), now, symbol)
    if bars.empty:
        raise ValueError(f"no price data for {symbol} (no valid rows after validation)")
    notes.extend(dq["warnings"])

    # a plan without /dividend-adjusted: benchmarks go straight to /full (no wasted requests)
    adj_restricted = not adj_rows and str(adj_problem or "").startswith("restricted")
    bench_endpoints = (FULL,) if adj_restricted else (ADJUSTED, FULL)
    bench_start = today - pd.DateOffset(years=C.BENCHMARK_YEARS)
    benchmarks, basis = {}, {}

    def benchmark(module, sym):
        frame, bdq, problems = _benchmark(client, sym, bench_endpoints, now, bench_start)
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

    vix, vix_dq, problems = _benchmark(client, C.VIX, (FULL,), now, bench_start)
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

    return {
        "bars": bars, "spy": spy, "sector": sector, "sector_etf": sector_etf, "vix": vix,
        "profile": profile, "quote": quote, "earnings": earnings, "is_etf": is_etf,
        "data_quality": dq, "notes": notes, "skipped": skipped,
        "calls": client.stats["network_calls"] - calls_before,
        "connection": client.describe(),
        "fetched_at": now.isoformat(timespec="seconds"),
    }
