# report.md template

Claude writes `report.md` next to `metrics.json` after running `scripts/analyze.py`. This file
defines the section order, which `metrics.json` fields feed each section (exact key paths), the table
layouts and the wording rules.

## Rules (apply to every section)

**Numbers**
- Every number comes from `metrics.json`. Copy it; do not estimate, recompute or "adjust" anything.
- **Allowed changes:**
  - unit display: a fraction as a percent (0.0502 → 5.0%), and hit-rate differences in percentage points;
  - rounding: prices 2 decimals, ATR multiples 1 decimal, percents 1 decimal (2 below 1%);
  - copying a sign.
- **Fractions vs percentiles:**
  - fractions: `*_pct`, `returns.*`, `rel_return_*`, `mom_12_1`, `hit_rate`, `median`, `mean`, `p25`,
    `p75`, `median_mae`, `edge.*`, `rv20`, `atr_pct`, `adj_coverage`, `expected_move_1sd_pct`,
    `median_abs_move`, `notional_pct_account`;
  - already 0-100: `*_pctl`, `pctl_1y`, `vol_pctl`, RSI, ADX, DI.
- `null` means "not computable": say so and give the reason from the module's `notes` or `reason`.
  Never fill it in.

**Wording**
- **Conditional and probabilistic.** Write "If X closes above Y on above-average volume, …" and
  "historically, in N cases since <first date>, …". Never "X will …".
- **No advice.** No buy / sell / hold / accumulate / reduce recommendations, no "you should", and no
  price targets presented as advice. In Chinese, avoid 买入 / 卖出 / 持有 / 建议买入 and similar.
  Scenario targets are "zones", and the risk section is illustrative arithmetic.
- **Skipped modules.** When a module or block has `available: false`, keep the section, write one line
  saying it was skipped and why (`reason`), and move on. Sources: `meta.skipped`,
  `meta.modules_unavailable`, and each module's `reason`.
- **Data source.** Always name `data_source`. If it contains "third-party proxy", add: the data comes
  from a third-party proxy, not FMP's official server; verify key figures. If
  `meta.connection.tls_verify` is false, also say HTTPS certificate verification was off.
- **Data warnings** go in the Snapshot section (`data_quality.warnings` lists them as text):
  - partial bar (`data_quality.partial_bar_excluded`): the reason, and that today's bar was excluded;
  - stale data (`data_quality.stale.is_stale`);
  - illiquid (`data_quality.liquidity.illiquid` with `reason`);
  - adjusted coverage below 1 (`data_quality.adj_coverage`): returns are price-only where dividend data
    is missing.
- **Signals with insufficient samples** (`confidence == "insufficient"`, `scorable: false`) are listed
  with their n and clearly marked "too few cases to be evidence; not used in the scorecard". Never
  describe them as supporting anything.
- **Bearish signals:** `hit_rate` is the share of *positive* forward returns. A bearish signal's
  hypothesis is supported when its median and hit rate sit *below* the same-regime baseline, so a
  negative edge supports it.

**Language**
- Write in the user's language: Chinese if the user asked in Chinese, otherwise English.
- Indicator names stay in English in every language: SMA, EMA, RSI, MACD, ATR, ADX, +DI/-DI,
  Bollinger %B, Keltner, RVOL, OBV, A/D, VWAP, POC, HVN/LVN, z-score.
- Fixed labels from `metrics.json` (regime label, bias, signal names, level sources) may be
  translated, with the English original in parentheses the first time.
- Free-text fields (pillar reasons, conflict meanings, scenario texts) are in English. Translate them
  faithfully, keeping every number exactly.

**Reading metrics.json**
- The file is 50-80 KB. The large, rarely needed parts are `levels.candidates`,
  `levels.volume_profile.bins`, `signals.library`, `signals.active[*].horizons[*].dates_sample` and
  `meta.config_snapshot`.
- Horizon keys inside `signals.active[*].horizons` are strings (`"10"`). The primary horizon is
  `signals.primary_horizon`.

---

## Section order and contents

The report starts with a title line, then exactly these 11 sections in this order. In Chinese, use
the bracketed headings.

### 0. Title
`# {ticker} — technical analysis ({horizon}, as of {as_of})` (Chinese: `# {ticker} 技术分析（{波段|中线}，截至 {as_of}）`)

### 1. Snapshot (快照)
**Fields:**
- `snapshot.name`, `ticker`, `snapshot.exchange`, `snapshot.sector` / `snapshot.industry` (stocks only),
  `snapshot.is_etf`;
- `snapshot.price` (the last complete close, dated `snapshot.price_date` = `as_of`) and
  `snapshot.change_1d_pct`;
- `snapshot.quote_price` / `snapshot.quote_time` (FMP's latest quote; may be intraday);
- `data_source`;
- `horizon` with `meta.config_snapshot.horizon.primary` (days) and `stat_horizons`;
- `snapshot.history.first_date` and `snapshot.history.bars`;
- `meta.network_calls` and `meta.cache_hits`.

**Layout:** a two-column table (Item | Value), then the warnings from the rules above as a short
bullet list, or "No data-quality warnings."

### 2. Market context (市场环境)
**Fields:**
- SPY: `market_context.spy.close`, `.sma200`, `.pct_vs_sma200`, `.above_sma200`, `.sma200_slope_20d`.
- ^VIX: `market_context.vix.level`, `.pctl_1y`, `.date`.
- Sector ETF: `market_context.sector.etf`, `.trend_label`, `.pct_vs_sma50`, `.pct_vs_sma200`,
  `.sma200_slope`, `.rs_3m`, `.rs_6m`, `.rs_new_high`, `.rs_new_low`.

**Layout:** a three-row table (Benchmark | Reading | Detail).

**Skipped blocks:** for ETFs the sector block is skipped ("ETF: sector module skipped"). A missing SPY
or ^VIX block shows its `reason`.

### 3. Regime (市场状态)
**Fields:**
- `regime.label` and `regime.type`;
- direction: `regime.direction.label` and `regime.direction.votes`; `close_vs_sma50`,
  `close_vs_sma200` and `sma50_vs_sma200` are percent distances;
- strength: `regime.strength.adx`, `.plus_di`, `.minus_di`, `.label`;
- volatility: `regime.volatility.rv20`, `.rv20_pctl`, `.atr_pct`, `.atr_pct_pctl`, `.label`,
  `.compression`, `.bb_width_pctl`;
- multi-timeframe: `multi_timeframe.weekly.direction`, `.sma10w`, `.sma40w`, `.rsi14w`,
  `.partial_week`, `multi_timeframe.alignment` and `multi_timeframe.note`.

**Layout:** open with the label in bold, then a table (Dimension | Reading | Rule). For example:
"ADX 28.7 | trending | ADX ≥ 25". Then 1-2 sentences:
- what the regime implies for which signals matter: in trend regimes trend, momentum and RS carry
  the weight; in range regimes mean reversion, flow and volatility setup do;
- name the weights used (`scorecard.weight_basis.weights_used`).

List `regime.notes` and `multi_timeframe.notes` if present, e.g. fewer than 200 bars.

### 4. Scorecard (评分卡)
**Fields:**
- `scorecard.pillars.<p>.score`, `.reason` and `.available` for the six pillars in this order: trend,
  momentum, relative_strength, volume_flow, volatility_setup, mean_reversion;
- `scorecard.weights.<p>`;
- `scorecard.weighted_score`, `scorecard.bias`;
- `scorecard.confidence.level`, `.agreement`, `.mtf_alignment`, `.reasons`;
- `scorecard.signal_nudges`, and `scorecard.pillars.<p>.raw_score` when a nudge changed the score.

**Layout:**
1. Pillar table (Pillar | Score (-2..+2) | Weight | Reason). Use the reason text; translate it but keep
   the numbers. Mark unavailable pillars "insufficient data" or "not applicable" with their reason.
2. One line: "Weighted score {weighted_score:+.2f} → **{bias}**; confidence **{level}**", followed by
   the confidence reasons as bullets.
3. Supporting readings: a compact table of the most relevant raw values.
   - `trend_momentum.mom_12_1`, `.returns.1m/3m/6m/12m`, `.sma.stack`, `.macd.hist`,
     `.macd.last_cross` (type, date), `.from_52w_high.pct`;
   - `relative_strength.vs_spy.rel_return_3m/6m`, `relative_strength.beta_1y`, `.corr_1y`;
   - `mean_reversion.rsi14`, `.rsi2`, `.z20`, `.bb_pct_b`, `.dist_sma50_atr`;
   - `volume_flow.rvol`, `.updown_volume_ratio_50d`, `.obv.divergence`, `.ad.divergence`;
   - `volatility.atr14`, `.atr_pct`, `.bb_width_pctl`, `.squeeze.on`, `.squeeze.bars`.

   Skip null values with "n/a (reason)".

### 5. Conflicts (矛盾信号)
**Fields:** each item of `scorecard.conflicts`: `type`, `description`, `typical_meaning`.

**Layout:** one bullet per conflict, "**{description}** — {typical_meaning}". If the list is empty,
write "No conflicting signals were flagged."

Do not invent conflicts, and do not drop any.

### 6. Key levels (关键价位)
**Fields:**
- resistances and supports: `levels.resistances[*]` and `levels.supports[*]`, each with `price`,
  `zone_low`, `zone_high`, `distance_pct`, `distance_atr`, `sources`, `touches`, `score`;
- the last close and ATR: `levels.last_close`, `levels.atr`;
- the window: `levels.lookback_bars`, `levels.window_start`;
- volume profile: `levels.volume_profile.poc`, `.value_area_low`, `.value_area_high`;
- the 52-week range: `levels.high_52w` / `levels.high_52w_date` and `levels.low_52w` / `levels.low_52w_date`;
- `levels.extremes` and `levels.notes`;
- anchored VWAPs: `volume_flow.anchored_vwaps[*]` with `anchor`, `anchor_date`, `vwap`,
  `price_vs_vwap_pct`, `price_vs_vwap_atr`.

**Layout:**
1. One table, top to bottom: R3, R2, R1, **Close**, S1, S2, S3. Columns: Level | Price | Zone | Distance
   (% / ATR) | Sources | Touches.
2. One line with the POC and value area.
3. A small anchored-VWAP table.

**Labels:** call the volume profile and VWAPs "daily-bar approximations". If a side has fewer than 3
levels, quote the note (e.g. "price at an all-time high (available history): no resistance levels
above the last close in the window").

### 7. Signal statistics (信号统计)
**Fields:**
- `signals.active[*]`: `name`, `id`, `bias`, `fired_date`, `bars_ago`, `regime_used`, `confidence`,
  `scorable`, `rule`;
- at the primary horizon `h = signals.primary_horizon`: `signals.active[*].horizons["h"]` with `n`,
  `hit_rate`, `median`, `p25`, `p75`, `median_mae`, `same_regime.median`, `same_regime.hit_rate`,
  `unconditional.median`, `edge.median`, `edge.hit_rate`;
- `signals.history.first_date` and `.bars`, `signals.caveats`, `signals.notes`.

**Layout:**
1. A table at the primary horizon: Signal (bias ▲/▼) | Fired | Regime used | n | Hit rate | Median |
   p25-p75 | Median MAE | Same-regime median / hit | Edge (median / hit, pp) | Confidence.
   Median MAE is measured against the signal's bias (`signals.active[*].mae_basis`): for a bullish
   signal it is the lowest low after entry (negative = adverse), for a bearish one the highest high
   (positive = adverse). Say which in the table note.
2. Optionally, the other horizons in one line each.
3. One sentence per scorable signal, in this form: "Historically, in {n} de-clustered cases since
   {history.first_date} when {plain description of the rule} {in regime_used}, the median {h}-day
   forward return was {median} (hit rate {hit_rate}) versus {same_regime.median} ({same_regime.hit_rate})
   for all comparable days: an edge of {edge.median} / {edge.hit_rate} pp."
4. Insufficient-n signals: "{name} fired {bars_ago} bar(s) ago, but only {n} past cases exist: too few
   to be evidence; not used in the scorecard."

**No active signals:** write "No rule from the pre-registered library (references/signals.md) fired in
the last 3 bars."

**Caveats:** finish with the caveats from `signals.caveats`, condensed to one bullet each. Always cover
single stock, in-sample, no costs or slippage, and history depth.

### 8. Event risk (事件风险)
**Fields:**
- `event_risk.next_earnings.date`, `.calendar_days`, `.trading_days_est`;
- `event_risk.within_horizon`, `event_risk.horizon_days`;
- `event_risk.reactions.n`, `.median_abs_move`, `.mean_abs_move`;
- `event_risk.method`, `event_risk.notes`.

**Layout:** 2-4 sentences. Say whether earnings fall inside the horizon. Give the median absolute
two-day reaction over n reports, and label it a two-day close-to-close move (FMP has no before/after
market flag). If n < 8 (`config.EARNINGS_REACTIONS_MIN`), say the sample is small.

**Skipped:** ETFs: "Skipped: ETF (no earnings)." A restricted or missing earnings endpoint: give its
`reason`.

### 9. Scenarios (情景)
**Fields:**
- for `bull` and `bear` in `scenarios`:
  - `trigger.level`, `.condition`, `.volume`, `.basis`, `.distance_pct`, `.distance_atr`;
  - `targets[*]` with `zone_low`, `zone_high`, `kind`, `label`;
  - `invalidation.level` and `.condition`;
  - `text`;
- base case: `scenarios.base.expected_move_1sd_pct`, `.expected_move_1sd_price`, `.expected_move_atr`,
  `.range_low`, `.range_high`, `.earnings_caveat`;
- `scenarios.horizon_days`, `scenarios.leaning`.

**Layout:** a table (Scenario | Trigger | Target zones | Invalidation), with rows Bull, Bear and Base
(the 1σ range). Below it, the scenario `text`s (translated), the base-case text and the earnings caveat.
Projection targets must be called "projections (no level beyond the trigger)". Mention `leaning` as
"the scorecard bias leans toward the {bull|bear} scenario", not as a forecast.

### 10. Risk (风险测算)
**Fields:** for `risk.long_side` and `risk.short_side`:
- `entry_ref`;
- `structural_stop` (`structural_stop_basis`) and `atr_stop` (`atr_stop_basis`);
- `stop_distance_atr.structural/atr` and `stop_distance_pct.structural/atr`;
- `first_target` (`first_target_basis`);
- `reward_risk.structural/atr`;
- `position_size_example.risk_dollars`, `.stop_basis`, `.shares`, `.notional`, `.notional_pct_account`,
  `.flag`;
- `risk.label` and `risk.notes`.

**Layout:** a table with columns Item | Long side | Short side. Head it with "{risk.label}". Then the
notes as bullets.

**Framing:** say this shows what a 1% risk on a hypothetical $100,000 account would mean. It is not
a suggestion to trade.

### 11. Methodology & caveats (方法与说明)
**Content:**
- **Data:** `data_source`; split-adjusted `/full` prices drive levels, indicators and signals;
  dividend-adjusted closes drive returns, volatility, beta and the forward-return statistics.
  `data_quality.adj_coverage`.
- **Approximations:** daily-bar VWAP, the volume profile, the two-day earnings move, and trading days
  estimated without holidays.
- **Signal caveats:** one line, pointing to section 7.
- **Notes:** any `meta.notes`, and `meta.errors` if non-empty (say which module failed; its numbers
  are absent).
- **Pointers:** `references/methodology.md` and `references/signals.md` for definitions; `chart.html`
  for the interactive chart.

**Last line:** the disclaimer from `meta.disclaimer`, alone on its own line. English: *This is
technical analysis for information only, not investment advice.* Chinese: *仅供信息参考的技术分析，不构成投资建议。*

---

## Worked skeleton (English, swing; values illustrative)

```markdown
# ACME — technical analysis (swing, as of 2026-10-16)

## 1. Snapshot
| Item | Value |
|---|---|
| Name / exchange | ACME Inc. · NASDAQ · Technology / Software |
| Last close (2026-10-16) | 4475.34 (-1.4% on the day) |
| Data source | FMP stable API (official) |
| Horizon | swing: 10-day primary horizon; signal stats at 5/10/20 days |
| History used | 7,839 bars since 1996-10-01 · 10 API calls |

- No data-quality warnings.

## 2. Market context
| Benchmark | Reading | Detail |
|---|---|---|
| SPY | below its 200-day SMA (-17.2%) | SMA200 falling (-2.2% over 20 days) |
| ^VIX | 18.4, 1-year percentile 4 | as of 2026-10-16 |
| XLK (sector) | Downtrend | 3m relative return vs SPY -10.2% |

## 3. Regime
**Strong uptrend / normal vol** (type: trend)
| Dimension | Reading | Rule |
|---|---|---|
| Direction | all four votes up | close > SMA50 > SMA200, SMA200 rising |
| Trend strength | ADX 28.7 (+DI 42.4 / -DI 13.2) | ADX ≥ 25 → trending |
| Volatility | normal (rv20 25.2%, 72nd percentile) | 20-80th percentile → normal |
| Weekly | uptrend, confirms the daily | close > 10w > 40w |

In a trend regime the scorecard weights trend, momentum and relative strength most (trend weights).

## 4. Scorecard
| Pillar | Score | Weight | Reason |
|---|---|---|---|
| Trend | +2 | 25% | close above SMA50 (+15.0%) … net +4 of 4 votes |
| … | | | |

Weighted score +1.25 → **Bullish**; confidence **high**
- pillar agreement 0.85 …

## 5. Conflicts
- **trend +2 vs mean_reversion -2** — Overbought in an uptrend: this often resolves by sideways
  consolidation or a shallow pullback rather than a reversal.

## 6. Key levels
| Level | Price | Zone | Distance | Sources | Touches |
|---|---|---|---|---|---|
| R1 | 4509.87 | 4509.87-4509.87 | +0.8% / +0.4 ATR | AVWAP 52-week high | 1 |
| **Close** | **4475.34** | | | | |
| S1 | 4266.35 | … | -4.7% / -2.2 ATR | gap-up top | 2 |

POC 3,540.12; value area 3,402.55-3,781.20 (daily-bar approximation, last 126 bars).

## 7. Signal statistics
| Signal | Fired | Regime | n | Hit | Median | Same-regime | Edge | Confidence |
|---|---|---|---|---|---|---|---|---|
| ▲ New 52-week closing high | 1 bar ago | above 200 SMA | 107 | 55% | +0.8% | +0.4% / 53% | +0.4% / +2 pp | normal |

Historically, in 107 de-clustered cases since 1996-10-01 when ACME made a new 52-week closing high,
the median 10-day forward return was +0.8% (hit rate 55%), versus +0.4% (53%) for all days above the
200-day SMA: an edge of +0.4% / +2 pp, below the scorecard's thresholds.
- Caveats: single stock, in-sample, no costs or slippage, history since 1996-10-01.

## 8. Event risk
Earnings on 2026-10-29 (13 calendar days, ~9 trading days) fall inside the 10-day horizon. The median
absolute two-day reaction over the last 12 reports was 1.6%.

## 9. Scenarios
| Scenario | Trigger | Target zones | Invalidation |
|---|---|---|---|
| Bull | close above 4509.87 on RVOL ≥ 1.5 | 4552.98-4601.15 | close back below 4461.69 |
| Bear | close below 4266.35 on RVOL ≥ 1.5 | 4186.54-4234.71, 4084.49-4132.66 | close back above 4314.52 |
| Base (1σ, 10 days) | — | 4250.80-4699.87 (±5.0%, 2.3 ATR) | — |

## 10. Risk
Illustrative arithmetic, not a recommendation.
| Item | Long side | Short side |
|---|---|---|
| Structural stop | 4218.17 (2.7 ATR, 5.7%) | 4558.04 (0.9 ATR, 1.8%) |
| … | | |

## 11. Methodology & caveats
- Prices: split-adjusted FMP /full for levels, indicators and signals; dividend-adjusted closes for
  returns, volatility and forward-return statistics.
- Approximations: daily-bar anchored VWAP and volume profile; two-day earnings move.
- Definitions: references/methodology.md and references/signals.md. Interactive chart: chart.html.

*This is technical analysis for information only, not investment advice.*
```
