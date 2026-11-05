"""FMP-shaped synthetic responses and a fake client shared by the data-layer and end-to-end tests (no network)."""
import json
import urllib.parse
from functools import lru_cache
from pathlib import Path

import pandas as pd

import config as C
import fetch
import fmp_client
from conftest import synthetic_bars

NY = "America/New_York"
NOW = pd.Timestamp("2026-10-16 18:00", tz=NY)          # Friday, after the close buffer
FMP_ROW_CAP = 5000                                      # what the real endpoint returns at most
FULL, ADJ = fetch.FULL, fetch.ADJUSTED
HOLIDAYS, DIVIDENDS, ECON = fetch.HOLIDAYS, fetch.DIVIDENDS, fetch.ECONOMIC_CALENDAR
OFFICIAL_BASE = fmp_client.OFFICIAL_BASE
HOLIDAY_FIXTURE = Path(__file__).resolve().parent / "data" / "nyse_holidays_fmp.json"
NEXT_EX_DATE = "2026-10-26"        # the stock's declared ex-dividend date after NOW (6 sessions after it)
SPY_NEXT_EX_DATE = "2026-12-18"    # SPY's (44 sessions after NOW: inside the position horizon only)


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


@lru_cache(maxsize=1)
def nyse_holiday_rows():
    """FMP's NYSE holiday list 1990-2028 as recorded (tests/data/nyse_holidays_fmp.json)."""
    return tuple(json.loads(HOLIDAY_FIXTURE.read_text(encoding="utf-8"))["rows"])


def dividend_rows(symbol, upcoming=NEXT_EX_DATE, first="2011-02-07", amount=0.5, declared="2026-10-01"):
    """FMP dividends rows (newest first): one quarterly ex-date every 13 weeks from `first` up to NOW, plus
    the declared one at `upcoming` (None: none announced). adjDividend = dividend (no split since)."""
    past = pd.date_range(first, NOW.tz_localize(None).normalize(), freq="13W-MON")
    dates = [*(f"{d:%Y-%m-%d}" for d in past), *([upcoming] if upcoming else [])]
    rows = []
    for d in dates:
        ex = pd.Timestamp(d)
        rows.append({"symbol": symbol, "date": d, "recordDate": d,
                     "paymentDate": f"{ex + pd.Timedelta(days=14):%Y-%m-%d}",
                     "declarationDate": declared if d == upcoming else f"{ex - pd.Timedelta(days=30):%Y-%m-%d}",
                     "adjDividend": amount, "dividend": amount, "yield": 2.0, "frequency": "Quarterly"})
    return rows[::-1]


# Economic calendar around NOW (FMP shape, times in UTC). as_of 2026-10-16 -> window 2026-10-16 .. 2027-01-12.
MACRO_EVENTS = (
    ("2026-10-15 12:30:00", "US", "Inflation Rate YoY (Sep)", "High"),            # before the window
    ("2026-10-20 12:30:00", "US", "Inflation Rate YoY (Sep)", "High"),            # CPI, 08:30 EDT pre-open
    ("2026-10-20 12:30:00", "US", "Core Inflation Rate MoM (Sep)", "High"),
    ("2026-10-20 12:30:00", "US", "CPI s.a (Sep)", "Medium"),
    ("2026-10-21 14:30:00", "US", "EIA Crude Oil Stocks Change (Oct/16)", "Medium"),   # unclassified
    ("2026-10-21 20:30:00", "US", "API Crude Oil Stock Change (Oct/16)", "Low"),        # unclassified
    ("2026-10-22 12:30:00", "US", "Initial Jobless Claims (Oct/17)", "High"),     # excluded
    ("2026-10-28 18:00:00", "US", "Fed Interest Rate Decision", "High"),          # FOMC, 14:00 EDT intraday
    ("2026-10-28 18:30:00", "US", "Press Conference", "High"),
    ("2026-10-29 12:15:00", "EU", "ECB Interest Rate Decision", "High"),          # not US
    ("2026-11-06 13:30:00", "US", "Non Farm Payrolls (Oct)", "High"),             # jobs, 08:30 EST (DST ended)
    ("2026-11-06 13:30:00", "US", "Unemployment Rate (Oct)", "High"),
)


def macro_rows(events=MACRO_EVENTS):
    """FMP economic-calendar rows (newest first) for (UTC date, country, event, impact) tuples."""
    rows = [{"date": d, "country": c, "event": e, "currency": "USD" if c == "US" else "EUR", "previous": None,
             "estimate": None, "actual": None, "change": None, "impact": i, "changePercentage": 0, "unit": None}
            for d, c, e, i in events]
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def earnings_history(symbol, first="2021-01-28", last="2026-07-30", upcoming="2026-10-29"):
    """Quarterly reports (newest first, FMP shape) from `first` to `last`, plus one upcoming row."""
    past = pd.date_range(first, last, freq="91D")
    rows = [{"symbol": symbol, "date": upcoming, "epsActual": None, "epsEstimated": 1.6,
             "revenueActual": None, "revenueEstimated": 1.0e11, "lastUpdated": "2026-10-16"}]
    rows += [{"symbol": symbol, "date": f"{d:%Y-%m-%d}", "epsActual": 1.0 + 0.01 * i, "epsEstimated": 1.0,
              "revenueActual": 9.0e10, "revenueEstimated": 9.0e10, "lastUpdated": f"{d:%Y-%m-%d}"}
             for i, d in reversed(list(enumerate(past)))]
    return rows


class FakeClient:
    """Stands in for fmp_client.FMPClient: serves FMP-shaped JSON (EOD rows newest first, the
    newest FMP_ROW_CAP rows within from..to) and counts every request as a network call.

    holidays-by-exchange serves FMP's recorded NYSE list (tests/data) within from..to; dividends the
    rows in `dividends` ({symbol: rows}, [] for other symbols); economic-calendar the `macro` rows
    within from..to (by their UTC date), newest first. `blocked` restricts any endpoint."""

    def __init__(self, prices, profiles=None, quotes=None, earnings=None, adjusted=None,
                 blocked=(), indicator_rows=None, base=OFFICIAL_BASE, holidays=None, dividends=None, macro=None):
        self.prices, self.adjusted = prices, adjusted or {}
        self.profiles, self.quotes, self.earnings = profiles or {}, quotes or {}, earnings or {}
        self.blocked = set(blocked)            # endpoint or (endpoint, symbol) -> 403-style restriction
        self.indicator_rows = indicator_rows or {}
        self.holidays = list(nyse_holiday_rows() if holidays is None else holidays)
        self.dividends = dividends or {}
        self.macro = macro_rows() if macro is None else macro
        self.base = base
        self.stats = {"network_calls": 0, "cache_hits": 0}
        self.restricted, self.errors, self.requests = [], [], []

    def get(self, endpoint, ttl=None, **params):
        self.stats["network_calls"] += 1
        self.requests.append((endpoint, dict(params)))
        return self.respond(endpoint, params)

    def respond(self, endpoint, params):
        """The JSON payload for one request (raises like the real client for restricted data)."""
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
        if endpoint == HOLIDAYS:
            if params.get("exchange") != C.CALENDAR_EXCHANGE:
                return []
            return [r for r in self.holidays if self._within(r["date"], params)]
        if endpoint == DIVIDENDS:
            return list(self.dividends.get(sym, []))
        if endpoint == ECON:
            if not isinstance(self.macro, list):
                return self.macro
            rows = [r for r in self.macro if self._within(r["date"], params)]
            return sorted(rows, key=lambda r: r["date"], reverse=True)
        raise fmp_client.FMPError(f"HTTP 404 on {endpoint}")

    @staticmethod
    def _within(date, params):
        """FMP's from/to filter on the date part (UTC for the economic calendar); no bound = open."""
        d = str(date)[:10]
        return str(params.get("from") or d) <= d <= str(params.get("to") or d)

    def try_get(self, endpoint, **params):
        return fmp_client.get_with_reason(self, endpoint, **params)[0]

    def record_failure(self, endpoint, exc, params):
        (self.restricted if isinstance(exc, fmp_client.RestrictedEndpoint) else self.errors).append(endpoint)

    def describe(self):
        """Same shape as FMPClient.describe(); a non-official base reads as a third-party proxy."""
        host = urllib.parse.urlsplit(self.base).hostname or self.base
        official = host in fmp_client.OFFICIAL_HOSTS
        return {"route": "official FMP API" if official else f"third-party proxy at {host}", "base": self.base,
                "prefix": "stable", "auth_style": "header", "source": "test", "tls_verify": official}

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

    def http_get(self, url, style=None):
        """Drop-in for FMPClient._http_get: (status, body) for a stable-API URL."""
        u = urllib.parse.urlsplit(url)
        endpoint = u.path.split("/stable/", 1)[-1]
        params = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items() if k != "apikey"}
        try:
            return 200, json.dumps(self.respond(endpoint, params))
        except fmp_client.RestrictedEndpoint:
            return 403, json.dumps({"Error Message": "Premium endpoint: upgrade your subscription"})
        except fmp_client.FMPError:
            return 404, json.dumps({"Error Message": f"unknown endpoint {endpoint}"})


def market(stock="ACME", stock_start="1996-10-01", end="2026-10-16", profile=None, earnings=None, dividends=None, **kw):
    """A fake FMP with one stock (30 years by default), SPY, XLK and ^VIX, FMP's NYSE holidays and an
    economic calendar around NOW. The stock and SPY pay quarterly dividends with one declared after NOW
    (`dividends` = {symbol: rows} replaces them); other symbols pay none."""
    prices = {
        stock: series(stock_start, end, seed=3),
        "SPY": series("1993-01-29", end, seed=5),
        "XLK": series("1998-12-22", end, seed=7),
        C.VIX: series("2000-01-03", end, seed=9, volume=0),
    }
    prices.update(kw.pop("prices", {}))
    profiles = {stock: profile or profile_row(stock), "SPY": profile_row("SPY", "Financial Services", "1993-01-22", etf=True)}
    if dividends is None:
        dividends = {"SPY": dividend_rows("SPY", upcoming=SPY_NEXT_EX_DATE, amount=1.5)}
        if stock != "SPY":
            dividends[stock] = dividend_rows(stock)
    return FakeClient(prices, profiles=profiles, quotes={stock: quote_row(stock), "SPY": quote_row("SPY")},
                      earnings={stock: earnings if earnings is not None else earnings_rows(stock)},
                      dividends=dividends, **kw)


def day(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d")
