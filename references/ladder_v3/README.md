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

Steps 5 to 7 add `power_main.json` (S14.2), `results_main.json` (the main test, the 27 or 23 secondary
items and the descriptive tables) and `confirm_holdout.json` (S14.4):

    python scripts/ladder_run.py zones --set main
    python scripts/ladder_run.py power --set main
    python scripts/ladder_run.py simulate --set main
    python scripts/ladder_run.py report --set main          # --no-valuation: m = 23
    python scripts/ladder_run.py confirm --set holdout      # step 7, after the held-out run
