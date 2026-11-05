# fmp-technical-analysis skill

A Claude skill for technical analysis of one US stock or ETF from Financial Modeling Prep (FMP) daily
data. Python computes every number; Claude writes the report from `metrics.json`. `SKILL.md` is the
entry point Claude reads.

```
pip install -r requirements.txt
python scripts/fmp_client.py check                    # connection and plan check (needs FMP_API_KEY)
python scripts/analyze.py AAPL --horizon swing        # -> ta-output/AAPL_<date>/metrics.json, chart.html (3 charts)
python scripts/analyze.py AAPL --contract aapl.json   # also the ta-2.1 contract (swing + position) for other tools
python ui/server.py                                   # local UI at http://127.0.0.1:8876
python -m pytest -q                                   # offline tests
```

If pytest cannot write to its default temp folder (some Windows sandboxes block
`%LOCALAPPDATA%\Temp\pytest-of-<user>`), run `python -m pytest -q --basetemp=.pytest_tmp`.

**ta-2.1 contract for other tools.** A decision engine or screener should read the contract, not
`metrics.json`, whose layout is internal and may change. `--contract PATH` writes it: the requested horizon
runs as usual and the other one runs from the same-day cache (no extra API calls). To combine two saved runs
of the same ticker and day instead, give each horizon its own `--out` (by default both write to the same
`ta-output/<TICKER>_<as_of>/` folder, so the second run would overwrite the first):

```
python scripts/analyze.py AAPL --horizon swing --out runs/aapl_swing
python scripts/analyze.py AAPL --horizon position --out runs/aapl_position
python scripts/contract.py runs/aapl_swing/metrics.json runs/aapl_position/metrics.json -o aapl.json
```

`contracts/ta-2.1.schema.json` describes every field: horizon-independent blocks (data quality, market,
earnings, dividend, macro, volatility, extension, trend, regime, relative strength, flow, weekly, price-action
context) at the top, and the scorecard, support/resistance zones (`low` / `high`), `price_location`, scenarios,
reference stops, earnings, the ex-dividend date and macro events inside the horizon, and signal statistics under
`horizons.swing` and `horizons.position`. 1.1 adds the latest earnings date (`event.last_earnings`) and whether
each scenario target is a real level or an ATR projection (`kind`). 1.2 adds `levels_note` (the
support/resistance zones are reference only), the calibrated 95% band (`range_95`, about ±2.4σ) and the
volatility-band stop and target with their calibrated touch rates (`stops_ref`). 2.0 removes
`event.eps_estimated`: the skill carries no fundamentals. 2.1 adds the exchange-calendar source
(`data_quality.calendar_source`), the last and next ex-dividend dates (`dividend`), the scheduled US macro
releases after `as_of` (`macro`) and, per horizon, whether the next ex-dividend date and which macro events fall
inside it; trading-day counts now exclude NYSE holidays. The contract selects facts and judges nothing; position
sizing is left to the caller.
Fields are only ever added within a major version; renaming or removing one means ta-3.0.

Tickers: share classes may be typed `BRK.B`, `BRK/B` or `brk b`; every entry point turns them into FMP's
`BRK-B` (`scripts/symbols.py`). Foreign suffixes such as `SHOP.TO` are refused with a hint.

With an official FMP key, set only `FMP_API_KEY` and leave `FMP_BASE_URL` and `FMP_TLS_VERIFY` unset;
certificate verification stays on. Only with a third-party proxy that has a self-signed certificate
set `FMP_TLS_VERIFY=0`, for that process only (see `SKILL.md` > Setup). Never store or print the API key.

| Path | Contents |
|---|---|
| `SKILL.md` | setup, workflow, interpretation rules, options, edge cases, troubleshooting |
| `scripts/` | `analyze.py` (entry point), `fmp_client.py`, `fetch.py`, `market_calendar.py` (NYSE trading calendar), `indicators.py`, `regime.py`, `modules.py`, `levels.py`, `dividends.py` (ex-dividend dates), `macro.py` (scheduled US macro events), `signal_stats.py`, `price_action.py`, `candlesticks.py`, `scorecard.py`, `chart.py`, `crosscheck.py`, `contract.py` (the ta-2.1 contract), `symbols.py` (ticker normalisation), `config.py` (every threshold) |
| `contracts/` | `ta-2.1.schema.json`: every field of the contract other tools read instead of `metrics.json` (JSON Schema) |
| `references/` | `report_template.md` (report structure), `methodology.md`, `signals.md`, `patterns.md` (price action and candlestick patterns) |
| `ui/` | standard-library local web UI and the chart's JS/CSS |
| `tests/` | offline pytest suite with a fake FMP client (`tests/fakes.py`) and FMP's NYSE holiday list for 1990-2028 (`tests/data/nyse_holidays_fmp.json`) |

The research back-tests that used to live in this repository (support/resistance and node-zone tests,
staged-entry ladder tests, data cleaning) were removed on 2026-11-04 to keep the skill to technical analysis,
together with `scripts/vol_bands.py`, which measured the volatility-band tables still used in
`scripts/config.py` (`VOL_BAND_COVER`, `VOL_TOUCH`, `VOL_PAIR_ODDS`). They remain in the git history
(commit 145a174).

Output is technical analysis for information only, not investment advice.
