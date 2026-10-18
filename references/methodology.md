# Methodology

How every number in `metrics.json` is produced. Parameter names in `CODE` refer to
`scripts/config.py`, the only place parameters live; the values quoted here are the defaults.

**Principles**
- **Regime first.** The regime (trend, range, transitional or volatility extreme) is decided before
  anything else, and it sets the scorecard weights.
- **Everything is computed in Python** from FMP daily bars. No TA-Lib, and no FMP indicator endpoints in
  the analysis. Claude never estimates a figure in prose.
- **Causal.** Every indicator and signal at bar t uses bars up to and including t. The only forward-looking
  quantities are the labelled forward returns and MAE in signal statistics. Fractal pivots need k bars on
  their right, but levels are measured as of the last bar, so no future bar is ever used.
- **Nothing is filled in.** A missing input gives `null` and a note. A missing module gives
  `available: false` with the reason.
- **Conditional output.** Scenarios have triggers and invalidation levels. Nothing is a buy, sell or hold
  recommendation.

---

## 1. Data and adjustments

### 1.1 Sources

| Data | FMP stable endpoint | Columns | Used for |
|---|---|---|---|
| Stock OHLCV | `historical-price-eod/full` | `open, high, low, close, volume` (split-adjusted price **and** volume; not dividend-adjusted) | levels, indicators, signals, chart |
| Stock adjusted prices | `historical-price-eod/dividend-adjusted` | `adj_open, adj_high, adj_low, adj_close`, left-joined onto the /full dates | returns, realized volatility, beta, forward returns, earnings reactions |
| SPY, sector ETF | `historical-price-eod/dividend-adjusted`, last `BENCHMARK_YEARS` = 5 years, one request each | `open..close` = adjusted prices; `adj_*` = the same | market context, relative strength |
| ^VIX | `historical-price-eod/full`, last 5 years | volume is 0 | VIX level and percentile |
| Profile, quote, earnings | `profile`, `quote`, `earnings` | as returned | name, sector, ETF flag, IPO date; snapshot quote; earnings calendar |

- `^VIX` (with the caret) is the VIX index on FMP; `VIX` is a different instrument.
- FMP's daily `vwap` field is (O+H+L+C)/4. It is ignored.
- The sector ETF comes from `profile.sector` via `SECTOR_ETF`: Technology→XLK, Financial Services→XLF,
  Healthcare→XLV, Consumer Cyclical→XLY, Consumer Defensive→XLP, Energy→XLE, Industrials→XLI,
  Basic Materials→XLB, Real Estate→XLRE, Utilities→XLU, Communication Services→XLC.
- ETFs and funds (`isEtf` or `isFund`) skip the earnings and sector modules; their benchmark is SPY.
  SPY itself skips relative strength vs SPY and reuses its own split-adjusted bars as the benchmark (no
  extra request), so its market-context and regime readings cannot disagree. FMP labels SPY's sector "Financial Services", which means nothing for an ETF, so the snapshot
  leaves `sector` empty for ETFs.

### 1.2 Which price series drives what

| Quantity | Price basis |
|---|---|
| Candles, SMAs, EMAs, MACD, RSI, ATR, ADX, Bollinger, Keltner, z-score, OBV, A/D, RVOL | split-adjusted `/full` |
| Key levels, pivots, volume profile, gaps, anchored VWAPs, 52-week high/low, scenarios, risk | split-adjusted `/full` |
| Signal rules (evaluated on day t) | split-adjusted `/full` and the indicators above |
| Period returns, 12-1 momentum, RS lines, beta/correlation, `rv20`, earnings reactions | dividend-adjusted closes (total return) |
| Signal forward returns and MAE | dividend-adjusted closes and lows |

Daily returns use `adj_close[t] / adj_close[t-1] - 1`. Where that adjusted pair is incomplete, the
split-adjusted close is used for that day instead (price return only). If `/dividend-adjusted` is
unavailable, every return is a price return and the run notes it. In forward returns, both prices of a
ratio are always on the same basis.

**Split check.** NVDA split 10-for-1 on 2024-06-10. FMP `/full` shows 2024-06-07 close 120.89 and
2024-06-10 close 121.79, so the close is continuous across the split. Volume is adjusted too: 412.4M
and 314.2M on those two days. `/full` is therefore split-adjusted in price and volume, but carries no
dividend adjustment.

### 1.3 History fetch

- **Earliest date requested:** max(`HISTORY_START` = 1990-01-01, `profile.ipoDate`, `--history-start`).
  If the median of the first `SPAC_CHECK_BARS` = 126 closes is within `SPAC_MEDIAN_BAND` = 9.50-11.50 and
  at least `SPAC_MIN_SHARE_IN_BAND` = 60% of them sit within `SPAC_PRICE_BAND` = 9.00-12.00 (so a
  deal-announcement spike does not hide it), `data_quality.spac_like_start` flags a probable pre-merger
  SPAC shell. The bars are kept, never dropped silently; `--history-start` cuts them.
- **Row cap:** each EOD request returns at most `EOD_ROW_CAP` = 5000 rows, the *newest* rows within
  from..to. Requests therefore run newest-first, in date chunks.
- **Chunk size:** each chunk spans `CHUNK_YEARS` = 18 calendar years (about 4,540 sessions). When the
  whole remaining span fits within `CHUNK_FIT_SHARE` = 97% of the cap (estimated as calendar days ×
  252/365.25), it is taken in one request. AAPL from 1990 therefore needs 2 requests per endpoint, not 3.
- **When a chunk hits the cap:** the next chunk's `to` is the earliest returned date minus one day.
- **The fetch stops when:**
  - a chunk is empty;
  - a chunk below the cap starts more than `HISTORY_EXHAUSTED_DAYS` = 10 days after its `from`;
  - `from` reaches the earliest date;
  - `MAX_CHUNKS` = 3 requests have been made (reported).
- **A failed older chunk** keeps the rows already fetched and is noted.
- **Requests per uncached run:**
  - stock: 10 (profile, quote, earnings, /full ×2, /dividend-adjusted ×2, SPY, sector ETF, ^VIX)
  - ETF: 8
  - SPY itself: 7
  - Histories under about 19 years need one request per price endpoint.

### 1.4 Caching

- **Location and key:** responses are cached in `~/.cache/fmp-technical` (or `FMP_CACHE_DIR`), keyed by
  base URL, endpoint and sorted query parameters. The API key is never part of the key, the file name
  or the file contents. Files are written to a temporary name and then renamed.
- **Price requests** carry `to=<today in New York>`, so each New York date is a new key.
- **Session rule** (daily-bar endpoints, technical indicators and the quote): a cached copy is valid
  only if it was written on the same New York date AND either it was written at or after
  `MARKET_CLOSE_BUFFER` (16:15 New York), or it is still before 16:15. On weekends, the same date is
  enough.
  - A same-day re-run makes zero requests.
  - A run made during the session is refetched once after the close, so a partial bar is never frozen.
- **Other endpoints** use a time-to-live: profile 1 day, earnings 12 hours.
- `--refresh` bypasses the cache.
- **Retries and rate limit:** on HTTP 429, 5xx and network errors a request is tried up to 4 times
  with exponential backoff, under a token-bucket rate limit (`FMP_MAX_PER_MIN`, default 280 per minute).

### 1.5 Validation (stock and every benchmark)

1. Sort by date and drop duplicate dates, keeping the last row.
2. Drop rows with a missing or non-positive open, high, low or close, and (except ^VIX) rows with
   missing or negative volume. The counts are recorded.
3. **Partial bar.** If the last bar is dated today in New York and New York time is before 16:15,
   the bar is dropped. Its values and the reason go to `data_quality.partial_bar_excluded`. `as_of` is
   always the date of the last *complete* bar, and every module, signal and chart stops there.
4. **Flagged but kept:**
   - inconsistent bars (high < low, or close outside [low, high]);
   - zero-volume bars;
   - gaps longer than `GAP_CALENDAR_DAYS` = 4 calendar days. Long histories also show real market
     closures, such as September 2001 and October 2012.
5. **Stale data.** The expected last session is today if it is a weekday and New York time is at or
   after 16:15, otherwise the previous weekday. The data is stale when the last bar is more than
   `STALE_BDAYS` = 2 business days behind it. Exchange holidays are not modelled; one holiday stays
   under the threshold.
6. **Adjusted coverage:** the share of bars that have `adj_close`.
7. **Liquidity.** Mean shares and mean dollar volume (close × volume) over the last
   `LIQUIDITY_BARS` = 50 bars. The name is flagged illiquid below `ILLIQUID_MIN_AVG_SHARES` = 200,000
   shares or `ILLIQUID_MIN_AVG_DOLLARS` = $5M.

### 1.6 Restricted or missing data

- **Plan-restricted:** HTTP 402/403, or an FMP message mentioning restricted, premium, subscription,
  upgrade or exclusive. HTTP 401 means an invalid key and stops the run.
- **Empty arrays** count as "no data".
- **Where it is recorded:** `meta.skipped[<module>]`, `meta.restricted` and the notes.
  - The analysis continues without the affected input and fills nothing in.
  - Skip keys: `profile`, `quote`, `earnings`, `sector`, `spy`, `vix`.
- **Modules** whose inputs are missing report `available: false` with the reason. They are listed in
  `meta.modules_unavailable`.
- **No usable /full history** stops the run with "no price data for X".

---

## 2. Indicators (`scripts/indicators.py`)

Every indicator is NaN until it has its full window; nothing is back-filled. Column names carry the
default periods (`sma20`, `rsi14`, …); the periods themselves come from config.

| Indicator | Definition |
|---|---|
| SMA(n) | rolling mean, first value at bar n-1 |
| EMA(n) | `ewm(span=n, adjust=False)`, alpha = 2/(n+1), seeded at the first value (so MACD is biased toward 0 in roughly the first 3×26 bars of a short history) |
| Wilder RMA(n) | first value = mean of the first n values; then avg += (x - avg)/n |
| SMA slope | `SMA[t] / SMA[t - SLOPE_BARS] - 1`, `SLOPE_BARS` = 20 |
| MACD(12, 26, 9) | line = EMA12 - EMA26; signal = EMA9 of the line; hist = line - signal |
| RSI(n), n = 14 and 2 | Wilder averages of gains and losses from the first change; RSI = 100·AG/(AG+AL); 100 with only gains, 0 with only losses, 50 when both are 0 |
| True range | max(H-L, \|H-C₋₁\|, \|L-C₋₁\|); H-L on the first bar |
| ATR(14) | Wilder(TR, 14); `atr_pct` = ATR / close |
| ADX(14) | Wilder's +DM/-DM; DMs and TR Wilder-smoothed over the same bars, so DI stays in 0..100; DX = 100·\|+DI - -DI\|/(+DI + -DI); ADX = Wilder(DX, 14), first value at bar 27 |
| Bollinger(20, 2) | SMA20 ± 2 × population std (ddof=0); %B = (C - lower)/(upper - lower); bandwidth = (upper - lower)/mid |
| Keltner(20, 20, 1.5) | EMA20(close) ± 1.5 × Wilder ATR(20) |
| Squeeze (TTM style) | on when the BB upper is below the KC upper **and** the BB lower is above the KC lower; `squeeze_bars` = length of the current run (0 when off); off during warm-up |
| z20 | (close - SMA20) / population std(20) |
| `rv20` | sample std (ddof=1) of 20 daily log returns (log(1 + ret1), dividend-adjusted) × √252 |
| Percentiles (`rv20_pctl`, `atr_pct_pctl`, `bb_width_pctl`) | 100 × share of the trailing `PERCENTILE_WINDOW` = 252 values (today included) that are ≤ today's value; first available after about 264-271 bars |
| OBV | cumulative sign(close change) × volume |
| A/D line | cumulative CLV × volume, CLV = ((C-L) - (H-C))/(H-L), 0 when H = L |
| RVOL(50) | today's volume / mean volume of the previous 50 sessions (today excluded) |
| `hh55_prev`, `hh252_prev` | highest close of the previous 55 / 252 bars, today excluded |
| Weekly bars | `W-FRI` groups: open first, high max, low min, close last, volume sum; dated by the week's last trading day; the latest week is `partial` when it does not end on a Friday |

---

## 3. Analysis modules

### A. Market context (`market_context`)
- **SPY:**
  - the last close against its SMA(200) on dividend-adjusted closes (when SPY itself is analysed: on its
    split-adjusted bars). Dividend adjustment lowers past prices, so the dividend-adjusted SMA(200) sits
    roughly half a year's dividend yield (about 0.4% for SPY) below the split-adjusted one shown on
    SPY's own chart. The basis is in `data_quality.benchmark_basis`;
  - `pct_vs_sma200` = close / SMA200 - 1;
  - `sma200_slope_20d` = SMA200[t] / SMA200[t-20] - 1.
- **^VIX:** the last close and `pctl_1y`, its percentile among the last 252 closes, today included.
  ^VIX has a few rows on non-equity days; only dates on which the stock or SPY traded are kept.
- **Sector ETF:**
  - close against SMA50 and SMA200, plus both 20-bar slopes;
  - a trend label (Uptrend / Downtrend / Mixed) from the same four direction votes as the regime;
  - relative strength against SPY, computed as in module D (`rs_3m`, `rs_6m`, RS-line slope,
    252-bar RS extremes).
- **Benchmarks** are cut to dates ≤ `as_of`.

### B. Regime (`regime`)
- **Direction votes:** each is +1, -1 or 0 (null when the input is missing).
  - close vs SMA50
  - close vs SMA200
  - SMA50 vs SMA200
  - sign of the SMA200 20-bar slope
- **Direction:** Uptrend if every available vote is +1, Downtrend if every one is -1, otherwise Mixed.
  `direction_sign` is +1 / -1 / 0.
- **Trend strength** from ADX(14):
  - trending: ADX ≥ `ADX_TREND` (25)
  - range: ADX < `ADX_RANGE` (20)
  - transitional: in between
- **Volatility:** `vol_pctl` = max(`rv20_pctl`, `atr_pct_pctl`).
  - extreme: above `VOL_EXTREME_PCTL` (95)
  - high: above `VOL_HIGH_PCTL` (80)
  - low: below `VOL_LOW_PCTL` (20)
  - otherwise normal

  Taking the max means either measure alone can flag high or extreme vol, while "low" needs both.
  *Compression* means the Bollinger bandwidth percentile is below `COMPRESSION_BW_PCTL` (10).
- **Label:** `"<head> / <vol> vol[ (compression)]"`.

  | Strength | Head |
  |---|---|
  | range | Range |
  | transitional | Transitional (uptrend bias / downtrend bias / mixed) |
  | trending, all votes agree | Strong uptrend / Strong downtrend |
  | trending, votes disagree | Mixed (trending up / down), by +DI vs -DI |
  | ADX unavailable | Uptrend / Downtrend / Mixed / Undetermined |

  The vol part is omitted while the percentiles are unavailable.
- **Type** (drives the scorecard weights):
  - `vol_extreme` if the vol label is extreme;
  - otherwise `trend`, `range` or `transitional`;
  - transitional also when ADX is unavailable.

### C. Trend and momentum (`trend_momentum`)
- **Return index:** all returns use the total-return index P (cumulative product of daily returns,
  section 1.2).
- **12-1 momentum** = P[t-21] / P[t-252] - 1. It needs 253 bars; otherwise it is null with a note.
- **Returns:** 1m / 3m / 6m / 12m over 21 / 63 / 126 / 252 bars.
- **SMA20/50/200:**
  - values, 20-bar slopes and close vs each SMA;
  - the stack: the available SMAs ordered top to bottom. "20>50>200" is bullish, "200>50>20" bearish,
    any other order mixed.
- **MACD:** latest values, and the most recent signal-line cross after the EMA warm-up (bar index ≥
  `MACD_SLOW` + `MACD_SIGNAL` = 35), with the side of zero it happened on.
- **Distance from the 52-week high:** the highest split-adjusted high of the last 252 bars, as a
  percentage of the close and in ATRs.

### D. Relative strength (`relative_strength`)
- **RS line:** the stock's return index divided by the benchmark's, on dates both have (inner join).
  It equals adj_close(stock) / adj_close(benchmark) up to a constant.
- **`ratio`:** the RS line rebased to 100 at the start of the last 252 common bars.
- **Relative returns:** `rel_return_3m` / `rel_return_6m` = stock return minus benchmark return over 63 /
  126 common bars. `ratio_change_*` is the RS line's own change over the same windows.
- **`rs_new_high` / `rs_new_low`:** today's RS line is ≥ the max (or ≤ the min) of the last 252 common
  bars. Needs 252 common bars.
- **`rs_line_slope`:** the RS line's change over 20 bars.
- **Beta and correlation** over the last `BETA_WINDOW` = 252 common daily returns vs SPY:
  `beta` = cov(s, b) / var(b) with sample statistics; Pearson correlation.
- **Skips:** SPY skips vs SPY; ETFs skip vs sector.

### E. Mean reversion (`mean_reversion`)
- **Measures:**
  - RSI(14) and RSI(2);
  - z20;
  - Bollinger %B;
  - `dist_sma50_atr` = (close - SMA50) / ATR14.
- **State labels** use strict inequalities:

| Measure | Overbought | Oversold |
|---|---|---|
| RSI(14) | > `MR_RSI14_OVERBOUGHT` (70) | < `MR_RSI14_OVERSOLD` (30) |
| RSI(2) | > `MR_RSI2_OVERBOUGHT` (90) | < `MR_RSI2_OVERSOLD` (10) |
| z20 | > +`MR_Z_EXTENDED` (2) | < -2 |
| %B | > 1 "above upper band" | < 0 "below lower band" |

### F. Volume and flow (`volume_flow`)
- **RVOL:** today's volume / mean volume of the previous 50 sessions.
- **OBV and A/D slope:** `slope_20d` is the line's change over 20 bars divided by those bars' total
  volume. It is scale-free, between -1 and +1.
- **Divergence** (L = `DIVERGENCE_LOOKBACK` = 20):
  - bearish: the close is ≥ the max of the prior L closes while the line is below its prior L-bar max;
  - bullish: the mirror at lows;
  - the last `SIGNAL_ACTIVE_BARS` = 3 bars are checked, and the most recent hit is reported.
- **Up/down volume ratio:** volume on up-close days / volume on down-close days over the last
  `UPDOWN_VOLUME_PERIOD` = 50 bars.
- **Anchored VWAPs** (daily-bar approximation, section 7): cumulative typical price (H+L+C)/3 × volume /
  cumulative volume, from the anchor bar inclusive.
  - **Anchors:**
    - `last_earnings`: the bar of the latest past report date (stocks only);
    - `high_52w` / `low_52w`: the bar with the highest high / lowest low of the last 252 bars;
    - `ytd_start`: the first bar of the year;
    - `largest_gap_6m`: the bar with the largest |open - prior close| / prior-day ATR in the last
      `LARGEST_GAP_LOOKBACK` = 126 bars.
  - **Each anchor reports** price vs VWAP in % and in ATRs.

### G. Volatility structure (`volatility`)
- **Measures:** ATR(14), ATR%, `rv20`, Bollinger bandwidth, and the percentiles of each.
- **Squeeze:** whether it is on, and its current run length.
- **`last_release`:** the most recent bar where the squeeze switched off, with direction = close vs SMA20
  on that bar and the length of the run that ended.

### H. Key levels (`levels`)
- **Prices and window:**
  - split-adjusted OHLC and volume;
  - the window is the last `levels_lookback` bars: 126 for swing, 504 for position (all history if
    shorter, with a note);
  - ATR = ATR(14) at the last bar; without it the module is unavailable.
- **Fractal pivots** (`FRACTAL_K` = 2):
  - bar i is a pivot high when its high is strictly above every other high in bars i-2..i+2; pivot lows
    mirror this;
  - equal extremes never form a pivot, and the last 2 bars cannot be pivots yet.
- **Volume profile** (`PROFILE_BINS` = 60, approximation, section 7):
  - equal-width bins from the window's lowest low to its highest high; each bar's volume is spread
    evenly across its high-low range;
  - **POC:** the midpoint of the fullest bin (on a tie, the lowest-priced one);
  - **value area:** grows from the POC, adding the fuller neighbouring bin (both on a tie), until it
    holds `VALUE_AREA_SHARE` = 70% of volume;
  - **HVN:** an interior local maximum above `HVN_FACTOR` = 1.5 × the mean bin;
  - **LVN:** an interior local minimum below `LVN_FACTOR` = 0.5 × the mean bin. Flat runs count as one
    plateau.
- **Unfilled gaps:**
  - gap up: low[t] > high[t-1]; filled once a later low reaches high[t-1], otherwise the zone shrinks
    to what is left;
  - gap down: the mirror case.
- **Candidates and their source types:**
  - pivot highs and lows (pivot);
  - POC, VAL, VAH and HVNs (volume_profile);
  - 52-week high and low (range_52w);
  - both edges of each remaining gap (gap);
  - current SMA50 and SMA200 (moving_average);
  - current anchored VWAPs (anchored_vwap).
- **Merging** (`LEVEL_MERGE_ATR` = 0.5): one greedy pass in ascending price.
  - A candidate joins the current cluster when it is within 0.5 ATR of the cluster's running mean.
  - Cluster price = the mean; zone = min..max member price. Zones can reach about 1 ATR.
- **Ranking:**
  - band = zone ± `TOUCH_ATR` (0.25) ATR;
  - `touches` = bars whose high or low lies in the band;
  - `volume_share` = profile volume in the band / total;
  - `score` = touches / max touches + volume_share / max volume_share + `LEVEL_SOURCE_BONUS` (0.5) ×
    (distinct source types - 1).

  Scores are relative within one run.
- **Selection** (`LEVELS_PER_SIDE` = 3):
  - the nearest 3 clusters below the close (supports) and above it (resistances), nearest first;
  - each carries its score, `distance_pct` and `distance_atr` (negative below the close).
- **Short side:** if a side has fewer than 3 levels, a note says so, naming an all-time or 52-week
  extreme when price is at one.

### I. Multi-timeframe (`multi_timeframe`)
- **Weekly bars:** W-FRI, with SMA10w, SMA40w and Wilder RSI(14) on weekly closes.
- **Weekly direction** (needs 40 weekly bars):
  - Uptrend if close > 10w > 40w;
  - Downtrend if close < 10w < 40w;
  - otherwise Mixed.
- **Alignment with the daily `direction_sign`:**
  - confirms: same non-zero sign;
  - contradicts: opposite non-zero signs;
  - mixed: anything else.
- **Partial week:** a week still in progress is flagged.

### J. Event risk (`event_risk`, stocks only)
- **Next earnings:** the earliest report after `as_of`, or one dated `as_of` without actual EPS.
  - Calendar days to the report.
  - Estimated trading days: numpy business days, with no holiday calendar.
  - `within_horizon`: trading days ≤ the primary horizon. `within_stat_horizons` gives the same flag
    for each stat horizon.
- **Reactions** (approximation, section 7): the two-day move, adj close of the first bar after the
  report date / adj close of the last bar before it - 1.
  - Up to `EARNINGS_REACTIONS_MAX` = 12 latest reports, giving n, median and mean absolute move.
  - Fewer than `EARNINGS_REACTIONS_MIN` = 8 gets a small-sample note.

---

## 4. Signal statistics (`signals`)

The full pre-registered library, the rules exactly as coded, and the statistics are in
[signals.md](signals.md). In short:

- **The library** has 14 fixed rules in 10 families, evaluated at the close of day t on data ≤ t. The
  rules are never added or tuned per ticker. `tests/test_lookahead.py` checks that changing data after
  t changes nothing at t.
- **Active:** a signal is active if it fired on any of the last `SIGNAL_ACTIVE_BARS` = 3 bars.
  Statistics are computed for active signals only, over the stock's full history, at the mode's
  horizons: swing 5/10/20, position 20/60. The primary horizon is 10 for swing and 60 for position.
- **De-clustering:** after a kept event at t, repeats at t+1..t+h are ignored. Only events with t+h in
  the data count.
- **Outcomes:**
  - forward return = adj_close[t+h] / adj_close[t] - 1;
  - MAE = the worst move against the signal's bias: min(adj_low[t+1..t+h]) / adj_close[t] - 1 for bullish
    signals, max(adj_high[t+1..t+h]) / adj_close[t] - 1 for bearish ones (baselines use the same side).
- **Reported:** n, hit rate (share > 0, for bearish signals too), median, mean, p25, p75, median MAE.
- **Baselines:** unconditional (all days) and same-regime (days on the same side of the SMA200, as the
  rule defines). The edge is signal minus same-regime baseline.
- **Confidence** at the primary horizon:
  - n ≥ `CONFIDENCE_N_NORMAL` (30): normal;
  - n ≥ `CONFIDENCE_N_LOW` (15): low;
  - fewer: insufficient. Listed, but not scored (`scorable: false`).
- **Caveats** (emitted in `signals.caveats`): single stock, in-sample, no costs or slippage,
  overlapping baselines vs de-clustered events, history depth limited by the plan.

---

## 5. Scorecard (`scorecard`)

### 5.1 Pillar scores (-2..+2)

Every input casts a vote: +1 bullish, 0 neutral, -1 bearish.

- **Vote share** (trend, momentum, relative strength, volume/flow):
  - score = clip(round_half_away(2 × net / N), -2, +2), where N is the number of slots that apply.
  - A slot whose input is missing abstains: it stays in N as 0, which pulls the score toward 0.
  - A slot that does not apply (sector RS for an ETF, RS vs SPY for SPY) is removed from N.
- **Condition count** (volatility setup, mean reversion): each condition that fires adds ±1, and
  score = clip(net, -2, +2). These are flags that are usually off, so "not stretched" is a real 0.
- **No usable input:** the pillar scores 0, is marked `available: false` with "insufficient data (…)"
  or "not applicable (…)", and keeps its weight.

| Pillar | Inputs (vote slots) | Mode |
|---|---|---|
| Trend | close vs SMA50; close vs SMA200; SMA50 vs SMA200; sign of the SMA200 slope; position mode adds the weekly direction sign | vote share, N = 4 (5) |
| Momentum | signs of 12-1 momentum, 3m return, 6m return, MACD histogram, MACD line vs 0 | vote share, N = 5 |
| Relative strength | per benchmark (SPY; sector ETF for stocks): sign of 3m and 6m relative return; RS line at a new 252-bar high +1 / low -1 | vote share, N = 6 stock, 3 ETF, n/a for SPY |
| Volume/flow | up/down volume ratio > `FLOW_UPDOWN_HIGH` (1.2) +1, < `FLOW_UPDOWN_LOW` (0.8) -1; OBV/A-D divergence (one combined vote); OBV/A-D 20-bar slope (+1 if every available slope is positive, -1 if every one is negative); close above more than `FLOW_AVWAP_MAJORITY` (50%) of the anchored VWAPs +1, below more than 50% -1 | vote share, N = 4 |
| Volatility setup | squeeze on or compression: +1 × regime direction sign (0 with no direction, "break direction unknown"); a squeeze release fewer than `SQUEEZE_RELEASE_RECENT_BARS` (3) bars ago: +1 up / -1 down; extreme vol: -1 unless the direction is up (then 0, "fragile") | condition count |
| Mean reversion | RSI14 < 30 +1, > 70 -1; RSI2 < 10 +1, > 90 -1; z20 < -2 +1, > +2 -1 (%B is used only when z20 is missing; with 2-sigma population bands the two are the same condition); (close - SMA50)/ATR < -`MR_DIST_SMA50_ATR` (2) +1, > +2 -1 | condition count; positive = oversold |

Each pillar returns `score`, `raw_score` (before nudges), a one-line `reason` with the numbers, `inputs`,
`votes` and `nudges`.

### 5.2 Signal nudges
- **Which signals nudge:** an active signal nudges its pillar only when both hold:
  1. its confidence at the primary horizon is normal or low;
  2. its same-regime edge confirms the rule's pre-registered bias: bias × median edge >
     `SIGNAL_EDGE_MIN` (0.5%) **and** bias × hit-rate edge > `SIGNAL_HIT_EDGE_MIN` (5 points).
- **Size:** each qualifying signal moves its pillar by its bias (±1). The net move per pillar is capped
  at `SIGNAL_NUDGE_MAX` = ±1, and the result is clipped to -2..+2.
- **Opposite edges:** an edge in the opposite direction never nudges. Flipping a rule to match one
  stock's history would be data mining.

### 5.3 Weights and bias

**Weights.**
- Start from `REGIME_WEIGHTS[regime.type]`; an unknown or missing type uses transitional.
- Position mode adds `POSITION_WEIGHT_SHIFT` (trend +0.05, mean reversion -0.05), floors at 0 and
  renormalizes. In a trend regime this leaves mean reversion at 0.
- There is no separate "levels" pillar. Levels drive the scenarios and risk framing; in range regimes,
  the mean-reversion, volume/flow and volatility-setup pillars carry the weight.

| Regime type | Trend | Momentum | Relative strength | Volume/flow | Volatility setup | Mean reversion |
|---|---|---|---|---|---|---|
| trend | 0.25 | 0.25 | 0.20 | 0.15 | 0.10 | 0.05 |
| range | 0.10 | 0.10 | 0.10 | 0.20 | 0.20 | 0.30 |
| transitional | 0.20 | 0.20 | 0.15 | 0.15 | 0.15 | 0.15 |
| vol_extreme | 0.15 | 0.10 | 0.10 | 0.20 | 0.25 | 0.20 |

**Weighted score** = Σ weight × score, in the range -2..+2. The **bias** comes from `BIAS_STEPS`. A score
exactly on a boundary takes the label nearer Neutral.

| Weighted score s | Bias |
|---|---|
| s < -1.2 | Bearish |
| -1.2 ≤ s < -0.4 | Moderately bearish |
| -0.4 ≤ s ≤ 0.4 | Neutral |
| 0.4 < s ≤ 1.2 | Moderately bullish |
| s > 1.2 | Bullish |

### 5.4 Confidence
- **Agreement:** the weight share of pillars whose score sign equals the bias sign. For a Neutral bias,
  it is the weight share of pillars that have data and score 0.
- **Multi-timeframe vs the bias:**
  - confirms: the weekly sign equals the bias sign and daily/weekly alignment is "confirms";
  - contradicts: the weekly sign is opposite to the bias, or the alignment is "contradicts";
  - otherwise mixed.

  With a Neutral bias, a mixed weekly direction confirms.
- **Signal support:**
  - supporting: confirmed signals (section 5.2) with the bias's sign;
  - opposing: confirmed signals with another bias;
  - insufficient: n < 15.
- **Level:**
  - **low** if agreement < `CONF_AGREEMENT_LOW` (0.5) or the weekly contradicts the bias;
  - **high** if agreement ≥ `CONF_AGREEMENT_HIGH` (0.7), the weekly confirms, no opposing confirmed
    signal exists, and the active signals are not all insufficient;
  - otherwise **medium**.

  Having no active signals does not lower confidence. `confidence.reasons` states each factor.

### 5.5 Conflicts
Each conflict has `type`, `involved`, `description` (with the numbers) and `typical_meaning`.

| Type | When | Typical meaning (abridged) |
|---|---|---|
| `pillar_pair` | two pillars with opposite non-zero scores | e.g. trend + / mean reversion -: overbought in an uptrend often resolves sideways or by a shallow pullback rather than a reversal |
| `price_flow_divergence` | OBV or A/D divergence active | participation not confirming the price extreme; can persist in strong trends |
| `daily_vs_weekly` | weekly contradicts daily | counter-trend move inside the larger trend |
| `rs_vs_market` | RS pillar > 0 while SPY is below its SMA200 | leadership against a risk-off tape |
| `squeeze_no_direction` | squeeze on with direction 0 | break direction unknown |
| `earnings_in_horizon` | next earnings inside the primary horizon | the event can override the technicals; reactions often exceed the 1σ range |
| `signals_vs_regime` | bullish signals in a downtrend, or bearish in an uptrend | counter-trend setups; judge them on same-regime statistics |
| `signals_both_ways` | bullish and bearish signals active together | turning point or noise |

---

## 6. Scenarios and risk (`scenarios`, `risk`)

The horizon h is `HORIZONS[mode].primary`: 10 trading days for swing, 60 for position. All prices are
split-adjusted, and ATR is ATR(14).

**Bull scenario.**
- **Trigger:** a close above the top (`zone_high`) of the nearest resistance zone, on RVOL ≥
  `TRIGGER_RVOL` (1.5). If there is no resistance zone:
  - the 52-week high, if it is above the close (labelled "all-time high (available history)" when it
    is the highest high in the data);
  - otherwise the close + `NO_LEVEL_TRIGGER_ATR` (1) ATR.
- **Targets:** the resistance levels beyond the trigger, each as a zone of price ± `TARGET_ZONE_ATR`
  (0.25) ATR. With no level beyond the trigger, the projections trigger + `SCENARIO_PROJECTION_ATR`
  (1 and 2) ATR are used, labelled as projections.
- **Invalidation:** a close back below min(trigger zone low, trigger - `INVALIDATION_MIN_ATR` (0.5)
  ATR). This defines a failed breakout.

**Bear scenario.** The mirror image, built from supports.

**Base case.**
- 1σ move = close × `rv20` × √(h/252). The output gives it in %, in price and in ATRs, plus the range
  close ± move.
- It assumes zero drift and that recent volatility persists. About 68% of outcomes fall inside under a
  normal approximation; real tails are fatter.
- If earnings fall inside the horizon, `earnings_caveat` gives the date and the median two-day reaction.

**Wording.** Each scenario carries a conditional `text`, e.g. "If X closes above Y (basis) on
RVOL ≥ 1.5, the next zones are …; the scenario is invalidated by a close back below Z." `leaning`
records which scenario the bias points toward; it is a description, not a forecast.

**Risk framing (illustrative arithmetic, not a recommendation).**

| Item | Long side (short side mirrors it) |
|---|---|
| Entry reference | last close |
| Structural stop | nearest support `zone_low` - `STRUCTURE_STOP_BUFFER_ATR` (0.5) ATR; none if no support below |
| ATR stop | entry - `STOP_ATR_MULT` (2) ATR |
| Stop distance | in ATRs and % of entry, for each stop |
| First target | nearest resistance price; else the bull scenario's first target (projection) |
| Reward/risk | (target - entry) / (entry - stop), for each stop |
| Position-size example | risk $ = `ACCOUNT_SIZE` × `RISK_PER_TRADE` = $100,000 × 1% = $1,000; shares = floor(risk $ / (entry - stop)); notional and % of account; flagged when above 100% of the account |

The headline example uses the structural stop when it exists, otherwise the ATR stop. No commissions,
slippage, financing or borrow are modelled. A gap can fill a stop well beyond its level.

---

## 7. Approximations

| Item | Approximation | Why |
|---|---|---|
| Anchored VWAP | daily bars: typical price (H+L+C)/3 × daily volume, cumulated from the anchor bar | no intraday data; FMP's daily `vwap` is (O+H+L+C)/4 and is not a VWAP |
| Volume profile | each bar's volume spread evenly across its high-low range | no intraday volume-at-price |
| Earnings reaction | two-day close-to-close move across the report date | FMP `earnings` has no before-open / after-close flag; the window contains the reaction either way, plus one extra day of noise |
| Trading days to earnings | weekdays, no exchange holidays | no holiday calendar; can overstate by about a day around holidays |
| Weekly partial flag | the latest week is partial if its last bar is not a Friday | a Good-Friday week reads as partial until the next week starts |
| Stale check / gaps | weekdays only; real market closures appear as gaps | no exchange calendar |

---

## 8. Chart (`chart.html` and the local UI)

- **What it shows:** the last `chart_bars` complete bars (252 for swing, 504 for position) of
  split-adjusted candles, with:
  - SMA20/50/200 and the anchored VWAPs;
  - the nearest 3 supports and resistances as zones;
  - earnings markers;
  - a volume-profile sidebar (POC, value area);
  - volume with RVOL, RSI(14) with 30/70 lines, and the RS line vs SPY rebased to 100 at the window start.
- **Weekly view:** weekly candles are shown automatically when the screen has fewer than 3 px per daily
  bar. In the weekly view, indicator values are the daily readings on each week's last session.
- **Numbers:** every number comes from Python; the page only draws. Prices are rounded to
  `CHART_PRICE_DECIMALS` = 4 decimals, and other floats to `CHART_VALUE_SIG_DIGITS` = 6 significant
  digits.
- **Self-contained:** the file has no external URLs and opens offline.

---

## 9. Cross-check against FMP's indicator endpoints (optional)

`python scripts/crosscheck.py TICKER [--json PATH]` compares the skill's SMA(50) and RSI(14) with FMP's
`technical-indicators/sma` (periodLength 50, timeframe 1day) and `technical-indicators/rsi`
(periodLength 14). The analysis never uses these endpoints.

- **Inputs:** the skill's side runs `indicators.compute_all` on its own validated `/full` bars, from the
  same earliest date as the analysis.
- **Comparison:** the two series are aligned by date and compared over the last `CROSSCHECK_WINDOW` =
  250 shared dates. The output gives max and mean absolute difference, share within tolerance and the
  worst date.
- **SMA(50) tolerance:** `CROSSCHECK_SMA_TOL` = 0.01% of that day's close. A rolling mean has no
  smoothing choices, so a larger difference means different input prices.
- **RSI(14) tolerance:** `CROSSCHECK_RSI_TOL` = 0.5 points, after skipping `CROSSCHECK_RSI_WARMUP` = 250
  bars of each series.
  - Wilder smoothing is path-dependent: a value depends on the seed bar and on how much history comes
    before it, and the seed's weight decays like (13/14)^k.
  - FMP does not document its seeding or history depth, so small RSI differences can come from that
    alone.
- **Today's partial bar** is excluded on the skill's side.
- **Restricted plan:** if the plan lacks both endpoints, the script says so and exits 0.

---

## 10. Output conventions and limitations

**`metrics.json`.**
- Top-level keys in this order: `ticker`, `as_of`, `data_source`, `horizon`, `snapshot`,
  `data_quality`, `market_context`, `regime`, `trend_momentum`, `relative_strength`, `mean_reversion`,
  `volume_flow`, `volatility`, `levels`, `multi_timeframe`, `event_risk`, `signals`, `scorecard`,
  `scenarios`, `risk`, `meta`.
- **Values:** floats are rounded to `METRICS_SIG_DIGITS` = 6 significant digits, dates are
  `YYYY-MM-DD`, and `null` means "not computable" (the module's `notes` say why). Percent-like values
  are fractions (0.05 = 5%).
- **`meta`** holds network calls, cache hits, restricted endpoints, errors, skipped data,
  `modules_unavailable`, notes, the connection (`route`, `base`, `tls_verify`; never the key) and a
  `config_snapshot` of the key parameters.
- **Module failures:** a module that raises becomes `available: false` with "internal error: …" and is
  listed in `meta.errors`. The rest of the run continues.

**Limitations.**
- Single-stock, in-sample statistics, with no transaction costs.
- History depth is limited by the FMP plan and `HISTORY_START`. The IPO date from the profile sets the
  floor; a relisted name may have older bars that are never requested.
- No exchange-holiday calendar: stale checks, gap flags, trading-day counts and the weekly partial flag
  treat holidays as ordinary weekdays.
- Daily data only. VWAP and the volume profile are approximations, and intraday structure is invisible.
- Benchmarks cover the last 5 years, so RS lines, beta and the VIX percentile use at most that span.
- A third-party proxy (`FMP_BASE_URL`) may serve data that differs from FMP's official API. The report
  names the data source, and key figures should be verified.
