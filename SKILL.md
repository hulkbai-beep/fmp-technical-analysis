---
name: technical-analysis
description: Technical analysis of one US-listed stock or ETF from Financial Modeling Prep (FMP) daily data - market regime, trend and moving averages, RSI, MACD, momentum, relative strength vs SPY and the sector ETF, volume flow, volatility, support/resistance levels, back-tested signal statistics, earnings event risk, scenarios and risk arithmetic, plus an interactive chart. Use it when the user asks for technical analysis, a chart, support/resistance, moving averages, RSI, MACD, momentum or the trend of a ticker, including in Chinese (技术面, 技术分析, K线, 支撑阻力, 均线, 趋势, 走势).
---

# Technical analysis (FMP)

`scripts/analyze.py` fetches daily bars, benchmarks and earnings dates from FMP, computes every number
and writes `metrics.json` plus a self-contained `chart.html`. Claude then writes `report.md` from
`metrics.json` only, following `references/report_template.md`. Python does the arithmetic; Claude
does the explaining. The output describes conditions and historical base rates. It is not advice.

## Setup (once)

1. `pip install -r requirements.txt` (pandas and numpy; Python 3.11+, built for Python 3.13 with
   pandas 3.0 and numpy 2.5).
2. Set the API key as an environment variable, never in a file or a command you show:
   - Windows: `setx FMP_API_KEY "<key>"`, then open a **new** terminal.
   - macOS / Linux: `export FMP_API_KEY=<key>` in the shell profile.
   - Never print, echo or log the key, and never paste it into a reply. The scripts redact it.
3. Route. With an official FMP key, leave `FMP_BASE_URL` and `FMP_TLS_VERIFY` unset: requests go to
   `https://financialmodelingprep.com/stable` with certificate verification on. The client ignores
   `FMP_TLS_VERIFY=0` for the official host.
   - If `check` or a run names a host other than `financialmodelingprep.com` (for example
     `Detecting request format for 54.236.92.205`, `HTTPS certificate verification failed for
     54.236.92.205`, `cannot reach ...` or `route : third-party proxy at ...`) although the user has
     an official key, `FMP_BASE_URL` is still set. Ask the user to remove it (PowerShell:
     `[Environment]::SetEnvironmentVariable("FMP_BASE_URL", $null, "User")`), then to restart Claude
     (quit and reopen the app, or start Claude Code in a new terminal) and any running
     `ui/server.py`; processes already running keep the old value. Never turn certificate
     verification off to get past an error.
   - Only with a third-party proxy that has a self-signed certificate (`FMP_BASE_URL` set to it), set
     `FMP_TLS_VERIFY=0` **for that one process only**. Never `setx` it or put it in a profile.
     - PowerShell: `$env:FMP_TLS_VERIFY="0"; python scripts/analyze.py AAPL`
     - cmd: `set FMP_TLS_VERIFY=0&& python scripts\analyze.py AAPL`
     - bash: `FMP_TLS_VERIFY=0 python scripts/analyze.py AAPL`
     - `ui/server.py` does this by itself only when `FMP_BASE_URL` is the old proxy host
       `54.236.92.205`.
4. Check the connection: `python scripts/fmp_client.py check` shows the route and which endpoints
   your plan includes, without the key.

## Workflow

1. **Pick the ticker and horizon.** US listings only; share classes use a dot (`BRK.B`).
   `swing` (default) is days to weeks, with a 10-day primary horizon. `position` is months, with a
   60-day horizon and a weekly chart. 短线 / 波段 → swing; 中线 / 中长线 → position.
2. **Run** from the skill folder:
   `python scripts/analyze.py TICKER --horizon swing|position [--out DIR] [--refresh] [--history-start YYYY-MM-DD]`.
   - Output goes to `./ta-output/<TICKER>_<as_of>/` by default.
   - An uncached run makes 10 API calls for a stock, 8 for an ETF and 7 for SPY. A same-day
     re-run makes 0.
   - Exit codes: 0 means OK, 1 a data or symbol problem, 2 an API key problem. Relay the message
     printed on stderr.
3. **Read `metrics.json`.** Skip the bulky parts listed in the template (`levels.candidates`,
   `levels.volume_profile.bins`, `signals.library`, `dates_sample`, `meta.config_snapshot`).
4. **Write `report.md`** next to `metrics.json`, following `references/report_template.md`
   exactly: 11 sections in fixed order, the exact key paths and its wording rules. Write it in the
   user's language.
5. **Reply** with a short summary:
   - regime, bias and confidence;
   - the nearest support and resistance;
   - any active signals, each with its n;
   - the earnings date if it falls inside the horizon;
   - any data warning;
   - the paths to `report.md` and `chart.html`;
   - the disclaimer, last.

## Local UI

`python ui/server.py [--port 8876] [--no-browser]` serves http://127.0.0.1:8876.
- Type a symbol, pick swing or position and get the same analysis and chart in the browser.
- The interface is Chinese by default, with an EN toggle.
- It listens on localhost only and reads `FMP_*` values saved with `setx` even in an older terminal.
- It is optional; the CLI is the main path.

## Interpretation rules

- **Numbers come only from `metrics.json`.** Copy them; never estimate, recompute or fill a `null`.
  A `null` or `available: false` is reported with its `reason`.
- **Conditional, probabilistic wording:**
  - write "if X closes above Y on RVOL ≥ 1.5, …" and "historically, in n cases since …";
  - never "will";
  - no buy / sell / hold advice (in Chinese, no 买入 / 卖出 / 持有);
  - targets are zones, and the risk section is illustrative arithmetic.
- **Regime first.** In trend regimes, trend, momentum and RS carry the scorecard. In range regimes,
  mean reversion, flow and the volatility setup do. An overbought RSI in a strong uptrend is not a
  sell signal.
- **Signal statistics:**
  - always state n, the history start and the confidence;
  - `insufficient` (n < 15) is not evidence;
  - compare with the same-regime baseline, not with zero;
  - for a bearish rule, a *negative* edge supports it.
- **Report every conflict** in `scorecard.conflicts`, with its typical meaning. Do not add your own.
- **Data source:** if `data_source` names a third-party proxy, say so and suggest verifying key
  figures.
- **Closing bar:** the price is the last *complete* close. Before 16:15 New York time, today's bar
  is excluded (`data_quality.partial_bar_excluded`).
- End every report and summary with `meta.disclaimer`.

## Options

| Option | Values | Default | Effect |
|---|---|---|---|
| `TICKER` | e.g. `AAPL`, `BRK.B`, `SPY` | required | US stock or ETF |
| `--horizon` | `swing`, `position` | `swing` | stats horizons 5/10/20 vs 20/60 days; levels window 126 vs 504 bars; daily vs weekly chart |
| `--out` | folder | `./ta-output/<TICKER>_<as_of>/` | where `metrics.json` and `chart.html` go |
| `--refresh` | flag | off | ignore the cache and fetch everything again |
| `--history-start` | `YYYY-MM-DD` | IPO date | ignore the symbol's history before this date (e.g. a pre-merger SPAC shell) |
| `FMP_BASE_URL` | URL | unset (official FMP) | only for a third-party proxy |
| `FMP_TLS_VERIFY` | `0` / `1` | `1` | `0` only per process, only for a proxy; ignored for the official host |
| `FMP_CACHE_DIR` | folder | `~/.cache/fmp-technical` | response cache (never contains the key) |
| `FMP_MAX_PER_MIN`, `FMP_TIMEOUT` | numbers | 280, 30 s | rate cap, per-request timeout |

All analysis thresholds live in `scripts/config.py`, and each run records them in
`meta.config_snapshot`.

## Edge cases

- **ETFs:** no earnings or sector block, and SPY has no RS vs SPY. These show as skipped
  (`meta.skipped`, `meta.modules_unavailable`). That is expected, not an error.
- **Short history** (recent IPO, fewer than 200 bars): SMA200-based readings, percentiles and
  signal statistics become unavailable or `insufficient`. Say so and do not extrapolate.
- **Plan restrictions:** without the dividend-adjusted endpoint, returns are price-only
  (`data_quality.adj_coverage` is below 1). Without earnings, event risk is skipped. Both are
  listed in `meta.restricted`.
- **SPAC history:** FMP dates a de-SPAC listing from the shell's first trading day (IONQ starts
  2021-01-04 near $10). `data_quality.spac_like_start` flags it; mention it, and offer a re-run with
  `--history-start <merger date>` so signal statistics exclude the shell.
- **Stale or illiquid data:** report `data_quality.stale` and `data_quality.liquidity` warnings
  first.
- **Module failure:** a failing module shows `available: false`,
  `reason: "internal error: ..."` and an entry in `meta.errors`. The rest of the report still
  stands. Name the gap.
- **Unknown or non-US symbol:** exit 1 with a hint. Ask the user to confirm the symbol.

## Troubleshooting

- `python scripts/fmp_client.py check [SYMBOL]` shows the connection route, the auth style and
  which endpoints work on this plan. Run it first for any FMP error.
- Exit code 2 or HTTP 401 means the key is wrong or missing. After `setx`, open a new terminal.
- "certificate verify failed" on the official API: something on the network (a corporate proxy or
  antivirus HTTPS scanning) is intercepting the connection. Do not turn verification off; the client
  keeps it on for the official host anyway. With a third-party proxy, see Setup 3.
- `python scripts/crosscheck.py TICKER` compares the skill's SMA(50) and RSI(14) with FMP's own
  indicator endpoints. Use it when a number looks off.
- `python scripts/fmp_client.py clear-cache` deletes cached responses and the saved proxy detection.
- `python -m pytest -q` runs the offline test suite (no network, no key).

## References

- `references/report_template.md`: the report's section order, key paths and wording rules. Read
  it before writing `report.md`.
- `references/methodology.md`: data, adjustments, indicators, modules, the scorecard, scenarios
  and approximations.
- `references/signals.md`: the 14 pre-registered signal rules and how their statistics are
  computed.
