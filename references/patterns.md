# Price action (chart 2) and candlestick patterns (chart 3)

Two pattern libraries sit next to the indicator signals of `references/signals.md`:

- `scripts/price_action.py`: daily-bar approximations of Al Brooks' price-action reading
  (metrics.json `price_action`, chart 2);
- `scripts/candlesticks.py`: classic candlestick patterns (metrics.json `candlesticks`, chart 3).

Both follow the same rules as the signal library: every rule is fixed in advance and applied to every
ticker, evaluated at the close of bar t with bars <= t only (`tests/test_lookahead.py`), and measured
with the forward-return statistics of `references/signals.md` (de-clustering, same-regime and
unconditional baselines, edge, confidence labels, `current_side` regime filter). **They do not feed the
scorecard.** They are context next to it, and the report quotes only the statistics measured here.
All parameters live in `scripts/config.py` (`PA_*`, `CS_*`) and are recorded in
`meta.config_snapshot`.

## What this is not

Al Brooks reads 5-minute E-mini charts bar by bar and leaves much to judgement. The skill only has
daily bars, so:

- intraday material (opening reversals, the first hour, bar-by-bar scalps, the trader's equation on a
  single entry) is left out until an intraday data source exists;
- his definitions are turned into fixed rules, so a chart reader will sometimes see a pattern the
  rules miss, or the reverse;
- his stated probabilities ("most trading-range breakouts fail", "60% for a second entry") are rules
  of thumb from his books. They are never reported; only this stock's own measured statistics are.

## Chart 2: price action

### Readings (per bar, causal)

| Reading | Definition |
|---|---|
| EMA | EMA(20) of the close, Brooks' moving average. Rising / falling = above / below its value 10 bars earlier. |
| Bar type | body / range >= 0.5: trend bar (bull or bear); <= 0.25: doji; in between: weak bar. A bar with no range is a doji. |
| Trading range | over the last 20 bars: high - low <= 5 x ATR14 and the close crossed the EMA at least 3 times. |
| Market state | over the last 20 bars: **tight bull channel** if the EMA is rising and >= 80% of bars have their low above the EMA (mirror: tight bear channel); otherwise **trading range** if the trading-range test holds; otherwise **bull channel (broad)** if the EMA is rising and >= 60% of closes are above it (mirror: bear channel); otherwise trading range. No state before bar 30 (EMA warm-up). |
| Always-in | flips long after 2 bull trend bars in a row closing above the EMA, the last closing above the high of the bar before them, or whenever the state is a tight bull channel; flips short on the mirror image; keeps its direction otherwise. |
| H / L counts | bull side: a pullback starts on a bar with a lower high than the bar before. Each later bar whose high exceeds the prior bar's high is the next count (H1, H2, ...), once a lower high has come after the previous count. A high above the leg's high ends the pullback. The count stops after H4 and resets whenever the EMA is not rising. L counts mirror this with lows, while the EMA is falling. |
| EMA gap bars | bars in a row with the low above the EMA (bull) or the high below it (bear). |
| Swing pivots | fractal pivots with k = 2 (strictly above / below the k bars on each side), known only k bars later. |
| Wedge | three pushes: three successive swing highs (or lows) of the same kind, each beyond the last, within 60 bars, at least 3 bars apart, an opposite swing between each, the third push shorter than the second, and the third the extreme of the window. |
| Failed breakout | a bar trades beyond the high (low) of the trading range of the previous 20 bars and a close back inside follows on that bar or within 2 bars. |
| Measured moves | **leg**: from the last three alternating swings A-B-C (C above A for an up move, C below A for a down move, A at most 120 bars old, price not back beyond A since C): target = C + (B - A), or C - (A - B). **Range**: while a trading range is on, range high + its height and range low - its height. `reached` says whether price has traded at the target since C. |

### Setups (statistics, chart markers)

| id | Name | Bias | Rule (as coded) |
|----|------|------|-----------------|
| `pa_h2_ema` | H2 pullback near the rising 20 EMA | +1 | `second high-count bar (high > prior high) of a pullback, EMA20 rising over 10 bars, close > EMA, pullback low <= EMA + 0.5 x ATR14` |
| `pa_l2_ema` | L2 rally near the falling 20 EMA | -1 | `second low-count bar (low < prior low) of a rally, EMA20 falling over 10 bars, close < EMA, rally high >= EMA - 0.5 x ATR14` |
| `pa_wedge_bottom` | Wedge bottom (three pushes down) | +1 | `three successively lower confirmed swing lows (k=2) within 60 bars, >= 3 bars apart, a swing high between each, the third push shorter than the second and the lowest low of the window; fires on the confirming bar` |
| `pa_wedge_top` | Wedge top (three pushes up) | -1 | `three successively higher confirmed swing highs (k=2) within 60 bars, >= 3 bars apart, a swing low between each, the third push shorter than the second and the highest high of the window; fires on the confirming bar` |
| `pa_failed_breakdown` | Failed breakdown below a trading range | +1 | `in a trading range (20-bar span <= 5 x ATR14, >= 3 EMA crosses), a bar trades below the range low and a close back above it follows on that bar or within 2 bars` |
| `pa_failed_breakout` | Failed breakout above a trading range | -1 | `in a trading range (20-bar span <= 5 x ATR14, >= 3 EMA crosses), a bar trades above the range high and a close back below it follows on that bar or within 2 bars` |
| `pa_gap20_bull_test` | First EMA test after 20 bull gap bars | +1 | `low <= EMA after at least 20 bars in a row with low > EMA` |
| `pa_gap20_bear_test` | First EMA test after 20 bear gap bars | -1 | `high >= EMA after at least 20 bars in a row with high < EMA` |

All setups use the `current_side` regime filter (the side of the 200 SMA on the fire date) and fire
only from bar 30 on. Entry is assumed at the setup bar's close, not at a stop order beyond the
signal bar as Brooks would place it.

### Chart 2

Daily candles over the main chart's window (panning or zooming any chart moves all three). Candles are
coloured by bar type (solid trend bars, pale weak bars, grey dojis); the EMA, trading-range boxes,
wedges (pushes numbered 1-3), H/L counts (the counts that meet the H2 / L2 setup rule are bold), failed
breakouts (FB), 20-gap-bar tests (20) and measured-move targets (MM tags on the price axis) can each be
toggled. Two strips under the price panel show the market state and the always-in direction. The panel
on the right gives the current reading, the setups that fired in the last 3 bars with their statistics,
and every setup's record at the primary horizon.

## Chart 3: candlestick patterns

A **decline** (advance) before a pattern means the close of the bar before the pattern is below (above)
the close 5 bars earlier. A **long body** is at least 0.6 x the ATR14
of the bar before the pattern. Single-bar shapes need a range of at least 0.5 x ATR14
(the bar before's).

| id | Name | Bars | Bias | Rule (as coded) |
|----|------|------|------|-----------------|
| `cs_bullish_engulfing` | Bullish engulfing | 2 | +1 | `after a decline: a bearish bar, then a bullish bar opening at or below its close and closing at or above its open, with the larger body; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_bearish_engulfing` | Bearish engulfing | 2 | -1 | `after an advance: a bullish bar, then a bearish bar opening at or above its close and closing at or below its open, with the larger body; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_hammer` | Hammer | 1 | +1 | `after a decline: lower shadow >= 2 x body, upper shadow <= 0.25 x range, range >= 0.5 x ATR14; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_shooting_star` | Shooting star | 1 | -1 | `after an advance: upper shadow >= 2 x body, lower shadow <= 0.25 x range, range >= 0.5 x ATR14; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_morning_star` | Morning star | 3 | +1 | `after a decline: a long bearish bar (body >= 0.6 x ATR14), a small body (<= 0.3 x the first), then a bullish bar closing above the first body's midpoint; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_evening_star` | Evening star | 3 | -1 | `after an advance: a long bullish bar (body >= 0.6 x ATR14), a small body (<= 0.3 x the first), then a bearish bar closing below the first body's midpoint; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_piercing_line` | Piercing line | 2 | +1 | `after a decline: a long bearish bar, then a bullish bar opening below its close and closing above its body midpoint but below its open; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_dark_cloud_cover` | Dark cloud cover | 2 | -1 | `after an advance: a long bullish bar, then a bearish bar opening above its close and closing below its body midpoint but above its open; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_bullish_harami` | Bullish harami | 2 | +1 | `after a decline: a long bearish bar, then a smaller bullish body inside its body; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_bearish_harami` | Bearish harami | 2 | -1 | `after an advance: a long bullish bar, then a smaller bearish body inside its body; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_three_white_soldiers` | Three white soldiers | 3 | +1 | `after a decline: three bullish trend bars (body >= half the range, upper shadow <= 0.25 x range), each closing higher and opening inside the previous body; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |
| `cs_three_black_crows` | Three black crows | 3 | -1 | `after an advance: three bearish trend bars (body >= half the range, lower shadow <= 0.25 x range), each closing lower and opening inside the previous body; decline / advance = close of the bar before the pattern below / above the close 5 bars earlier` |

**Doji** (`body <= 0.1 x range and range >= 0.5 x ATR14 (marked on the chart; no direction, so no statistics)`): marked on the chart only.

The chart shows the same window as the main chart; candles that belong to a pattern are solid, the
rest pale, with the pattern's short name above (bearish) or below (bullish) it and a bracket over
multi-bar patterns. The panel lists the recent patterns, the patterns that fired in the last 3 bars
with their statistics, and every pattern's record at the primary horizon.

## Statistics and output

- `active`: the setups / patterns that fired in the last 3 bars, in the same shape as
  `signals.active` (per-horizon statistics, same-regime and unconditional baselines, edge, confidence).
- `library`: every rule at the primary horizon over all days: `last_fired`, `fires_total`, `n`,
  `hit_rate`, `median`, `confidence`, and the edge against the **unconditional** baseline on the same
  side (`base_median`, `base_hit_rate`, `edge_median`, `edge_hit_rate`). This shows which patterns
  have had no edge on this stock.
- `price_action` also carries `state`, `always_in`, `bar`, `ema`, `ema_slope`, `close_vs_ema_atr`,
  `trading_range`, `gap_bars`, `last_high_count`, `last_low_count`, `last_wedge`,
  `last_failed_breakout` and `measured_moves`; `candlesticks` carries `recent` (the 5 most recent
  patterns) and `last_doji`.
- Classic candlestick patterns usually show little edge on daily US stocks. A near-zero or wrong-signed
  edge is a result to report as such, not a reason to drop or retune a rule.
