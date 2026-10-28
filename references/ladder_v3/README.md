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
