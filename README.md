# technical-analysis skill

A Claude skill for technical analysis of one US stock or ETF from Financial Modeling Prep (FMP) daily
data. Python computes every number; Claude writes the report from `metrics.json`. `SKILL.md` is the
entry point Claude reads.

```
pip install -r requirements.txt
python scripts/fmp_client.py check                    # connection and plan check (needs FMP_API_KEY)
python scripts/analyze.py AAPL --horizon swing        # -> ta-output/AAPL_<date>/metrics.json, chart.html
python ui/server.py                                   # local UI at http://127.0.0.1:8876
python -m pytest -q                                   # offline tests
```

When `FMP_BASE_URL` points at the third-party proxy `https://54.236.92.205`, set `FMP_TLS_VERIFY=0`
for that process only (see `SKILL.md` > Setup). Never store or print the API key.

| Path | Contents |
|---|---|
| `SKILL.md` | setup, workflow, interpretation rules, options, edge cases, troubleshooting |
| `scripts/` | `analyze.py` (entry point), `fmp_client.py`, `fetch.py`, `indicators.py`, `regime.py`, `modules.py`, `levels.py`, `signal_stats.py`, `scorecard.py`, `chart.py`, `crosscheck.py`, `config.py` (every threshold) |
| `references/` | `report_template.md` (report structure), `methodology.md`, `signals.md` |
| `ui/` | standard-library local web UI and the chart's JS/CSS |
| `tests/` | offline pytest suite with a fake FMP client (`tests/fakes.py`) |

Output is technical analysis for information only, not investment advice.
