# Support/resistance ladder test V3: registered rules

This file makes plan V3 ("支撑压力测试计划 V3", Tianyang, 2026-10-27) executable. The plan is the
authority; where it leaves a detail open, the choice made here is marked **[default]**, and where this
file departs from the plan's wording the reason is given and the item is listed in section 18 for the
user's sign-off. `scripts/ladder_sim.py` implements these rules and nothing else; section numbers
(S1, S2, ...) in the code refer to this file. Step 4 of the plan commits this file, with the constants
of section 17 filled in, before any test result is computed; after that it does not change, except
that the S17 row "MDE80, batches added" is filled in at step 5 and, if the power check adds stocks,
D_1 .. D_5 are recomputed by the rule of S6 into `references/ladder_v3/fixed_depths.json`, which the run
reads.

Three independent reviews (fidelity to the plan, causality, statistics) of the first draft are folded
in; the main changes were a breakout rule that almost never fired, a random control that sat deeper
than the zone ladder, a gaps-only control that could not exist on this data, and a power check with
the wrong noise structure.

## 0. Notation

- Bars of one stock, split-adjusted (FMP `historical-price-eod/full`), after cleaning (S1):
  `O[d] H[d] L[d] C[d] V[d]`, d = the stock's bar index; `date[d]` its date.
- `zones(d)`: `node_zones.node_zones(bars[d-755 .. d], 756)` (PR #14, unchanged); zones are closed
  price intervals `[low, high]`. Defined for d >= 755; uses no bar after d.
- `SMA200[d]` = mean of `C[d-199 .. d]`. `rv60[d]` = standard deviation (ddof 1) of the 60 daily log
  returns ending at d; `sig3m[d] = rv60[d] * sqrt(63)` (one 3-month sigma as a fraction of price).
- `SPY[d]`: the latest SPY split-adjusted close dated on or before `date[d]` (price only).
- Horizons: 3 / 6 / 12 / 24 months = h in {63, 126, 252, 504} bars of the stock **[default]**.
- Capital of every plan = 1. Cost `COST = 0.05%` of the traded amount on every buy and every sale.
- Data end: 2026-10-27, SPY's last bar in the frozen data (`references/ladder_v3/data_manifest.json`).

## 1. Data and cleaning

Per stock: split-adjusted daily bars and the dividend-adjusted closes (FMP
`historical-price-eod/dividend-adjusted`) from 2005-01-03; dividends (ex-date `date`, `adjDividend`);
splits; earnings report dates; daily market cap; quarterly income statements (`filingDate`,
`epsDiluted`); the profile (`isActivelyTrading`). Market: SPY bars; the 3-month Treasury yield
(`month3`, percent). Membership: the S&P 500 spans of `references/ladder_v3/sample.json`.

Cleaning, in this order, before any bar is counted:
1. Placeholder bars are removed: volume 0 and `O = H = L = C` (plan V2's rule). Other zero-volume
   bars are kept and counted.
2. Every close-to-close move with `|C[d]/C[d-1] - 1| > 50%` is checked. It is confirmed when all of
   these hold, otherwise it is unverified **[default; V2 checked against hourly bars, which this test
   does not have]**:
   - no split has its ex-date within 5 bars of d;
   - the dividend-adjusted closes move by a factor within 5% of the split-adjusted one on that date;
   - it does not reverse: `log(C[d+5]/C[d-1]) / log(C[d]/C[d-1]) >= 0.5`. For a delisted stock (below)
     the test uses its last close when fewer than 5 bars follow d, and a move on its last bar meets it
     (the series ends there) **[default: SIVB's last bar, -60% on 2023-03-09, would otherwise mark its
     final 41 bars suspicious and drop every plan the collapse liquidates]**. For other stocks a move
     on the last 5 bars of the data stays unverified.
   An unverified move marks bars `d-40 .. d+40` suspicious. Every move is listed with its outcome.
3. A plan is left out at horizon h when a suspicious bar lies in `t-755 .. t+h` (its zone window or its
   bars) **[default]**; whole plans are dropped, horizon by horizon, never cut short. Counts are
   reported by horizon.

Delisting: a stock whose last bar is more than 5 SPY bars before the data end is delisted when its
FMP profile is missing or says `isActivelyTrading = false` (in the main sample: CXO, DAY, PBCT, SIVB,
TGNA, TIF). Its plans are liquidated as in S10. A stock whose bars stop early while the profile says it
trades has a data gap: its plans whose horizon runs past its last bar are left out and counted.

## 2. The sample

Frozen in `references/ladder_v3/` (seed 20261028; README there): the seven large caps (AAPL, MSFT,
GOOGL, AMZN, NVDA, META, TSLA) plus 43 companies drawn by FMP sector from the S&P 500 members of
2008-01 .. 2025-10 with usable bars, excluding every company with a ticker among the 96 stocks
examined in earlier tests (the user's decision of 2026-10-28; the plan's text says 32). The held-out
set is 50 companies drawn the same way, disjoint from the main sample. The post-fetch check replaced
nothing. If the power check (S14.2) adds stocks, reserve batches are added in their frozen order,
each batch 10 main and 10 held-out companies. "The 43 stocks" below means every non-large-cap stock of
the final main sample.

## 3. Plan starts

A plan starts at the close of `t`, the stock's first bar of a calendar month, from 2008-02 on, when all
of these hold:
1. on `date[t]` the company is an S&P 500 member (its spans) or its latest FMP market cap dated on or
   before `date[t]` exceeds 200e9 USD;
2. at least 756 bars up to and including t (from the company's `bars_from` where set). The data begin
   on 2005-01-03, so the first possible start is 2008-02-01 and 2008 has 11 plan months **[the plan
   says plans start in 2008; January 2008 has only 755 bars]**;
3. `C[t] > SMA200[t]`.

There is no other start condition: a plan whose zone ladder at t has no level is simulated (its
ladder groups hold cash until a breakout re-placement, S7; lump buys as usual) and counted
**[default]**. Valuation enters only in the variants of S13. A start on the stock's last bar has no
bar left to run on; it is not simulated and is counted with the other skipped starts (one in the
main sample: PBCT, whose last bar is 2022-04-01).

Each plan is evaluated at each horizon h with `t+h` on or before the data end (for a delisted stock,
on SPY's calendar, S10). The rules never look at h, so one simulation to 504 bars gives all four.

## 4. The zone ladder at day d with reference price P = C[d]

Candidate supports: the zones of `zones(d)` with `high < P` (wholly below; the zone holding P is
skipped), in order of `high`, highest first. Walk them and accept zone z as the next level, at price
`z.high`, when:
- `z.high <= 0.98 * P` (at least 2% below the reference price), and
- `z.high <= 0.98 * (previous accepted level)` (adjacent levels at least 2% apart; a zone too close
  is skipped and the walk goes on);
- the walk stops at the first zone with `z.high < 0.65 * P` (at most 35% below), or after `k` accepted
  levels, k = the number of parts of the ratio.
The ladder is the accepted levels `L_1 > L_2 > ... > L_m` (m in 0..k), each with its zone's low
`lo_i`. `g` = the smallest relative gap `1 - L_{i+1}/L_i` of the ladder (at least 2%; m >= 2).

## 5. Parts, orders and fills

The ratio `w = (w_1 .. w_k)` is normalised to sum 1 (main 1:2:4:1). Part i is the i-th shallowest.

- **Placement.** At every placement (the start, S3, and each breakout re-placement, S7) the group's
  unfilled parts, in increasing part index, take levels 1, 2, ... of the group's own ladder (S6).
  Unfilled parts beyond its last level get no order and stay cash until the next placement
  **[default: "支撑区不够时只挂现有的几档" read literally; spreading the ratio over fewer levels would
  be the alternative]**. Any earlier unfilled order of the group is cancelled first.
- Orders created at the close of d are live from bar d+1. A live buy limit at price p fills on the
  first bar with `L <= p`, at `min(O, p)` (a gap below the limit fills at the open); several parts can
  fill on one bar. A filled part i buys `w_i * (1 - COST) / fill` shares and spends `w_i` of cash.
- Unfilled orders are never cancelled except by a re-placement, the start of scaling out (S9), or a
  stop (S8).
- `avgcost` = the group's share-weighted mean fill price, costs excluded.

## 6. Placement groups (same stock, start, ratio and exits; only the order prices differ)

At each placement the zone ladder of S4 at d gives m levels; every group builds its own m levels:

- **zone** (main): `L_1 .. L_m`.
- **random** (main control): the zone ladder shifted as a whole, in 10 antithetic pairs. For pair q
  (q = 1..10) draw `delta_q` uniform on `[1%, g/2]` (on [1%, 2%] when m = 1). Draw `2q-1` uses
  `L_i * (1 + delta_q)` and draw `2q` uses `L_i * (1 - delta_q)` for every level, except that on the
  up draw level 1 is set to `0.98 * P` when `L_1 * (1 + delta_q)` would be above it. A new delta is
  drawn at every placement. A plan's random result is the mean of its 20 draws. **[The plan:
  "所有档同一个偏移，偏移 1% 到半个档距之间、方向随机；第一档仍要在现价下方至少 2%". With a fair coin the
  2% rule forced about 30% of up-shifts down and left the random ladder 0.6-0.9% deeper on average,
  which favours the zone ladder in rising markets. The pairs keep the direction exactly balanced; only
  level 1 of an up draw can move less than delta. "半个档距" is read as half the smallest gap, so no
  shifted level reaches its neighbour.]** Diagnostics reported: how often the cap binds (at the
  start placement, and over every placement with an order), the part-weighted mean signed shift of
  random's start orders against zone's, the mean start depths, and the deployed-share difference.
- **gaps only** (secondary control, 20 draws): each level moves out of the zones, on its own. For
  level i, in order i = 1..m, with `cap_i = 0.98 * P` for i = 1 and `0.98 *` (the previous placed
  level) after: `Up` = the first gap above `L_i` (between the top of the connected run of zones that
  holds `L_i` and the next zone's low), below `cap_i`; `Down` = the first gap below `lo_i` (between
  the next zone's high and the bottom of the run that holds `lo_i`), above `0.65 * P` and below
  `cap_i`. Gaps are the prices outside every zone of `zones(d)`. Draw a uniform point in each side
  that exists, at relative distances a (up) and b (down) from `L_i`; take the up point with
  probability `b / (a + b)`, so the expected shift is 0. With one side only, take it; with none, the
  part gets no order at this placement (counted). A plan is never dropped. **[The plan's version
  shifts the whole ladder by one offset; zones cover 80-88% of the price range, so on 99-100% of
  starts no offset puts every level in a gap.]** Its mean depth and signed shift against zone are
  reported.
- **vol** (equal volatility spacing): level i at `P * (1 - 0.5 * i * sig3m[d])`, i = 1..m; a level at
  or below `0.05 * P` is not placed.
- **fixed** (fixed percentages): level i at `P * (1 - D_i)`, i = 1..m. `D_i` = the median depth
  `1 - L_i / C[t]` of the i-th level of the zone ladders at all plan starts of the final main sample,
  pooled over stocks (k = 5), computed at step 4 from the ladders alone (no simulation, no returns),
  written into S17 and read as constants **[default: "各档距离的中位数" read per level]**. It is
  recomputed once if the power check adds stocks, before the main run. It uses ladders from dates after
  many plan starts; it carries no outcome, and is the one input exempt from the look-ahead check.
- **lump** (benchmark): all capital bought at `C[t]` at the start close (cost included); no orders;
  the same exits.
- **placebo k** (power check only, k = 1..20): one random ladder kept for the whole stock: a sign
  `s_k` (+1 or -1, probability 1/2) and a quantile `u_k` (uniform) drawn once per stock; at every
  placement `delta = 1% + u_k * (g/2 - 1%)` (2% for g/2 when m = 1, as for random) and levels
  `L_i * (1 + s_k * delta)`, level 1 capped at `0.98 * P` as for random.

## 7. Confirmed breakouts and re-placement

One detector per stock, run on every close from bar 755 (common to all plans and groups). It keeps a
watched zone W (its edges copied when it is chosen) and a counter n. At each close d:
- if W is none, or `C[d] < W.low`: W = the zone of `zones(d)` with the lowest low among those with
  `low > C[d]` (none if there is none), n = 0;
- else if `C[d] > W.high`: n = n + 1; at n = 2 a breakout is confirmed on d (the broken zone is W);
  then W = the zone of `zones(d)` with the lowest low above `C[d]`, n = 0;
- else (the close is inside W): n = 0.
So W stays fixed while the price trades inside it, and a breakout needs two consecutive closes above
the high of the resistance zone the price approached from below ("连续 2 天收盘在当时上方最近的压力区
上沿之上"). While no zone lies above the close, no breakout can be confirmed. **[The first draft
looked for a zone wholly above the close two bars earlier and missed almost every breakout.]**

A plan acts on confirmations at d >= t+1. At the close of d, every group that is still running, is not
scaling out and has no sale pending cancels its unfilled orders and re-places its unfilled parts (S5)
on its own ladder built at d from the zone ladder of S4 with `P = C[d]` ("按确认那天的收盘价和当时的
支撑区重新挂（挂单位置的规则不变），比例不变"). The new orders are live from d+1.

## 8. Stop loss

At the close of d, a group holding shares is stopped when all three hold (closes only):
1. `C[d] <= (1 - 0.15) * avgcost`;
2. `C[d] < floor[d]` and `C[d-1] < floor[d-1]`;
3. `(C[d]/C[t] - 1) - (SPY[d]/SPY[t] - 1) < -0.05`.
It cancels its unfilled orders at that close and sells all shares at `O[d+1]` (cost included); the plan
ends for that group.

The floor is common to all groups of a run ("各组共用支撑区挂法的止损线"). It comes from a reference
ladder R that follows the zone group's placements but never its exits:
- R starts as the zone ladder at t, one level per part (parts beyond m have none).
- At every confirmed breakout d >= t+1, the parts the zone group has not filled by that close take the
  levels of the zone group's new ladder at d as S5 assigns them (parts beyond its last level have
  none), whether the zone group is running, scaling out, stopped or finished. Filled parts keep their
  levels. Cancellations never remove levels.
- `floor[d]` = the lowest zone low among R's levels in force during bar d; a change at the close of d
  applies from d+1. `floor[t]` is the start ladder's. A level without a zone (the 200-day level of S13)
  adds nothing. While R has no level with a zone, condition 2 cannot be met.
This keeps "最后一个支撑区（当前阶梯里最深的一档，含已成交的）下沿" while making each group's stop
independent of the zone group's own exits **[default]**.

## 9. Scaling out

At the first close d with shares held and `C[d] >= 1.5 * avgcost` (and no stop at that close), the
group cancels its unfilled orders, sets `base` = its shares, and chooses a target:
- the zone of `zones(d)` with the lowest low among those with `low > max(C[d], u)`, its edges copied
  and frozen until it is hit, u = the high of the last zone target sold at (0 before the first sale);
- with no such zone (the price is above every zone), the price `C[r] * (1 + sig3m[r])`, r = d (the
  anchor is this close; "从上一次卖出（或刚到 50% 那天）的收盘价往上 3 个月 1σ") **[default: linear,
  rv60 based]**.
At a later close d with `C[d] >= target low` (or `>= target price`), the group signals a sale of
`base / 3` (the third sale: all remaining shares) at `O[d+1]` and chooses the next target from this
close the same way (r = d). One sale per close **[default]**; three sales end the plan. Sales execute
at the next open, like stops **[default: no same-close sale]**. If a stop and a sale fall on the same
close, the stop wins.

## 10. Cash, dividends, delisting and value

- Cash starts at 1. Before each bar d's open: `cash *= 1 + y * days / 365`, y = `month3 / 100` of the
  latest Treasury row dated on or before `date[d-1]`, days = calendar days from `date[d-1]` to
  `date[d]`. All cash earns it, sale proceeds and dividends included **[default; the plan's formula
  pays interest on unused cash only, which would penalise a group that sold early against one that
  never bought]**.
- Dividends: on the first bar on or after an ex-date, `adjDividend * (shares held at the close of the
  previous bar)` is added to cash before the open (shares sold at that open still collect it).
- Delisting (S1): at the close of the stock's last bar, after that bar's events, every group sells its
  shares at that close (cost included), cancels its orders and drops pending sales; its cash then
  earns interest until each horizon, counted on SPY's bars after the last bar (`t + h` minus the bars
  the stock still had). "计划中途退市的，按最后一个交易日的收盘价清算".
- Value at horizon h: `V_h = shares * C[t+h] + cash` (marked at the close, no exit cost; an order
  signalled at that close is not executed). Plan return `R_h = V_h - 1`. A group that ended earlier
  keeps its cash earning interest until t+h.

## 11. Order of events within bar d (d >= t+1)

1. Pre-open: interest; then dividends.
2. Open: sales signalled at the close of d-1 execute at `O[d]`.
3. Intraday: buy limits created at or before the close of d-1 fill (S5). No order created at the close
   of d acts on bar d.
4. Close, in this order: (a) close-priced buys of the variants (S13), a known approximation of a
   market-on-close order; (b) avgcost includes every fill of bar d; (c) stop check with `floor[d]`;
   if it fires, skip (d) and the group's own re-placement in (e), while the update of R in (e) still
   runs (S8); (d) scaling out (start, or a sale signal); (e) breakout re-placement (S7) and the
   update of R; (f) values at the horizons; (g) on the last bar of a delisted stock, liquidation
   (S10).
The lump buy at `C[t]` and the start placement happen at the close of t.

## 12. Outputs per plan, group (and random draw) and horizon

`R_h`; deployed share (capital spent on buys); average cost `avgcost / C[t] - 1` (groups with a fill);
safety margin: the smaller of 0 and the worst `L[d] / avgcost_d - 1` over bars d >= t+1 at whose close
shares are held, avgcost_d as of that close (bar t's low comes before the lump's buy at its close), and
whether a stop executed by t+h; number of fills; scale-out started; sales; ended.
Earnings flags are computed after the run, from the events, never inside the simulator: G = for each
FMP report date r, the first bar on or after r and the bar after it (FMP gives no time of day). A fill
on a bar in G is flagged; a stop is flagged when its signal close d, the close before it or its sale
bar d+1 is in G ("财报跳空指财报公布后的第一个交易日"; a gap-driven stop needs two closes below the floor).
**[default]** A report date equal to one of the stock's income period ends is an FMP placeholder and is
dropped (CPAY's dates are quarter ends from 2013 to 2019). A quarter with period end p whose report
date is missing (no remaining date in p+1 .. p+100 days, the window ending by the data end and starting
by the stock's last bar; CPAY has none from 2020-01 to 2022-05) leaves its earnings bars unknown: a plan
whose bars t..t+h touch p+1 .. p+100 cannot be classified, and the without-earnings variant leaves it out
(counted). A report date FMP gives on the wrong day without a gap (JPM's Q4 2018 report under its 10-K
date) is not caught.

Metrics that are not plain means (win rate, payoff ratio, average cost, safety margin, stop rate) are
computed for each random draw over all plans as if the draw were its own group, then averaged over the
20 draws; never from the 20-draw mean return. Win = `R_h > 0`; payoff ratio = mean `R_h` of winners /
|mean `R_h` of losers|; plans with no fill (for random, the draw's plans with no fill) are left out of
both. The stop rate is over plans with a fill. These are also reported on the common subset (plans
where both zone and the draw filled).

## 13. Variants (each changes one thing; zone and random groups; the same seeds as the main run)

| Variant | Change |
|---|---|
| close confirm | a part fills on a bar with `L <= level` and `C >= ` its band low (the zone's low; for random moved by the same factor as the level), at C |
| breakout start | t = the first confirmed breakout (S7) of each month from 2008-02 with S3's conditions; level 1 = the broken zone W's high, without the 2% test; levels 2.. from the S4 walk on `zones(t)` below it (each `<= 0.98 *` the previous, `>= 0.65 * C[t]`); random's up draw caps level 1 at `C[t] * (1 - 0.0025)` instead of the 2% of S6 (S18); a breakout that fails S3's conditions does not use up the month **[default]** |
| time limit | one check at the close of t+42 **[default: "开始 2 个月都没成交、且仍在 200 日均线之上" read as one check]**: a group with no fill yet and `C > SMA200` buys part 1 at that close (its order, if any, is cancelled) |
| 200-day average | **[default]** at each placement, if `0.65P <= SMA200[d] <= 0.98P` (the window of S4): the zone level within 2% of it (the nearest) becomes a 200-day level (keeping its zone low for the floor), else a 200-day level is inserted by depth and the deepest zone level is dropped if there are k+1; a 200-day part fills on a bar with `L <= S` and `C >= S`, S = `SMA200` of that bar (times the draw's factor for random: `1 + s*delta`, or, on an up draw whose level 1 is the 200-day level, the factor that puts it at `0.98P` at the placement when `1 + delta` would put it higher, as in S6), at C; parts keep their levels until the next placement |
| stop 20% | stop condition 1 at 20% |
| without extremes | the main run without plans whose bars t..t+h touch 2008-09-15 .. 2009-03-31 or 2020-02-20 .. 2020-04-30 |
| without earnings | the main run without plans where any group or draw (zone, the 20 random, the 20 gaps-only, vol, fixed, lump) has an earnings flag by t+h, nor plans whose earnings bars are unknown (S12); both shares left out are reported, and the zone-only version as a check |
| valuation A / B | the main run restricted to starts where the trailing P/E meets A or B (below) |

Trailing P/E **[the plan's forward P/E has no point-in-time history in FMP; the user was asked on
2026-10-28 and the recommended answer is used until they reply: trailing P/E as variants only, the
main test unchanged]**: TTM EPS at d = the sum of `epsDiluted` of the latest 4 quarters known by
`date[d-1]` (FMP restates EPS for splits), defined only when their period ends span at most 330 days,
so that they are consecutive quarters (CXO has no 2013 rows, ADM no 2012-12 quarter) **[default]**. A
quarter is known from its `filingDate`; when FMP's filingDate is missing or on or before the period end
(a placeholder: 224 of the main sample's 4,340 rows from 2003 on, GOOGL's to 2015 and CPAY's to 2024),
from the period end + 45 days for Q1-Q3 and + 90 days for Q4, the latest SEC deadlines for a 10-Q and a
10-K **[default; the plan forbids earnings that were published later]**. `PE[d] = C[d] / TTM EPS[d]`,
undefined when TTM EPS <= 0. At t, A: `PE[t] <` the median of the defined PE values over bars
`t-1260 .. t-1`; B: `q10 <= PE[t] <= 1.1 * q10`, q10 their 10th percentile ("价格在底部线上方 10% 以内"). Both can be
evaluated at t when TTM EPS > 0 at t and the window holds at least 756 defined values (bars before the
stock's first bar are absent, so starts before 2010 use a shorter window; for a company with positive
TTM EPS throughout, the first evaluable start is in 2008); how many starts this removes is
reported.

## 14. Tests

### 14.1 Main test (one)

`theta = mean over plans of (R_252(zone) - R_252(random))`, random = the 20-draw mean, over all
main-sample plans counted at 252 bars. It passes the screen when all hold:
- the lower bound of its 90% interval is above 0, and theta >= +1 percentage point;
- theta at 126 bars on the same plans has the same sign;
- theta on the 43 stocks alone has the same sign.

Interval: calendar-block bootstrap. Blocks are calendar years of the plan start (Jan-Dec, "12 个月的
结果用 12 个月的块"); all plans of a block are resampled together; G = the years with at least one plan
(18 at 252 bars); B = 10,000 resamples from seed BOOT_SEED; each resample's statistic is the sum of its
plans' differences over their number. `SE = sqrt(G/(G-1)) * SD*`, SD* the standard deviation of the
resampled statistics; interval `theta +/- t(0.95, G-1) * SE`; the screen's lower bound is the smaller
of `theta - t(0.95, G-1) * SE` and the 5th percentile of the resampled statistics. **[With 18 blocks a
plain percentile interval is too narrow: it ignores the small block count, which the t quantile and
sqrt(G/(G-1)) correct (at G = 18 the bound is about 9% wider than a plain percentile bound). Adjacent
year blocks also share up to 11 months of outcomes; that is not corrected, and the 2-year-block version
below is the check for it.]**

Always reported with it: the same with 2-year blocks (2008-09 .. 2024-25), with the pass labelled
fragile if that lower bound is not above 0; the range of theta leaving out one year at a time; theta
on plans whose start placement had no capped up draw; the per-stock contributions; the deployed-share
and average-cost differences of each group against zone.

### 14.2 Power, before the main test

For each placebo k = 1..20: `SE_k` = the SE of 14.1 for `mean(R_252(placebo k) - R_252(random))` on
the main sample (the zone group runs only to give the floor R of S8; its outcomes are not kept). `MDE80 = (t(0.95, G-1) +
t(0.80, G-1)) * (80th percentile of SE_k)` (2.60 x for G = 18) **[the plan's "两组互相独立的随机挂法相
减" read as one persistent placebo ladder against the 20-draw mean: two 20-draw means would understate
the noise of a single persistent ladder several times]**. If MDE80 > 1 pp, reserve batches are added in
order (10 main + 10 held-out each, S2), MDE80 recomputed after each, until it is <= 1 pp or all six are
in; if it is still above, the test runs anyway and the report says "MDE80 = x pp; a true effect below x
would usually not pass". D_i (S6) is then recomputed. After the main test its own SE is reported next to
the 80th percentile of SE_k; if it is more than 1.25 times larger, the report says power was overstated.
The +1 pp bar itself means a true +1 pp effect passes only about half the time.

### 14.3 Secondary comparisons (reference only)

Each is a mean difference at 252 bars with the interval method of 14.1 (paired on the same plans
unless marked unpaired; unpaired ones compute both means in every resample from the same resampled
blocks). `p = 2 * (1 - F_t(|theta| / SE; G-1))`. One Holm step-down family at family-wise 0.10,
two-sided, m = 27:

1. zone - vol; 2. zone - fixed; 3. zone - lump; 4. zone - gaps only;
5. zone - random at 1:2:3:2:2; 6. zone - random at 1:2:4;
7. zone at 1:2:4:1 - zone at 1:1:1:1; 8. 1:2:3:2:2 - 1:1:1:1:1; 9. 1:2:4 - 1:1:1 (zone group);
10. close confirm: zone - random; 11. close confirm (zone) - main (zone);
12. breakout start: zone - random; 13. breakout start (zone) - main (zone), unpaired;
14. time limit: zone - random; 15. time limit (zone) - main (zone);
16. 200-day: zone - random; 17. 200-day (zone) - main (zone);
18. stop 20%: zone - random; 19. stop 20% (zone) - main (zone);
20. without extremes: zone - random; 21. without earnings: zone - random;
22. valuation A: zone - random; 23. valuation B: zone - random;
24. A starts (zone) - the other starts where A can be evaluated (S13) (zone), unpaired; 25. the same
for B;
26. win rate: zone - random; 27. payoff ratio: zone - random.

Other horizons are descriptive (3, 6 and 12 months with 1-year blocks, 24 months with 2-year blocks),
as are the time-limit variant on plans where the main zone group had no fill within 42 bars (its zone -
random, and its zone against main's zone), every ratio's zone - random at the equal ratios, vol - fixed,
and the metrics of S12 for every group against zone (average cost, safety margin, stop rate, win rate,
payoff ratio; per draw and on the common subset). If the valuation variants are dropped before the
secondary items are computed (step 6), items 22-25 go and m = 23 (the other items keep their numbers); a
choice made later only leaves items 22-25 out of the conclusions, and m stays 27. An item without a p
(no SE) counts in m and is not rejected.

### 14.4 Held-out confirmation

The confirmation set **[default]**: the main rule if it passes the screen, plus every rule item of 14.3
(5, 6, 10-19 and 22-25) with a Holm-adjusted p below 0.10 and |theta| >= 1 pp. The benchmark comparisons
(1-4, 7-9), the subsets (20, 21) and the metrics (26, 27) stay reference only **[the plan confirms "通过
筛选的规则" and calls the comparisons reference only; every member raises K, and with it the bar for the
main rule]**. One run on the held-out stocks (the 50 plus any added batches) covers the set; each member
counts as confirmed when its one-sided lower bound at level `1 - 0.05/K` in the direction found on the
main sample is above 0 **[default: "单边 95% 区间高于 0" read in that direction, so a rule found worse is
confirmed as worse]**, K = the set's size, with the interval method of 14.1 (for the unpaired items 13,
24 and 25, both means from the same resampled blocks, as in 14.3).

### 14.5 Step 8

Confirmed main rule: the zone ladder goes into the skill's entry advice. Otherwise the zones stay a map
and the orders use vol or fixed, whichever has the higher mean `R_252` on the main sample; vol - fixed
is reported with its interval, with both groups' deployed share and part-weighted depth. That choice is
a default, not a confirmed result.

## 15. Self-tests (step 3, before step 4)

**Self-test 1: no future data.** It fails unless every part passes.
- Inputs: every precomputed array (cleaned bars, SMA200, sig3m, zones, the detector, SPY, interest,
  dividends, membership, market cap, trailing P/E, plan starts) built from data cut after a bar c
  equals the full build up to c (zones checked on the 5 bars up to each c).
- Invariance: at least 300 (plan, cut) pairs on main-sample stocks (never held-out bars), including at
  least 20 cuts each on a fill bar, a stop-signal close, a scale-out start, a sale signal, a breakout
  re-placement, an ex-dividend bar and a horizon bar. Each runs three ways: full inputs, inputs after c
  blanked (NaN), and inputs after c replaced by another path (bar c+j takes the values of bar c-j: prices,
  zones, breakouts, dividends, interest, SPY). Every group, draw and placebo of the main configuration
  runs, and zone and random under each variant and ratio of S13 and 14.3 (the breakout start on its own
  plans); before step 4 the fixed group uses placeholder depths, which test the same code. The whole state at the close of c (every
  order, fill, share count, cash, avgcost, R and floor, detector state, targets, pending orders and
  random placement index, for every group and draw) must be identical in all three. The test reports
  pass or fail and the coverage counts only; no returns are computed.
- Timing audit: every executed order was created at an earlier close; dividends use the shares of the
  previous close; interest uses a rate dated on or before the previous bar; no income row counts on or
  before its period end (S13). The simulator takes no earnings input.
- The input part compares the cut build at every plan month of every main-sample stock and at every
  kernel cut. A cut build keeps only data dated on or before the cut (income statements by the day S13
  says they are known).
- Positive controls: each of these injected leaks, switched on alone by a test-only flag, must make the
  test fail: zones of d+1 at decisions on d; C[d+1] in stop condition 1; SMA200[d+1] in the start
  condition; sig3m[d+1] in the vol ladder; C[d+1] in the detector; SPY[d+1] in stop condition 3; a
  limit created at the close of d filled on bar d; a stop sale at the open of its signal bar; a dividend
  on shares bought on the ex-date; interest at the rate of the bar's own date; EPS filed after t.
- Two runs with the same seeds are identical; changing MASTER changes every random level.

**Self-test 2: planted levels that truly hold.** On synthetic stocks, so no run touches main-sample or
held-out outcomes.
- Sources: 20 of the previously tested stocks with a bar on each of SPY's 5,489 dates 2005-01-03 ..
  2026-10-27 and no close-to-close move over 50% (their data have no splits or adjusted closes to check
  one against), not large caps and not funds, chosen with seed MASTER.
- A world: one stationary block bootstrap of source dates (mean block 40 bars), common to all stocks
  and SPY; synthetic stock s repeats stock s's own open/high/low/close ratios to the previous close and
  volume on those dates, from its real first close; real dates and Treasury rates; no dividends; always
  a member.
- Planting, causal and sequential: at each month start the simulator's own S4 code builds the ladder
  (k = 4) from PR #14 zones of the 756 synthetic bars up to that close; from the next bar until the next
  month start, the first bar whose low would go below a planted level p (p = a ladder level; the placebo
  uses 1.03 p) holds with probability q (one draw per level and month; a level whose first crossing
  does not hold is not planted again that month; one hold per bar, the highest level first): its low
  becomes `p (1 - u)`, u uniform on [0, 0.5%], its open is raised to that low if below it, its close to
  at least `p (1 + v)`, v uniform on [0, 1%], and its high to at least its open and close; the next 10
  bars' returns are replaced by their absolute values (each bar's open, high and low moved with its
  close); the path then continues with its own ratios. The same base paths and uniforms are used for
  every q; at q = 0 nothing is planted.
- Worlds: q = 0 (10 replications), q = 0.5, q = 1 and the placebo at q = 1 (5 each). The whole pipeline
  runs unchanged: PR #14 zones on the synthetic bars, all main groups (the fixed group with each world's
  own D_i, the median start-ladder depths as in S6), 20 random draws, 14.1's test (every synthetic
  stock counts as one of "the 43").
- Pass only when: q = 0 has a pooled theta within +/-0.3 pp and passes the screen in at most 1 of 10;
  q = 1 passes in at least 4 of 5 with a pooled theta >= +1 pp; q = 0.5 has a pooled theta whose 90%
  interval over replications is above 0; pooled theta rises from q = 0 to 0.5 to 1; the placebo's
  pooled theta is at most +0.3 pp; and the share of zone limit fills followed by two consecutive closes
  below their zone's low (among the fill bar's close and the next 20) falls as q rises. If the q = 0 or placebo gate fails, the control or the
  simulator is fixed before step 4, never the planting.

## 16. Random numbers

`seed(tag)` = the first 8 bytes (big-endian) of SHA-256 of the tag, for numpy's PCG64. Random pair q
(1..10): tag `MASTER|SYMBOL|YYYY-MM-DD of t|random|q`; gaps-only draw j (1..20): `...|gaps|j`; each
generator yields a 64 x 16 table of uniforms, row = placement index (0 = the start), so a placement's
numbers never depend on what other code drew; more than 64 placements in one plan raise an error.
Random uses column 0 for delta; gaps-only uses columns 3(i-1), 3(i-1)+1 and 3(i-1)+2 for level
i = 1..m (up point, down point, side). Placebo k: tag `MASTER|SYMBOL|placebo|k`, two uniforms: `s_k = +1`
when the first is below 0.5, else -1, and `u_k` = the second. Variants and ratios reuse the main run's
tags, so they are paired with it. Bootstrap: the table of a block set is
`PCG64(seed("BOOT_SEED|blocks|" + the comma-joined sorted block labels)).integers(0, G, size=(B, G))`,
shared by every statistic on that set. Percentiles (the 5th of S14.1, the `0.05/K` tail of S14.4, the
80th of the SE_k) use linear interpolation (numpy's default). Self-test 2: tags `MASTER|selftest2|...`.

## 17. Constants (filled in at step 4)

| Constant | Value |
|---|---|
| MASTER | 20261028 |
| BOOT_SEED | 20261029 |
| B | 10,000 |
| data end | 2026-10-27 |
| D_1 .. D_5 (fixed group) | 4.5715%, 9.7441%, 14.7084%, 19.5311%, 24.2840% (4,772 plan starts of the 50 main-sample stocks, k = 5; `references/ladder_v3/fixed_depths.json`; recomputed by S6's rule if batches are added) |
| MDE80, batches added | step 5 |

## 18. Departures from the plan's wording, for the user's sign-off

1. The pool skips all 96 previously tested stocks, not 32 (the user's choice, 2026-10-28).
2. Plans start in February 2008 (S3).
3. Valuation: trailing P/E instead of forward P/E, as variants only (S13; pending the user's answer).
4. Random control: antithetic pairs, level 1 capped on up draws, half the smallest gap (S6).
5. Gaps-only control: each level moved to the nearest gap above or below (S6).
6. Interest on all cash (S10).
7. Power check: a persistent placebo ladder against the 20-draw mean, MDE80 as the trigger (S14.2).
8. Interval: t-based with a small-block correction, lower bound the smaller of two; the overlap of
   adjacent year blocks is not corrected and is checked with 2-year blocks (S14.1).
9. Jumps over 50% checked against splits, the dividend-adjusted closes and a reversal test, not hourly
   bars (S1).
10. Breakout start: level 1 is the broken zone's high without the 2% test, and random's up draw is capped
    0.25% below C[t] instead of 2%, so that it stays an up draw (S13).
11. Held-out confirmation: only the rule items enter the set, each tested in the direction found on the
    main sample (S14.4).
