"""
Technical indicators computed locally from daily OHLCV (pandas + numpy only, no TA-Lib).

Every function is causal: the value at bar t uses bars <= t only. Series/DataFrames go in on a
DatetimeIndex and come out on the same index. RSI, ATR and ADX use Wilder smoothing (`wilder`).
Definitions and parameter choices are documented in references/methodology.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from pandas.tseries.frequencies import to_offset

import config as C

# Columns of compute_all(), in contract order. Names carry the default periods; the periods
# themselves come from config.
IND_COLUMNS = (
    "sma20", "sma50", "sma200", "sma20_slope", "sma50_slope", "sma200_slope",
    "ema12", "ema26", "macd", "macd_signal", "macd_hist",
    "rsi14", "rsi2",
    "tr", "atr14", "atr_pct",
    "plus_di", "minus_di", "adx14",
    "bb_mid", "bb_upper", "bb_lower", "bb_pct_b", "bb_width",
    "kc_mid", "kc_upper", "kc_lower", "squeeze_on", "squeeze_bars",
    "z20",
    "obv", "ad",
    "rvol50",
    "ret1",
    "rv20",
    "rv20_pctl", "atr_pct_pctl", "bb_width_pctl",
    "hh55_prev", "hh252_prev",
)

_WEEKLY_AGG = {
    "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    "adj_open": "first", "adj_high": "max", "adj_low": "min", "adj_close": "last",
}


# ---------------------------------------------------------------------------- averages
def sma(s: pd.Series, n: int) -> pd.Series:
    """Simple moving average; NaN until n values are available."""
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """Exponential moving average, alpha = 2/(n+1), seeded at the first valid value (no warm-up)."""
    return s.ewm(span=n, adjust=False).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's moving average (RMA, alpha = 1/n).

    NaN until n valid values have been seen; the first value is their simple mean, then
    avg += (x - avg) / n. A NaN input after the seed gives NaN at that bar and leaves the
    average unchanged.
    """
    x = s.to_numpy(dtype=float, na_value=np.nan)
    out = np.full(len(x), np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) >= n:
        start = int(valid[n - 1])
        avg = float(x[valid[:n]].mean())
        out[start] = avg
        vals = x.tolist()
        for i in range(start + 1, len(vals)):
            v = vals[i]
            if v == v:  # not NaN
                avg += (v - avg) / n
                out[i] = avg
    return pd.Series(out, index=s.index, name=s.name)


# ---------------------------------------------------------------------------- momentum
def rsi(close: pd.Series, n: int = C.RSI_PERIOD) -> pd.Series:
    """Wilder RSI. First value at bar n (the averages start on the first close-to-close change).

    Written as 100 * avg_gain / (avg_gain + avg_loss), which equals 100 - 100 / (1 + RS):
    100 when there are only gains, 0 with only losses, 50 when both averages are 0.
    """
    delta = close.diff()
    avg_gain = wilder(delta.clip(lower=0.0), n)
    avg_loss = wilder((-delta).clip(lower=0.0), n)
    total = avg_gain + avg_loss
    out = 100.0 * avg_gain / total.where(total > 0)
    return out.mask(total == 0, 50.0)


def macd(close: pd.Series, fast: int = C.MACD_FAST, slow: int = C.MACD_SLOW,
         signal: int = C.MACD_SIGNAL) -> pd.DataFrame:
    """MACD line = EMA(fast) - EMA(slow); signal = EMA(line, signal); hist = line - signal."""
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


# ---------------------------------------------------------------------------- range / trend strength
def true_range(df: pd.DataFrame) -> pd.Series:
    """max(high - low, |high - prev close|, |low - prev close|); high - low on the first bar."""
    prev_close = df["close"].shift(1)
    hl = df["high"] - df["low"]
    tr = pd.concat([hl, (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
                   axis=1).max(axis=1)
    return tr.where(hl.notna()).rename("tr")


def atr(df: pd.DataFrame, n: int = C.ATR_PERIOD) -> pd.Series:
    """Average true range: Wilder average of the true range (first value at bar n-1)."""
    return wilder(true_range(df), n).rename("atr")


def adx(df: pd.DataFrame, n: int = C.ADX_PERIOD) -> pd.DataFrame:
    """Wilder's +DI, -DI and ADX.

    +DM = up move if it beats the down move and is positive, else 0 (mirror for -DM), from bar 1.
    The DMs and the true range are Wilder-smoothed over the same bars (bar 1 on), so DI stays in
    0..100; +DI/-DI first appear at bar n, ADX = wilder(DX, n) at bar 2n-1.
    """
    up = df["high"].diff()
    down = -df["low"].diff()
    has_prev = up.notna() & down.notna()
    plus_dm = up.where((up > down) & (up > 0), 0.0).where(has_prev)
    minus_dm = down.where((down > up) & (down > 0), 0.0).where(has_prev)
    atr_dm = wilder(true_range(df).where(has_prev), n)
    plus_di = (100.0 * wilder(plus_dm, n) / atr_dm).mask(atr_dm == 0, 0.0)
    minus_di = (100.0 * wilder(minus_dm, n) / atr_dm).mask(atr_dm == 0, 0.0)
    di_sum = plus_di + minus_di
    dx = (100.0 * (plus_di - minus_di).abs() / di_sum).mask(di_sum == 0, 0.0)
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": wilder(dx, n)})


# ---------------------------------------------------------------------------- bands / volatility
def bollinger(close: pd.Series, n: int = C.BB_PERIOD, k: float = C.BB_STD) -> pd.DataFrame:
    """Bollinger bands on SMA(n) +/- k population std; %B is NaN when the bands have zero width."""
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper = mid + k * sd
    lower = mid - k * sd
    span = upper - lower
    return pd.DataFrame({
        "mid": mid,
        "upper": upper,
        "lower": lower,
        "pct_b": (close - lower) / span.where(span > 0),
        "width": span / mid.where(mid != 0),
    })


def keltner(df: pd.DataFrame, n: int = C.KC_PERIOD, atr_n: int = C.KC_ATR_PERIOD,
            mult: float = C.KC_MULT) -> pd.DataFrame:
    """Keltner channel: EMA(close, n) +/- mult x ATR(atr_n)."""
    mid = ema(df["close"], n)
    band = mult * atr(df, atr_n)
    return pd.DataFrame({"mid": mid, "upper": mid + band, "lower": mid - band})


def squeeze(bb: pd.DataFrame, kc: pd.DataFrame) -> pd.DataFrame:
    """TTM-style squeeze: Bollinger bands inside the Keltner channel (False while inputs are NaN).

    `bars` is the length of the current run of consecutive squeeze bars, 0 when off.
    """
    on = (bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])
    run = on.astype("int64").groupby((~on).cumsum()).cumsum()
    return pd.DataFrame({"on": on, "bars": run})


def zscore(s: pd.Series, n: int = C.ZSCORE_PERIOD) -> pd.Series:
    """(s - SMA(n)) / rolling population std(n); NaN when the std is 0."""
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return (s - sma(s, n)) / sd.where(sd > 0)


def _annualized_std(log_ret: pd.Series, n: int) -> pd.Series:
    return log_ret.rolling(n, min_periods=n).std(ddof=1) * np.sqrt(C.TRADING_DAYS_PER_YEAR)


def realized_vol(close: pd.Series, n: int = C.REALIZED_VOL_PERIOD) -> pd.Series:
    """Annualized close-to-close volatility: sample std (ddof=1) of n daily log returns x sqrt(252)."""
    return _annualized_std(np.log(close.where(close > 0)).diff(), n)


# ---------------------------------------------------------------------------- volume / flow
def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-balance volume: cumulative sign(close change) x volume, 0 on the first bar."""
    flow = np.sign(close.diff()) * volume
    return flow.fillna(0.0).cumsum()


def ad_line(df: pd.DataFrame) -> pd.Series:
    """Accumulation/distribution line: cumsum(CLV x volume), CLV = ((c-l) - (h-c)) / (h-l), 0 if h == l."""
    rng = df["high"] - df["low"]
    clv = (((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng.where(rng > 0)).fillna(0.0)
    return (clv * df["volume"]).fillna(0.0).cumsum()


def rvol(volume: pd.Series, n: int = C.RVOL_PERIOD) -> pd.Series:
    """Relative volume: today's volume / mean volume of the previous n bars (today excluded)."""
    base = volume.shift(1).rolling(n, min_periods=n).mean()
    return volume / base.where(base > 0)


def anchored_vwap(df: pd.DataFrame, anchor_date) -> pd.Series:
    """VWAP anchored at the first bar on/after anchor_date (inclusive); NaN before the anchor.

    Daily-bar approximation: cumulative typical price (H+L+C)/3 x volume / cumulative volume.
    NaN while the cumulative volume is still zero.
    """
    in_window = df.index >= pd.Timestamp(anchor_date)
    vol = df["volume"].where(in_window)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_vol = vol.cumsum()
    return ((typical * vol).cumsum() / cum_vol.where(cum_vol > 0)).rename("avwap")


# ---------------------------------------------------------------------------- statistics
def rolling_percentile(s: pd.Series, window: int = C.PERCENTILE_WINDOW) -> pd.Series:
    """Percentile rank (0..100) of each value within its trailing window, today included.

    100 x share of the window's values <= today's value (ties count as below). NaN until the
    window is full and whenever the window contains a NaN.
    """
    x = s.to_numpy(dtype=float, na_value=np.nan)
    out = np.full(len(x), np.nan)
    if len(x) >= window:
        win = sliding_window_view(x, window)
        below = (win <= win[:, -1:]).sum(axis=1)
        complete = ~np.isnan(win).any(axis=1)
        out[window - 1:] = np.where(complete, 100.0 * below / window, np.nan)
    return pd.Series(out, index=s.index, name=s.name)


def daily_returns(bars: pd.DataFrame) -> pd.Series:
    """Close-to-close simple returns on adj_close, falling back to split-adjusted close where
    the adjusted pair (t-1, t) is incomplete."""
    close = bars["close"]
    raw = close / close.shift(1) - 1.0
    if "adj_close" not in bars.columns:
        return raw
    adj = bars["adj_close"]
    total = adj / adj.shift(1) - 1.0
    return total.where(total.notna(), raw)


# ---------------------------------------------------------------------------- resampling
def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Weekly bars (C.WEEKLY_RULE, W-FRI) indexed by the last actual trading date of each week.

    OHLCV and adj_* use first/max/min/last/sum. `partial` is True only for the latest week when
    its last bar is not on the rule's anchor day (no exchange calendar: a latest week cut short by
    a Friday holiday is flagged too).
    """
    spec = {c: f for c, f in _WEEKLY_AGG.items() if c in df.columns}
    week = df.index.to_period(C.WEEKLY_RULE)
    out = df[list(spec)].groupby(week).agg(spec)
    last_day = df.index.to_series().groupby(week).max()
    out.index = pd.DatetimeIndex(last_day.to_numpy(), name="date")
    partial = np.zeros(len(out), dtype=bool)
    if len(out):
        partial[-1] = out.index[-1].dayofweek != to_offset(C.WEEKLY_RULE).weekday
    out["partial"] = partial
    return out


# ---------------------------------------------------------------------------- everything
def compute_all(bars: pd.DataFrame) -> pd.DataFrame:
    """All daily indicators used by the modules, on bars.index, with columns IND_COLUMNS."""
    close = bars["close"]
    out = {}

    for name, period in zip(("sma20", "sma50", "sma200"), C.SMA_PERIODS):
        out[name] = sma(close, period)
        out[name + "_slope"] = out[name] / out[name].shift(C.SLOPE_BARS) - 1.0

    m = macd(close, C.MACD_FAST, C.MACD_SLOW, C.MACD_SIGNAL)
    out["ema12"] = ema(close, C.MACD_FAST)
    out["ema26"] = ema(close, C.MACD_SLOW)
    out["macd"], out["macd_signal"], out["macd_hist"] = m["macd"], m["signal"], m["hist"]

    out["rsi14"] = rsi(close, C.RSI_PERIOD)
    out["rsi2"] = rsi(close, C.RSI_SHORT_PERIOD)

    out["tr"] = true_range(bars)
    out["atr14"] = wilder(out["tr"], C.ATR_PERIOD)
    out["atr_pct"] = out["atr14"] / close

    dmi = adx(bars, C.ADX_PERIOD)
    out["plus_di"], out["minus_di"], out["adx14"] = dmi["plus_di"], dmi["minus_di"], dmi["adx"]

    bb = bollinger(close, C.BB_PERIOD, C.BB_STD)
    kc = keltner(bars, C.KC_PERIOD, C.KC_ATR_PERIOD, C.KC_MULT)
    sq = squeeze(bb, kc)
    for col in ("mid", "upper", "lower", "pct_b", "width"):
        out["bb_" + col] = bb[col]
    for col in ("mid", "upper", "lower"):
        out["kc_" + col] = kc[col]
    out["squeeze_on"], out["squeeze_bars"] = sq["on"], sq["bars"]

    out["z20"] = zscore(close, C.ZSCORE_PERIOD)
    out["obv"] = obv(close, bars["volume"])
    out["ad"] = ad_line(bars)
    out["rvol50"] = rvol(bars["volume"], C.RVOL_PERIOD)

    out["ret1"] = daily_returns(bars)
    out["rv20"] = _annualized_std(np.log1p(out["ret1"]), C.REALIZED_VOL_PERIOD)

    out["rv20_pctl"] = rolling_percentile(out["rv20"], C.PERCENTILE_WINDOW)
    out["atr_pct_pctl"] = rolling_percentile(out["atr_pct"], C.PERCENTILE_WINDOW)
    out["bb_width_pctl"] = rolling_percentile(out["bb_width"], C.PERCENTILE_WINDOW)

    prev_close = close.shift(1)
    breakout_n = C.SIGNAL_PARAMS["breakout_lookback"]
    out["hh55_prev"] = prev_close.rolling(breakout_n, min_periods=breakout_n).max()
    out["hh252_prev"] = prev_close.rolling(C.HIGH_52W_BARS, min_periods=C.HIGH_52W_BARS).max()

    ind = pd.DataFrame(out, index=bars.index)
    return ind[list(IND_COLUMNS)]
