# Signal library (pre-registered)

This file is the fixed signal library used by `scripts/signal_stats.py`. Each rule is written
exactly as it is coded (the `rule` string of each entry in `signal_stats.SIGNALS` is the same text).
Parameters come from `config.SIGNAL_PARAMS`.

**Do not add, remove or tune rules per ticker.** If you search many rule variants on one stock's
history and keep the one that looks best, you are data mining, and the in-sample statistics stop
meaning anything. A change to this library (a new rule, a different threshold) is a methodology change:
edit this file, `signal_stats.py` and `config.py` together, apply it to every ticker, and say so in the
report.

## Evaluation timing

- Every rule is evaluated **at the close of day t, using bars and indicators at dates <= t only**.
  `tests/test_lookahead.py` checks this: indicators and signals at dates <= t are identical when the
  data after t is removed or replaced with random values.
- Rules run on **split-adjusted** OHLCV (FMP `/full`) and the indicators computed from it
  (`indicators.compute_all`). Forward returns are measured on **dividend-adjusted** prices (see below).
- A missing input (NaN, for example the SMA200 during its first 199 bars) makes the rule False.
- Today's partial bar, if the run happens during market hours, has already been dropped by the data
  layer, so a signal never fires on an incomplete bar.

## The rules

| # | id | Name | Rule (as coded) | Regime filter | Bias | Pillar |
|---|----|------|-----------------|---------------|------|--------|
| 1 | `uptrend_pullback` | Uptrend pullback | `close > sma200 and rsi2 < 10` | above_200 | +1 | mean_reversion |
| 2 | `downtrend_oversold` | Downtrend oversold | `close < sma200 and rsi14 < 30` | below_200 | +1 | mean_reversion |
| 3 | `breakout_55d` | 55-day closing-high breakout on volume | `close > hh55_prev (highest close of the previous 55 bars) and rvol50 >= 1.5` | current_side | +1 | momentum |
| 4 | `new_52w_high` | New 52-week closing high | `close > hh252_prev (highest close of the previous 252 bars) and sma200 is defined` | above_200 | +1 | momentum |
| 5 | `squeeze_release_up` | Squeeze release up | `squeeze_on[t-1] and not squeeze_on[t] and squeeze_bars[t-1] >= 5 and close > sma20` | current_side | +1 | volatility_setup |
| 5 | `squeeze_release_down` | Squeeze release down | `squeeze_on[t-1] and not squeeze_on[t] and squeeze_bars[t-1] >= 5 and close <= sma20` | current_side | -1 | volatility_setup |
| 6 | `golden_cross` | Golden cross (50/200 SMA) | `sma50[t-1] <= sma200[t-1] and sma50[t] > sma200[t]` | current_side | +1 | trend |
| 6 | `death_cross` | Death cross (50/200 SMA) | `sma50[t-1] >= sma200[t-1] and sma50[t] < sma200[t]` | current_side | -1 | trend |
| 7 | `pullback_rising_50` | Pullback to a rising 50 SMA | `sma50 > sma200 and sma50 > sma50[t-20] and abs(close - sma50) <= 1 x atr14` | current_side | +1 | trend |
| 8 | `extension_2sigma` | Extension > 2 sigma above the 20-day mean | `z20 > 2` | current_side | -1 | mean_reversion |
| 9 | `gap_up_atr` | Gap up > 1 ATR on volume | `open - close[t-1] > 1 x atr14[t-1] and rvol50 >= 2` | current_side | +1 | volume_flow |
| 9 | `gap_down_atr` | Gap down > 1 ATR on volume | `close[t-1] - open > 1 x atr14[t-1] and rvol50 >= 2` | current_side | -1 | volume_flow |
| 10 | `macd_bull_cross_below_zero` | MACD bullish cross below zero | `macd[t-1] <= macd_signal[t-1] and macd[t] > macd_signal[t] and macd[t] < 0 (bar index >= 35)` | current_side | +1 | momentum |
| 10 | `macd_bear_cross_above_zero` | MACD bearish cross above zero | `macd[t-1] >= macd_signal[t-1] and macd[t] < macd_signal[t] and macd[t] > 0 (bar index >= 35)` | current_side | -1 | momentum |

`[t-k]` means the value k bars earlier; no index means bar t. **Bias** is the pre-registered hypothesis
(+1: forward returns should beat the baseline, -1: they should lag it). It is a hypothesis to test,
not a conclusion: the statistics decide. `downtrend_oversold` is +1 because the hypothesis is a bounce;
`extension_2sigma` is -1 because the hypothesis is mean reversion from a stretched close.

### Inputs (columns of `indicators.compute_all`)

| Column | Definition |
|--------|------------|
| `sma20`, `sma50`, `sma200` | simple moving averages of the split-adjusted close |
| `rsi2`, `rsi14` | Wilder RSI over 2 and 14 bars |
| `atr14` | Wilder average of the true range over 14 bars |
| `rvol50` | volume / mean volume of the previous 50 bars (today excluded) |
| `hh55_prev`, `hh252_prev` | highest close over the previous 55 / 252 bars (today excluded) |
| `z20` | (close - sma20) / population std of the last 20 closes |
| `squeeze_on` | Bollinger(20, 2) bands inside the Keltner channel EMA(20) +/- 1.5 x ATR(20) |
| `squeeze_bars` | length of the current run of consecutive squeeze bars (0 when off) |
| `macd`, `macd_signal` | EMA(12) - EMA(26) of the close; signal = EMA(9) of the MACD line |

### Notes on individual rules

- **new_52w_high**: a new 52-week closing high always sits above the 200 SMA once the SMA exists,
  because every close in the SMA window (the previous 199 closes and today's) is at or below today's
  close and the previous 199 are strictly below it. The `above_200` filter therefore only adds the
  requirement that the SMA200 is defined (at least 200 bars of history).
- **squeeze releases**: the squeeze must have lasted at least 5 bars (`squeeze_bars[t-1] >= 5`) and be
  off at t. Direction is the close versus the 20 SMA at t; a close exactly on the 20 SMA counts as down.
- **golden/death cross**: the previous bar may sit exactly on the 200 SMA (`<=` / `>=`); a touch that
  does not cross does not count. They use the `current_side` filter because at a cross the close can be
  on either side of the 200 SMA.
- **pullback_rising_50**: "rising" means the 50 SMA is above its value 20 bars earlier
  (`SIGNAL_PARAMS["pullback50_slope_bars"]`). The close may be above or below the 50 SMA, within 1 ATR.
  The rule stays true for several consecutive bars during a pullback; de-clustering keeps one event.
- **gaps**: the gap is measured against the prior bar's ATR, so the gap bar's own range does not widen
  the threshold. RVOL uses the gap bar's full-day volume, which is known at the close of t.
- **MACD crosses**: the EMAs are seeded at the first bar, so crosses before bar index 35
  (`MACD_SLOW + MACD_SIGNAL`) are ignored as warm-up noise. The zero condition is on the MACD line at t.

## Statistics (per signal, per horizon h)

The horizons are those of the chosen mode (`config.HORIZONS[mode]["stat_horizons"]`): swing 5/10/20
trading days, position 20/60. The primary horizon (swing 10, position 60) carries the confidence label
and is the one the scorecard uses.

1. **Events**: the days t on which the rule fired, restricted to the signal's regime mask (below).
2. **Outcome available**: only events with t+h inside the data count. Fires in the last h bars,
   including a currently active fire, are not part of their own sample.
3. **De-clustering**: scanning forward, after a kept event at t the events at t+1..t+h are ignored.
   This is done separately for each horizon, so longer horizons keep fewer events.
4. **Forward return**: `adj_close[t+h] / adj_close[t] - 1`. Where the dividend-adjusted pair is
   missing, `close[t+h] / close[t] - 1` (split-adjusted, price return only). Both prices of a ratio are
   always on the same basis.
5. **MAE (maximum adverse excursion)**: `min(adj_low[t+1..t+h]) / adj_close[t] - 1`, falling back to
   `min(low[t+1..t+h]) / close[t] - 1` where the adjusted window is incomplete. It is a long-side
   measure for every signal (the worst low after entry at the close of t); it is positive when no low in
   the window trades below the entry close.
6. Reported: `n` (de-clustered events), `hit_rate` (share of forward returns > 0), `median`, `mean`,
   `p25`, `p75` (linear interpolation between order statistics), `median_mae`, `confidence`, and
   `dates_sample` (the most recent 10 event dates used).

The hit rate is the share of **positive** forward returns for every signal, bearish ones included.
A bearish signal supports its hypothesis when its median and hit rate sit **below** the baseline.

## Baselines and edge

- **Unconditional**: every day with a valid t+h outcome in the full history.
- **Same-regime**: every day with a valid outcome that satisfies the signal's regime mask:
  - `above_200`: `close > sma200`
  - `below_200`: `close < sma200`
  - `current_side`: the side of the 200 SMA on the date of the currently active fire. The signal's own
    events are restricted to that side as well, so a breakout above the 200 SMA is compared with other
    breakouts above it, and with all days above it. If the SMA200 is undefined (or equal to the close) on
    the fire date, no filter is applied and `regime_used` is `all_days`.
- Baselines use **overlapping windows with no de-clustering**: every eligible day is one observation.
  Their samples are therefore much larger and serially correlated, and they estimate the typical h-day
  outcome rather than a count of independent bets.
- **Edge**: `edge.median = signal median - same-regime median`,
  `edge.hit_rate = signal hit rate - same-regime hit rate`,
  `edge.vs_unconditional_median = signal median - unconditional median`.
  An edge is `null` when either side has no observations.

## Confidence labels

| n (de-clustered events at the horizon) | Label | Use |
|----------------------------------------|-------|-----|
| n >= 30 (`CONFIDENCE_N_NORMAL`) | normal | reported and scored |
| 15 <= n < 30 (`CONFIDENCE_N_LOW`) | low | reported and scored, flagged as low confidence |
| n < 15 | insufficient | reported for transparency, excluded from scoring (`scorable: false`) |

The signal's `confidence` is the label at the primary horizon. Only active signals with normal or low
confidence can nudge their pillar in the scorecard, and only when their same-regime edge at the primary
horizon clears `SIGNAL_EDGE_MIN` (median) and `SIGNAL_HIT_EDGE_MIN` (hit rate); the exact rule is in
methodology.md.

## Active signals

A signal is **active** when it fired on any of the last `SIGNAL_ACTIVE_BARS` (3) bars: the last bar or
the two before it. The most recent fire is reported (`fired_date`, `bars_ago`). Statistics are computed
only for active signals. Every signal, active or not, appears in `library` with its last fire date and
its total number of (raw, not de-clustered) fire days, for transparency.

## Output (`metrics.json` -> `signals`)

- `active`: list of `{id, family, name, bias, pillar, rule, fired_date, bars_ago, regime_used,
  horizons, confidence, scorable}`; `horizons[h]` holds the signal statistics above plus
  `unconditional`, `same_regime` and `edge`.
- `inactive_ids`, `library` (`{id, name, last_fired, fires_total}`), `primary_horizon`,
  `stat_horizons`, `history {first_date, last_date, bars, adj_coverage}`, `definitions`, `caveats`,
  `notes`.

## Caveats (always stated in the report)

- Single stock: the statistics come from this stock's own history only.
- In-sample: the rules are fixed in advance but measured on the history they describe; there is no
  out-of-sample test.
- No transaction costs, slippage or financing; entry at the signal-day close is assumed.
- Baselines use overlapping windows; signal samples are de-clustered.
- History depth is limited by the FMP data plan; the report gives the first date and bar count used.
- Signals are evaluated on split-adjusted prices; returns are measured on dividend-adjusted closes.
