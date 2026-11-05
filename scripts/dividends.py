"""
Ex-dividend dates (FMP dividends) and the `dividends` module.

FMP's `dividends` endpoint lists a symbol's ex-dividend dates, including the ones already announced
for the coming weeks. `date` is the ex-dividend date and `adjDividend` the amount adjusted for later
splits, the same basis as the split-adjusted /full bars (NVDA's $0.04 of 2021 is 0.004 after the 10:1
split of 2024). The /full bars are not dividend-adjusted, so on an ex-dividend date the price opens
lower by about the dividend for mechanical reasons. That drop is not a gap.

- parse_dividends(rows): FMP rows -> one entry per ex-date, ascending.
- match_to_bars(index, dividends, calendar) / dividend_column(index, dividends, calendar): the
  amount on the bar each ex-date lands on (with an exchange calendar, the bar of the first session
  on or after the ex-date). dividend_column is the bars["dividend"] column. levels.unfilled_gaps,
  signal_stats.events and modules._vwap_anchors read it through bar_dividends / prev_close_net: on
  an ex-dividend bar, a gap down is measured from the prior bar minus the dividend. A dividend can
  only explain a drop, so gaps up are measured from the prior bar as it traded. Without the column,
  every result is the same as before.
- build_dividends(ctx): the `dividends` metrics block. It gives the last and the next ex-dividend
  date, the next one's size against the close and the ATR, and whether it falls inside the
  horizon. It is context only and never changes the scorecard. There is no dividend yield: the
  skill carries no fundamentals.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

import config as C
import regime as R

SOURCE = "FMP dividends: ex-dividend dates; adjDividend is split-adjusted, the same basis as the /full bars"
GAP_BASIS = ("on an ex-dividend bar, a gap down is measured from the prior bar minus the dividend, so a drop the "
             "dividend explains is no gap (unfilled-gap levels, the gap_down_atr signal and the largest_gap_6m "
             "VWAP anchor); a dividend cannot explain a rise, so gaps up are measured from the prior bar as it "
             "traded")

_DATE_FIELDS = {"declarationDate": "declaration_date", "recordDate": "record_date", "paymentDate": "payment_date"}
NOTE_DATES_MAX = 5   # dates named in a note (the most recent)


# ---------------------------------------------------------------------------- small helpers
def _day(value) -> pd.Timestamp | None:
    """'YYYY-MM-DD' (possibly followed by a time), a date or a Timestamp as a tz-naive midnight
    Timestamp; anything else -> None."""
    if isinstance(value, str):
        ts = pd.to_datetime(value.strip()[:10], format="%Y-%m-%d", errors="coerce")
    elif isinstance(value, (dt.date, np.datetime64)):
        ts = pd.Timestamp(value)
    else:
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def _amount(value) -> float | None:
    """A finite float, else None (booleans are not amounts)."""
    if value is None or isinstance(value, (bool, np.bool_)):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _label(value) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _iso_or_none(value) -> str | None:
    day = _day(value)
    return None if day is None else R.iso(day)


def _bar_days(index) -> pd.DatetimeIndex:
    """Bar dates as tz-naive midnights; they must be ascending (bars after fetch.validate are)."""
    days = pd.DatetimeIndex(index)
    if days.tz is not None:
        days = days.tz_localize(None)
    days = days.normalize()
    if not days.is_monotonic_increasing:
        raise ValueError("bar dates must be in ascending order")
    return days


# ---------------------------------------------------------------------------- parsing
def _merge(day: pd.Timestamp, parts: list[dict]) -> dict:
    """One entry for the distributions going ex on one date: amounts summed; the other fields from
    the largest distribution first (frequency labels joined with '+', e.g. 'Special+Quarterly')."""
    parts = sorted(parts, key=lambda p: -p["amount"])          # stable: FMP's order among equals
    raws = [p["dividend"] for p in parts]
    out = {"ex_date": R.iso(day), "amount": sum(p["amount"] for p in parts),
           "dividend": None if any(r is None for r in raws) else sum(raws),
           "frequency": "+".join(dict.fromkeys(p["frequency"] for p in parts if p["frequency"])) or None}
    for field in _DATE_FIELDS.values():
        out[field] = next((p[field] for p in parts if p[field]), None)
    return out


def parse_dividends(rows) -> tuple[list[dict], int]:
    """FMP dividends rows -> (entries ascending by ex-date, dropped).

    Each entry: {ex_date "YYYY-MM-DD", amount (adjDividend: split-adjusted, the basis of the /full
    bars), dividend (the amount as paid, before later splits; None if missing), frequency,
    declaration_date, record_date, payment_date}; empty strings become None.

    Same ex-date twice: FMP sometimes lists one distribution twice (Realty Income 2022-11-30: the same
    amounts and dates, once "Monthly", once "Irregular"), so a row repeating an earlier row's ex-date,
    adjDividend and dividend counts once. Rows on one ex-date with different amounts are separate
    distributions (a regular dividend and a special one, an income and a capital-gains distribution):
    the price drops by their total, so they become one entry with the amounts summed (see _merge).

    dropped = rows not used: no parseable ex-date, adjDividend missing or <= 0, not a row at all, or an
    exact repeat. Anything that is not a list of rows (or a {"historical": [...]} wrapper) gives ([], 0).
    """
    if isinstance(rows, dict):                                # some proxies wrap rows in "historical"
        rows = rows.get("historical")
    if not isinstance(rows, (list, tuple)):
        return [], 0
    dropped, seen, by_date = 0, set(), {}
    for row in rows:
        day = _day(row.get("date")) if isinstance(row, dict) else None
        amount = _amount(row.get("adjDividend")) if day is not None else None
        if amount is None or amount <= 0:
            dropped += 1
            continue
        raw = _amount(row.get("dividend"))
        if (day, amount, raw) in seen:
            dropped += 1
            continue
        seen.add((day, amount, raw))
        by_date.setdefault(day, []).append(
            {"amount": amount, "dividend": raw, "frequency": _label(row.get("frequency")),
             **{new: _iso_or_none(row.get(old)) for old, new in _DATE_FIELDS.items()}})
    return [_merge(day, parts) for day, parts in sorted(by_date.items())], dropped


# ---------------------------------------------------------------------------- bars
def _dated(dividends) -> list[tuple[pd.Timestamp, dict]]:
    """(ex-date, entry) for the usable entries (a parseable ex_date and a positive amount), by ex-date."""
    out = []
    for e in dividends or ():
        day = _day(e.get("ex_date")) if isinstance(e, dict) else None
        amount = _amount(e.get("amount")) if day is not None else None
        if amount is not None and amount > 0:
            out.append((day, e))
    return sorted(out, key=lambda x: x[0])


def _exchange_calendar(calendar):
    """`calendar` when it knows exchange closures (a market_calendar.MarketCalendar other than
    weekday_calendar()), else None."""
    return None if calendar is None or getattr(calendar, "source", None) == "weekdays" else calendar


def match_to_bars(index, dividends, calendar=None) -> dict:
    """Which bar of `index` (ascending dates) each ex-date of `dividends` lands on.

    An ex-date with a bar maps to that bar. One without a bar maps, with an exchange calendar
    (`calendar`, a market_calendar.MarketCalendar), to the bar of the first session on or after the
    ex-date, where its drop shows, if the symbol has a bar that session, else it is unmatched: an
    ex-date on a weekend or holiday, or inside a multi-day closure (2001-09-12 -> 2001-09-17), finds
    its bar however long the closure, and one on a session without a bar (a data gap) stays
    unmatched. Without a calendar (or with weekday_calendar()), it maps to the first bar at most
    C.DIVIDEND_MATCH_DAYS calendar days later, else it is unmatched. Ex-dates before the first bar or
    after the last one, and entries without a usable ex_date and positive amount, are left out.
    Returns {"matched": [(bar position, entry)], "unmatched": [entry]}, both in ex-date order."""
    days = _bar_days(index)
    calendar = _exchange_calendar(calendar)
    matched, unmatched = [], []
    for ex, entry in _dated(dividends) if len(days) else ():
        if not days[0] <= ex <= days[-1]:
            continue
        pos = int(days.searchsorted(ex, side="left"))         # first bar on or after the ex-date
        if days[pos] != ex:
            if calendar is None:
                pos = pos if (days[pos] - ex).days <= C.DIVIDEND_MATCH_DAYS else None
            else:
                session = calendar.next_session(ex, inclusive=True)
                pos = int(days.searchsorted(session, side="left"))
                pos = pos if pos < len(days) and days[pos] == session else None
        if pos is None:
            unmatched.append(entry)
        else:
            matched.append((pos, entry))
    return {"matched": matched, "unmatched": unmatched}


def dividend_column(index, dividends, calendar=None) -> pd.Series:
    """The bars["dividend"] column: on each bar, the amount going ex on it (summed when several
    ex-dates land on one bar; see match_to_bars, which `calendar` is passed to), 0.0 on every other
    bar. Float, on `index`."""
    values = np.zeros(len(index), dtype=float)
    for pos, entry in match_to_bars(index, dividends, calendar)["matched"]:
        values[pos] += _amount(entry["amount"])
    return pd.Series(values, index=index, name="dividend")


def bar_dividends(df: pd.DataFrame) -> pd.Series:
    """df["dividend"] as floats, with missing, non-finite and non-positive values read as 0; all
    zeros when df has no such column."""
    if "dividend" not in df.columns:
        return pd.Series(0.0, index=df.index, name="dividend")
    d = pd.to_numeric(df["dividend"], errors="coerce").astype(float)
    return d.where(np.isfinite(d) & (d > 0), 0.0)


def prev_close_net(df: pd.DataFrame) -> pd.Series:
    """close[t-1] - dividend[t]: the prior close on the footing of bar t's prices (on an ex-dividend
    bar the price opens lower by about the dividend). It is the base a drop is measured from, so a
    drop the dividend explains is no gap down; a rise is measured from close[t-1] itself, since a
    dividend cannot explain one. Equal to close.shift(1) without dividends."""
    return df["close"].shift(1) - bar_dividends(df)


# ---------------------------------------------------------------------------- module
def _trading_days(as_of: pd.Timestamp, ex: pd.Timestamp, calendar) -> int:
    """np.busday_count(as_of, ex), as for the earnings date: the sessions in [as_of, ex), which is
    the sessions in (as_of, ex] when both are sessions. An ex-date that is not a session counts up to
    the next session, where its drop shows. Holidays come from `calendar` (a MarketCalendar); without
    one, every weekday counts."""
    holidays = getattr(calendar, "holidays_array", None)
    holidays = [] if holidays is None else holidays
    return int(np.busday_count(as_of.date(), ex.date(), holidays=holidays))


def _empty(hz: dict) -> dict:
    return {"available": False, "source": SOURCE, "pays_dividends": None, "last_ex_dividend": None,
            "next_ex_dividend": None, "within_horizon": None, "within_stat_horizons": None,
            "horizon_days": int(hz["primary"]), "ex_dates_in_history": None, "matched_to_bars": None,
            "gap_basis": GAP_BASIS, "notes": []}


def build_dividends(ctx) -> dict:
    """Dividends: the last ex-dividend date on or before as_of and the next one after it (its size
    against the last close and ATR(14), and the trading days to it), whether that one falls inside
    the primary and each stat horizon, and how many ex-dates in the price history matched a bar.

    ctx["dividends"] is the parse_dividends list ([] for a symbol that pays none, None when the fetch
    failed, the reason in ctx["skipped"]["dividends"]). ctx["calendar"] (a MarketCalendar, optional)
    supplies the exchange holidays for the trading-day count and matches ex-dates to bars as the
    bars["dividend"] column does (match_to_bars). A symbol counts as paying dividends with an
    ex-dividend date in the C.DIVIDEND_ACTIVE_DAYS calendar days up to as_of or one announced after
    it. Every key is always present; unknown values are null."""
    hz = ctx.get("hz") or C.HORIZONS[ctx.get("horizon", C.DEFAULT_HORIZON)]
    out = _empty(hz)
    divs = ctx.get("dividends")
    if divs is None:
        return {**out, "reason": R.skip_reason(ctx, ("dividends",), "no dividend data")}
    bars = ctx.get("bars")
    if bars is None or len(bars) == 0:
        return {**out, "reason": "no price bars up to as_of"}
    as_of = R.as_of(ctx)
    bars = bars.loc[bars.index <= as_of]
    if len(bars) == 0:
        return {**out, "reason": "no price bars up to as_of"}

    notes: list[str] = []
    ticker = ctx.get("ticker") or "this symbol"
    calendar = ctx.get("calendar")
    dated = _dated(divs)
    past = [(d, e) for d, e in dated if d <= as_of]
    upcoming = [(d, e) for d, e in dated if d > as_of]
    m = match_to_bars(bars.index, [e for _, e in dated], calendar)
    out.update(available=True, ex_dates_in_history=len(m["matched"]) + len(m["unmatched"]),
               matched_to_bars=len(m["matched"]), notes=notes)
    if m["unmatched"]:
        late = [_iso_or_none(e["ex_date"]) for e in m["unmatched"][-NOTE_DATES_MAX:]]
        where = (f"within {C.DIVIDEND_MATCH_DAYS} calendar days" if _exchange_calendar(calendar) is None
                 else f"on the first {C.CALENDAR_EXCHANGE} session on or after the ex-date")
        notes.append(f"{len(m['unmatched'])} ex-dividend date(s) in the price history have no bar {where} "
                     f"(data gaps), so their drops are not taken out of the gaps (latest: {', '.join(late)})")
    if not dated:
        notes.append(f"FMP lists no dividends for {ticker}: no ex-dividend dates to report or to take out of gaps")
        return {**out, "pays_dividends": False}

    close = R.num(bars["close"].iloc[-1])
    ind = ctx.get("ind")
    atr = R.num(ind["atr14"].get(bars.index[-1])) if ind is not None and "atr14" in ind else None

    def share(amount: float, base: float | None) -> float | None:
        return amount / base if base else None

    if past:
        d, e = past[-1]
        amount = _amount(e["amount"])
        out["last_ex_dividend"] = {"date": R.iso(d), "amount": amount, "amount_pct": share(amount, close),
                                   "frequency": e.get("frequency")}
    lapsed = not upcoming and (as_of - past[-1][0]).days > C.DIVIDEND_ACTIVE_DAYS
    out["pays_dividends"] = not lapsed
    if lapsed:
        notes.append(f"no ex-dividend date in the {C.DIVIDEND_ACTIVE_DAYS} calendar days up to as_of and none "
                     f"announced (the last was {R.iso(past[-1][0])}): treated as not paying dividends now")
        return out
    if not upcoming:
        notes.append(f"no upcoming ex-dividend date announced in FMP's data yet (the last was {R.iso(past[-1][0])}); "
                     f"one declared later may still fall inside the horizon")
        return out

    ex, e = upcoming[0]
    amount = _amount(e["amount"])
    td = _trading_days(as_of, ex, calendar)
    if calendar is None or getattr(calendar, "source", None) == "weekdays":
        notes.append("trading days to the ex-dividend date counted as weekdays: no exchange calendar, so US "
                     "market holidays are not excluded")
    if atr is None:
        notes.append(f"ATR({C.ATR_PERIOD}) unavailable at {R.iso(bars.index[-1])}: amount_atr is null")
    out["next_ex_dividend"] = {
        "date": R.iso(ex), "amount": amount, "amount_pct": share(amount, close),
        "amount_atr": share(amount, atr), "frequency": e.get("frequency"),
        "declaration_date": e.get("declaration_date"), "payment_date": e.get("payment_date"),
        "calendar_days": int((ex - as_of).days), "trading_days_to": td,
    }
    out["within_horizon"] = td <= int(hz["primary"])
    out["within_stat_horizons"] = {str(h): td <= int(h) for h in hz["stat_horizons"]}
    return out
