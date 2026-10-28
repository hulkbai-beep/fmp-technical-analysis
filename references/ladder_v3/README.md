# Ladder test V3: the frozen stock list

Drawn on 2026-10-28 with seed 20261028, before any price of a drawn stock was fetched or simulated
(plan V3, step 2). Built by `scripts/ladder_universe.py`: `frame` → `check` → `pairs` → `closes` →
`wiki` → `entities` → `sample`. Rerunning `sample --entities entities.json --seed 20261028` gives the
same lists.

| File | What it holds |
|---|---|
| `frame.json` | S&P 500 membership spans per ticker since 2008, from github.com/fja05680/sp500, "S&P 500 Historical Components & Changes (Updated).csv" (daily snapshots 1996-01-02 to 2026-08-18, md5 16979a5cebe42c4d3e3e0cced0f9d532) |
| `entities.json` | the 898 tickers merged into companies: series ticker, membership, FMP sector, kind (large cap, previously tested, no usable ticker, not eligible, pool); the same-series pairs, the hand decisions and the spans whose Wikipedia name differs from FMP's |
| `sample.json` | the strata, each stratum's seeded order and the draw: main (the seven large caps and 43), held out (50), six reserve batches of 10 + 10, three spares per stratum, with each drawn company's record |
| `main.txt`, `holdout.txt` | the series tickers |

Inputs kept in the project files, not here (FMP data): `check.jsonl` (md5 babcc47d048f09b00b5d23b0e98ffb8a)
and `check2.jsonl` (23 tickers re-checked after the frame fix, md5 ca52a15a9cbf3094dfea5d132049142e),
`pairs.json` (md5 38ace288b7e8a2affee82c1043ee95f2), `closes.jsonl` (md5 7435f41d15d656d2753aaa75aa1d6ca5),
and Wikipedia's "List of S&P 500 companies" (md5 da8a3f93a6d3d3f54be761a278e41dad) and "Historical
components of the S&P 500" (md5 5d42f147aec8170330f7f0561a38fe18) as saved on 2026-10-28.

## The pool

589 companies: S&P 500 members on some day from 2008-01 to 2025-10 whose FMP bars cover their
membership and the three years before it, without the seven large caps (always in the main sample)
and without any company that has a ticker among the 96 stocks examined in earlier tests.

- Tickers are one company when FMP keeps one series under both (closes before the ticker switch in a
  constant ratio: 20 pairs, among them UTX = RTX, so United Technologies counts as tested), when FMP
  gives them the same company name (share classes such as NWSA/NWS), or by `SAME_COMPANY` (8 renames
  whose old ticker has no usable bars). A company's plans use the bars of its series ticker, the one
  whose data run latest.
- `BARS_FROM` drops what FMP holds under a reused ticker: CCU, EP, LSI, BBT, PARA and AGN entirely,
  and TT, FOX, JCI and DD before the date the current company took the ticker.
- Strata are FMP sectors (12 with "Unknown" for delisted companies without a profile); the main and
  held-out counts per stratum are proportional (largest remainder).

## After the fetch (`verify`)

A drawn company is replaced when its series has no month that can start a plan (a member on the
month's first bar, with 756 bars of history), or when it duplicates another drawn company or a
previously tested or large-cap stock: one series (the log close ratio has a standard deviation below
0.001 over 60 common days while the price moves) or twins (daily log returns correlated 0.97 or more
over at least 250 common days). Of two drawn duplicates the later one in the lists is replaced. The
replacement is the next company of the same stratum's order that is not drawn yet (the spares first),
main sample before held out, in list order.

The held-out prices stay on the user's computer, in a folder of their own, until step 7; only
`verify`'s statuses (counts, no prices) come back before then.

Run on 2026-10-28 (`verify.json`): all 136 fetched companies (main 50, held out 50, spares 36) have
plan months and none duplicates another, so nothing was replaced and `main.txt` and `holdout.txt`
are final. The 120 reserve companies were not fetched; they are only needed if the power check asks
for more stocks. For held-out companies `verify.json` keeps only what the rule needs (no counts of
zero-volume bars or large moves). `data_manifest.json` lists the md5 of every FMP file of the main
sample, the spares and the previously tested stocks (bars from 2005-01-03 to 2026-10-27).

## Steps 3 and 4: the code, the self-tests and the registration

The rules are `references/ladder_v3_spec.md`, with the constants of its section 17. They are run by
`scripts/ladder_sim.py` (cleaning, zones, plan starts, the groups and the bar loop of S1-S12),
`scripts/ladder_stats.py` (the calendar-block bootstrap, Holm, MDE80), `scripts/ladder_analysis.py`
(the tests of S14 on the cached runs), `scripts/ladder_run.py` (the driver) and
`scripts/ladder_selftest.py` (S15). The unit tests (`tests/test_ladder_sim.py`, `test_ladder_stats.py`,
`test_ladder_analysis.py`) use hand-made bars and plan tables, no FMP data. The step-4 commit holds all
of them before any outcome of the main sample is computed. A change after it is a commit of its own
that gives the reason and names the results it touches.

| File | What it holds |
|---|---|
| `selftest1.json` | self-test 1 (no future data) on the 50 main-sample stocks: pass/fail and coverage counts only, no returns. The code as registered passes all three parts (inputs, kernel, audit), each of the 11 planted leaks is caught, reruns are identical and every level moves with MASTER |
| `selftest2.json` | self-test 2 (planted levels that truly hold) on synthetic stocks resampled from 20 previously tested stocks: theta +0.00 pp at q = 0 (screen passed 0 of 10 times), +4.60 pp at q = 0.5 (5 of 5), +5.41 pp at q = 1 (5 of 5), placebo -0.57 pp; fill-break share 36.5%, 15.3%, 6.1%; all eight gates pass |
| `fixed_depths.json` | D_1..D_5 of the fixed group (S6): the median depth of each zone-ladder level at the 4,772 plan starts of the main sample, from the ladders alone |

Two reviews of the code and the rules came before the registration, each finding checked by a second
reader. The first (22 findings, none of high severity) led to fixes in the statistics and the reports.
The second found the main test and the statistics right, and 10 issues in the variants' inputs and the
rules' wording, all addressed before the commit: FMP placeholder dates in the income and earnings files
(S12, S13), quarters missing from the trailing EPS (S13), the cap of a 200-day level 1 on random's up
draws (S13), which items enter the held-out confirmation (S14.4; the one issue that could change the
final decision), and wording in S11, S12, S14.1 and S16. The self-tests ran after each
round; the files here are from the code as registered, and self-test 2 gave the same numbers every
time, to 1e-14. An early trial of self-test 2 on three source stocks put theta below 0 at q = 0. On
all 20 sources it is 0.00 pp in the block-resampled worlds, -0.14 pp with bars resampled one at a
time and +0.07 pp with the zones of an unrelated synthetic path, so the trial's value was noise.

## Steps 5 to 7: the results

Run on 2026-10-28 with the code of the step-4 commit, unchanged:

    python scripts/ladder_run.py zones --set main
    python scripts/ladder_run.py power --set main
    python scripts/ladder_run.py simulate --set main
    python scripts/ladder_run.py report --set main          # --no-valuation: m = 23
    python scripts/ladder_run.py confirm --set holdout      # step 7, only with a non-empty confirmation set

| File | What it holds |
|---|---|
| `power_main.json` | S14.2: the 20 placebo SEs at 252 bars (0.087 to 0.185 pp), MDE80 0.41 pp, so no reserve batch was added and D_1..D_5 stand |
| `results_main.json` | S14.1 to S14.5: the main test and its checks, the 27 secondary items with Holm, the descriptive tables, the confirmation set and the step-8 choice |

The main sample gives 4,772 plan starts and 4,512 plans counted at 252 bars, in 18 calendar-year
blocks (260 run past the data end; none is left out for suspicious bars or a data gap). All figures
below are at 252 bars, in percentage points (pp) of the plan's capital.

- **Main test: not passed.** theta = R_252(zone) - R_252(random) = -0.03 pp, SE 0.10 pp, 90% interval
  -0.21 to +0.15 pp, p 0.78. The lower bound is below 0 and theta is below +1 pp, so the screen fails.
  At 126 bars theta is -0.005 pp (the same sign); on the 43 stocks alone +0.03 pp (the opposite sign).
  With 2-year blocks the interval is -0.19 to +0.14; leaving out one year at a time theta runs from
  -0.07 to +0.01; on the 2,971 plans whose start placement had no capped up draw it is +0.07 pp (-0.15
  to +0.29). The test's own SE (0.10 pp) is below the 80th percentile of the power check's, 0.157 pp,
  so power was not overstated (S14.2).
- **The control matched the zones.** Random's start orders sat 13.20% below the start close against
  zone's 13.19% (signed shift -0.02 pp of price), and random deployed 1.2 pp less capital than zone.
- **Secondary items (Holm at 0.10, m = 27): two rejected, neither a rule that passes.** Item 4, zone -
  gaps only, +3.66 pp (Holm p 0.002), is exposure: the gaps-only control's start orders sit deeper
  (21.4% below the start close vs 13.2%), and in 48% of the start's part slots it had no order where
  zone had one, so it deployed 35% of capital against zone's 87%. Item 16, the 200-day variant's zone -
  random, -0.27 pp (Holm p 0.09): the ladder with a 200-day level did slightly worse than the same
  ladder shifted. The next smallest raw p values are zone - lump (-3.65 pp, p 0.01, the sample's
  upward drift, as in H.2 of `references/methodology.md`), 1:2:3:2:2 - 1:1:1:1:1 (-0.30 pp, p 0.01),
  close confirm (zone) - main (zone) (-0.33 pp, p 0.03) and zone - vol (+1.22 pp, p 0.05); none
  survives Holm. The trailing P/E variants (items 22-25, valuation A 1,615 plans, B 341) show nothing
  either. The user had not yet answered whether to keep them; since the items are computed, dropping
  them now only leaves them out of the conclusions and m stays 27 (S14.3), and no conclusion changes
  either way.
- **Step 7: nothing to confirm.** The confirmation set (S14.4) is empty: the main rule failed the
  screen, item 4 is a benchmark comparison rather than a rule, and item 16's |theta| is below 1 pp. The
  held-out prices were therefore never simulated or opened, and `confirm_holdout.json` was not written.
  The held-out list stays unused for a later test.
- **Step 8 (S14.5): the zones stay a map; orders use fixed percentages.** Mean R_252: fixed 8.48%, vol
  7.21% (zone 8.43%, random 8.46%, lump 12.08%, gaps only 4.77%). vol - fixed = -1.27 pp, 90% interval
  -2.26 to -0.28, p 0.04. Deployed share: fixed 86.1%, vol 80.2% (zone 86.6%). Part-weighted start
  depth: fixed 12.5%, vol 17.2% (zone 13.2%). This choice is a default, not a confirmed result.

Other descriptive rows: zone - random is +0.01 pp at 63 bars, -0.00 at 126 and -0.11 at 504; on the 34%
of plans where the main zone group had no fill within 42 bars, the time-limit variant's zone - random is
-0.20 pp. Win rate (plans with a fill) zone 65.5%, random 65.8%, fixed 66.8%, vol 68.5%; payoff ratio
1.52, 1.51, 1.44, 1.31; stop rate 24.9%, 24.4%, 23.7%, 21.7%; safety margin -10.3% (vol) to -11.1% (gaps
only) for the ladders and -13.5% for lump. Cleaning: CXO, DAY, PBCT, SIVB, TGNA and TIF are delisted and
liquidated; of the moves over 50%, LUMN (+93%, 2024-08-06) and SIVB (-60%, 2023-03-09) are confirmed and
PBI (+81%, 2021-01-26) is not, with no plan in its window; 147 plans at 252 bars touch a quarter with a
missing report date. Starts skipped: 2,861 below the 200-day average, 2,543 when the company was not a
member, 359 with fewer than 756 bars, 1 on the stock's last bar.
