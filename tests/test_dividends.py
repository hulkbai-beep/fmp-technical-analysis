"""Tests for scripts/dividends.py: FMP row parsing, matching ex-dates to bars, the bars["dividend"]
column helpers and the `dividends` module. Synthetic data only (no network)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config as C
import dividends as D
import indicators as I
import market_calendar as MC
from conftest import synthetic_bars

NYSE_FIXTURE = Path(__file__).resolve().parent / "data" / "nyse_holidays_fmp.json"


@pytest.fixture(scope="module")
def nyse():
    """The NYSE calendar as load_all builds it: FMP's recorded holiday list (1990-2028) and the exchange rules."""
    with open(NYSE_FIXTURE, encoding="utf-8") as f:
        return MC.build_calendar(json.load(f)["rows"], end_year=2028)


def day(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def fmp_row(date, adj, raw=None, frequency="Quarterly", declaration="", record="", payment=""):
    """One FMP dividends row (FMP sends empty strings for unknown dates)."""
    return {"symbol": "TEST", "date": date, "recordDate": record, "paymentDate": payment,
            "declarationDate": declaration, "adjDividend": adj, "dividend": adj if raw is None else raw,
            "yield": 1.0, "frequency": frequency}


def entry(date, amount, frequency="Quarterly", **kw):
    return {"ex_date": day(date), "amount": amount, "dividend": amount, "frequency": frequency,
            "declaration_date": kw.get("declaration_date"), "record_date": kw.get("record_date"),
            "payment_date": kw.get("payment_date")}


# ---------------------------------------------------------------------------- parse_dividends
def test_parse_dividends_shapes_sorts_and_keeps_split_adjusted_amounts():
    rows = [  # FMP order: newest first
        fmp_row("2026-12-01", 0.53, frequency="Quarterly", declaration="2026-10-15", record="2026-12-01",
                payment="2026-12-15"),
        fmp_row("2021-08-31", 0.004, raw=0.04, frequency="Irregular", declaration="2021-08-18"),
        fmp_row("1970-06-08", 0.00375, raw=0.36),
    ]
    out, dropped = D.parse_dividends(rows)
    assert dropped == 0
    assert [e["ex_date"] for e in out] == ["1970-06-08", "2021-08-31", "2026-12-01"]
    assert out[1] == {"ex_date": "2021-08-31", "amount": 0.004, "dividend": 0.04, "frequency": "Irregular",
                      "declaration_date": "2021-08-18", "record_date": None, "payment_date": None}
    assert out[2]["payment_date"] == "2026-12-15" and out[2]["record_date"] == "2026-12-01"
    assert list(out[0]) == ["ex_date", "amount", "dividend", "frequency", "declaration_date", "record_date",
                            "payment_date"]


def test_parse_dividends_drops_unusable_rows_and_counts_them():
    rows = [fmp_row("2024-03-01", 0.5),
            fmp_row("2024-02-30", 0.5),               # not a date
            fmp_row("", 0.5),
            {"adjDividend": 0.5},                     # no date at all
            fmp_row("2024-01-02", 0.0),               # no positive amount
            fmp_row("2024-01-03", -0.1),
            fmp_row("2024-01-04", None),
            fmp_row("2024-01-05", float("nan")),
            fmp_row("2024-01-08", True),              # a boolean is not an amount
            "not a row", None]
    out, dropped = D.parse_dividends(rows)
    assert [e["ex_date"] for e in out] == ["2024-03-01"] and dropped == 10


def test_parse_dividends_reads_strings_timestamps_and_wrappers():
    rows = {"historical": [{"date": "2024-05-10 00:00:00", "adjDividend": "0.25", "dividend": "0.25",
                            "frequency": "  Monthly ", "paymentDate": "2024-05-20T00:00:00"}]}
    (e,), dropped = D.parse_dividends(rows)
    assert (e["ex_date"], e["amount"], e["dividend"], e["frequency"], e["payment_date"]) == \
        ("2024-05-10", 0.25, 0.25, "Monthly", "2024-05-20")
    assert dropped == 0
    assert D.parse_dividends(None) == ([], 0)
    assert D.parse_dividends({"Error Message": "restricted"}) == ([], 0)
    assert D.parse_dividends("oops") == ([], 0)
    (e,), _ = D.parse_dividends([{"date": pd.Timestamp("2024-05-10"), "adjDividend": 1}])
    assert e["ex_date"] == "2024-05-10" and e["dividend"] is None and e["frequency"] is None


def test_parse_dividends_same_ex_date_repeat_counts_once_distinct_amounts_sum():
    # Realty Income 2022-11-30 as FMP lists it: one distribution twice, with different frequency labels
    repeat = [fmp_row("2022-11-30", 0.248, frequency="Monthly", declaration="2022-11-08", record="2022-12-01",
                      payment="2022-12-15"),
              fmp_row("2022-11-30", 0.248, frequency="Irregular", declaration="2022-11-08", record="2022-12-01",
                      payment="2022-12-15")]
    (e,), dropped = D.parse_dividends(repeat)
    assert dropped == 1 and e["amount"] == 0.248 and e["frequency"] == "Monthly"

    # a regular and a special dividend going ex together: the price drops by both
    both = [fmp_row("2023-12-27", 1.02, frequency="Quarterly", payment="2024-02-16"),
            fmp_row("2023-12-27", 15.0, frequency="Special", declaration="2023-12-13", payment="2024-01-12")]
    (e,), dropped = D.parse_dividends(both)
    assert dropped == 0
    assert e["amount"] == pytest.approx(16.02) and e["dividend"] == pytest.approx(16.02)
    assert e["frequency"] == "Special+Quarterly"                       # the largest distribution first
    assert (e["declaration_date"], e["payment_date"]) == ("2023-12-13", "2024-01-12")

    # a raw amount missing on one part: the summed raw amount is unknown, the adjusted one is not
    (e,), _ = D.parse_dividends([fmp_row("2023-12-27", 1.0), {"date": "2023-12-27", "adjDividend": 2.0}])
    assert e["amount"] == 3.0 and e["dividend"] is None and e["frequency"] == "Quarterly"


# ---------------------------------------------------------------------------- bars
def test_dividend_column_matches_ex_dates_to_bars(nyse):
    idx = pd.DatetimeIndex(["2024-11-25", "2024-11-26", "2024-11-27", "2024-11-29", "2024-12-02",
                            "2024-12-16", "2024-12-17"], name="date")   # no bar on Thanksgiving or Dec 3-13
    divs = [entry("2024-11-20", 9.0),             # before the first bar: ignored
            entry("2024-11-26", 0.5),             # on a bar
            entry("2024-11-28", 0.25),            # Thanksgiving: the next bar (1 day later)
            entry("2024-11-29", 0.1),             # the same bar as the one above: summed
            entry("2024-12-05", 0.7),             # data gap: next bar 11 days later -> unmatched
            entry("2024-12-12", 0.3),             # 4 days before the next bar: matched without a calendar
            entry("2024-12-18", 0.9)]             # after the last bar: ignored (an announced one)
    col = D.dividend_column(idx, divs)
    assert col.name == "dividend" and col.dtype == float and col.index.equals(idx)
    assert col.tolist() == pytest.approx([0.0, 0.5, 0.0, 0.35, 0.0, 0.3, 0.0])
    m = D.match_to_bars(idx, divs)
    assert [(p, e["ex_date"]) for p, e in m["matched"]] == [(1, "2024-11-26"), (3, "2024-11-28"),
                                                            (3, "2024-11-29"), (5, "2024-12-12")]
    assert [e["ex_date"] for e in m["unmatched"]] == ["2024-12-05"]
    # with the NYSE calendar an ex-date goes to its first session's bar: Thanksgiving to 11-29 as before, but
    # 2024-12-12 is a session without a bar (the data gap), so it stays unmatched
    m = D.match_to_bars(idx, divs, nyse)
    assert [(p, e["ex_date"]) for p, e in m["matched"]] == [(1, "2024-11-26"), (3, "2024-11-28"), (3, "2024-11-29")]
    assert [e["ex_date"] for e in m["unmatched"]] == ["2024-12-05", "2024-12-12"]
    assert D.dividend_column(idx, divs, nyse).tolist() == pytest.approx([0.0, 0.5, 0.0, 0.35, 0.0, 0.0, 0.0])


def test_an_ex_date_inside_a_multi_day_closure_maps_to_the_first_session(nyse):
    # KO-like: the NYSE was closed 2001-09-11..14, so ex-dates 09-11 and 09-12 show on 09-17, 6 and 5 days later
    idx = pd.DatetimeIndex(["2001-09-07", "2001-09-10", "2001-09-17", "2001-09-18"], name="date")
    divs = [entry("2001-09-11", 0.1), entry("2001-09-12", 0.18)]
    m = D.match_to_bars(idx, divs, nyse)
    assert [(p, e["ex_date"]) for p, e in m["matched"]] == [(2, "2001-09-11"), (2, "2001-09-12")]
    assert m["unmatched"] == []
    assert D.dividend_column(idx, divs, nyse).tolist() == pytest.approx([0.0, 0.0, 0.28, 0.0])
    # the calendar-day rule alone (no calendar, or weekday_calendar() without holidays) leaves both unmatched
    for cal in (None, MC.weekday_calendar()):
        plain = D.match_to_bars(idx, divs, cal)
        assert plain["matched"] == [] and [e["ex_date"] for e in plain["unmatched"]] == ["2001-09-11", "2001-09-12"]
    # the symbol has no bar on that first session (a data gap right after the closure): unmatched
    gap = D.match_to_bars(idx.drop(pd.Timestamp("2001-09-17")), divs, nyse)
    assert gap["matched"] == [] and len(gap["unmatched"]) == 2


def test_an_ex_date_on_a_session_without_a_bar_stays_unmatched(nyse):
    idx = pd.DatetimeIndex(["2024-12-02", "2024-12-03", "2024-12-05", "2024-12-06"], name="date")   # no 12-04 bar
    divs = [entry("2024-12-04", 0.5), entry("2024-12-07", 0.2)]       # a Wednesday session; a Saturday
    m = D.match_to_bars(idx, divs, nyse)
    assert m["matched"] == [] and [e["ex_date"] for e in m["unmatched"]] == ["2024-12-04"]   # 12-07: after the last bar
    assert [(p, e["ex_date"]) for p, e in D.match_to_bars(idx, divs)["matched"]] == [(2, "2024-12-04")]
    weekend = D.match_to_bars(idx.append(pd.DatetimeIndex(["2024-12-09"])), divs, nyse)      # Saturday -> Monday
    assert [(p, e["ex_date"]) for p, e in weekend["matched"]] == [(4, "2024-12-07")]


def test_match_to_bars_edges_and_bad_input():
    idx = pd.bdate_range("2024-01-01", periods=5, name="date")
    assert D.match_to_bars(idx, None) == {"matched": [], "unmatched": []}
    assert D.match_to_bars(idx[:0], [entry(idx[0], 1.0)]) == {"matched": [], "unmatched": []}
    first_last = D.match_to_bars(idx, [entry(idx[-1], 1.0), entry(idx[0], 2.0), {"ex_date": "junk", "amount": 1},
                                       {"ex_date": day(idx[2]), "amount": 0.0}])
    assert [p for p, _ in first_last["matched"]] == [0, 4]             # ex-date order; bad entries left out
    tz = D.dividend_column(idx.tz_localize("America/New_York"), [entry(idx[1], 1.0)])
    assert tz.tolist() == [0.0, 1.0, 0.0, 0.0, 0.0]
    with pytest.raises(ValueError):
        D.match_to_bars(idx[::-1], [entry(idx[1], 1.0)])


def test_bar_dividends_and_prev_close_net():
    idx = pd.bdate_range("2024-01-01", periods=4, name="date")
    bars = pd.DataFrame({"close": [10.0, 11.0, 12.0, 13.0]}, index=idx)
    assert D.bar_dividends(bars).tolist() == [0.0] * 4
    pd.testing.assert_series_equal(D.prev_close_net(bars), bars["close"].shift(1), check_names=False)
    bars["dividend"] = [0.0, 0.5, np.nan, -1.0]
    assert D.bar_dividends(bars).tolist() == [0.0, 0.5, 0.0, 0.0]
    net = D.prev_close_net(bars)
    assert np.isnan(net.iloc[0]) and net.iloc[1:].tolist() == [9.5, 11.0, 12.0]


# ---------------------------------------------------------------------------- build_dividends
def make_ctx(bars, dividends, horizon="swing", **kw):
    ctx = dict(ticker="TEST", as_of=day(bars.index[-1]), horizon=horizon, hz=C.HORIZONS[horizon], bars=bars,
               ind=I.compute_all(bars), dividends=dividends, calendar=None, skipped={}, notes=[])
    ctx.update(kw)
    return ctx


@pytest.fixture
def payer():
    """300 bars and a quarterly payer with four ex-dates in the history."""
    bars = synthetic_bars(300, start="2024-01-02")
    idx = bars.index
    past = [entry(idx[i], 0.5, payment_date=day(idx[i + 10])) for i in (-200, -137, -74, -11)]
    return bars, past


def test_payer_with_next_ex_date_inside_the_horizon(payer):
    bars, past = payer
    as_of = bars.index[-1]
    ex = as_of + pd.offsets.BDay(5)
    nxt = entry(ex, 0.6, declaration_date=day(as_of - pd.Timedelta(days=20)),
                payment_date=day(ex + pd.Timedelta(days=14)))
    ctx = make_ctx(bars, past + [nxt, entry(ex + pd.offsets.BDay(63), 0.6)])
    out = D.build_dividends(ctx)
    close, atr = bars["close"].iloc[-1], ctx["ind"]["atr14"].iloc[-1]
    assert out["available"] is True and out["pays_dividends"] is True and "reason" not in out
    assert out["source"] == D.SOURCE and out["gap_basis"] == D.GAP_BASIS and out["horizon_days"] == 10
    assert out["last_ex_dividend"] == {"date": day(bars.index[-11]), "amount": 0.5,
                                       "amount_pct": pytest.approx(0.5 / close), "frequency": "Quarterly"}
    assert out["next_ex_dividend"] == {
        "date": day(ex), "amount": 0.6, "amount_pct": pytest.approx(0.6 / close),
        "amount_atr": pytest.approx(0.6 / atr), "frequency": "Quarterly",
        "declaration_date": nxt["declaration_date"], "payment_date": nxt["payment_date"],
        "calendar_days": (ex - as_of).days, "trading_days_to": 5}
    assert out["within_horizon"] is True
    assert out["within_stat_horizons"] == {"5": True, "10": True, "20": True}
    assert (out["ex_dates_in_history"], out["matched_to_bars"]) == (4, 4)
    assert any("counted as weekdays" in n for n in out["notes"])         # no exchange calendar in ctx
    json.dumps(out, allow_nan=False)


@pytest.mark.parametrize("horizon, ahead, primary, per_h", [
    ("swing", 15, False, {"5": False, "10": False, "20": True}),
    ("swing", 10, True, {"5": False, "10": True, "20": True}),
    ("position", 61, False, {"20": False, "60": False}),
    ("position", 20, True, {"20": True, "60": True}),
])
def test_next_ex_date_against_the_horizons(payer, horizon, ahead, primary, per_h):
    bars, past = payer
    ex = bars.index[-1] + pd.offsets.BDay(ahead)
    out = D.build_dividends(make_ctx(bars, past + [entry(ex, 0.6)], horizon=horizon))
    assert out["next_ex_dividend"]["trading_days_to"] == ahead
    assert out["within_horizon"] is primary and out["within_stat_horizons"] == per_h
    assert out["horizon_days"] == C.HORIZONS[horizon]["primary"]


def test_ex_date_on_as_of_is_the_last_one(payer):
    bars, past = payer
    as_of = bars.index[-1]
    later = entry(as_of + pd.offsets.BDay(40), 0.55)
    out = D.build_dividends(make_ctx(bars, past + [entry(as_of, 0.6), later]))
    assert out["last_ex_dividend"]["date"] == day(as_of)
    assert out["next_ex_dividend"]["date"] == later["ex_date"] and out["within_horizon"] is False
    assert (out["ex_dates_in_history"], out["matched_to_bars"]) == (5, 5)


def test_trading_days_skip_exchange_holidays_with_a_calendar():
    n = len(pd.bdate_range("2025-06-02", "2026-11-20"))
    bars = synthetic_bars(n, start="2025-06-02")                     # as_of: Friday before Thanksgiving week
    assert day(bars.index[-1]) == "2026-11-20"
    cal = MC.build_calendar(None, end_year=2027)                     # NYSE rules (no network)
    for ex, weekdays, sessions in (("2026-11-30", 6, 5),            # Thanksgiving (Nov 26) skipped
                                   ("2026-11-26", 4, 4),            # on the holiday: counts to Nov 27
                                   ("2026-11-21", 1, 1)):           # a Saturday: counts to Monday
        divs = [entry("2026-08-14", 0.5), entry(ex, 0.5)]
        plain = D.build_dividends(make_ctx(bars, divs))
        nyse = D.build_dividends(make_ctx(bars, divs, calendar=cal))
        assert plain["next_ex_dividend"]["trading_days_to"] == weekdays, ex
        assert nyse["next_ex_dividend"]["trading_days_to"] == sessions, ex
        assert not any("counted as weekdays" in n for n in nyse["notes"])
    weekdays_only = D.build_dividends(make_ctx(bars, divs, calendar=MC.weekday_calendar()))
    assert any("counted as weekdays" in n for n in weekdays_only["notes"])


def test_payer_without_an_announced_next_date(payer):
    bars, past = payer
    out = D.build_dividends(make_ctx(bars, past))
    assert out["pays_dividends"] is True and out["last_ex_dividend"]["date"] == day(bars.index[-11])
    assert out["next_ex_dividend"] is None
    assert out["within_horizon"] is None and out["within_stat_horizons"] is None
    assert any("no upcoming ex-dividend date announced" in n for n in out["notes"])


MONTHLY = ["2025-12-01", "2026-01-02", "2026-02-02", "2026-03-02", "2026-04-01", "2026-05-01", "2026-06-01",
           "2026-07-01", "2026-08-03", "2026-09-01", "2026-10-01", "2026-11-02"]


@pytest.fixture(scope="module")
def monthly():
    """A monthly payer as of Wednesday 2026-11-04 (HYG's ex-dates of the last 12 months), the NYSE rules."""
    bars = synthetic_bars(len(pd.bdate_range("2025-06-02", "2026-11-04")), start="2025-06-02")
    assert day(bars.index[-1]) == "2026-11-04"
    return bars, [entry(d, 0.4, frequency="Monthly") for d in MONTHLY], MC.build_calendar(None, end_year=2027)


def test_ex_dates_inside_the_horizon_are_projected_from_the_spacing_of_the_last_12_months(monthly):
    # HYG 2026-11-04 had no next ex-date announced, and the note said only that one "may still fall inside"
    # 60 sessions, where about three usually do (12-02, 01-02 and 02-01 at 30.4-day spacing; 02-01 is session 59)
    bars, divs, cal = monthly
    close = bars["close"].iloc[-1]
    pos = D.build_dividends(make_ctx(bars, divs, horizon="position", calendar=cal))
    assert pos["ex_dates_12m"] == 12 and pos["next_ex_dividend"] is None and pos["within_horizon"] is None
    assert pos["projected_in_horizon"] == {"ex_dates": 3, "amount_pct": pytest.approx(1.2 / close)}
    (note,) = pos["notes"]
    assert note.startswith("no upcoming ex-dividend date announced in FMP's data yet (the last was 2026-11-02); "
                           "at the spacing of the last 12 months (12 ex-dates) about 3 would fall inside the "
                           "60-session horizon, about ")
    assert note.endswith("% of the close in all at the last amount")
    swing = D.build_dividends(make_ctx(bars, divs, calendar=cal))          # 10 sessions: the next is ~19 away
    assert swing["projected_in_horizon"] == {"ex_dates": 0, "amount_pct": 0.0}
    assert swing["notes"] == ["no upcoming ex-dividend date announced in FMP's data yet (the last was "
                              "2026-11-02); one declared later may still fall inside the horizon"]
    # announced inside the horizon: it counts, and the later ones at the same spacing are added
    nxt = D.build_dividends(make_ctx(bars, divs + [entry("2026-12-01", 0.5, frequency="Monthly")],
                                     horizon="position", calendar=cal))
    assert nxt["within_horizon"] is True
    assert nxt["projected_in_horizon"] == {"ex_dates": 3, "amount_pct": pytest.approx(1.5 / close)}
    assert any("about 3 would fall inside the 60-session horizon, the announced 2026-12-01 included" in n
               and n.endswith("in all at its amount") for n in nxt["notes"])
    json.dumps(pos, allow_nan=False)


def test_projection_follows_the_phase_and_needs_two_ex_dates(payer):
    bars, past = payer                                    # quarterly, ~91 days apart, the last 11 sessions ago
    out = D.build_dividends(make_ctx(bars, past, horizon="position"))
    assert out["ex_dates_12m"] == 4 and out["projected_in_horizon"]["ex_dates"] == 1
    assert "about 1 would fall inside the 60-session horizon" in out["notes"][0]
    assert " in all " not in out["notes"][0]
    just_paid = D.build_dividends(make_ctx(bars, past[1:] + [entry(bars.index[-1], 0.5)], horizon="position"))
    assert just_paid["projected_in_horizon"]["ex_dates"] == 0    # the next is ~63 sessions away
    assert "one declared later may still fall inside the horizon" in just_paid["notes"][0]
    beyond = D.build_dividends(make_ctx(bars, past + [entry(bars.index[-1] + pd.offsets.BDay(61), 0.6)],
                                        horizon="position"))
    assert beyond["within_horizon"] is False and beyond["projected_in_horizon"] == {"ex_dates": 0, "amount_pct": 0.0}
    annual = D.build_dividends(make_ctx(bars, past[-1:], horizon="position"))
    assert annual["ex_dates_12m"] == 1 and annual["projected_in_horizon"] is None
    assert "one declared later may still fall inside the horizon" in annual["notes"][0]


def test_lapsed_payer_is_not_paying_now():
    bars = synthetic_bars(800, start="2022-01-03")
    idx = bars.index
    last = idx[-1] - pd.Timedelta(days=C.DIVIDEND_ACTIVE_DAYS + 30)
    out = D.build_dividends(make_ctx(bars, [entry(last - pd.Timedelta(days=91), 0.2), entry(last, 0.2)]))
    assert out["available"] is True and out["pays_dividends"] is False
    assert out["last_ex_dividend"]["date"] == day(last) and out["next_ex_dividend"] is None
    assert out["within_horizon"] is None
    assert any("treated as not paying dividends now" in n for n in out["notes"])
    recent = D.build_dividends(make_ctx(bars, [entry(idx[-1] - pd.Timedelta(days=C.DIVIDEND_ACTIVE_DAYS), 0.2)]))
    assert recent["pays_dividends"] is True                         # an annual payer, a year later


def test_first_announced_dividend_counts_as_paying(payer):
    bars, _ = payer
    ex = bars.index[-1] + pd.offsets.BDay(3)
    out = D.build_dividends(make_ctx(bars, [entry(ex, 0.1)]))
    assert out["pays_dividends"] is True and out["last_ex_dividend"] is None
    assert out["next_ex_dividend"]["trading_days_to"] == 3 and out["within_horizon"] is True
    assert (out["ex_dates_in_history"], out["matched_to_bars"]) == (0, 0)


def test_non_payer():
    bars = synthetic_bars(300)
    out = D.build_dividends(make_ctx(bars, []))
    assert out["available"] is True and out["pays_dividends"] is False
    for key in ("last_ex_dividend", "next_ex_dividend", "within_horizon", "within_stat_horizons"):
        assert out[key] is None, key
    assert (out["ex_dates_in_history"], out["matched_to_bars"]) == (0, 0)
    assert any("FMP lists no dividends for TEST" in n for n in out["notes"])
    json.dumps(out, allow_nan=False)


def test_failed_fetch_is_unavailable_with_the_reason():
    bars = synthetic_bars(300)
    out = D.build_dividends(make_ctx(bars, None, skipped={"dividends": "restricted: Premium endpoint"}))
    assert out["available"] is False and out["reason"] == "restricted: Premium endpoint"
    assert out["pays_dividends"] is None and out["next_ex_dividend"] is None and out["within_horizon"] is None
    assert set(D._empty(C.HORIZONS["swing"])) <= set(out)               # every key present
    plain = D.build_dividends(make_ctx(bars, None))
    assert plain["available"] is False and plain["reason"] == "no dividend data"
    no_bars = D.build_dividends({**make_ctx(bars, []), "bars": bars.iloc[:0], "as_of": "2024-01-01"})
    assert no_bars["available"] is False and "no price bars" in no_bars["reason"]
    before = D.build_dividends({**make_ctx(bars, []), "as_of": day(bars.index[0] - pd.Timedelta(days=3))})
    assert before["available"] is False and "no price bars" in before["reason"]


def test_bars_after_as_of_are_ignored(payer):
    bars, past = payer
    cut = bars.index[-30]
    ex = bars.index[-20]                                            # after as_of, inside the loaded bars
    out = D.build_dividends(make_ctx(bars, past + [entry(ex, 0.6)], as_of=day(cut)))
    ref = D.build_dividends(make_ctx(bars.loc[:cut], past + [entry(ex, 0.6)]))
    assert out == ref
    assert out["last_ex_dividend"]["date"] == day(bars.index[-74])
    assert out["next_ex_dividend"]["date"] == day(ex) and out["next_ex_dividend"]["trading_days_to"] == 10
    assert out["ex_dates_in_history"] == 3                          # idx[-11] lies after as_of
    assert out["last_ex_dividend"]["amount_pct"] == pytest.approx(0.5 / bars["close"].loc[cut])


def test_unmatched_ex_dates_and_missing_atr_are_noted(payer, nyse):
    bars, past = payer
    holes = bars.drop(bars.index[-80:-60])                          # a 20-bar data gap around idx[-74]
    ctx = make_ctx(holes, past + [entry(bars.index[-1] + pd.offsets.BDay(2), 0.6)])
    ind = ctx["ind"].copy()
    ind.loc[ind.index[-1], "atr14"] = np.nan
    out = D.build_dividends({**ctx, "ind": ind})
    assert (out["ex_dates_in_history"], out["matched_to_bars"]) == (4, 3)
    assert any("no bar within 4 calendar days" in n and day(bars.index[-74]) in n for n in out["notes"])
    assert out["next_ex_dividend"]["amount_atr"] is None
    assert any("amount_atr is null" in n for n in out["notes"])
    cal = D.build_dividends({**ctx, "ind": ind, "calendar": nyse})
    assert (cal["ex_dates_in_history"], cal["matched_to_bars"]) == (4, 3)
    assert any("no bar on the first NYSE session on or after the ex-date" in n and day(bars.index[-74]) in n
               for n in cal["notes"])


def test_the_module_matches_ex_dates_as_the_bar_column_does(nyse):
    bars = synthetic_bars(120, start="2001-06-01").drop(pd.bdate_range("2001-09-11", "2001-09-14"))
    divs = [entry("2001-09-12", 0.18)]                               # inside the September 2001 closure
    out = D.build_dividends(make_ctx(bars, divs, calendar=nyse))
    assert (out["ex_dates_in_history"], out["matched_to_bars"]) == (1, 1)
    assert not any("no bar" in n for n in out["notes"])
    assert D.dividend_column(bars.index, divs, nyse).loc["2001-09-17"] == pytest.approx(0.18)
    plain = D.build_dividends(make_ctx(bars, divs))
    assert (plain["ex_dates_in_history"], plain["matched_to_bars"]) == (1, 0)
    assert any("no bar within 4 calendar days" in n and "2001-09-12" in n for n in plain["notes"])


def test_entries_with_timestamps_strings_or_junk_are_read_or_skipped(payer):
    bars, past = payer
    ex = bars.index[-1] + pd.offsets.BDay(4)
    divs = [{**past[-1], "ex_date": pd.Timestamp(past[-1]["ex_date"])}, {"ex_date": ex, "amount": "0.6"},
            "junk", None, {"ex_date": None, "amount": 1.0}, {"ex_date": day(ex), "amount": -1.0}]
    out = D.build_dividends(make_ctx(bars, divs))
    assert out["last_ex_dividend"]["date"] == past[-1]["ex_date"] and out["last_ex_dividend"]["amount"] == 0.5
    nxt = out["next_ex_dividend"]
    assert (nxt["date"], nxt["amount"], nxt["frequency"], nxt["trading_days_to"]) == (day(ex), 0.6, None, 4)
    assert (out["ex_dates_in_history"], out["matched_to_bars"]) == (1, 1)
    assert D.dividend_column(bars.index, divs).sum() == pytest.approx(0.5)
    json.dumps(out, allow_nan=False)
