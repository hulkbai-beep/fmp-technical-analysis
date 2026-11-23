# Price action (chart 2) and candlestick patterns (chart 3)

Two pattern libraries sit next to the indicator signals of `references/signals.md`:

- `scripts/price_action.py`: daily-bar approximations of Al Brooks' price-action reading
  (metrics.json `price_action`, chart 2);
- `scripts/candlesticks.py`: classic candlestick patterns (metrics.json `candlesticks`, chart 3).

Both follow the same rules as the signal library: every rule is fixed in advance and applied to every
ticker, evaluated at the close of bar t with bars <= t only (`tests/test_lookahead.py`), and measured
with the forward-return statistics of `references/signals.md` (de-clustering, same-regime and
unconditional baselines, edge, confidence labels, `current_side` regime filter). Candlestick patterns
are measured from the close of their last bar, price-action setups from a stop entry beyond their
signal bar (see "Entry" below). They are context, and the report quotes only the statistics measured
here.
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
| Signal bar | a **strong bull signal bar** closes above its open with a body over 0.25 x its range, closes in the top third of its range, and has a range of at most 2 x ATR14 (a bigger bar puts the stop too far away). A strong bear signal bar is the mirror image. Any other bar is a weak signal bar for that side, and `signal_bar.weak_because` says why. |
| Bar shape | **inside** bar (high <= prior high and low >= prior low), **outside** bar (high > prior high and low < prior low), **ii** (two inside bars in a row), **ioi** (inside, outside, inside). ii and ioi are Brooks' breakout mode: a stop above the last bar's high buys, a stop below its low sells. `breakout_mode` gives the last one and which side went first. A reading only. |
| Trading range | over the last 20 bars: high - low <= 5 x ATR14 and the close crossed the EMA at least 3 times. |
| Market state | What the last 20 bars say (the **reading**): **tight bull channel** if the EMA is rising and >= 80% of bars have their low above the EMA (mirror: tight bear channel); otherwise **bull channel (broad)** if the EMA is rising, >= 60% of closes are above it and the trading-range test does not hold (mirror: bear channel); otherwise **trading range**. The **state** is the reading with confirmation: it changes once the reading has differed from it 3 bars in a row (it then takes the latest reading), or at once on the follow-through bar of a trading-range breakout. A channel that is the state gets the benefit of the doubt while the last close is on its own side of the EMA (above it for a bull channel): there its share thresholds are 0.1 lower (60% to enter a broad channel, 50% to stay in it; 80% and 70% for a tight one) and the trading-range test does not end it, so it ends when the EMA stops moving its way or its share of closes is lost. A close on the other side of the EMA is read with the entry thresholds. After a breakout with follow-through the reading is at least a channel that way for up to 20 bars while every close holds beyond the range. `state.pending` gives the latest reading when it differs from the state, and for how many bars in a row the reading has differed. No state before bar 30 (EMA warm-up). |
| Always-in | flips long after 2 bull trend bars in a row closing above the EMA, the last closing above the high of the bar before them, or whenever the state is a tight bull channel; flips short on the mirror image; keeps its direction otherwise. |
| H / L counts | bull side: a pullback starts on a bar with a lower high than the bar before. Each later bar whose high exceeds the prior bar's high is the next count (H1, H2, ...), once a lower high has come after the previous count. A high above the leg's high ends the pullback. The count stops after H4 and resets whenever the EMA is not rising. L counts mirror this with lows, while the EMA is falling. **Inside a trading range** the same counting runs on its own, without the EMA condition (`last_range_high_count`, `last_range_low_count`): Brooks buys a second entry low in a range and sells one high in it. |
| EMA gap bars | bars in a row with the low above the EMA (bull) or the high below it (bear). |
| Swing pivots | the ATR ZigZag of the key levels (`swings.zigzag`, 1.5 x ATR14): a swing high is confirmed on the bar whose low has come down 1.5 ATR from it, a swing low on the mirror image, and swings alternate. A pivot is known only from its confirming bar on. |
| Wedge | three pushes: three successive swing highs (or lows), each beyond the last, within 60 bars, at least 3 bars apart, the third push shorter than the second, and the third the extreme of the window. |
| Double top / bottom | two consecutive swing highs (lows) within 0.5 x ATR14 of each other and at most 60 bars apart. `variant` **reversal**: the pair is the highest high (lowest low) of 60 bars, the end of a move. **flag**: a lower double top or a higher double bottom, Brooks' double-top bear flag and double-bottom bull flag, a with-trend pattern. |
| Major trend reversal | bottom: (1) a swing low that is the lowest low of 60 bars while the EMA is falling, the trend's extreme; (2) a rally to the next swing high with at least one close above the EMA, the break of the trend; (3) once that high is confirmed, a test whose low holds above the extreme - 0.5 x ATR14; (4) the first strong bull signal bar within 3 bars of the test's low. `variant` **higher_low** when the test stops more than 0.5 ATR above the extreme, **double** when it ends within 0.5 ATR of it on either side (a lower low beyond that is the trend resuming, not a test). Tops mirror this (**lower_high**, **double**). |
| Failed breakout | a bar trades beyond the high (low) of the trading range of the previous 20 bars and a close back inside follows on that bar or within 2 bars. |
| Breakout | a bull trend bar closing above the high (a bear trend bar closing below the low) of the trading range of the previous 20 bars. Read with it: the breakout bar's range in ATRs, where it closed in its range, whether it opened beyond the prior bar (a gap), and how many trend bars in a row it ends. **Follow-through** = the next bar is a bar the same way (not a doji) closing beyond the breakout bar's close. The breakout lasts until the first close back at or inside the broken edge (`back_inside_date`), 20 bars at most. |
| Climax | 3 or more trend bars in a row in one direction, the last closing more than 2 x ATR14 beyond the EMA: a buy climax up, a sell climax down. A reading only: Brooks expects a pause or a two-legged correction after a climax, and it is not a signal to fade the move. |
| Trend channel | the up and down trendlines of the key-levels window (`levels.trendlines`), each with its **channel line**: the parallel through the swing on the other side that lies farthest from the trendline between its two points (Murphy: from the first prominent peak between the two lows of an up trendline). A bar after the trendline's second point whose high (low for a down channel) went more than 0.25 ATR beyond the channel line overshot it. |
| Measured moves | **leg**: from the last three ZigZag swings A-B-C (C above A for an up move, C below A for a down move, A at most 120 bars old, price not back beyond A since C): target = C + (B - A), or C - (A - B). **Range**: while a trading range is on, range high + its height and range low - its height. `reached` says whether price has traded at the target since C. |

The state and the trading-range box are separate readings. With a channel as the state,
`trading_range.active` can still be true (the last 20 bars fit the box): Brooks treats a broad channel
as a sloping trading range, and the range setups below work off the box.

### Entry: a stop beyond the signal bar

Every setup names a **signal bar**. A bullish setup is entered only if the next bar trades above the
signal bar's high, at that high, or at the next bar's open when it opens above it. A bearish setup
mirrors this below the signal bar's low. A setup whose next bar does not trigger is no trade. This is
Brooks' entry (a stop one tick beyond the signal bar); the one-tick offset is left out.

- `fired_date` is the signal bar's date. A setup on the last bar is `entry.status: "pending"` with its
  `trigger_price`. A setup of the last 3 bars that triggered is `"triggered"` with `entry_date` and
  `entry_price`. One that did not trigger is listed under `lapsed` and not under `active`.
- For H2 / L2 the signal bar is the bar before the count bar: the pullback bar, after the first count
  and a lower high, whose high the next bar must exceed. The bar that triggers is the H2 itself.
- Statistics run from the entry price to the close h bars after the entry bar. MAE covers the entry bar
  and the h bars after it; the entry bar is included because the order of its high and low is unknown,
  which overstates MAE a little. Prices are dividend-adjusted, the entry scaled by the entry bar's
  adjustment factor.
- Signal bars that did not trigger are not in the sample: `fires_total` counts signal bars,
  `triggered_total` those that triggered (about half do), and `n` the triggered ones with a complete
  forward window after de-clustering.
- The baselines (same-regime and unconditional) are every day entered the same way: a stop beyond that
  day's high (low), triggered the next day. The edge therefore compares like with like; a stop entry by
  itself selects days that kept going.

### Setups (statistics, chart markers)

| id | Name | Bias | Rule (as coded) |
|----|------|------|-----------------|
| `pa_h2_ema` | H2 pullback near the rising 20 EMA | +1 | `signal bar: in a pullback after its first high count (H1) and a lower high since, EMA20 rising over 10 bars, signal-bar high > EMA, pullback low <= EMA + 0.5 x ATR14; the bar that triggers the entry is the H2` |
| `pa_l2_ema` | L2 rally near the falling 20 EMA | -1 | `signal bar: in a rally after its first low count (L1) and a higher low since, EMA20 falling over 10 bars, signal-bar low < EMA, rally high >= EMA - 0.5 x ATR14; the bar that triggers the entry is the L2` |
| `pa_wedge_bottom` | Wedge bottom (three pushes down) | +1 | `three successively lower confirmed swing lows (ATR ZigZag, 1.5 x ATR14) within 60 bars, >= 3 bars apart, the third push shorter than the second and the lowest low of the window; signal bar = the bar that confirms the third low` |
| `pa_wedge_top` | Wedge top (three pushes up) | -1 | `three successively higher confirmed swing highs (ATR ZigZag, 1.5 x ATR14) within 60 bars, >= 3 bars apart, the third push shorter than the second and the highest high of the window; signal bar = the bar that confirms the third high` |
| `pa_failed_breakdown` | Failed breakdown below a trading range | +1 | `in a trading range (20-bar span <= 5 x ATR14, >= 3 EMA crosses), a bar trades below the range low and a close back above it follows on that bar or within 2 bars; signal bar = the bar that closes back inside` |
| `pa_failed_breakout` | Failed breakout above a trading range | -1 | `in a trading range (20-bar span <= 5 x ATR14, >= 3 EMA crosses), a bar trades above the range high and a close back below it follows on that bar or within 2 bars; signal bar = the bar that closes back inside` |
| `pa_gap20_bull_test` | First EMA test after 20 bull gap bars | +1 | `low <= EMA after at least 20 bars in a row with low > EMA; signal bar = that bar` |
| `pa_gap20_bear_test` | First EMA test after 20 bear gap bars | -1 | `high >= EMA after at least 20 bars in a row with high < EMA; signal bar = that bar` |
| `pa_tr_h2` | H2 in the bottom third of a trading range | +1 | `signal bar: in a trading range (20-bar span <= 5 x ATR14, >= 3 EMA crosses), in a pullback after its first high count and a lower high since, pullback low in the bottom third of the range; the bar that triggers the entry is the H2` |
| `pa_tr_l2` | L2 in the top third of a trading range | -1 | `signal bar: in a trading range (20-bar span <= 5 x ATR14, >= 3 EMA crosses), in a rally after its first low count and a higher low since, rally high in the top third of the range; the bar that triggers the entry is the L2` |
| `pa_double_bottom` | Double bottom | +1 | `two consecutive confirmed swing lows (ATR ZigZag, 1.5 x ATR14) within 0.5 x ATR14 of each other and at most 60 bars apart; signal bar = the bar that confirms the second low` |
| `pa_double_top` | Double top | -1 | `two consecutive confirmed swing highs (ATR ZigZag, 1.5 x ATR14) within 0.5 x ATR14 of each other and at most 60 bars apart; signal bar = the bar that confirms the second high` |
| `pa_mtr_bottom` | Major trend reversal up (bottom) | +1 | `a confirmed swing low (ATR ZigZag, 1.5 x ATR14) that is the lowest low of 60 bars with the EMA20 falling, a rally to a confirmed swing high with at least one close above the EMA, then a test whose low stays above that swing low - 0.5 x ATR14; signal bar = the first strong bull signal bar within 3 bars of the test's low` |
| `pa_mtr_top` | Major trend reversal down (top) | -1 | `a confirmed swing high (ATR ZigZag, 1.5 x ATR14) that is the highest high of 60 bars with the EMA20 rising, a selloff to a confirmed swing low with at least one close below the EMA, then a test whose high stays below that swing high + 0.5 x ATR14; signal bar = the first strong bear signal bar within 3 bars of the test's high` |
| `pa_breakout_up` | Breakout above a trading range with follow-through | +1 | `a bull trend bar closes above the high of a trading range (20-bar span <= 5 x ATR14, >= 3 EMA crosses) and the next bar is a bull bar closing above that close; signal bar = that follow-through bar` |
| `pa_breakout_down` | Breakout below a trading range with follow-through | -1 | `a bear trend bar closes below the low of a trading range (20-bar span <= 5 x ATR14, >= 3 EMA crosses) and the next bar is a bear bar closing below that close; signal bar = that follow-through bar` |

All setups use the `current_side` regime filter (the side of the 200 SMA on the signal bar's date) and
fire only from bar 30 on. Wedges, double tops / bottoms and major trend reversals wait for the ZigZag to
confirm their last swing, 1.5 ATR after the extreme, so they appear several bars after the turn. That is
the price of using no bar the reader could not have seen.

### Where a setup formed (`location`, `at_level`)

Brooks and the classic texts read a setup by where it forms: a reversal at support, at a trendline or
at the edge of a trading range has more behind it than one in the middle of nowhere. Each row of
`price_action.active` carries labels for that:

- `location.ref_price` / `ref_date`: the price the setup hangs on. The pullback low (H2) or rally high
  (L2), the low / high of the bar that tested the EMA (20 gap bars), the third push (wedge), the second
  bottom / top (double), the test's extreme (major trend reversal), the failed breakout's extreme, the
  broken range edge (breakout).
- `location.range_third`: `bottom`, `middle` or `top`, where the reference price lies in the trading
  range when the signal bar is in one; else null.
- `location.line`: the trendline or channel line the reference price sits on (within 0.25 ATR): for a
  bullish setup the up trendline or the channel line of the down trend, for a bearish setup the down
  trendline or the channel line of the up trend (`kind`, `line`, `price`, `touches`, `state`). A line
  drawn through the reference swing itself says nothing about it, so a trendline counts there only with
  a third touch and a channel line never.
- `at_level` / `level`: the key zone, by the rule of the candlestick patterns below, with the reference
  price in place of the pattern's extreme and the signal bar's close. `level` carries no score; the
  zone's test record stays in `levels`.

These are labels. The statistics stay those of the setup wherever it formed, and no rule or threshold
depends on a label.

### Chart 2

Daily candles over the main chart's window (panning or zooming any chart moves all three). Candles are
coloured by bar type (solid trend bars, pale weak bars, grey dojis). Each of these can be toggled: the
EMA; trading-range boxes; H/L counts (the counts that are an entered H2 / L2 setup are bold); wedges
(pushes numbered 1-3) and double tops / bottoms (DT, DB, the two extremes joined); major trend
reversals (MTR, with a dotted line from the extreme through the break to the test); breakouts (BO, on
the follow-through bar) and failed breakouts (FB); 20-gap-bar tests (20); climaxes (CX) and ii / ioi;
the trendlines with their channel lines; measured-move targets (MM tags on the price axis). Setups are
marked on their signal bar, and a grey marker is a setup whose next bar did not trigger the entry. Two
strips under the price panel show the market state and the always-in direction. The panel on the right
gives the current reading, the setups of the last 3 bars (entered or pending) with their entry, signal
bar, place and statistics, the setups that did not trigger, and every setup's record at the primary
horizon.

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

### Where a pattern formed (`at_level`)

Classic candlestick reading gives a reversal pattern more weight when it forms at support or resistance
(Nison, *Japanese Candlestick Charting Techniques*). Each pattern in `candlesticks.active` therefore carries a label:

- `at_level: true` when a bullish pattern's lowest low reached a support zone, or the zone the close is
  in (came within `TEST_TOL_ATR` = 0.1 ATR of the zone's top or lower), and its last bar closed at or
  above the zone's bottom; a bearish pattern mirrors this with its highest high at a resistance zone or
  the zone the close is in;
- `level` names the zone (`label` S1..S3, R1..R3 or Z for the zone the close is in; `side`, `price`,
  `zone_low`, `zone_high`); it carries no score. The zone's test record stays in `levels`;
- zones are the horizon's key levels as of the last bar, so the same pattern can be at a level for swing
  and not for position; `at_level` is `null` when the key levels are unavailable.

It is a label only. The statistics stay those of the pattern wherever it formed: a pattern at a level
is too rare on one stock to measure separately, and no rule or threshold depends on the label.

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
- `price_action` also carries `state` (with `pending`), `always_in`, `bar` (with `shape` and `signal`),
  `ema`, `ema_slope`, `close_vs_ema_atr`, `trading_range` (with `close_position`), `gap_bars`, `climax`,
  `breakout_mode`, `last_high_count`, `last_low_count`, `last_range_high_count`,
  `last_range_low_count`, `last_wedge`, `last_double`, `last_mtr`, `last_failed_breakout`,
  `last_breakout`, `measured_moves`, `trend_channels`, `lapsed`, `entry_rule` and `signal_bar_rule`.
  Each `active` row adds `entry`, `signal_bar`, `variant`, `location`, `at_level` and `level`; each
  `library` row adds `triggered_total`.
- `candlesticks` carries `recent` (the 5 most recent patterns), `last_doji` and, on each `active` row,
  `at_level` and `level` (see above).
- Classic candlestick patterns usually show little edge on daily US stocks. A near-zero or wrong-signed
  edge is a result to report as such, not a reason to drop or retune a rule.
