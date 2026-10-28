"""Offline checks of the V3 data probe: membership rebuild, date parsing and a full run on a fake client."""
import json
from datetime import date

import fmp_client
import ladder_probe as P


def test_parse_day_formats():
    assert P.parse_day("2023-12-18") == date(2023, 12, 18)
    assert P.parse_day("2023-12-18 00:00:00") == date(2023, 12, 18)
    assert P.parse_day("December 18, 2023") == date(2023, 12, 18)
    assert P.parse_day("") is None and P.parse_day("n/a") is None


def test_members_on_undoes_later_changes():
    current = {"A", "B", "NEW"}
    changes = [{"day": date(2020, 6, 1), "added": "NEW", "removed": "OLD"},
               {"day": date(2010, 1, 4), "added": "B", "removed": "GONE"}]
    assert P.members_on(current, changes, date(2021, 1, 1)) == {"A", "B", "NEW"}
    assert P.members_on(current, changes, date(2015, 1, 1)) == {"A", "B", "OLD"}
    assert P.members_on(current, changes, date(2009, 1, 1)) == {"A", "GONE", "OLD"}
    # a change dated on the day itself is already in effect
    assert P.members_on(current, changes, date(2020, 6, 1)) == {"A", "B", "NEW"}


class _Fake:
    def __init__(self):
        self.stats = {"network_calls": 0, "cache_hits": 0}

    def get(self, endpoint, **params):
        self.stats["network_calls"] += 1
        if endpoint == "sp500-constituent":
            return [{"symbol": "AAPL", "sector": "Technology"}, {"symbol": "NEW", "sector": "Energy"}]
        if endpoint == "historical-sp500-constituent":
            return [{"dateAdded": "June 1, 2020", "date": "2020-06-01", "symbol": "NEW",
                     "addedSecurity": "New Co", "removedTicker": "CELG", "removedSecurity": "Celgene"}]
        if endpoint == "delisted-companies":
            return [{"symbol": "CELG", "delistedDate": "2019-11-21"}] if params.get("page") == 0 else []
        if endpoint == "analyst-estimates":
            return [{"symbol": "AAPL", "date": "2027-09-27", "epsAvg": 9.0}]
        if endpoint.startswith("candidate") or endpoint in P.ESTIMATE_CANDIDATES:
            raise fmp_client.FMPError("HTTP 404 on x")
        if endpoint == "ratios":
            raise fmp_client.RestrictedEndpoint("ratios: not included in your plan (x)")
        if endpoint == "treasury-rates":
            return [{"date": params["to"], "month3": 4.1}]
        if endpoint.startswith("historical-price-eod"):
            return [{"symbol": params["symbol"], "date": params["to"], "close": 1.0}]
        return []


def test_probe_runs_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(fmp_client, "FMPClient", lambda: _Fake())
    assert P.main(["--out", str(tmp_path), "--removed-sample", "3"]) == 0
    checks = json.loads((tmp_path / "probe.json").read_text())
    statuses = {c["label"]: c["status"] for c in checks}
    assert statuses["AAPL ratios (quarter)"] == "RESTRICTED"
    assert statuses["candidate endpoint historical-analyst-estimates"] == "ERROR"
    rebuilt = next(c for c in checks if c["label"].startswith("rebuilt membership"))
    assert rebuilt["sizes"]["2016"] == 2
    removed = next(c for c in checks if c["label"] == "removed members: what is available")
    celg = next(r for r in removed["table"] if r["ticker"] == "CELG")
    assert celg["removed"] == "2020-06-01" and celg["in_delisted_list"]
    assert (tmp_path / "raw" / "treasury.json").exists()
    assert (tmp_path / "probe.md").read_text().startswith("| group |")
