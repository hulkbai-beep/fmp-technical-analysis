import numpy as np
import pandas as pd

import fundamentals_zones as F


def _raw(n=12, filing_lag=35, placeholder_at=()):
    ends = pd.date_range("2015-03-31", periods=n, freq="QE")
    inc, bal, cf = [], [], []
    for i, e in enumerate(ends):
        filed = e if i in placeholder_at else e + pd.Timedelta(days=filing_lag)
        f = str(filed.date())
        inc.append({"date": str(e.date()), "filingDate": f, "revenue": 100.0 + i, "grossProfit": 40.0,
                    "netIncome": 10.0, "weightedAverageShsOutDil": 50.0})
        bal.append({"date": str(e.date()), "filingDate": f, "totalAssets": 500.0, "totalCurrentAssets": 200.0,
                    "totalCurrentLiabilities": 100.0, "longTermDebt": 50.0})
        cf.append({"date": str(e.date()), "filingDate": f, "operatingCashFlow": 12.0})
    return {"income": inc[::-1], "balance": bal[::-1], "cashflow": cf[::-1]}


def test_placeholder_filing_date_falls_back():
    q = F.quarters(_raw(placeholder_at=(3,)))
    row = q.iloc[3]
    assert row["fallback"]
    assert row["known"] == q.index[3] + pd.Timedelta(days=F.LAG_DAYS)
    assert not q.iloc[4]["fallback"]


def test_fundamentals_use_only_filed_quarters():
    raw = _raw()
    dates = pd.bdate_range("2015-01-01", "2018-12-31")
    mcap = [{"date": str(d.date()), "marketCap": 1000.0} for d in dates]
    out = F.daily_fundamentals(raw, mcap, dates)
    q = F.ttm_table(F.quarters(raw))
    first = q.dropna(subset=["fscore"]).iloc[0]
    # nothing before the first usable quarter's filing day, and not on that day itself
    assert out.loc[:first["known"], "fscore"].isna().all()
    assert np.isfinite(out.loc[first["known"] + pd.Timedelta(days=1):, "fscore"]).iloc[0]


def test_share_growth_undoes_unrestated_split():
    q = pd.DataFrame({"weightedAverageShsOutDil": [100.0] * 4 + [400.0] * 4},
                     index=pd.date_range("2019-03-31", periods=8, freq="QE"))
    g = F.share_growth(q, [{"date": "2020-02-15", "numerator": 4, "denominator": 1}])
    assert np.allclose(g.iloc[4:], 1.0)


def test_trade_payoff_zero_when_unfilled_and_fill_at_open_below_limit():
    n = 60
    c = np.full(n, 100.0)
    o, h, l = c.copy(), c + 1, c - 1
    assert F.trade(o, h, l, c, 0, 80.0, 90.0, 2.0)[:2] == (0.0, False)
    o2, l2 = o.copy(), l.copy()
    o2[1], l2[1] = 85.0, 84.0
    pnl, filled, _ = F.trade(o2, h, l2, c, 0, 80.0, 90.0, 2.0)
    assert filled and pnl == (c[F.FILL_BARS + F.HOLD] - 85.0) / 2.0
