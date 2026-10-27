# Methodology

How every number in `metrics.json` is produced. Parameter names in `CODE` refer to
`scripts/config.py`, the only place parameters live; the values quoted here are the defaults.

**Principles**
- **Regime first.** The regime (trend, range, transitional or volatility extreme) is decided before
  anything else, and it sets the scorecard weights.
- **Everything is computed in Python** from FMP daily bars. No TA-Lib, and no FMP indicator endpoints in
  the analysis. Claude never estimates a figure in prose.
- **Causal.** Every indicator and signal at bar t uses bars up to and including t. The only forward-looking
  quantities are the labelled forward returns and MAE in signal statistics. A swing pivot is confirmed
  only by the reversal after it, but levels are measured as of the last bar, so no future bar is ever used.
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
- **Swing pivots** (ATR ZigZag, `SWING_ATR` = 1.5):
  - the running high since the last pivot becomes a pivot high once a later bar's low is 1.5 ATR (that
    bar's ATR) below it; pivot lows mirror this, and pivots alternate;
  - the latest swing is not confirmed yet, so it is not a pivot, and the first bar only starts the count;
  - `swing_atr` = the smaller of the legs into and out of the pivot, in ATRs.
- **Supply/demand zones** (`BASE_MAX_ATR` = 1, `IMPULSE_ATR` = 2, `IMPULSE_BARS` = 3):
  - a demand base is a bar with a range of at most 1 ATR after which, within 3 bars, a close is at least
    2 ATR above its close, the first of those bars closes up, and no low goes under the base's low;
    supply mirrors this;
  - zone = the base bar's low..high; zones a later close has gone through are dropped;
  - `fresh` = no bar after the impulse has traded back into the zone.
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
- **Candidates, source types and weights** (`LEVEL_SOURCE_WEIGHTS`):
  - swing pivot highs and lows (pivot, 1.0 x `swing_atr` / 1.5, between 1 and `SWING_WEIGHT_CAP` = 3);
  - supply/demand zones (supply_demand, 1.0; x `LEVEL_FRESH_FACTOR` = 1.5 when fresh);
  - POC (volume_profile, 1.0), VAL, VAH and HVNs (0.5);
  - 52-week high and low (range_52w, 1.0);
  - each remaining gap: one zone if at most `GAP_ZONE_MAX_ATR` = 1 ATR wide, else its two edges (gap, 0.75);
  - current SMA50 and SMA200 (moving_average, 0.25) and anchored VWAPs (anchored_vwap, 0.25);
  - dated candidates (pivots, supply/demand zones, gaps) are also x 0.5 ^ (bars since / half-life),
    half-life = `LEVEL_HALF_LIFE` (0.5) x the window: 63 bars for swing, 252 for position.
- **Zones** (`KDE_BANDWIDTH_ATR` = 0.25): candidates below and above the close are handled apart.
  - Per side, each candidate adds its weight x a Gaussian kernel of width 0.25 ATR at its price (a
    candidate with a zone spreads its weight across it).
  - Every peak of that density is a zone: the prices around the peak where the density is at least
    half the peak (`ZONE_HALF_MAX`), not crossing the low point towards a neighbouring peak, clamped to
    `ZONE_MIN_ATR` = 0.2 to `ZONE_MAX_ATR` = 1 ATR and to the close (supports end below it,
    resistances start above it). A lone candidate gives a zone about 0.6 ATR wide.
  - Price = the weight-averaged member candidates inside the zone; `weight` = the members' total weight.
- **Tests** (`TEST_TOL_ATR` = 0.1, `REACTION_ATR` = 1, `REACTION_BARS` = 10), over the window:
  - a bar is above the zone when its low is more than 0.1 ATR above it, below it in the mirror case, and in
    it otherwise;
  - a test is a run of bars in the zone between a bar on one side and the next bar outside it (or a jump
    straight across);
  - **hold:** price leaves on the side it came from and then trades 1 ATR beyond the zone's near edge
    within 10 bars (before testing again); **break:** it leaves on the other side; **weak:** neither;
  - **flip** (role reversal): a hold right after a break;
  - tests still running at the last bar, holds that could still complete and a run at the window's
    first bar are left out;
  - `touches` = tests, `holds`, `breaks`, `flips`, `fresh` = no test, `last_test`.
- **Score:** recency x (`LEVEL_HOLD_POINTS` 1 per hold, plus `LEVEL_FLIP_POINTS` 0.5 per flip, minus
  `LEVEL_BREAK_POINTS` 0.5 per break) summed over the tests, recency = 0.5 ^ (bars since the test /
  half-life), + `LEVEL_CONFLUENCE_WEIGHT` (0.5) x `weight`. Scores are absolute, so they compare across
  runs. `volume_share` (profile volume in the zone / total) is shown, not scored.
- **Selection** (`LEVELS_PER_SIDE` = 3, `LEVEL_MIN_SCORE` = 1, `levels_reach_atr` = 4 ATR for swing, 8 for
  position):
  - zones scoring at least 1, or holding the 52-week high or low, qualify;
  - per side: the nearest qualifying zone, then the highest-scoring other qualifying zones within reach
    of the close; the nearest zones beyond reach fill any shortfall;
  - listed nearest first, each with its score, `distance_pct` and `distance_atr` (negative below the close).
- **Walk-forward check** (`scripts/levels_backtest.py`): builds the levels every 5 bars from past bars only
  and records whether each zone held (price trades 1 ATR beyond its near edge before a close beyond its far
  edge). A zone counts as touched when a bar's range overlaps it within 20 bars of the build date; a zone
  that price skips past without trading in it is a `gap`, counted on its own. The outcome is then read over
  the next 20 bars from that touch, so it can end up to 39 bars after the build. A bar that reaches the
  reaction and closes beyond the far edge counts as held (the close is the bar's last print); on the
  touching bar itself the reaction counts only if the bar opened in or beyond the zone, otherwise the
  outcome is `ambiguous` and left out. Each zone is compared with the same zone shape moved to other
  dates; the edge gets a 90% cluster-bootstrap interval over (symbol, year) blocks, and tables are split
  by rank, symbol, daily direction at the build bar and period (`--split`). `scripts/fetch_csv.py` saves
  the 32-symbol baseline universe (trending, declining, sideways, high-volatility stocks and ETFs) as CSVs
  for it. On six large-cap stocks (1995-2018 daily bars, with the earlier one-sided touch rule) neither
  these zones nor the earlier fractal-pivot clusters held more often than the moved ones (edge within about
  ±2 percentage points), while zones at levels planted in synthetic data showed about +23 points. Read the
  zones as where price has reacted before, not as predictions.
- **Trading-range boxes V2** (`scripts/trading_ranges.py`, experimental, not yet in the analysis output): a box
  is a run of alternating ATR ZigZag pivots (`TR_SWING_ATR` = 1 ATR) with at least 2 highs in one band and
  2 lows in another, each band at most 1 ATR wide, 2-8 ATR apart (ATR at confirmation). It is confirmed on
  the bar that confirms its last pivot and its edges are frozen there; two closes in a row more than 0.25
  ATR beyond a band break it, a breach that closes back inside is a failed breakout, and 120 bars without a
  new test expire it. Boxes are searched from the start of the series, so the boxes known at a date never
  change later. `levels_backtest.py --source boxes` scores the active box's edges like the key levels.
- **Baseline (2026-10-26, swing, 32 symbols, 2005-2026 daily bars, `scripts/fetch_csv.py` universe):**
  key levels held 46.7% vs 47.0% for the moved zones, edge -0.3 points (90% interval -0.7 to +0.1,
  ~157,000 zones); S2/S3 were below the placebo (-1.4 and -2.3 points). Active-box edges (box active on
  ~20% of build dates) held 42.8% vs 43.7%, edge -0.9 points (-2.7 to +0.8, 10,000 zones). Neither is a
  better place for a reaction than a zone of the same shape elsewhere.
- **Breakout follow-through** (`scripts/breakout_backtest.py`, same data): from the close that confirms a box
  breakout, the first of +-2 ATR hit within 20 bars in the breakout's direction 50.3% of the time vs 50.8%
  for the same barriers on other dates (edge -0.5 points, 90% interval -2.7 to +1.7, 2,233 resolved); the
  measured-move target before the box midline 56.2% vs 58.4% (-2.2, interval -4.2 to 0.0). A plain
  20-bar-extreme breakout did about the same (+0.6, interval -0.6 to +1.9). Up breakouts continue more often
  than down ones only because prices drift up; the placebo shows the same split.
- **Source screen and stop test** (`scripts/sr_screen.py`, same data): each candidate source alone (nearest
  candidate per side as a fixed 0.5-ATR zone within 4 ATR) against its moved-zone placebo. In the train
  period (2005-2016) no source's 90% interval lay above 0: pivots +0.2 points, weekly pivots +0.6, POC
  +1.2, unfilled gaps +0.8, 52-week high/low -1.5, SMA50 -0.8, SMA200 -1.1 (all intervals include 0).
  The scorecard's structural stop (S1 zone_low - 0.5 ATR) was hit within 20 bars as often as a stop at the
  same ATR distance on other dates (edge 0.0 points, -0.2 to +0.3, ~61,000 stops).
- **Short side:** if a side has fewer than 3 levels, a note says so, naming an all-time or 52-week
  extreme when price is at one.
- **Reference only:** `levels.evidence` = `{backtested_edge: false, note}` (`LEVELS_EVIDENCE_NOTE`), for the
  reasons in H.1. The report, the chart and the contract (`levels_note`) carry the note.

### H.1 Do the levels hold? (test plan V2, 2026-10-27)
- **Data:** the 32-symbol universe, daily bars 2005-2026 and regular-session hourly bars 2010-2026 (no pre- or
  post-market), cleaned by `scripts/clean_data.py`:
  - each hourly session is put on the daily bars' price basis (a centred 11-session median of daily close /
    last hourly close; volume is rescaled only when that ratio is a split fraction n/m with m ≤ 4), which
    fixed spin-off and dividend basis steps (MMM, PFE, VZ, XLU);
  - sessions whose last close or range disagrees with the daily bar, or with fewer than 4 bars, are excluded;
  - daily placeholders are dropped, moves over 50% must be confirmed by the hourly bars, and windows where
    more than half of 20 bars traded under $10M are flagged illiquid;
  - every exclusion is a row of `exclusions.csv` (symbol, applies, start, end, reason), read with
    `--exclusions`; the 2020 crash (2020-02-20 to 2020-04-30) is excluded everywhere with `--exclude`.
- **Gates, written before the run:** edge ≥ 2 points, 90% cluster-bootstrap interval above 0 and a
  positive edge in each of three periods; a Bonferroni 99.5% interval for the 20 daily hypotheses. Edge =
  hold rate − the moved-zone placebo (Fibonacci ratios: − the other ratios of the same swings).
- **Daily** (`scripts/sr_methods.py`, 13 hypotheses): fresh vs tested once, twice or 3+ times; role
  flips; confirmation by a close or a wick back out of the zone; Fibonacci retracements (0.382, 0.5, 0.618)
  vs 0.30 / 0.45 / 0.70 of the same swings; Fibonacci extensions (1.272, 1.618) vs 1.15 / 1.45 / 1.80;
  weekly and monthly floor pivots and Camarilla levels; the Ichimoku cloud and Kijun. **None passed.**
  Fibonacci retracements did slightly worse than the other ratios (−1.1 points, interval −1.9 to −0.3);
  extensions +1.4 (−0.6 to +3.5). A limit-order trade at each zone (stop 0.5 ATR beyond, target 1 ATR,
  0.05% cost per side) lost 0.03-0.09 ATR per trade in every family, about as much as the same trade at
  the moved zones.
- **Hourly, 2010-2021** (39 items; `intraday_levels.py`, `sr_methods.py --hourly`, `round_cross.py`,
  `confluence_test.py`): prior-day and overnight levels, the session VWAP, the opening gap, daily pivots
  and Camarilla, the daily methods on hourly bars, round-number crosses and confluence. **None passed**;
  the best edge was about +0.6 points. The session VWAP was −6.6, the opening gap −1.4 and zones where 3+
  families agree −1.4. After crossing a round number, price continued 49.4% of the time, the same as after
  crossing a non-round price.
- **Holdout:** hourly bars from 2022 on were kept out of every screen. Since nothing passed, they were
  never read.
- **Earlier baselines** (key levels, boxes, breakouts, sources, structural stop, regimes) moved by less
  than 0.5 points on the cleaned data.
- **Reading:** 52 method variants, none better than the same zone at other prices. The zones are kept as
  reference prices (where price has traded and reacted before), not as places likely to hold. Stops and
  targets come from the calibrated volatility bands in section 6.

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
- **Last earnings:** the latest report on or before `as_of` (`last_earnings`: date, calendar days ago and
  its two-day move, `null` while the bar after the report is missing). A caller compares it with the
  fundamental skill's latest statement period to tell when the fundamentals are out of date.
- **Reactions** (approximation, section 7): the two-day move, adj close of the first bar after the
  report date / adj close of the last bar before it - 1.
  - Up to `EARNINGS_REACTIONS_MAX` = 12 latest reports, giving n, median and mean absolute move.
  - Fewer than `EARNINGS_REACTIONS_MIN` = 8 gets a small-sample note.

---

### K. Price action (`price_action`) and candlestick patterns (`candlesticks`)
Two further pattern libraries, both context next to the scorecard and never part of it. The rules,
parameters (`PA_*`, `CS_*` in config.py) and what the daily-bar approximation leaves out are in
[patterns.md](patterns.md). In short:
- **Price action:** daily-bar versions of Al Brooks' reading: bar types (trend bar, weak bar, doji),
  the market state over the last 20 bars (tight / broad channel or trading range against the EMA20),
  the always-in direction, H1-H4 / L1-L4 pullback counts, wedges, trading-range breakouts that fail,
  20-gap-bar EMA tests and measured-move targets. Eight setups (`pa_*`) get statistics.
- **Candlestick patterns:** twelve classic directional patterns after a 5-bar decline or advance
  (`cs_*`), plus doji markers without statistics.
- **Statistics:** the same machinery as section 4 (`signal_stats.OutcomeCache`, `active_rows`): active
  rows with same-regime baselines, plus a `library` table of every rule at the primary horizon against
  the unconditional baseline.

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
- σ_h = `rv20` × √(h/252); 1σ move = close × σ_h. The output gives it in %, in price and in ATRs, plus the
  range close ± move.
- The 95% band is close × (1 ± `VOL_BAND_K95` × σ_h) with `VOL_BAND_K95` = 2.4, not 2: returns have fat
  tails, and ±2σ held only 92-93% of closes. `range_95_low` is null when the band reaches zero.
- It assumes zero drift and that recent volatility persists. `coverage` gives each band's calibrated share
  (below).
- If earnings fall inside the horizon, `earnings_caveat` gives the date and the median two-day reaction.

**Volatility-band calibration** (`scripts/vol_bands.py`, the skill-bands tables; 32 symbols, daily bars
2005-2026 cleaned as in H.1, the 2020 crash and flagged windows excluded, a start every 5 bars, up and down
moves pooled; ~29,000 starts per horizon). Cover = share of h-day closes inside close × (1 ± kσ_h); touch =
share of paths whose high (low) reached the upper (lower) edge within h days.

| k | cover 10 d | touch 10 d | cover 60 d | touch 60 d | normal model (cover / touch) |
|---|---|---|---|---|---|
| 1 | 68.2% | 31.5% | 65.9% | 33.8% | 68.3% / 31.7% |
| 1.5 | 84.7% | 15.5% | 82.9% | 17.6% | 86.6% / 13.4% |
| 2 | 92.6% | 7.6% | 91.6% | 8.9% | 95.4% / 4.6% |
| 2.4 | 95.8% | 4.3% | 94.9% | 5.3% | 98.4% / 1.6% |
| 3 | 98.0% | 2.0% | 97.6% | 2.5% | 99.7% / 0.3% |

- `VOL_BAND_COVER`, `VOL_TOUCH` (k from 0.25 to 4) and `VOL_PAIR_ODDS` in `config.py` hold these numbers for
  h = 5, 10, 20 and 60; the scorecard uses the calibrated horizon nearest h and interpolates touch rates in k
  on a log scale.
- Up moves reached their edge more often than down moves (10 days, k = 1: 34.0% vs 29.0%; 60 days 40.0% vs
  27.5%): the sample's upward drift. The pooled number assumes no drift, like the base case.
- For comparison, the old 2 ATR stop was touched within 10 days in 34% of cases and within 60 days in 71%.

**Wording.** Each scenario carries a conditional `text`, e.g. "If X closes above Y (basis) on
RVOL ≥ 1.5, the next zones are …; the scenario is invalidated by a close back below Z." `leaning`
records which scenario the bias points toward; it is a description, not a forecast. The triggers and level
targets come from the key levels, so `scenarios.notes` says they are reference only.

**Risk framing (illustrative arithmetic, not a recommendation).**

| Item | Long side (short side mirrors it) |
|---|---|
| Entry reference | last close |
| Volatility stop (primary) | entry × (1 - `VOL_STOP_K` (2.4) × σ_h): the 95% band edge; none without rv20 or at or below zero |
| ATR stop | entry - `STOP_ATR_MULT` (2) ATR |
| Structural stop (reference) | nearest support `zone_low` - `STRUCTURE_STOP_BUFFER_ATR` (0.5) ATR; none if no support below |
| Stop distance | in ATRs, % of entry and σ_h, for each stop |
| Touch rate | for each stop and the first target: the calibrated share of paths that reached a price that many σ_h away within h days |
| First target | entry × (1 + `VOL_TARGET_K` (1) × σ_h), the 1σ band edge; without rv20, the level target |
| Level target (reference) | nearest resistance price; else the bull scenario's first target (projection) |
| Pair odds | the 1σ target and 95% stop played from the close in calibration: target first, stop first, neither within h days (10 days: 31.4% / 4.1% / 64.4%; 60 days: 33.7% / 5.1% / 61.3%) |
| Reward/risk | (target - entry) / (entry - stop), for each stop |
| Position-size example | risk $ = `ACCOUNT_SIZE` × `RISK_PER_TRADE` = $100,000 × 1% = $1,000; shares = floor(risk $ / (entry - stop)); notional and % of account; flagged when above 100% of the account |

The headline example uses the volatility stop when it exists, otherwise the ATR stop; `by_stop` sizes all
three. With no measured edge a stop/target pair is worth about zero before costs whatever its reward/risk
(the 1σ / 2.4σ pair has reward/risk 0.42 and reached its target first 7.6 times as often as its stop; the
64% of paths that reached neither within the horizon make up the difference). The touch rates show the
trade-off between a near and a far stop. No commissions, slippage, financing or
borrow are modelled. A gap can fill a stop well beyond its level.

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
- **Charts 2 and 3:** below the main chart, daily candles over the same window: price action after
  Al Brooks (bar types, EMA20, H/L counts, wedges, trading ranges, failed breakouts, 20-gap-bar tests,
  measured moves, and strips for the market state and always-in direction) and candlestick patterns.
  Panning or zooming any of the three moves all of them. Rules: `references/patterns.md`.
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
- The default route is FMP's official API with certificate verification on. A third-party proxy
  (`FMP_BASE_URL`) may serve data that differs from it; the report names the data source, and key
  figures from a proxy should be verified.
