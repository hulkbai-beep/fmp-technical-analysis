#!/usr/bin/env python3
"""
Day-by-day simulation of the buying plans of the support/resistance ladder test V3. The rules are
registered in references/ladder_v3_spec.md; S1, S2, ... below are its sections.

Layers:
  build_stock()  one stock's cleaned split-adjusted bars (S1) and everything aligned to its dates:
                 SMA200, the 3-month sigma, dividends, interest, SPY, membership, market cap, P/E.
  zone_arrays()  zones(d) of every bar from the 756 bars ending at d (node_zones, PR #14), cached.
  detector()     the stateful breakout detector of S7, one per stock.
  run_group()    the numba kernel: one placement group (one random draw) of one plan, bar by bar,
                 in the event order of S11.
  run_plan()     every group of one plan; the zone group runs first, its reference ladder R gives
                 the stop floor of every group (S8).

Earnings dates never enter the kernel; the flags of S12 are computed from its events afterwards.
`leak` arguments switch on the injected leaks of self-test 1 (S15) and are 0 in every real run.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit

import node_zones as NZ

LOOKBACK = NZ.LOOKBACK                     # 756 bars of the zone window
HORIZONS = np.array([63, 126, 252, 504], dtype=np.int64)
H_MAX = 504
COST = 0.0005                              # per side
MAX_Z = 200                                # zones per bar: each holds >= 0.5% of the volume (node_zones)
MAX_ROWS = 64                              # placements per plan (start + breakouts)
N_U = 16                                   # uniforms per placement row
K_MAX = 5                                  # parts of the longest ratio
MIN_GAP = 0.02
MAX_DEPTH = 0.35
SCALE_AT = 1.5
SPY_LAG = 0.05
VOL_STEP = 0.5
TIME_LIMIT_BARS = 42
BREAKOUT_CAP = 0.0025                      # breakout start: an up-shifted level 1 stays one bin below C[t]
FRAME_START = pd.Timestamp("2008-02-01")   # S3: the first plan month (the breakout start too)
INCOME_LAG = {"Q1": 45, "Q2": 45, "Q3": 45, "Q4": 90}   # S13: days after the period end for a placeholder filing date
TTM_SPAN = 330                             # S13: the 4 summed quarters' period ends span at most this many days
REPORT_WINDOW = 100                        # S12: a quarter's report date is expected within this many days
MASTER = 20261028
BOOT_SEED = 20261029

# group kinds
ZONE, RANDOM, GAPS, VOL, FIXED, LUMP, PLACEBO = range(7)
GROUP_NAMES = ("zone", "random", "gaps", "vol", "fixed", "lump", "placebo")
# fill modes
LIMIT, CLOSE_CONFIRM = 0, 1
# outputs per horizon
OUT_FIELDS = ("value", "deployed", "avgcost", "worst", "stopped", "fills", "scaled", "sales", "ended",
              "status")
N_OUT = len(OUT_FIELDS)
ST_BAR, ST_DELISTED = 1.0, 2.0             # status: valued at bar t+h / sold at a delisting before t+h
# event codes
EV_FILL, EV_CLOSE_FILL, EV_TIME_BUY, EV_LUMP_BUY = 1, 2, 3, 4
EV_STOP_SIGNAL, EV_STOP_SALE = 10, 11
EV_SCALE_START, EV_SALE_SIGNAL, EV_SALE = 20, 21, 22
EV_ORDER, EV_CAP, EV_NO_GAP = 30, 32, 33
EV_LIQUIDATE, EV_DIVIDEND = 40, 50
EV_COLS = 7                                # bar, code, price, qty, part, created/row, band low
N_STATE = 22 + 7 * K_MAX
# injected leaks (self-test 1)
LEAK_ZONES_NEXT, LEAK_STOP_NEXT_CLOSE, LEAK_START_SMA_NEXT, LEAK_VOL_NEXT_SIGMA = 1, 2, 3, 4
LEAK_DETECTOR_NEXT, LEAK_SPY_NEXT, LEAK_SAME_BAR_FILL, LEAK_STOP_SAME_OPEN = 5, 6, 7, 8
LEAK_DIVIDEND_NEW_SHARES, LEAK_RATE_SAME_DAY, LEAK_EPS_PERIOD_END = 9, 10, 11


class LookAheadError(RuntimeError):
    """A decision at a bar changed when later data were removed or replaced, or an order acted too early."""


# ----------------------------------------------------------------------------- inputs (S1)
_ROWS: dict = {}


def read_rows(path) -> list:
    """The rows of an FMP JSON file ([] when missing), parsed once per process; callers never mutate them."""
    key = str(path)
    if key not in _ROWS:
        p = Path(path)
        r = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        _ROWS[key] = r if isinstance(r, list) else [r]
    return _ROWS[key]


def _day(x) -> np.datetime64:
    return np.datetime64(str(x)[:10], "D")


@dataclass
class Market:
    """SPY bars and the 3-month Treasury yield, cut at the data end."""
    spy_dates: np.ndarray          # datetime64[D], ascending
    spy_close: np.ndarray
    tsy_dates: np.ndarray
    tsy_y: np.ndarray              # month3 / 100
    data_end: np.datetime64

    @classmethod
    def load(cls, spy_rows: list, tsy_rows: list, data_end=None) -> "Market":
        s = sorted((_day(r["date"]), float(r["close"])) for r in spy_rows if r.get("close"))
        end = _day(data_end) if data_end is not None else s[-1][0]
        s = [x for x in s if x[0] <= end]
        y = sorted((_day(r["date"]), float(r["month3"]) / 100.0) for r in tsy_rows
                   if r.get("month3") is not None and _day(r["date"]) <= end)
        return cls(np.array([a for a, _ in s]), np.array([b for _, b in s]),
                   np.array([a for a, _ in y]), np.array([b for _, b in y]), end)

    def cut(self, end: np.datetime64) -> "Market":
        a, b = self.spy_dates <= end, self.tsy_dates <= end
        return Market(self.spy_dates[a], self.spy_close[a], self.tsy_dates[b], self.tsy_y[b], end)

    def yield_asof(self, day: np.datetime64) -> tuple[float, np.datetime64]:
        i = int(np.searchsorted(self.tsy_dates, day, side="right")) - 1
        if i < 0:
            return 0.0, np.datetime64("NaT", "D")
        return float(self.tsy_y[i]), self.tsy_dates[i]

    def interest_growth(self, start: np.datetime64, n_bars: int) -> tuple[float, np.datetime64 | None]:
        """Cash growth over the next n_bars SPY bars after `start` (S10 delisting) and the last date;
        None when the bars run past the data end."""
        i0 = int(np.searchsorted(self.spy_dates, start, side="right"))
        if i0 + n_bars - 1 >= len(self.spy_dates):
            return float("nan"), None
        g, prev = 1.0, start
        for i in range(i0, i0 + n_bars):
            y, _ = self.yield_asof(prev)
            day = self.spy_dates[i]
            g *= 1.0 + y * float((day - prev).astype(int)) / 365.0
            prev = day
        return g, prev


@dataclass
class StockData:
    symbol: str
    dates: np.ndarray              # datetime64[D]
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    suspicious: np.ndarray         # S1.2: plans touching these bars are left out (counting only)
    div: np.ndarray                # adjDividend credited before the open of this bar
    rate: np.ndarray               # interest y * days / 365 accrued before the open of this bar
    rate_src: np.ndarray           # date of the Treasury row behind rate (timing audit)
    spy: np.ndarray
    member: np.ndarray
    mcap: np.ndarray
    pe: np.ndarray                 # trailing P/E, NaN when TTM EPS <= 0 or unknown (S13)
    report_dates: np.ndarray       # earnings report dates (used after the run only)
    last: int                      # index of the stock's last bar
    delisted: bool                 # S1: liquidate at the last close
    gap_end: bool                  # S1: bars stop early without a delisting (data gap)
    cleaning: dict = field(default_factory=dict)
    report_unknown: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype="datetime64[D]"))  # S12
    sma200: np.ndarray = field(init=False)
    sig3m: np.ndarray = field(init=False)

    def __post_init__(self):
        cs = pd.Series(self.c)
        self.sma200 = cs.rolling(200, min_periods=200).mean().to_numpy(dtype=float)
        lr = np.log(cs).diff()
        self.sig3m = (lr.rolling(60, min_periods=60).std(ddof=1) * math.sqrt(63)).to_numpy(dtype=float)


def clean_bars(rows: list, adj_rows: list, split_rows: list, final: bool = False) -> tuple[pd.DataFrame, np.ndarray, dict]:
    """S1: drop placeholder bars, check one-day moves over 50%, mark unverified ones +-40 bars.
    final: the series ends because the stock stopped trading (a delisted stock): the reversal test of a
    move on its last 5 bars uses its last close, and a move on its last bar cannot reverse."""
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]), np.zeros(0, bool), {}
    df["date"] = pd.to_datetime(df["date"].astype(str).str[:10])
    df = df.drop_duplicates("date", keep="last").set_index("date").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[df["close"] > 0]
    vol = df["volume"].fillna(0)
    flat = (df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"])
    placeholder = (vol == 0) & flat
    report = {"placeholders_removed": int(placeholder.sum()),
              "zero_volume_kept": int(((vol == 0) & ~flat).sum()), "jumps": []}
    df = df[~placeholder]
    c = df["close"].to_numpy(dtype=float)
    n = len(c)
    sus = np.zeros(n, dtype=bool)
    adj = pd.Series({pd.Timestamp(str(r["date"])[:10]): r.get("adjClose") for r in adj_rows}, dtype=float)
    adj = adj.reindex(df.index)
    splits = sorted(pd.Timestamp(str(r["date"])[:10]) for r in split_rows if r.get("date"))
    split_idx = [int(df.index.searchsorted(s)) for s in splits]
    for d in range(1, n):
        move = c[d] / c[d - 1]
        if abs(move - 1.0) <= 0.5:
            continue
        near_split = any(abs(i - d) <= 5 for i in split_idx)
        a0, a1 = adj.iloc[d - 1], adj.iloc[d]
        adj_ok = bool(np.isfinite(a0) and np.isfinite(a1) and a0 > 0 and abs((a1 / a0) / move - 1.0) <= 0.05)
        if d + 5 < n or (final and d < n - 1):
            holds = bool(math.log(c[min(d + 5, n - 1)] / c[d - 1]) / math.log(move) >= 0.5)
        else:
            holds = bool(final)
        ok = (not near_split) and adj_ok and holds
        report["jumps"].append({"date": str(df.index[d].date()), "move": round(move - 1.0, 4),
                                "near_split": near_split, "adjusted_agrees": adj_ok, "holds": holds,
                                "verified": ok})
        if not ok:
            sus[max(0, d - 40):min(n, d + 41)] = True
    return df, sus, report


def income_known(r: dict):
    """S13: the day an income row counts from: its filingDate, or, when FMP's filingDate is missing or on
    or before the period end (a placeholder), the period end + 45 days (Q1-Q3) or + 90 days (Q4)."""
    if not r.get("date"):
        return None
    end = _day(r["date"])
    f = r.get("filingDate")
    if f and _day(f) > end:
        return _day(f)
    return end + np.timedelta64(INCOME_LAG.get(r.get("period"), 90), "D")


def ttm_pe(dates: np.ndarray, close: np.ndarray, income_rows: list, leak: int = 0) -> np.ndarray:
    """S13: C[d] / the sum of epsDiluted of the latest 4 quarters known by date[d-1], when their period ends
    span at most TTM_SPAN days (consecutive quarters)."""
    q = []
    for r in income_rows:
        eps = r.get("epsDiluted", r.get("eps"))
        known = (_day(r["date"]) if r.get("date") else None) if leak == LEAK_EPS_PERIOD_END else income_known(r)
        if eps is None or known is None:
            continue
        q.append((known, _day(r["date"]), float(eps)))
    q.sort()
    out = np.full(len(dates), np.nan)
    if not q or len(dates) < 2:
        return out
    days, ttm = [], []
    seen: dict = {}
    j = 0
    while j < len(q):
        day = q[j][0]
        while j < len(q) and q[j][0] == day:
            seen[q[j][1]] = q[j][2]          # one value per period end, the latest filing wins
            j += 1
        eps = np.nan
        if len(seen) >= 4:
            last4 = sorted(seen)[-4:]
            if int((last4[-1] - last4[0]).astype(int)) <= TTM_SPAN:
                eps = sum(seen[p] for p in last4)
        days.append(day)
        ttm.append(eps if eps > 0 else np.nan)
    k = np.searchsorted(np.array(days), dates[:-1], side="right") - 1      # filings known by date[d-1]
    e = np.where(k >= 0, np.array(ttm)[np.clip(k, 0, None)], np.nan)
    out[1:] = close[1:] / e
    return out


def interest_rates(dates: np.ndarray, mk: Market, leak: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """S10: interest accrued before the open of each bar, y * days / 365, at the yield of the latest
    Treasury row dated on or before the previous bar; and that row's date (the timing audit)."""
    n = len(dates)
    rate = np.zeros(n)
    rate_src = np.full(n, np.datetime64("NaT", "D"), dtype="datetime64[D]")
    if n > 1 and len(mk.tsy_dates):
        ref = dates[1:] if leak == LEAK_RATE_SAME_DAY else dates[:-1]
        yi = np.searchsorted(mk.tsy_dates, ref, side="right") - 1
        ok = yi >= 0
        y = np.where(ok, mk.tsy_y[np.clip(yi, 0, None)], 0.0)
        rate[1:] = y * (dates[1:] - dates[:-1]).astype(int).astype(float) / 365.0
        rate_src[1:] = np.where(ok, mk.tsy_dates[np.clip(yi, 0, None)], np.datetime64("NaT", "D"))
    return rate, rate_src


def build_stock(symbol: str, folder, entity: dict, market: Market, cut=None, leak: int = 0) -> StockData:
    """One stock's inputs from a fetch folder (`<part>/<SYMBOL>.json`). With `cut` (a date), every
    input is cut there, as if the data ended on that day (self-test 1)."""
    folder = Path(folder)
    part = lambda p: read_rows(folder / p / f"{symbol}.json")
    end = market.data_end if cut is None else _day(cut)
    mk = market if cut is None else market.cut(end)
    rows = [r for r in part("full") if _day(r["date"]) <= end]
    bars_from = entity.get("bars_from")
    if bars_from:
        rows = [r for r in rows if _day(r["date"]) >= _day(bars_from)]
    adj_rows = [r for r in part("adj") if _day(r["date"]) <= end]
    split_rows = [r for r in part("split") if _day(r["date"]) <= end]
    df, sus, report = clean_bars(rows, adj_rows, split_rows)
    dates = df.index.values.astype("datetime64[D]")
    n = len(dates)
    c = df["close"].to_numpy(dtype=float)
    # dividends on the first bar on or after the ex-date
    div = np.zeros(n)
    for r in part("div"):
        amt = r.get("adjDividend", r.get("dividend"))
        if amt is None or not r.get("date") or _day(r["date"]) > end:
            continue
        i = int(np.searchsorted(dates, _day(r["date"])))
        if 0 < i < n:
            div[i] += float(amt)
    rate, rate_src = interest_rates(dates, mk, leak)
    # SPY: the latest close on or before the date
    si = np.searchsorted(mk.spy_dates, dates, side="right") - 1
    spy = np.where(si >= 0, mk.spy_close[np.clip(si, 0, None)], np.nan)
    # membership spans [start, end)
    member = np.zeros(n, dtype=bool)
    for a, b in entity.get("spans", []):
        member |= (dates >= _day(a)) & (dates < _day(b))
    m_rows = sorted((_day(r["date"]), float(r["marketCap"])) for r in part("mcap")
                    if r.get("marketCap") is not None and _day(r["date"]) <= end)
    if m_rows:
        md = np.array([a for a, _ in m_rows])
        mv = np.array([b for _, b in m_rows])
        mi = np.searchsorted(md, dates, side="right") - 1
        mcap = np.where(mi >= 0, mv[np.clip(mi, 0, None)], np.nan)
    else:
        mcap = np.full(n, np.nan)
    # income statements count from the day they are known (S13): a cut copy keeps only those known by the cut
    inc = [r for r in part("income") if cut is None or (income_known(r) is not None and income_known(r) <= end)]
    pe = ttm_pe(dates, c, inc, leak=leak)
    rep, unknown = report_dates(part("earn"), part("income"), end, dates[-1] if n else end)
    # delisting (S1): from the full data, never from a cut copy (self-test 1 passes the real flags)
    prof = part("profile")
    active = bool(prof and prof[0].get("isActivelyTrading"))
    spy_after = int(np.sum(market.spy_dates > dates[-1])) if n else 0
    ends_early = spy_after > 5
    if ends_early and not active and report["jumps"]:
        _, sus, report = clean_bars(rows, adj_rows, split_rows, final=True)    # S1: the bars end at the delisting
    return StockData(symbol, dates, df["open"].to_numpy(float), df["high"].to_numpy(float),
                     df["low"].to_numpy(float), c, df["volume"].fillna(0).to_numpy(float), sus, div, rate,
                     rate_src, spy, member, mcap, pe, rep, n - 1, ends_early and not active,
                     ends_early and active, report, unknown)


def report_dates(earn_rows: list, income_rows: list, end, last_date) -> tuple[np.ndarray, np.ndarray]:
    """S12: the earnings report dates up to `end`, without FMP's placeholders (a date equal to one of the
    stock's income period ends), and the spans whose report date is missing: for a quarter with period end
    p, (p, p + REPORT_WINDOW days] when no report date falls in it, that window ends by `end` and it
    starts on or before the stock's last bar."""
    ends = {_day(r["date"]) for r in income_rows if r.get("date")}
    rep = np.array(sorted({_day(r["date"]) for r in earn_rows if r.get("date") and _day(r["date"]) <= end} - ends),
                   dtype="datetime64[D]")
    win = np.timedelta64(REPORT_WINDOW, "D")
    spans = []
    for p in sorted(ends):
        a, b = p + np.timedelta64(1, "D"), p + win
        if b > end or a > last_date:
            continue
        if not np.any((rep >= a) & (rep <= b)):
            spans.append((a, b))
    return rep, np.array(spans, dtype="datetime64[D]").reshape(-1, 2)


# ----------------------------------------------------------------------------- zones and the detector (S0, S7)
def zone_arrays(data: StockData, cache_file=None, rows=None):
    """zones(d) for every bar d >= LOOKBACK - 1 (or only `rows`), as NaN-padded (n, MAX_Z) arrays.
    `cache_file` (.npz) holds earlier results keyed by the md5 of the bars."""
    n = len(data.c)
    bars = pd.DataFrame({"high": data.h, "low": data.l, "close": data.c, "volume": data.v})
    key = hashlib.md5(np.ascontiguousarray(np.c_[data.h, data.l, data.c, data.v]).tobytes()).hexdigest()
    if cache_file is not None and rows is None and Path(cache_file).exists():
        z = np.load(cache_file)
        if str(z["key"]) == key:
            return z["lo"], z["hi"], z["n"]
    z_lo = np.full((n, MAX_Z), np.nan)
    z_hi = np.full((n, MAX_Z), np.nan)
    n_z = np.zeros(n, dtype=np.int64)
    todo = range(LOOKBACK - 1, n) if rows is None else [d for d in rows if d >= LOOKBACK - 1]
    for d in todo:
        zs = NZ.node_zones(bars.iloc[d - LOOKBACK + 1:d + 1], LOOKBACK)
        if len(zs) > MAX_Z:
            raise ValueError(f"{data.symbol} bar {d}: {len(zs)} zones > MAX_Z")
        for j, zz in enumerate(zs):
            z_lo[d, j], z_hi[d, j] = zz["zone_low"], zz["zone_high"]
        n_z[d] = len(zs)
    if cache_file is not None and rows is None:
        np.savez_compressed(cache_file, key=key, lo=z_lo, hi=z_hi, n=n_z)
    return z_lo, z_hi, n_z


@njit(cache=True)
def _nearest_above(zl, zh, nz, x):
    """(low, high) of the zone with the lowest low among those with low > x; NaNs if none."""
    best = -1
    for j in range(nz):
        if zl[j] > x and (best < 0 or zl[j] < zl[best]):
            best = j
    if best < 0:
        return np.nan, np.nan
    return zl[best], zh[best]


@njit(cache=True)
def detector(c, z_lo, z_hi, n_z, first, leak):
    """S7. Returns brk (a breakout confirmed at this close), the broken zone at each confirmation,
    and the state after each close (watched zone, counter)."""
    n = len(c)
    brk = np.zeros(n, dtype=np.bool_)
    b_lo = np.full(n, np.nan)
    b_hi = np.full(n, np.nan)
    s_lo = np.full(n, np.nan)
    s_hi = np.full(n, np.nan)
    s_n = np.zeros(n, dtype=np.int64)
    w_lo, w_hi, cnt = np.nan, np.nan, 0
    for d in range(first, n):
        x = c[d]
        if leak == LEAK_DETECTOR_NEXT and d + 1 < n:
            x = c[d + 1]
        if np.isnan(x):
            break
        if np.isnan(w_lo) or x < w_lo:
            w_lo, w_hi = _nearest_above(z_lo[d], z_hi[d], n_z[d], x)
            cnt = 0
        elif x > w_hi:
            cnt += 1
            if cnt == 2:
                brk[d] = True
                b_lo[d], b_hi[d] = w_lo, w_hi
                w_lo, w_hi = _nearest_above(z_lo[d], z_hi[d], n_z[d], x)
                cnt = 0
        else:
            cnt = 0
        s_lo[d], s_hi[d], s_n[d] = w_lo, w_hi, cnt
    return brk, b_lo, b_hi, s_lo, s_hi, s_n


@dataclass
class Prepared:
    data: StockData
    z_lo: np.ndarray
    z_hi: np.ndarray
    n_z: np.ndarray
    brk: np.ndarray
    b_lo: np.ndarray
    b_hi: np.ndarray
    det: tuple                     # detector state after each close (watched low, high, counter)


def prepare(data: StockData, zones=None, cache_file=None, leak: int = 0) -> Prepared:
    z_lo, z_hi, n_z = zones if zones is not None else zone_arrays(data, cache_file)
    brk, b_lo, b_hi, s_lo, s_hi, s_n = detector(data.c, z_lo, z_hi, n_z, LOOKBACK - 1, leak)
    return Prepared(data, z_lo, z_hi, n_z, brk, b_lo, b_hi, (s_lo, s_hi, s_n))


# ----------------------------------------------------------------------------- ladders (S4, S6)
@njit(cache=True)
def zone_ladder(zl, zh, nz, p, kmax, px, lo, forced_lo, forced_hi):
    """S4 into px/lo (shallowest first); returns m. forced_*: a first level (breakout start, S13)."""
    m = 0
    prev = np.inf
    if not np.isnan(forced_hi):
        px[0], lo[0] = forced_hi, forced_lo
        prev = forced_hi
        m = 1
    used = np.zeros(max(nz, 1), dtype=np.bool_)
    while m < kmax:
        best = -1
        for j in range(nz):
            if not used[j] and zh[j] < p and (best < 0 or zh[j] > zh[best]):
                best = j
        if best < 0:
            break
        used[best] = True
        x = zh[best]
        if x < (1.0 - MAX_DEPTH) * p:
            break
        if x > (1.0 - MIN_GAP) * p or x > (1.0 - MIN_GAP) * prev:
            continue
        px[m], lo[m] = x, zl[best]
        prev = x
        m += 1
    return m


@njit(cache=True)
def sma_merge(px, lo, is_sma, m, kmax, s, p):
    """S13 200-day variant: merge or insert the 200-day level s into the ladder; returns m."""
    for i in range(K_MAX + 1):
        is_sma[i] = False
    if np.isnan(s) or s > (1.0 - MIN_GAP) * p or s < (1.0 - MAX_DEPTH) * p:
        return m
    best = -1
    for i in range(m):
        if abs(px[i] / s - 1.0) <= MIN_GAP and (best < 0 or abs(px[i] / s - 1.0) < abs(px[best] / s - 1.0)):
            best = i
    if best >= 0:
        px[best] = s
        is_sma[best] = True
        return m
    pos = m
    for i in range(m):
        if s > px[i]:
            pos = i
            break
    for i in range(m, pos, -1):
        px[i], lo[i], is_sma[i] = px[i - 1], lo[i - 1], is_sma[i - 1]
    px[pos], lo[pos], is_sma[pos] = s, np.nan, True
    m += 1
    if m > kmax:
        # drop the deepest zone level
        for i in range(m - 1, -1, -1):
            if not is_sma[i]:
                for q in range(i, m - 1):
                    px[q], lo[q], is_sma[q] = px[q + 1], lo[q + 1], is_sma[q + 1]
                break
        m = kmax
    return m


@njit(cache=True)
def _gap_level(zl, zh, nz, li, loi, cap, p, uu, ud, us):
    """S6 gaps only: the level's point in the nearest gap above or below (NaN if neither exists)."""
    x = li
    changed = True
    while changed:
        changed = False
        for j in range(nz):
            if zl[j] <= x and zh[j] > x:
                x = zh[j]
                changed = True
    up_lo = x
    up_hi = np.inf
    for j in range(nz):
        if zl[j] > up_lo and zl[j] < up_hi:
            up_hi = zl[j]
    up_hi = min(up_hi, cap)
    has_up = up_hi > up_lo * (1.0 + 1e-12)
    y = loi
    changed = True
    while changed:
        changed = False
        for j in range(nz):
            if zh[j] >= y and zl[j] < y:
                y = zl[j]
                changed = True
    dn_hi = min(y, cap)
    dn_lo = 0.0
    for j in range(nz):
        if zh[j] < y and zh[j] > dn_lo:
            dn_lo = zh[j]
    dn_lo = max(dn_lo, (1.0 - MAX_DEPTH) * p)
    has_dn = dn_hi > dn_lo * (1.0 + 1e-12)
    up = up_lo + uu * (up_hi - up_lo) if has_up else np.nan
    dn = dn_lo + ud * (dn_hi - dn_lo) if has_dn else np.nan
    if has_up and has_dn:
        a = up / li - 1.0
        b = 1.0 - dn / li
        return up if us < b / (a + b) else dn
    if has_up:
        return up
    if has_dn:
        return dn
    return np.nan


@njit(cache=True)
def group_levels(kind, p, m, px, lo, is_sma, row, U, sgn, placebo_u, sig_d, fixed_depth,
                 zl, zh, nz, cap1, gl, gband, gfac):
    """S6: the group's own levels for the zone ladder px/lo (m levels) at reference price p.
    gl: order prices (NaN: no order); gband: band lows (close confirm); gfac: the 200-day factor.
    Returns the number of capped up-shifts (diagnostic) or -1 for each level without a gap."""
    note = 0
    for i in range(K_MAX):
        gl[i], gband[i], gfac[i] = np.nan, np.nan, 1.0
    if m == 0:
        return 0
    if kind == ZONE:
        for i in range(m):
            gl[i], gband[i] = px[i], lo[i]
    elif kind == RANDOM or kind == PLACEBO:
        hi = 0.02
        if m >= 2:
            g = 1.0
            for i in range(m - 1):
                gi = 1.0 - px[i + 1] / px[i]
                if gi < g:
                    g = gi
            hi = g / 2.0
        if hi < 0.01:
            hi = 0.01
        u = U[row, 0] if kind == RANDOM else placebo_u
        delta = 0.01 + u * (hi - 0.01)
        for i in range(m):
            f = 1.0 + sgn * delta
            x = px[i] * f
            if i == 0 and sgn > 0 and x > cap1:         # a 200-day level 1 too (S6, S13)
                x = cap1
                f = cap1 / px[0]
                note += 1
            gl[i] = x
            gband[i] = lo[i] * f
            gfac[i] = f
    elif kind == GAPS:
        prev = np.nan
        for i in range(m):
            cap = cap1 if np.isnan(prev) else (1.0 - MIN_GAP) * prev
            x = _gap_level(zl, zh, nz, px[i], lo[i], cap, p, U[row, 3 * i], U[row, 3 * i + 1], U[row, 3 * i + 2])
            gl[i] = x
            if np.isnan(x):
                note -= 1
            else:
                prev = x
    elif kind == VOL:
        for i in range(m):
            x = p * (1.0 - VOL_STEP * (i + 1) * sig_d)
            if x > 0.05 * p:
                gl[i] = x
    elif kind == FIXED:
        for i in range(m):
            gl[i] = p * (1.0 - fixed_depth[i])
    return note


# ----------------------------------------------------------------------------- the kernel (S5-S11)
@njit(cache=True)
def _ev(events, ne, bar, code, price, qty, part, aux, band=np.nan):
    if ne < events.shape[0]:
        events[ne, 0], events[ne, 1], events[ne, 2] = bar, code, price
        events[ne, 3], events[ne, 4], events[ne, 5], events[ne, 6] = qty, part, aux, band
    return ne + 1


@njit(cache=True)
def run_group(kind, t, end, last, delisted,
              o, h, l, c, sma200, sig3m, div, rate, spy,
              z_lo, z_hi, n_z, brk, b_lo, b_hi,
              weights, stop_pct, fill_mode, time_limit, sma_level, start_on_breakout, fixed_depth,
              U, sgn, placebo_u, floor_in, stop_at, leak,
              out, floor_out, events, state):
    """One group (one random draw) of the plan started at the close of t, simulated to bar `end`
    (= min(t + H_MAX, last)). Writes out[horizon, field], floor_out (zone group: the floor in force
    during each bar, S8), events and, when stop_at >= t, the state after the close of stop_at.
    Returns the number of events, or -1 when a plan needs more than MAX_ROWS placements."""
    k = len(weights)
    ne = 0
    for i in range(4):
        for f in range(N_OUT):
            out[i, f] = np.nan
    st = np.zeros(K_MAX, dtype=np.int64)            # 0 no order, 1 live order, 2 filled
    px = np.full(K_MAX, np.nan)                     # order price (fixed levels)
    band = np.full(K_MAX, np.nan)
    fac = np.ones(K_MAX)                            # 200-day parts: factor on SMA200
    smap = np.zeros(K_MAX, dtype=np.bool_)
    created = np.full(K_MAX, -1, dtype=np.int64)
    r_lo = np.full(K_MAX, np.nan)                   # reference ladder R: zone low per part (zone group)
    lpx = np.empty(K_MAX + 1)
    llo = np.empty(K_MAX + 1)
    lsma = np.zeros(K_MAX + 1, dtype=np.bool_)
    gl = np.empty(K_MAX)
    gband = np.empty(K_MAX)
    gfac = np.empty(K_MAX)
    cash, shares, cnum, shb, spent = 1.0, 0.0, 0.0, 0.0, 0.0
    fills, sales = 0, 0
    scaling = False
    base = 0.0
    t_lo, t_hi, t_px, used_hi = np.nan, np.nan, np.nan, 0.0
    pend, pend_q, pend_bar = 0, 0.0, -1              # pend: 1 stop sale, 2 scale-out sale
    ended, stopped = False, False
    worst = 0.0
    row = 0                                         # placement row = confirmations since t
    sh_prev = 0.0                                   # shares at the previous close
    is_ladder = kind != LUMP

    # ------------------------------------------------------------ the start, at the close of t
    if kind == LUMP:
        sh = (1.0 - COST) / c[t]
        shares, cash, spent, cnum, shb, fills = sh, 0.0, 1.0, c[t] * sh, sh, 1
        for j in range(k):
            st[j] = 2
        ne = _ev(events, ne, t, EV_LUMP_BUY, c[t], sh, -1, t)
    else:
        zr = t + 1 if (leak == LEAK_ZONES_NEXT and t + 1 < len(c)) else t
        f_lo, f_hi = np.nan, np.nan
        if start_on_breakout:
            f_lo, f_hi = b_lo[t], b_hi[t]
        m = zone_ladder(z_lo[zr], z_hi[zr], n_z[zr], c[t], k, lpx, llo, f_lo, f_hi)
        for i in range(K_MAX + 1):
            lsma[i] = False
        if sma_level:
            m = sma_merge(lpx, llo, lsma, m, k, sma200[t], c[t])
        cap1 = (1.0 - BREAKOUT_CAP) * c[t] if start_on_breakout else (1.0 - MIN_GAP) * c[t]
        sg = sig3m[t + 1] if (leak == LEAK_VOL_NEXT_SIGMA and t + 1 < len(c)) else sig3m[t]
        note = group_levels(kind, c[t], m, lpx, llo, lsma, 0, U, sgn, placebo_u, sg, fixed_depth,
                            z_lo[zr], z_hi[zr], n_z[zr], cap1, gl, gband, gfac)
        if note > 0:
            ne = _ev(events, ne, t, EV_CAP, gl[0], note, 0, 0)
        q = 0
        for j in range(k):
            if q < m and not np.isnan(gl[q]):
                st[j], px[j], band[j], fac[j], smap[j], created[j] = 1, gl[q], gband[q], gfac[q], lsma[q], t
                ne = _ev(events, ne, t, EV_ORDER, gl[q], weights[j], j, 0, gband[q])
            elif q < m and kind == GAPS:
                ne = _ev(events, ne, t, EV_NO_GAP, np.nan, weights[j], j, 0)
            if kind == ZONE:
                r_lo[j] = llo[q] if q < m else np.nan
            q += 1
    fl_now = np.nan
    if kind == ZONE:
        fl_now = np.inf
        for j in range(k):
            if not np.isnan(r_lo[j]) and r_lo[j] < fl_now:
                fl_now = r_lo[j]
        if fl_now == np.inf:
            fl_now = np.nan
        floor_out[t] = fl_now
    sh_prev = shares

    if stop_at == t:
        _dump(state, cash, shares, cnum, shb, spent, fills, sales, scaling, base, t_lo, t_hi, t_px, used_hi,
              pend, pend_q, pend_bar, ended, stopped, worst, row, fl_now, st, px, band, fac, smap, created, r_lo)
        return ne

    d = t
    while d < end:
        d += 1
        fl_prev = floor_out[d - 1] if kind == ZONE else floor_in[d - 1]
        if kind == ZONE:
            floor_out[d] = fl_now                 # R as it stood after the close of d-1
        fl_d = floor_out[d] if kind == ZONE else floor_in[d]
        # -------- (1) pre-open: interest, then dividends on the shares of the previous close
        cash *= 1.0 + rate[d]
        if div[d] > 0 and leak != LEAK_DIVIDEND_NEW_SHARES and sh_prev > 0:
            cash += sh_prev * div[d]
            ne = _ev(events, ne, d, EV_DIVIDEND, div[d], sh_prev, -1, d - 1)
        # -------- (2) open: sales signalled at the close of d-1
        if pend == 1:
            cash += shares * o[d] * (1.0 - COST)
            ne = _ev(events, ne, d, EV_STOP_SALE, o[d], shares, -1, pend_bar)
            shares = 0.0
            stopped, ended = True, True
        elif pend == 2:
            qn = min(pend_q, shares)
            cash += qn * o[d] * (1.0 - COST)
            shares -= qn
            sales += 1
            ne = _ev(events, ne, d, EV_SALE, o[d], qn, -1, pend_bar)
            if sales >= 3 or shares <= 1e-12:
                shares = 0.0
                ended = True
        pend, pend_q = 0, 0.0
        # -------- (3) intraday: limits created at or before the close of d-1
        if not ended and not scaling and is_ladder:
            for j in range(k):
                if st[j] == 1 and not smap[j] and fill_mode == LIMIT and l[d] <= px[j]:
                    fp = min(o[d], px[j])
                    sh = weights[j] * (1.0 - COST) / fp
                    shares += sh
                    cash -= weights[j]
                    spent += weights[j]
                    cnum += fp * sh
                    shb += sh
                    fills += 1
                    st[j] = 2
                    ne = _ev(events, ne, d, EV_FILL, fp, sh, j, created[j], band[j])
        # -------- (4a) close-priced buys
        if not ended and not scaling and is_ladder:
            for j in range(k):
                if st[j] != 1:
                    continue
                fp = np.nan
                if smap[j]:
                    s_ = sma200[d] * fac[j]
                    if l[d] <= s_ and c[d] >= s_:
                        fp = c[d]
                elif fill_mode == CLOSE_CONFIRM:
                    if l[d] <= px[j] and c[d] >= band[j]:
                        fp = c[d]
                if not np.isnan(fp):
                    sh = weights[j] * (1.0 - COST) / fp
                    shares += sh
                    cash -= weights[j]
                    spent += weights[j]
                    cnum += fp * sh
                    shb += sh
                    fills += 1
                    st[j] = 2
                    ne = _ev(events, ne, d, EV_CLOSE_FILL, fp, sh, j, created[j], band[j])
            if time_limit and d - t == TIME_LIMIT_BARS and fills == 0 and c[d] > sma200[d]:
                sh = weights[0] * (1.0 - COST) / c[d]
                shares += sh
                cash -= weights[0]
                spent += weights[0]
                cnum += c[d] * sh
                shb += sh
                fills += 1
                st[0] = 2
                ne = _ev(events, ne, d, EV_TIME_BUY, c[d], sh, 0, d, band[0])
        # -------- (4b) avgcost and the safety margin
        avg = cnum / shb if shb > 0 else np.nan
        if shares > 0:
            w_ = l[d] / avg - 1.0
            if w_ < worst:
                worst = w_
        # -------- (4c) stop
        stop_now = False
        if not ended and shares > 0:
            cc = c[d + 1] if (leak == LEAK_STOP_NEXT_CLOSE and d + 1 < len(c)) else c[d]
            sp = spy[d + 1] if (leak == LEAK_SPY_NEXT and d + 1 < len(c)) else spy[d]
            c1 = cc <= (1.0 - stop_pct) * avg
            c2 = (not np.isnan(fl_d)) and (not np.isnan(fl_prev)) and c[d] < fl_d and c[d - 1] < fl_prev
            c3 = (c[d] / c[t] - 1.0) - (sp / spy[t] - 1.0) < -SPY_LAG
            if c1 and c2 and c3:
                stop_now = True
                pend, pend_q, pend_bar = 1, shares, d
                for j in range(k):
                    if st[j] == 1:
                        st[j] = 0
                ne = _ev(events, ne, d, EV_STOP_SIGNAL, c[d], shares, -1, d)
                if leak == LEAK_STOP_SAME_OPEN:
                    cash += shares * o[d] * (1.0 - COST)
                    ne = _ev(events, ne, d, EV_STOP_SALE, o[d], shares, -1, d)
                    shares = 0.0
                    stopped, ended = True, True
                    pend = 0
        # -------- (4d) scaling out
        zr = d + 1 if (leak == LEAK_ZONES_NEXT and d + 1 < len(c)) else d
        if not ended and shares > 0 and not stop_now:
            if not scaling:
                if c[d] >= SCALE_AT * avg:
                    scaling = True
                    base = shares
                    for j in range(k):
                        if st[j] == 1:
                            st[j] = 0
                    t_lo, t_hi = _nearest_above(z_lo[zr], z_hi[zr], n_z[zr], max(c[d], used_hi))
                    t_px = c[d] * (1.0 + sig3m[d]) if np.isnan(t_lo) else np.nan
                    ne = _ev(events, ne, d, EV_SCALE_START, c[d], shares, -1, d)
            else:
                hit = (c[d] >= t_lo) if not np.isnan(t_lo) else (c[d] >= t_px)
                if hit:
                    pend, pend_bar = 2, d
                    pend_q = base / 3.0 if sales < 2 else shares
                    if not np.isnan(t_lo):
                        used_hi = t_hi
                    t_lo, t_hi = _nearest_above(z_lo[zr], z_hi[zr], n_z[zr], max(c[d], used_hi))
                    t_px = c[d] * (1.0 + sig3m[d]) if np.isnan(t_lo) else np.nan
                    ne = _ev(events, ne, d, EV_SALE_SIGNAL, c[d], pend_q, -1, d)
        # -------- (4e) breakout re-placement and R
        if brk[d]:
            row += 1
            m = zone_ladder(z_lo[zr], z_hi[zr], n_z[zr], c[d], k, lpx, llo, np.nan, np.nan)
            for i in range(K_MAX + 1):
                lsma[i] = False
            if sma_level:
                m = sma_merge(lpx, llo, lsma, m, k, sma200[d], c[d])
            if kind == ZONE:
                q = 0
                for j in range(k):
                    if st[j] == 2:
                        continue
                    r_lo[j] = llo[q] if q < m else np.nan
                    q += 1
            if is_ladder and not ended and not scaling and pend == 0:
                unfilled = 0
                for j in range(k):
                    if st[j] != 2:
                        unfilled += 1
                if unfilled > 0:
                    if row >= MAX_ROWS:
                        return -1
                    sg = sig3m[d + 1] if (leak == LEAK_VOL_NEXT_SIGMA and d + 1 < len(c)) else sig3m[d]
                    note = group_levels(kind, c[d], m, lpx, llo, lsma, row, U, sgn, placebo_u, sg, fixed_depth,
                                        z_lo[zr], z_hi[zr], n_z[zr], (1.0 - MIN_GAP) * c[d], gl, gband, gfac)
                    if note > 0:
                        ne = _ev(events, ne, d, EV_CAP, gl[0], note, 0, row)
                    q = 0
                    for j in range(k):
                        if st[j] == 2:
                            continue
                        if q < m and not np.isnan(gl[q]):
                            st[j], px[j], band[j], fac[j], smap[j], created[j] = 1, gl[q], gband[q], gfac[q], lsma[q], d
                            ne = _ev(events, ne, d, EV_ORDER, gl[q], weights[j], j, row, gband[q])
                        else:
                            st[j], px[j], created[j] = 0, np.nan, -1
                            if q < m and kind == GAPS:
                                ne = _ev(events, ne, d, EV_NO_GAP, np.nan, weights[j], j, row)
                        q += 1
            if kind == ZONE:
                fl_now = np.inf
                for j in range(k):
                    if not np.isnan(r_lo[j]) and r_lo[j] < fl_now:
                        fl_now = r_lo[j]
                if fl_now == np.inf:
                    fl_now = np.nan
        if leak == LEAK_SAME_BAR_FILL and is_ladder and not ended and not scaling:
            for j in range(k):
                if st[j] == 1 and created[j] == d and not smap[j] and l[d] <= px[j]:
                    fp = min(o[d], px[j])
                    sh = weights[j] * (1.0 - COST) / fp
                    shares += sh
                    cash -= weights[j]
                    spent += weights[j]
                    cnum += fp * sh
                    shb += sh
                    fills += 1
                    st[j] = 2
                    ne = _ev(events, ne, d, EV_FILL, fp, sh, j, created[j], band[j])
        if leak == LEAK_DIVIDEND_NEW_SHARES and div[d] > 0 and shares > 0:
            cash += shares * div[d]
            ne = _ev(events, ne, d, EV_DIVIDEND, div[d], shares, -1, d - 1)
        # -------- (4f) the horizons
        hd = d - t
        for i in range(4):
            if hd == HORIZONS[i]:
                out[i, 0] = shares * c[d] + cash
                out[i, 1] = spent
                out[i, 2] = cnum / shb if shb > 0 else np.nan
                out[i, 3] = worst
                out[i, 4] = 1.0 if stopped else 0.0
                out[i, 5] = fills
                out[i, 6] = 1.0 if scaling else 0.0
                out[i, 7] = sales
                out[i, 8] = 1.0 if ended else 0.0
                out[i, 9] = ST_BAR
        # -------- (4g) delisting: sell at this close
        if delisted and d == last:
            if shares > 0:
                cash += shares * c[d] * (1.0 - COST)
                ne = _ev(events, ne, d, EV_LIQUIDATE, c[d], shares, -1, d)
                shares = 0.0
            for j in range(k):
                if st[j] == 1:
                    st[j] = 0
            pend, pend_q = 0, 0.0
            ended = True
            for i in range(4):
                if HORIZONS[i] > hd:
                    out[i, 0] = cash
                    out[i, 1] = spent
                    out[i, 2] = cnum / shb if shb > 0 else np.nan
                    out[i, 3] = worst
                    out[i, 4] = 1.0 if stopped else 0.0
                    out[i, 5] = fills
                    out[i, 6] = 1.0 if scaling else 0.0
                    out[i, 7] = sales
                    out[i, 8] = 1.0
                    out[i, 9] = ST_DELISTED
        sh_prev = shares
        if d == stop_at:
            _dump(state, cash, shares, cnum, shb, spent, fills, sales, scaling, base, t_lo, t_hi, t_px, used_hi,
                  pend, pend_q, pend_bar, ended, stopped, worst, row, fl_now, st, px, band, fac, smap, created, r_lo)
            return ne
    return ne


@njit(cache=True)
def _dump(state, cash, shares, cnum, shb, spent, fills, sales, scaling, base, t_lo, t_hi, t_px, used_hi,
          pend, pend_q, pend_bar, ended, stopped, worst, row, fl_now, st, px, band, fac, smap, created, r_lo):
    state[0], state[1], state[2], state[3], state[4] = cash, shares, cnum, shb, spent
    state[5], state[6], state[7], state[8] = float(fills), float(sales), 1.0 if scaling else 0.0, base
    state[9], state[10], state[11], state[12] = t_lo, t_hi, t_px, used_hi
    state[13], state[14], state[15] = float(pend), pend_q, float(pend_bar)
    state[16], state[17], state[18] = 1.0 if ended else 0.0, 1.0 if stopped else 0.0, worst
    state[19], state[20], state[21] = float(row), fl_now, 0.0
    for j in range(K_MAX):
        b = 22 + 7 * j
        state[b], state[b + 1], state[b + 2], state[b + 3] = float(st[j]), px[j], band[j], fac[j]
        state[b + 4], state[b + 5], state[b + 6] = 1.0 if smap[j] else 0.0, float(created[j]), r_lo[j]


# ----------------------------------------------------------------------------- random numbers (S16)
def seed_of(tag: str) -> int:
    return int.from_bytes(hashlib.sha256(tag.encode("utf-8")).digest()[:8], "big")


def uniform_table(tag: str) -> np.ndarray:
    return np.random.Generator(np.random.PCG64(seed_of(tag))).random((MAX_ROWS, N_U))


def placebo_draw(symbol: str, k: int, master: int = MASTER) -> tuple[float, float]:
    u = np.random.Generator(np.random.PCG64(seed_of(f"{master}|{symbol}|placebo|{k}"))).random(2)
    return (1.0 if u[0] < 0.5 else -1.0), float(u[1])


# ----------------------------------------------------------------------------- plans
@dataclass
class Config:
    ratio: tuple = (1, 2, 4, 1)
    stop_pct: float = 0.15
    fill_mode: int = LIMIT
    time_limit: bool = False
    sma_level: bool = False
    start_on_breakout: bool = False
    name: str = "main"

    def weights(self) -> np.ndarray:
        w = np.asarray(self.ratio, dtype=float)
        return w / w.sum()


START_WHY = ("", "fewer than 756 bars", "not a member", "below the 200-day average", "the stock's last bar")


def start_mask(prep: Prepared, cfg: Config, leak: int = 0) -> np.ndarray:
    """S3 (or S13 breakout start) decided at each close: -1 not a candidate bar, 0 a plan starts,
    1.. the first failed condition (START_WHY). Uses nothing after the bar (self-test 1)."""
    d = prep.data
    n = len(d.c)
    out = np.full(n, -1, dtype=np.int64)
    dates = pd.DatetimeIndex(d.dates)
    month = (dates.year * 12 + dates.month).to_numpy()
    first = np.r_[True, month[1:] != month[:-1]] if n else np.zeros(0, bool)
    seen = set()
    for t in range(n):
        if dates[t] < FRAME_START:
            continue
        if cfg.start_on_breakout:
            if not prep.brk[t] or month[t] in seen:
                continue
        elif not first[t]:
            continue
        sma = d.sma200[t + 1] if (leak == LEAK_START_SMA_NEXT and t + 1 < n) else d.sma200[t]
        why = 0
        if t < LOOKBACK - 1:
            why = 1
        elif not (d.member[t] or (np.isfinite(d.mcap[t]) and d.mcap[t] > 200e9)):
            why = 2
        elif not (np.isfinite(sma) and d.c[t] > sma):
            why = 3
        out[t] = why
        if why == 0 and cfg.start_on_breakout:
            seen.add(month[t])
    return out


def plan_starts(prep: Prepared, cfg: Config, leak: int = 0) -> tuple[list, dict]:
    """The plans (start bars before the stock's last bar) and why other candidates did not start (a start
    on the last bar has no bar left to run on, S3)."""
    m = start_mask(prep, cfg, leak)
    keep, skipped = [], {}
    for t in np.flatnonzero(m >= 0):
        why = 4 if (m[t] == 0 and t >= prep.data.last) else int(m[t])
        if why == 0:
            keep.append(int(t))
        else:
            skipped[START_WHY[why]] = skipped.get(START_WHY[why], 0) + 1
    return keep, skipped


def plan_end(prep: Prepared, t: int) -> int:
    return min(t + H_MAX, prep.data.last)


_DUMMY: dict = {}


def _floor_buffer(prep: Prepared, kind: int) -> np.ndarray:
    """The zone group writes its floor series into a fresh array; other groups never write theirs."""
    n = len(prep.data.c)
    if kind == ZONE:
        return np.full(n, np.nan)
    if _DUMMY.get("n") != n:
        _DUMMY["n"], _DUMMY["a"] = n, np.full(n, np.nan)
    return _DUMMY["a"]


def run_one(prep: Prepared, t: int, kind: int, cfg: Config, U=None, sgn=0.0, placebo_u=np.nan,
            floor=None, fixed_depth=None, stop_at=-1, leak=0):
    """One group of one plan; returns (out, floor_out, events, state)."""
    d = prep.data
    out, events, state = np.full((4, N_OUT), np.nan), np.zeros((512, EV_COLS)), np.zeros(N_STATE)
    floor_out = _floor_buffer(prep, kind)
    if U is None:
        U = np.zeros((MAX_ROWS, N_U))
    fd = np.zeros(K_MAX) if fixed_depth is None else np.asarray(fixed_depth, dtype=float)
    fl = floor if floor is not None else np.full(len(d.c), np.nan)
    ne = run_group(kind, t, plan_end(prep, t), d.last, d.delisted,
                   d.o, d.h, d.l, d.c, d.sma200, d.sig3m, d.div, d.rate, d.spy,
                   prep.z_lo, prep.z_hi, prep.n_z, prep.brk, prep.b_lo, prep.b_hi,
                   cfg.weights(), cfg.stop_pct, cfg.fill_mode, cfg.time_limit, cfg.sma_level,
                   cfg.start_on_breakout, fd, U, float(sgn), float(placebo_u), fl, stop_at, leak,
                   out, floor_out, events, state)
    if ne < 0:
        raise RuntimeError(f"{d.symbol} plan at {d.dates[t]}: more than {MAX_ROWS} placements")
    if ne > events.shape[0]:
        raise RuntimeError(f"{d.symbol} plan at {d.dates[t]}: {ne} events overflow the buffer")
    return out, floor_out, events[:ne].copy(), state


def random_tables(symbol: str, t_date, n_pairs: int = 10, group: str = "random", master: int = MASTER):
    day = str(np.datetime64(t_date, "D"))
    return [uniform_table(f"{master}|{symbol}|{day}|{group}|{q}") for q in range(1, n_pairs + 1)]


def run_plan(prep: Prepared, t: int, cfg: Config, groups=("zone", "random"), fixed_depth=None,
             n_pairs: int = 10, n_gaps: int = 20, placebos=(), stop_at=-1, leak=0, master: int = MASTER):
    """Every requested group of one plan (the zone group always runs first: its R gives the floor).
    Returns {name: [(out, events, state), ...]} with one entry per draw, and the floor series."""
    d = prep.data
    res = {}
    z_out, floor, z_ev, z_state = run_one(prep, t, ZONE, cfg, stop_at=stop_at, leak=leak)
    res["zone"] = [(z_out, z_ev, z_state)]
    day = d.dates[t]
    if "random" in groups:
        draws = []
        for U in random_tables(d.symbol, day, n_pairs, "random", master):
            for s in (1.0, -1.0):
                o_, _, ev, stt = run_one(prep, t, RANDOM, cfg, U=U, sgn=s, floor=floor, stop_at=stop_at, leak=leak)
                draws.append((o_, ev, stt))
        res["random"] = draws
    if "gaps" in groups:
        draws = []
        for j in range(1, n_gaps + 1):
            U = uniform_table(f"{master}|{d.symbol}|{str(day)}|gaps|{j}")
            o_, _, ev, stt = run_one(prep, t, GAPS, cfg, U=U, floor=floor, stop_at=stop_at, leak=leak)
            draws.append((o_, ev, stt))
        res["gaps"] = draws
    for name, kind in (("vol", VOL), ("fixed", FIXED), ("lump", LUMP)):
        if name in groups:
            o_, _, ev, stt = run_one(prep, t, kind, cfg, floor=floor, fixed_depth=fixed_depth, stop_at=stop_at,
                                     leak=leak)
            res[name] = [(o_, ev, stt)]
    if placebos:
        draws = []
        for k in placebos:
            s, u = placebo_draw(d.symbol, k, master)
            o_, _, ev, stt = run_one(prep, t, PLACEBO, cfg, sgn=s, placebo_u=u, floor=floor, stop_at=stop_at,
                                     leak=leak)
            draws.append((o_, ev, stt))
        res["placebo"] = draws
    return res, floor


def start_ladder(prep: Prepared, t: int, k: int = K_MAX) -> tuple[np.ndarray, np.ndarray]:
    """The zone ladder at a plan start (prices, zone lows): the fixed group's depths (S6)."""
    px, lo = np.empty(K_MAX + 1), np.empty(K_MAX + 1)
    m = zone_ladder(prep.z_lo[t], prep.z_hi[t], prep.n_z[t], prep.data.c[t], k, px, lo, np.nan, np.nan)
    return px[:m].copy(), lo[:m].copy()


EXCL_WHY = ("", "past the data end", "data gap", "suspicious bars")


def excluded(prep: Prepared, t: int, h: int, growth=None) -> str | None:
    """S1/S3/S10: why the plan at t is not counted at horizon h (None: counted). For a delisted stock
    the horizon is counted on SPY's bars after its last bar (`growth`, from delist_growth)."""
    d = prep.data
    if d.suspicious[max(0, t - LOOKBACK + 1):min(t + h, d.last) + 1].any():
        return "suspicious bars"
    if t + h > d.last:
        if d.delisted:
            n = t + h - d.last
            if growth is not None and n <= H_MAX and np.isfinite(growth[n]):
                return None
            return "past the data end"
        return "data gap" if d.gap_end else "past the data end"
    return None


def earnings_bars(data: StockData) -> np.ndarray:
    """S12: G = the first bar on or after each report date, and the bar after it."""
    g = np.zeros(len(data.c), dtype=bool)
    for r in data.report_dates:
        i = int(np.searchsorted(data.dates, r))
        for j in (i, i + 1):
            if 0 <= j < len(g):
                g[j] = True
    return g


def earnings_flag(events: np.ndarray, g: np.ndarray, upto: int) -> bool:
    """S12: a fill on a bar in G, or a stop whose signal close, the close before it or its sale bar is in G
    (bars up to `upto`)."""
    for e in events:
        bar, code = int(e[0]), int(e[1])
        if bar > upto:
            continue
        if code in (EV_FILL, EV_CLOSE_FILL, EV_TIME_BUY, EV_LUMP_BUY) and g[bar]:
            return True
        if code == EV_STOP_SALE:
            sig = int(e[5])
            if any(0 <= b < len(g) and g[b] for b in (sig - 1, sig, bar)):
                return True
    return False


def first_earnings_bar(events: np.ndarray, g: np.ndarray) -> int:
    """S12: the first bar at which a run carries an earnings flag (NO_BAR if none);
    earnings_flag(events, g, upto) == (first_earnings_bar(events, g) <= upto)."""
    if len(events) == 0:
        return NO_BAR
    n = len(g)
    bars = events[:, 0].astype(np.int64)
    codes = events[:, 1].astype(np.int64)
    sig = events[:, 5].astype(np.int64)
    at = lambda b: np.where((b >= 0) & (b < n), g[np.clip(b, 0, n - 1)], False)
    buy = np.isin(codes, (EV_FILL, EV_CLOSE_FILL, EV_TIME_BUY, EV_LUMP_BUY)) & at(bars)
    stop = (codes == EV_STOP_SALE) & (at(sig - 1) | at(sig) | at(bars))
    hit = bars[buy | stop]
    return int(hit.min()) if len(hit) else NO_BAR


NO_BAR = 1 << 40


def delist_growth(data: StockData, market: Market) -> np.ndarray:
    """S10: growth of cash over n = 0..H_MAX SPY bars after a delisted stock's last bar, at the yield
    dated on or before each previous day (NaN once the bars run past the data end)."""
    g = np.full(H_MAX + 1, np.nan)
    g[0] = 1.0
    if not data.delisted:
        return g
    prev = data.dates[data.last]
    i0 = int(np.searchsorted(market.spy_dates, prev, side="right"))
    acc = 1.0
    for n in range(1, H_MAX + 1):
        i = i0 + n - 1
        if i >= len(market.spy_dates):
            break
        y, _ = market.yield_asof(prev)
        day = market.spy_dates[i]
        acc *= 1.0 + y * float((day - prev).astype(int)) / 365.0
        g[n] = acc
        prev = day
    return g


def horizon_dates(data: StockData, market: Market, t: int) -> np.ndarray:
    """The date of t + h for each horizon (on SPY's calendar after a delisted stock's last bar; NaT
    past the data end)."""
    out = np.full(4, np.datetime64("NaT", "D"), dtype="datetime64[D]")
    for i, h in enumerate(HORIZONS):
        if t + h <= data.last:
            out[i] = data.dates[t + h]
        elif data.delisted:
            j = int(np.searchsorted(market.spy_dates, data.dates[data.last], side="right")) + (t + h - data.last) - 1
            if j < len(market.spy_dates):
                out[i] = market.spy_dates[j]
    return out


def plan_returns(out: np.ndarray, t: int, data: StockData, growth: np.ndarray) -> np.ndarray:
    """R_h = V_h - 1 for the four horizons (S10); a delisted group's cash grows on SPY's calendar."""
    r = np.full(4, np.nan)
    for i, h in enumerate(HORIZONS):
        if out[i, 9] == ST_BAR:
            r[i] = out[i, 0] - 1.0
        elif out[i, 9] == ST_DELISTED:
            n = t + h - data.last
            if 0 <= n <= H_MAX:
                r[i] = out[i, 0] * growth[n] - 1.0
    return r


def start_orders(events: np.ndarray, t: int, k: int) -> np.ndarray:
    """Order price per part placed at the start close (NaN: no order)."""
    px = np.full(k, np.nan)
    for e in events:
        if int(e[0]) != t:
            break
        if int(e[1]) == EV_ORDER:
            px[int(e[4])] = e[2]
    return px


def valuation_flags(data: StockData, t: int) -> tuple[bool, bool, bool]:
    """S13: (P/E defined with 756 values in t-1260 .. t-1, rule A, rule B) at the start t."""
    pe_t = data.pe[t]
    win = data.pe[max(0, t - 1260):t]
    win = win[np.isfinite(win)]
    if not np.isfinite(pe_t) or len(win) < 756:
        return False, False, False
    q10 = float(np.quantile(win, 0.10))
    return True, bool(pe_t < float(np.median(win))), bool(q10 <= pe_t <= 1.1 * q10)


def run_stock(prep: Prepared, market: Market, cfg: Config, groups=("zone", "random"), fixed_depth=None,
              placebos=(), starts=None, n_pairs: int = 10, n_gaps: int = 20, master: int = MASTER) -> dict:
    """Every plan of one stock under one configuration: per group (and draw) the outputs of S12 at the
    four horizons, R_h, the first earnings-flag bar, the start orders, the first fill, and the placements
    with an order and those with a capped up-shift (S6); per plan the exclusions, the horizon dates and
    the valuation flags; per stock the cleaning report (S1)."""
    d = prep.data
    ts = plan_starts(prep, cfg)[0] if starts is None else list(starts)
    g_earn = earnings_bars(d)
    growth = delist_growth(d, market)
    k = len(cfg.weights())
    P = len(ts)
    names = ["zone"] + [g for g in ("random", "gaps", "vol", "fixed", "lump") if g in groups and g != "zone"]
    n_draw = {"zone": 1, "random": 2 * n_pairs, "gaps": n_gaps, "vol": 1, "fixed": 1, "lump": 1}
    if placebos:
        names.append("placebo")
        n_draw["placebo"] = len(placebos)
    res = {g: {"out": np.full((P, n_draw[g], 4, N_OUT), np.nan), "R": np.full((P, n_draw[g], 4), np.nan),
               "earn": np.full((P, n_draw[g]), NO_BAR, dtype=np.int64),
               "start_px": np.full((P, n_draw[g], k), np.nan), "cap": np.zeros((P, n_draw[g]), dtype=np.int64),
               "first_fill": np.full((P, n_draw[g]), -1, dtype=np.int64),
               "placements": np.zeros((P, n_draw[g]), dtype=np.int64),
               "capped": np.zeros((P, n_draw[g]), dtype=np.int64)} for g in names}
    plan = {"t": np.array(ts, dtype=np.int64), "date": d.dates[ts] if P else np.array([], "datetime64[D]"),
            "c_t": d.c[ts] if P else np.array([]),
            "excl": np.zeros((P, 4), dtype=np.int64), "hdate": np.full((P, 4), np.datetime64("NaT", "D")),
            "report_unknown": np.zeros((P, 4), bool),
            "pe_defined": np.zeros(P, bool), "pe_a": np.zeros(P, bool), "pe_b": np.zeros(P, bool)}
    sp = d.report_unknown
    for p, t in enumerate(ts):
        for i, h in enumerate(HORIZONS):
            why = excluded(prep, t, int(h), growth)
            plan["excl"][p, i] = EXCL_WHY.index(why) if why else 0
        plan["hdate"][p] = horizon_dates(d, market, t)
        # S12: bars t .. t+h touch a span whose report date is missing
        hd = plan["hdate"][p]
        plan["report_unknown"][p] = [bool(not np.isnat(x) and np.any((sp[:, 0] <= x) & (sp[:, 1] >= d.dates[t])))
                                     for x in hd]
        plan["pe_defined"][p], plan["pe_a"][p], plan["pe_b"][p] = valuation_flags(d, t)
        out, _ = run_plan(prep, t, cfg, groups=tuple(names), fixed_depth=fixed_depth, n_pairs=n_pairs,
                          n_gaps=n_gaps, placebos=placebos, master=master)
        for g in names:
            r = res[g]
            for j, (o_, ev, _) in enumerate(out[g]):
                r["out"][p, j] = o_
                r["R"][p, j] = plan_returns(o_, t, d, growth)
                r["earn"][p, j] = first_earnings_bar(ev, g_earn)
                r["start_px"][p, j] = start_orders(ev, t, k)
                capped = ev[(ev[:, 0] == t) & (ev[:, 1] == EV_CAP)]
                r["cap"][p, j] = int(capped[0, 3]) if len(capped) else 0
                buys = ev[np.isin(ev[:, 1], (EV_FILL, EV_CLOSE_FILL, EV_TIME_BUY, EV_LUMP_BUY)), 0]
                r["first_fill"][p, j] = int(buys.min()) - t if len(buys) else -1
                r["placements"][p, j] = len(np.unique(ev[ev[:, 1] == EV_ORDER, 5]))
                r["capped"][p, j] = int((ev[:, 1] == EV_CAP).sum())
    return {"symbol": d.symbol, "cfg": cfg.name, "ratio": tuple(cfg.ratio), "plan": plan, "groups": res,
            "start_skipped": plan_starts(prep, cfg)[1] if starts is None else {},
            "cleaning": {"jumps": d.cleaning.get("jumps", []), "delisted": bool(d.delisted),
                         "data_gap": bool(d.gap_end), "last_date": str(d.dates[d.last]) if len(d.dates) else None}}
