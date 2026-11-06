# Methodology

How every number in `metrics.json` is produced. Parameter names in `CODE` refer to
`scripts/config.py`, the only place parameters live; the values quoted here are the defaults.

**Principles**
- **Regime first.** The regime (trend, range, transitional or volatility extreme) is decided before
  anything else; the other readings are read in its light.
- **Everything is computed in Python** from FMP daily bars. No TA-Lib, and no FMP indicator endpoints in
  the analysis. Claude never estimates a figure in prose.
- **Causal.** Every indicator and signal at bar t uses bars up to and including t. The only forward-looking
  quantities are the labelled forward returns and MAE in signal statistics. A swing pivot is confirmed
  only by the reversal after it, but levels are measured as of the last bar, so no future bar is ever used.
- **Nothing is filled in.** A missing input gives `null` and a note. A missing module gives
  `available: false` with the reason.
- **Scheduled events are context.** Earnings, ex-dividend dates and macro releases are reported with their
  dates; only earnings inside the horizon is listed as a conflict.
- **No scores.** Nothing is scored, weighted or added up into an overall bias or rating. Each module
  reports its own readings; section 5 lists where they disagree.
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
| Profile, quote, earnings | `profile`, `quote`, `earnings` | as returned; earnings: report dates only (`date`, `reported`) | name, sector, ETF flag, IPO date; snapshot quote; earnings calendar |
| NYSE holidays | `holidays-by-exchange`, `exchange=NYSE`, `HISTORY_START` through Dec 31 of next year, one request | `date, name, isClosed, adjCloseTime` (full closures and early closes) | the exchange calendar (section 1.7) |
| Ex-dividend dates | `dividends`, one request | `date` (the ex-dividend date), `adjDividend` (split-adjusted amount), `dividend`, `frequency`, `declarationDate`, `recordDate`, `paymentDate`; includes ex-dates already announced | the `dividend` bar column (section 1.8), the `dividends` module |
| US macro calendar | `economic-calendar`, `MACRO_WINDOW_DAYS` = 89 days starting on `as_of`, one request | `date` (UTC), `country`, `event`, `impact` | the `macro_events` module |

- `^VIX` (with the caret) is the VIX index on FMP; `VIX` is a different instrument.
- FMP's daily `vwap` field is (O+H+L+C)/4. It is ignored.
- The sector ETF comes from `profile.sector` via `SECTOR_ETF`: Technology→XLK, Financial Services→XLF,
  Healthcare→XLV, Consumer Cyclical→XLY, Consumer Defensive→XLP, Energy→XLE, Industrials→XLI,
  Basic Materials→XLB, Real Estate→XLRE, Utilities→XLU, Communication Services→XLC.
- ETFs and funds (`isEtf` or `isFund`) skip the earnings and sector modules; their benchmark is SPY.
  SPY itself skips relative strength vs SPY and reuses its own split-adjusted bars as the benchmark (no
  extra request), so its market-context and regime readings cannot disagree. FMP labels SPY's sector "Financial Services", which means nothing for an ETF, so the snapshot
  leaves `sector` empty for ETFs.
- Every symbol, ETFs and SPY included, gets the holiday list, its own ex-dividend dates and the economic
  calendar.

### 1.2 Which price series drives what

| Quantity | Price basis |
|---|---|
| Candles, SMAs, EMAs, MACD, RSI, ATR, ADX, Bollinger, Keltner, z-score, OBV, A/D, RVOL | split-adjusted `/full` |
| Key levels, pivots, volume profile, gaps, anchored VWAPs, 52-week high/low, scenarios, risk | split-adjusted `/full` |
| Signal rules (evaluated on day t) | split-adjusted `/full` and the indicators above |
| Gaps down on an ex-dividend bar (unfilled-gap levels, the `gap_down_atr` signal, the `largest_gap_6m` VWAP anchor) | split-adjusted `/full`, with the prior bar's prices minus the dividend going ex on the bar (section 1.8); gaps up use the prior bar as it traded |
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
  - stock: 13 (profile, holidays-by-exchange, quote, earnings, /full ×2, /dividend-adjusted ×2, dividends,
    SPY, sector ETF, ^VIX, economic-calendar), in that order: the holiday list comes before every request
    cached by the session rule, whose cutoff it sets (section 1.4)
  - ETF: 11 (no earnings, no sector ETF)
  - SPY itself: 10 (no earnings, no sector ETF, no separate SPY request)
  - Histories under about 19 years need one request per price endpoint.

### 1.4 Caching

- **Location and key:** responses are cached in `~/.cache/fmp-technical` (or `FMP_CACHE_DIR`), keyed by
  base URL, endpoint and sorted query parameters. The API key is never part of the key, the file name
  or the file contents. Files are written to a temporary name and then renamed.
- **Price requests** carry `to=<today in New York>`, so each New York date is a new key.
- **Session rule** (daily-bar endpoints, technical indicators, the quote, earnings and dividends): a
  cached copy is valid only if it was written on the same New York date AND either it was written at
  or after the time that day's bar counts as complete, or it is still before that time. The time is
  the one the partial-bar rule uses (section 1.5): the session's close plus `CLOSE_BUFFER_MINUTES` =
  15 minutes, so 16:15, and 13:15 on a 13:00 early close (`fetch.load_all` gives the client the NYSE
  calendar's cutoff before its first such request); a client without a calendar (`fmp_client.py get`,
  `crosscheck.py`) uses `MARKET_CLOSE_BUFFER` = 16:15. On weekends, the same date is enough.
  - A same-day re-run makes zero requests.
  - A run made during the session is refetched once the bar is complete, so a partial bar is never
    frozen: on an early-close day a copy cached at noon is refetched from 13:15, the moment validation
    takes today's bar as complete.
  - It matters for the benchmarks too: their cache keys are the same for every symbol on a given day,
    so a morning run of any symbol caches SPY, the sector ETF and ^VIX for later runs of the others.
  - Dividends follow the bars because FMP re-bases `adjDividend` on every split, as it does the
    `/full` prices: both come from the same day, so a split between two runs cannot leave cached
    amounts on the old basis against new bars.
- **Other endpoints** use a time-to-live: profile 1 day, the NYSE holiday list 7 days
  (`CALENDAR_TTL_SECONDS`), the economic calendar 12 hours (`MACRO_TTL_SECONDS`).
  - The holiday request's parameters change once a year, so it is nearly always a cache hit.
  - The economic-calendar window depends on `as_of` only, not on the horizon, so the second horizon of
    `--contract` is served from the cache. A re-run more than 12 hours after the first requests it again.
- `--refresh` bypasses the cache.
- **Retries and rate limit:** on HTTP 429, 5xx and network errors a request is tried up to 4 times
  with exponential backoff, under a token-bucket rate limit (`FMP_MAX_PER_MIN`, default 280 per minute).

### 1.5 Validation (stock and every benchmark)

Every step that depends on the date uses the NYSE calendar (section 1.7), for the benchmarks too.

1. Sort by date and drop duplicate dates, keeping the last row.
2. Drop rows with a missing or non-positive open, high, low or close, and (except ^VIX) rows with
   missing or negative volume. The counts are recorded.
3. **Partial bar.** If the last bar is dated today in New York and New York time is before that session's
   close plus `CLOSE_BUFFER_MINUTES` = 15 minutes (16:15; 13:15 on a 13:00 early close), the bar is
   dropped. Its values and the reason go to `data_quality.partial_bar_excluded`. `as_of` is always the
   date of the last *complete* bar, and every module, signal and chart stops there.
4. **Flagged but kept:**
   - inconsistent bars (high < low, or close outside [low, high]);
   - zero-volume bars;
   - data gaps: consecutive bars with at least `GAP_MISSING_SESSIONS` = 1 NYSE session between them.
     Holidays and unscheduled closures (September 2001, Hurricane Sandy in October 2012) are not
     sessions, so they are not gaps. `data_quality.gaps` gives `count`, the latest 10 `items` (`after`,
     `before`, `calendar_days`, `missing_sessions`) and `basis`.
5. **Stale data.** The expected last session is today when today is a session and its close plus 15
   minutes has passed, otherwise the previous NYSE session. The data is stale when the last bar is more
   than `STALE_BDAYS` = 2 sessions behind it. `data_quality.stale.business_days_behind` keeps its name but counts NYSE
   sessions; `stale.basis` says so.
6. **Adjusted coverage:** the share of bars that have `adj_close`.
7. **Liquidity.** Mean shares and mean dollar volume (close × volume) over the last
   `LIQUIDITY_BARS` = 50 bars. The name is flagged illiquid below `ILLIQUID_MIN_AVG_SHARES` = 200,000
   shares or `ILLIQUID_MIN_AVG_DOLLARS` = $5M.

A run always has a calendar (the rules at least). Called without one, as some unit tests do, validation
keeps the weekday rules: 16:15 (`MARKET_CLOSE_BUFFER`), weekdays for the stale check, and bars more than
`GAP_CALENDAR_DAYS` = 4 calendar days apart for gaps.

### 1.6 Restricted or missing data

- **Plan-restricted:** HTTP 402/403, or an FMP message mentioning restricted, premium, subscription,
  upgrade or exclusive. HTTP 401 means an invalid key and stops the run.
- **Empty arrays** count as "no data". So does an economic-calendar answer without a US row: an 89-day
  window always lists US releases, so `macro_events` is unavailable with the reason, never "no events".
- **Where it is recorded:** `meta.skipped[<module>]`, `meta.restricted` and the notes.
  - The analysis continues without the affected input and fills nothing in.
  - Skip keys: `profile`, `quote`, `earnings`, `sector`, `spy`, `vix`, `dividends`, `macro_events`.
- **Modules** whose inputs are missing report `available: false` with the reason. They are listed in
  `meta.modules_unavailable`.
- **The NYSE holiday list** has no skip key: without it the built-in exchange rules stand in (section
  1.7), `data_quality.calendar.problem` and a note say why, and nothing else changes.
- **Without dividends** the `dividend` column is 0 on every bar, so gaps down on ex-dividend bars are
  not adjusted; `levels.notes` says so.
- **No usable /full history** stops the run with "no price data for X".

### 1.7 Exchange calendar (`market_calendar.py`)

- **Source:** the one `holidays-by-exchange` request (section 1.1) united with built-in NYSE rules:
  - full closures = FMP's closed days ∪ the rule holidays ∪ the special closures below;
  - early closes = FMP's early closes ∪ the rule early closes, FMP's time winning.
- **Rules:**
  - holidays: New Year's Day (a Sunday moves to Monday; a Saturday is not observed), Martin Luther King
    Jr. Day (from 1998), Washington's Birthday, Good Friday, Memorial Day, Juneteenth (from 2022),
    Independence Day, Labor Day, Thanksgiving and Christmas; Juneteenth, Independence Day and
    Christmas move to the nearest weekday;
  - special closures: 1994-04-27 (Nixon), 2001-09-11 to 09-14, 2004-06-11 (Reagan), 2007-01-02 (Ford),
    2012-10-29 and 10-30 (Hurricane Sandy), 2018-12-05 (G. H. W. Bush), 2025-01-09 (Carter);
  - early closes at `EARLY_CLOSE_DEFAULT` = 13:00: July 3 and December 24 when they fall on Monday to
    Thursday, and the day after Thanksgiving.
- **Against FMP:** the rules give the same closed days as FMP's list for 1991-2028 and add 1990-01-01,
  which FMP's list lacks. FMP's early-close list is incomplete before about 2013; the rules fill it in.
  The union also picks up a closure FMP lists that no rule knows, such as a national day of mourning
  announced at short notice.
- **Fallback:** when FMP's list is restricted, fails or holds no usable row, the rules alone are used
  (`source` "rules").
- **Recorded:** `data_quality.calendar` = {`source` ("fmp+rules" or "rules"), `exchange`, `closed_days`,
  `early_closes`, `problem`}.
- **Used for:** the partial-bar rule, the stale check and data gaps (section 1.5); trading days to
  earnings, to the next ex-dividend date and to macro events (sections 3 J-L); the weekly partial flag
  (section 2). A session runs from `SESSION_OPEN` 09:30 to `SESSION_CLOSE` 16:00 New York time, or to
  its early close.

### 1.8 Ex-dividend dates (`dividends.py`)

- **Why:** `/full` prices are split-adjusted, not dividend-adjusted. On an ex-dividend date the price
  opens lower by about the dividend for mechanical reasons, and that drop looks like a gap down. On
  high-dividend names and monthly payers most ex-dividend dates show one: since 2010, HYG had 102 raw gaps
  down on ex-dividend dates, of which 7 were still gaps net of the dividend.
- **Rows:** `date` is the ex-dividend date and `adjDividend` the amount adjusted for later splits, the
  basis of the `/full` bars (NVDA's $0.04 of 2021 is 0.004 after the 10:1 split of 2024). Dropped and
  counted: rows without a usable date, with `adjDividend` missing or ≤ 0, and exact repeats (FMP
  sometimes lists one distribution twice). Different amounts on one ex-date (a regular and a special
  dividend) become one entry with the amounts summed. `data_quality.dividends` = {`ex_dates`,
  `rows_dropped`, `problem`}.
- **Bar column:** `bars["dividend"]` holds the amount going ex on each bar and 0 elsewhere; several on
  one bar are summed. Without dividend data the column is 0 throughout. An ex-date without a bar maps to
  the bar of the first NYSE session on or after it, where its drop shows, if the symbol has a bar that
  session:
  - an ex-date on a weekend or holiday, or inside a multi-day closure, finds its bar however long the
    closure (KO's 2001-09-12, during the September 2001 closure, maps to 2001-09-17);
  - an ex-date on a session without a bar (a data gap) stays unmatched, and the `dividends` module
    names it;
  - without an exchange calendar (as in some unit tests), the first bar at most `DIVIDEND_MATCH_DAYS` = 4
    calendar days later.
- **Where it applies:** only gaps down, in the three gap measures, on bar t with dividend D. A dividend
  can only explain a drop, so gaps up are measured from the prior bar as it traded, exactly as without
  dividends:
  - unfilled-gap levels (section 3 H): a gap down needs high[t] < low[t-1] - D, and its zone's top is
    low[t-1] - D; a gap up stays low[t] > high[t-1], with its zone's bottom at high[t-1];
  - the `gap_down_atr` signal ([signals.md](signals.md)): the prior close minus D; `gap_up_atr` keeps the
    prior close;
  - the `largest_gap_6m` anchored-VWAP anchor (section 3 F): an opening drop is measured from the prior
    close minus D and counts as no gap when the dividend explains all of it (never as a gap up); an
    opening rise is measured from the prior close.

  A drop the dividend explains is no gap, and a larger drop counts only the part beyond the dividend.
  An ex-dividend bar that opens flat or a little higher is no gap up. The dividend is declared before t,
  so this is causal. Without a dividend column, or with zeros in it, every result is the same as before.
- **Deliberately unchanged:** ATR and the true range, candles, price action, SMAs, RSI, MACD and the
  Bollinger and Keltner bands use the bars as they traded, as charting platforms do (section 7).

---

## 2. Indicators (`scripts/indicators.py`)

Every indicator is NaN until it has its full window; nothing is back-filled. Column names carry the
default periods (`rsi14`, `atr14`, …); the periods themselves come from config, except the SMA periods,
which are fixed (below).

**Trend SMAs and the context SMA100.** `SMA_PERIODS` = (20, 50, 200) are the trend SMAs: the SMA stack,
the regime direction votes and the signal rules use them, and regime.py and signal_stats.py
read its second and third periods as the 50- and 200-day lines. `SMA_CONTEXT_PERIODS` = (100,) adds the
100-day SMA (`sma100`, `sma100_slope`), a widely watched reference line. SMA100 is context only: it is
reported, drawn and used as a low-weight level candidate, and it is not in the regime votes, the stack
or the signals. Both settings are fixed, not tuning knobs: the columns are named `sma20` /
`sma50` / `sma200` (fast, mid, long, by position) and `sma100`, and the chart, levels, crosscheck, the
contract (`trend.sma100`) and the UI name the 100-day line. A period inserted or replaced would silently
put another line under one of these names (with (20, 50, 100), the `sma200` column, the regime's long
line and a second level candidate would all be the 100-day mean), so `compute_all` refuses any other
value of either setting. Another reference line would need its own column wired into the chart, levels,
crosscheck, contract and UI, not just a config entry.

| Indicator | Definition |
|---|---|
| SMA(n) | rolling mean, first value at bar n-1; n = 20, 50, 200 (trend) and 100 (context) |
| EMA(n) | `ewm(span=n, adjust=False)`, alpha = 2/(n+1), seeded at the first value (so MACD is biased toward 0 in roughly the first 3×26 bars of a short history) |
| Wilder RMA(n) | first value = mean of the first n values; then avg += (x - avg)/n |
| SMA slope | `SMA[t] / SMA[t - SLOPE_BARS] - 1`, `SLOPE_BARS` = 20 (all four SMAs) |
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
| Weekly bars | `W-FRI` groups: open first, high max, low min, close last, volume sum; dated by the week's last trading day; the latest week is `partial` while it has sessions left, i.e. its last bar comes before the week's last NYSE session (a week cut short by Good Friday is complete on Thursday) |

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
- **Type:**
  - `vol_extreme` if the vol label is extreme;
  - otherwise `trend`, `range` or `transitional`;
  - transitional also when ADX is unavailable.
- **Dividend note:** the votes, MACD, the weekly direction and the 52-week high use prices as traded,
  which drop by each dividend on its ex-date. When the 252-bar total return beats the price return by
  `DIVIDEND_DRIFT_NOTE` (3 points) or more, `regime.notes` gives both returns and says the dividends pull
  these readings down (HYG on 2026-11-04: price -3.3%, total +2.6%, every vote down). The readings
  themselves are unchanged.

### C. Trend and momentum (`trend_momentum`)
- **Return index:** all returns use the total-return index P (cumulative product of daily returns,
  section 1.2).
- **12-1 momentum** = P[t-21] / P[t-252] - 1. It needs 253 bars; otherwise it is null with a note.
- **Returns:** 1m / 3m / 6m / 12m over 21 / 63 / 126 / 252 bars.
- **SMA20/50/200 (the trend SMAs):**
  - values, 20-bar slopes and close vs each SMA;
  - the stack: the available SMAs ordered top to bottom. "20>50>200" is bullish, "200>50>20" bearish,
    any other order mixed. `stack_periods` = [20, 50, 200] names the SMAs it uses.
- **SMA100 (context only):** `sma100`, `slopes.sma100` and `close_vs.sma100` sit next to them (null with
  a note before bar 100). It is a reference line: not part of the stack or `stack_state`, and not used
  by the regime votes or the signals.
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
      `LARGEST_GAP_LOOKBACK` = 126 bars; on an ex-dividend bar an opening drop is measured from the
      prior close net of the dividend, never turning into a gap up, and an opening rise from the prior
      close itself (section 1.8).
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
  - gap down: the mirror case;
  - on an ex-dividend bar t paying D, a gap down needs high[t] < low[t-1] - D, and low[t-1] - D is its
    zone's top (section 1.8); a gap up keeps high[t-1] as it traded, in the test, the fill and the zone.
    Each gap carries `ex_dividend` (D when its bar is an ex-dividend bar, or null);
  - `gap_adjustment` gives the `method`, the number of ex-dividend bars in the window
    (`ex_dividend_bars`) and the raw gaps down the dividend explains (`neutralized`: `date`, `amount`,
    `raw_direction`, always "down", and `raw_unfilled`, whether it would otherwise be an unfilled-gap
    level). A note names those that would have been levels, and another says so when dividend data is
    missing and gaps are not adjusted.
- **Candidates, source types and weights** (`LEVEL_SOURCE_WEIGHTS`):
  - swing pivot highs and lows (pivot, 1.0 x `swing_atr` / 1.5, between 1 and `SWING_WEIGHT_CAP` = 3);
  - supply/demand zones (supply_demand, 1.0; x `LEVEL_FRESH_FACTOR` = 1.5 when fresh);
  - POC (volume_profile, 1.0), VAL, VAH and HVNs (0.5);
  - 52-week high and low (range_52w, 1.0);
  - each remaining gap: one zone if at most `GAP_ZONE_MAX_ATR` = 1 ATR wide, else its two edges (gap, 0.75);
  - current SMA50, SMA100 and SMA200 (moving_average, 0.25 each; SMA100 is the context line of
    section 2) and anchored VWAPs (anchored_vwap, 0.25). SMA100 joined on 2026-11-05: one more
    low-weight candidate can shift a zone's density, edges and score and so, marginally, which zones
    are chosen and what is built from them (section 6): the scenario triggers and targets, and the risk
    block's reference-only structural stop and level target, with their distance, touch rate,
    reward/risk and `by_stop.structural` size (contract `horizons.*.stops_ref.*.structural`,
    `structural_distance_pct` and `touch_rate.structural`). The volatility-band stop and target, the
    ATR stop and the headline position-size example do not use the levels and do not move (the first
    target falls back to the level target only when there is no volatility target, e.g. without rv20);
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
  - `touches` = tests = `holds` + `breaks` + `weak`; `flips` (counted among the holds), `fresh` = no test,
    `last_test`.
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
- **Extremes** (`levels.extremes`, with its `rule`): the close is at the 52-week high when it is the highest
  close of the last 252 bars, the last bar included, or within `EXTREME_NEAR_ATR` (0.5) ATR of their highest
  high; at the all-time high the same over all available history. Lows mirror it. A record close that ends
  well below its own bar's high still counts (AAPL on 2026-11-04 closed 0.58 ATR below a record high).
- **Short side:** if a side has fewer than 3 levels, a note says so, naming an all-time or 52-week
  extreme when price is at one.
- **Reference only:** `levels.evidence` = `{note}` (`LEVELS_EVIDENCE_NOTE`). The report, the chart and the
  contract (`levels_note`) carry the note. Read the zones as where price has traded or turned before, not
  as predictions.

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
- **Partial week:** the latest week is flagged while it has NYSE sessions left (section 2).

### J. Event risk (`event_risk`, stocks only)
- **Next earnings:** the earliest report after `as_of`, or one dated `as_of` that is not reported yet.
  - Calendar days to the report.
  - Trading days (`trading_days_est`): NYSE sessions from `as_of` to the report date, counted with numpy
    business days minus the calendar's closed days; `trading_days_note` names the calendar source.
  - `within_horizon`: trading days ≤ the primary horizon. `within_stat_horizons` gives the same flag
    for each stat horizon.
- **Last earnings:** the latest report on or before `as_of` (`last_earnings`: date, calendar days ago and
  its two-day move, `null` while the bar after the report is missing).
- **Reactions** (approximation, section 7): the two-day move, adj close of the first bar after the
  report date / adj close of the last bar before it - 1.
  - Up to `EARNINGS_REACTIONS_MAX` = 12 latest reports, giving n, median and mean absolute move.
  - Fewer than `EARNINGS_REACTIONS_MIN` = 8 gets a small-sample note.

### K. Ex-dividend dates (`dividends`)
Stocks, ETFs and SPY alike, from the parsed dividends (section 1.8) and the bars up to `as_of`.
- **Last:** the latest ex-date on or before `as_of` (`last_ex_dividend`: `date`, `amount`, `amount_pct` =
  amount / last close, `frequency`).
- **Next:** the first announced ex-date after `as_of` (`next_ex_dividend`: `date`, `amount`, `amount_pct`,
  `amount_atr` = amount / ATR(14), `frequency`, `declaration_date`, `payment_date`, `calendar_days`,
  `trading_days_to`).
  - `trading_days_to` counts NYSE sessions as for earnings; an ex-date that is not a session counts up to
    the next session, where the drop shows.
  - `amount_pct` and `amount_atr` size the mechanical drop against the price and against an ordinary
    day's range.
- **Horizon:** `within_horizon` is `trading_days_to` ≤ the primary horizon; `within_stat_horizons` gives
  the flag per stat horizon. Both are null when no ex-date is announced yet, and a note says one declared
  later may still fall inside the horizon.
- **Pace:** `ex_dates_12m` counts the ex-dates in the 365 days up to `as_of`. With two or more,
  `projected_in_horizon` (`ex_dates`, `amount_pct`) estimates how many fall inside the primary horizon:
  dates spaced 365 / `ex_dates_12m` days apart, counted on from the announced ex-date when it is inside the
  horizon (it counts too) or from the last one when none is announced, and their total at that amount as a
  share of the close; 0 when the announced one lies beyond the horizon. A note says so when none is
  announced and the estimate is 1 or more, or when the announced one is inside the horizon and the estimate
  is 2 or more (HYG on 2026-11-04, monthly, 60 sessions: about 3, 1.6% of the close). It is an estimate from
  the spacing, not a schedule.
- **Non-payers:** `pays_dividends` is false when FMP lists no dividends (TSLA), or when no ex-date lies
  in the `DIVIDEND_ACTIVE_DAYS` = 400 calendar days up to `as_of` and none is announced (suspended).
  `next_ex_dividend` and the horizon flags are then null (a suspended payer keeps `last_ex_dividend`),
  and a note says why.
- **Coverage:** `ex_dates_in_history` (ex-dates within the price history) and `matched_to_bars`; a note
  names ex-dates that found no bar.
- **No dividend yield:** the skill carries no fundamentals.
- **Context only:** never a conflict. When the next ex-date falls inside
  the primary horizon, or none is announced and the pace puts one or more inside it, the risk notes say so
  (section 6).
- **Unavailable** (`available: false` with the reason) when the dividends request is restricted or fails.

### L. Macro events (`macro_events`)
- **Input:** the one `economic-calendar` request: `MACRO_WINDOW_DAYS` = 89 days starting on `as_of`
  itself (`window`). FMP filters by UTC date, and a release after the `as_of` close is still dated `as_of`
  in UTC (until 20:00 New York in summer, 19:00 in winter), so the window includes that date; releases
  at or before the `as_of` close are dropped below. FMP returns at most about 90 days per request and
  silently drops the rest, and the window does not depend on the horizon. Only `MACRO_COUNTRY` = US rows
  are used.
- **Times:** FMP gives UTC; `time_ny` is New York time, daylight saving included. A row at exactly 00:00
  UTC is a date-only listing (holidays, elections, the Jackson Hole symposium): it keeps its date and
  has no time.
- **Classification** by the event name without its "(period)" suffix, not by FMP's `impact` label, which
  changes between releases of one event (Michigan consumer sentiment: 7 times High, 4 times Medium):
  1. `MACRO_EXCLUDE`: weekly jobless claims are dropped;
  2. `MACRO_GROUPS`, anchored case-insensitive regexes:
     - tier 1: `fomc` (rate decision, statement, projections, press conference), `cpi`, `jobs`
       (payrolls, unemployment rates, hourly earnings, weekly hours, participation rate), `pce` (PCE
       price index, personal income and spending), `ppi`, `gdp`, `retail_sales`;
     - tier 2: `fomc_minutes`, `fed_policy` (Jackson Hole, Monetary Policy Report),
       `ism_manufacturing`, `ism_services`, `jolts`, `consumer_sentiment` (Michigan, Conference Board);
  3. tier 3 (`id` "other", labelled with the event's own name): any other US event FMP rates "High";
  4. the rest is only counted (`unclassified`).
- **Merging:** the tier 1-2 rows of one group on one New York date form one event at the earliest row's
  time: the CPI MoM / YoY / core rows become one `cpi`, the FOMC decision at 14:00 and the press
  conference at 14:30 one `fomc`. `releases` lists the original names and `impact` the highest FMP label.
  Tier 3 is one event per release and date in the same way: FMP lists one release under several names
  (Existing Home Sales and Existing Home Sales MoM; Durable Goods Orders MoM, Ex Transp MoM and Ex Defense
  MoM), so rows whose names differ only by a trailing MoM, YoY or QoQ or an "Ex ..." qualifier are one
  event, labelled with the release name ("Durable Goods Orders"), and the tier-3 count counts releases.
- **Timing and session** (the first session the release can move):

  | `timing` | When | `session` |
  |---|---|---|
  | `non_session` | on a weekend or exchange holiday, with or without a time | the next session |
  | `date_only` | on a session day, no time given | that day |
  | `pre_open` | before 09:30 | that day |
  | `intraday` | before that day's close (13:00 on an early close) | that day |
  | `after_close` | at or after the close | the next session |

  Events whose session is on or before `as_of` are dropped (the last bar already holds them).
  `trading_days_to` = NYSE sessions in (`as_of`, `session`]; `calendar_days` = days from `as_of` to the
  date.
- **Output:** `events` (tiers 1-3, in session order); `within_horizon` (`id`, `label`, `tier`, `date`,
  `time_ny`, `timing`, `session`, `trading_days_to` of every event with `trading_days_to` ≤ the primary
  horizon); `within_stat_horizons`
  (the number of tier-1 events within each stat horizon); `next` (the first event of each tier-1 group,
  null when the window has none); `counts` (`tier1`, `tier2`, `tier3`, and the US rows after `as_of` that
  were `excluded` or `unclassified`).
- **Coverage:** FMP lists FOMC dates (`MACRO_FAR_AHEAD_GROUPS`) months ahead but other releases only
  about 6-8 weeks ahead. `coverage.listed_through` is the date of the last other US row.
  `coverage.complete` (and `complete_stat_horizons` per stat horizon) says whether the horizon's last
  session (`horizon_end`) comes by that date and within the window. A position horizon (60 sessions) is
  usually incomplete: its list is a lower bound, and a note says how far each part reaches (other releases
  through `listed_through`, FOMC dates through `window.to`, which can fall a few sessions before
  `horizon_end`).
- **Context only:** dates and times, never an expected outcome or direction. Macro events are not a conflict: a position horizon nearly always holds an FOMC decision and a CPI
  release, so a conflict would be constant noise. The contract carries tiers 1-2
  (`MACRO_CONTRACT_MAX_TIER` = 2); `metrics.json` lists all three.
- **No history:** the plan rejects an early `from` (2025-11-01 was refused; dates from 2026-01-29 were
  accepted when tested on 2026-11-04), so past releases, and the stock's moves on them, are not available.
- **Unavailable** (`available: false` with the reason; `events`, `within_horizon`, `next`, `counts` and
  `coverage` null) when the request is restricted or fails, or returns no usable US row. FMP answers `[]`
  without an error for a range it does not serve, and an 89-day window starting on `as_of` always lists
  US releases, so an empty listing is missing data, not a window without events.

---

### M. Price action (`price_action`) and candlestick patterns (`candlesticks`)
Two further pattern libraries, both context. The rules,
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
  - fewer: insufficient. Listed for transparency, but too few cases to be evidence.

  The label is about sample size only: it is not a score and says nothing about direction.
- **Confirmed:** an active signal whose label is normal or low and whose same-regime edge at the primary
  horizon backs its pre-registered bias: bias × median edge > `SIGNAL_EDGE_MIN` (0.5%) **and** bias ×
  hit-rate edge > `SIGNAL_HIT_EDGE_MIN` (5 points). An edge in the opposite direction never confirms;
  flipping a rule to match one stock's history would be data mining. The contract reports it as
  `confirmed`; it is not added up with anything.
- **Caveats** (emitted in `signals.caveats`): single stock, in-sample, no costs or slippage,
  overlapping baselines vs de-clustered events, history depth limited by the plan.

---

## 5. Conflicts (`conflicts`)

There is no scorecard: no pillar scores, weights, weighted score, bias label or confidence in a direction.
`conflicts.items` lists the places where the module readings disagree, each with `type`, `involved`,
`description` (with the numbers), `typical_meaning`, and `key` / `params` for the UI's translations. They are
listed only; nothing is added up.

| Type | When | Typical meaning (abridged) |
|---|---|---|
| `price_flow_divergence` | OBV or A/D divergence active | participation not confirming the price extreme; can persist in strong trends |
| `daily_vs_weekly` | weekly contradicts daily | counter-trend move inside the larger trend |
| `rs_vs_market` | the stock beat SPY over both 3m and 6m (`relative_strength.vs_spy.rel_return_3m` / `_6m` > 0) while SPY is below its SMA200 | leadership against a risk-off tape |
| `squeeze_no_direction` | squeeze on with regime direction 0 | break direction unknown |
| `earnings_in_horizon` | next earnings inside the primary horizon | the event can override the technicals; a reaction adds a jump to the ordinary range |
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
- If earnings fall inside the horizon, `earnings_caveat` gives the date, the median two-day reaction and how
  many of the listed reactions moved more than the horizon's 1σ move (and the largest): an earnings jump
  adds to the ordinary range the band describes.

**Volatility-band calibration** (`VOL_CALIBRATION`; measured once on 32 US stocks and ETFs, daily bars
2005-2026, the 2020 crash and illiquid windows excluded, a start every 5 bars, up and down moves pooled;
~29,000 starts per horizon). Cover = share of h-day closes inside close × (1 ± kσ_h); touch = share of
paths whose high (low) reached the upper (lower) edge within h days.

| k | cover 10 d | touch 10 d | cover 60 d | touch 60 d | normal model (cover / touch) |
|---|---|---|---|---|---|
| 1 | 68.2% | 31.5% | 65.9% | 33.8% | 68.3% / 31.7% |
| 1.5 | 84.7% | 15.5% | 82.9% | 17.6% | 86.6% / 13.4% |
| 2 | 92.6% | 7.6% | 91.6% | 8.9% | 95.4% / 4.6% |
| 2.4 | 95.8% | 4.3% | 94.9% | 5.3% | 98.4% / 1.6% |
| 3 | 98.0% | 2.0% | 97.6% | 2.5% | 99.7% / 0.3% |

- `VOL_BAND_COVER`, `VOL_TOUCH` (k from 0.25 to 4) and `VOL_PAIR_ODDS` in `config.py` hold these numbers for
  h = 5, 10, 20 and 60; the risk module uses the calibrated horizon nearest h and interpolates touch rates in k
  on a log scale.
- Up moves reached their edge more often than down moves (10 days, k = 1: 34.0% vs 29.0%; 60 days 40.0% vs
  27.5%): the sample's upward drift. The pooled number assumes no drift, like the base case.
- For comparison, the 2 ATR stop (`atr_stop`) was touched within 10 days in 34% of cases and within 60 days in 71%.

**Wording.** Each scenario carries a conditional `text`, e.g. "If X closes above Y (basis) on
RVOL ≥ 1.5, the next zones are …; the scenario is invalidated by a close back below Z." Neither scenario
is favoured: there is no bias to point toward one. The triggers and level
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

Stops and the first target come from the volatility bands; the structural stop and the level target come
from the key levels and are shown for reference only. The headline example uses the volatility stop when it
exists, otherwise the ATR stop; `by_stop` sizes all three. With no measured edge a stop/target pair is worth
about zero before costs whatever its reward/risk (the 1σ / 2.4σ pair has reward/risk 0.42 and reached its
target first 7.6 times as often as its stop; the 64% of paths that reached neither within the horizon make
up the difference). The touch rates show the trade-off between a near and a far stop. No commissions,
slippage, financing or borrow are modelled. A gap can fill a stop well beyond its level.

**Event notes.** `risk.notes` names the scheduled events inside the primary horizon; the arithmetic does
not change.
- Earnings: overnight gap risk through either stop.
- The next ex-dividend date: the price is expected to open lower by about the dividend (its amount, % of
  the close and ATRs) for mechanical reasons, so a long-side stop closer to the price than that can be
  touched by the ex-dividend drop alone. When `dividends.projected_in_horizon` puts 2 or more inside the
  horizon, the note adds their total.
- No ex-date announced but the pace puts one or more inside the horizon: the same, with the projected
  number and total at the last amount.

Both ex-dividend notes add that the volatility bands and touch rates assume zero drift, so they leave the
drop out.

---

## 7. Approximations

| Item | Approximation | Why |
|---|---|---|
| Anchored VWAP | daily bars: typical price (H+L+C)/3 × daily volume, cumulated from the anchor bar | no intraday data; FMP's daily `vwap` is (O+H+L+C)/4 and is not a VWAP |
| Volume profile | each bar's volume spread evenly across its high-low range | no intraday volume-at-price |
| Earnings reaction | two-day close-to-close move across the report date | FMP `earnings` has no before-open / after-close flag; the window contains the reaction either way, plus one extra day of noise |
| Trading days to earnings | NYSE sessions from `as_of` to the report date | FMP gives no time of day: after an after-close report the price reacts one session later than counted |
| Dividends in the bars | only gaps down, in the three gap measures, are taken net of the dividend (section 1.8); ATR and the true range, candles, price action, SMAs, RSI, MACD, the bands, the trend votes, the weekly direction and the 52-week high and low use the bars as they traded, so an ex-dividend bar can still read as a down bar or widen the true range, and a large payer's trend readings lean down (the regime note, module B) | split-adjusted prices, as on charting platforms |
| Size of the ex-dividend drop | the full split-adjusted dividend (`adjDividend`) | the actual open also reflects news and order flow |
| Early closes | an early close only the rules know (FMP's list is incomplete before about 2013) closes at `EARLY_CLOSE_DEFAULT` = 13:00 | early closes only decide whether today's bar is complete |
| Macro event times | FMP's published times, converted from UTC to New York; a date-only row (00:00 UTC) has no time and maps to that day's session | FMP may revise a time; the 12-hour cache picks it up |
| Macro event list | tiers by event name (`MACRO_GROUPS`); releases other than FOMC dates are listed only about 6-8 weeks ahead, FOMC dates up to the request window's end (`coverage`) | FMP's impact label changes between releases of one event |

---

## 8. Chart (`chart.html` and the local UI)

- **What it shows:** the last `chart_bars` complete bars (252 for swing, 504 for position) of
  split-adjusted candles, with:
  - SMA20/50/100/200 (SMA100, the context line, in its own colour) and the anchored VWAPs;
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

`python scripts/crosscheck.py TICKER [--json PATH]` compares the skill's SMA(50), SMA(100) and RSI(14)
with FMP's `technical-indicators/sma` (periodLength 50 and 100, timeframe 1day) and
`technical-indicators/rsi` (periodLength 14). The analysis never uses these endpoints.

- **Inputs:** the skill's side runs `indicators.compute_all` on its own validated `/full` bars, from the
  same earliest date as the analysis.
- **Comparison:** the two series are aligned by date and compared over the last `CROSSCHECK_WINDOW` =
  250 shared dates. The output gives max and mean absolute difference, share within tolerance and the
  worst date.
- **SMA(50) and SMA(100) tolerance:** `CROSSCHECK_SMA_TOL` = 0.01% of that day's close. A rolling mean
  has no smoothing choices, so a larger difference means different input prices.
- **RSI(14) tolerance:** `CROSSCHECK_RSI_TOL` = 0.5 points, after skipping `CROSSCHECK_RSI_WARMUP` = 250
  bars of each series.
  - Wilder smoothing is path-dependent: a value depends on the seed bar and on how much history comes
    before it, and the seed's weight decays like (13/14)^k.
  - FMP does not document its seeding or history depth, so small RSI differences can come from that
    alone.
- **Today's partial bar** is excluded on the skill's side.
- **Restricted plan:** if the plan lacks the endpoints, the script says so and exits 0.

---

## 10. Output conventions and limitations

**`metrics.json`.**
- Top-level keys in this order: `ticker`, `as_of`, `data_source`, `horizon`, `snapshot`,
  `data_quality`, `market_context`, `regime`, `trend_momentum`, `relative_strength`, `mean_reversion`,
  `volume_flow`, `volatility`, `levels`, `multi_timeframe`, `event_risk`, `dividends`, `macro_events`,
  `signals`, `price_action`, `candlesticks`, `conflicts`, `scenarios`, `risk`, `meta`.
- **Values:** floats are rounded to `METRICS_SIG_DIGITS` = 6 significant digits, dates are
  `YYYY-MM-DD`, and `null` means "not computable" (the module's `notes` say why). Percent-like values
  are fractions (0.05 = 5%).
- **`meta`** holds network calls, cache hits, restricted endpoints, errors, skipped data,
  `modules_unavailable`, notes, the connection (`route`, `base`, `tls_verify`; never the key) and a
  `config_snapshot` of the key parameters (including the `calendar`, `dividends` and `macro` settings and
  the context SMA periods, `indicators.sma_context`).
- **Module failures:** a module that raises becomes `available: false` with "internal error: …" and is
  listed in `meta.errors`. The rest of the run continues.

**Limitations.**
- Single-stock, in-sample statistics, with no transaction costs.
- History depth is limited by the FMP plan and `HISTORY_START`. The IPO date from the profile sets the
  floor; a relisted name may have older bars that are never requested.
- The exchange calendar is NYSE's (`CALENDAR_EXCHANGE`), built from FMP's list and the rules for 1990
  through the end of the year after next.
- Macro events are scheduled dates only: the plan gives no past releases, so there are no statistics of
  how this stock moved on them, and FMP's listing reaches only some weeks ahead (`coverage`).
- Daily data only. VWAP and the volume profile are approximations, and intraday structure is invisible.
- Benchmarks cover the last 5 years, so RS lines, beta and the VIX percentile use at most that span.
- The default route is FMP's official API with certificate verification on. A third-party proxy
  (`FMP_BASE_URL`) may serve data that differs from it; the report names the data source, and key
  figures from a proxy should be verified.
