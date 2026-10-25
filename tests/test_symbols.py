"""Ticker normalisation (scripts/symbols.py): what people type -> the symbol FMP's stable API expects."""
import pytest

from symbols import normalize_symbol


@pytest.mark.parametrize("typed, symbol", [
    ("AAPL", "AAPL"), ("aapl", "AAPL"), ("  msft ", "MSFT"), ("$nvda", "NVDA"), ("$ TSLA", "TSLA"),
    ("^VIX", "^VIX"), ("^gspc", "^GSPC"), ("GOOGL", "GOOGL"),
    ("BRK.B", "BRK-B"), ("brk.b", "BRK-B"), ("BRK-B", "BRK-B"), ("BRK/B", "BRK-B"), ("brk b", "BRK-B"),
    ("BRK  B", "BRK-B"), ("$BRK.B", "BRK-B"), ("BF.B", "BF-B"), ("hei.a", "HEI-A"), ("LEN.B", "LEN-B"),
])
def test_accepted_forms(typed, symbol):
    assert normalize_symbol(typed) == symbol
    assert normalize_symbol(symbol) == symbol            # idempotent: every entry point may call it again


@pytest.mark.parametrize("typed, message", [
    ("", "no ticker given"), ("   ", "no ticker given"), (None, "no ticker given"), ("$", "no ticker given"),
    ("SHOP.TO", "non-US listing"), ("0700.HK", "non-US listing"), ("SAP.DE", "non-US listing"),
    ("VOD.L", "London listing"), ("7203.T", "non-US"), ("6758.T", "non-US"),
    ("AAPL;rm", "not a US ticker"), ("TOOLONGSYMBOL", "not a US ticker"), ("A B C", "not a US ticker"),
    ("<script>", "not a US ticker"), ("12345", "not a US ticker"), ("BRK.B.C", "not a US ticker"),
])
def test_refused_forms(typed, message):
    with pytest.raises(ValueError, match=message):
        normalize_symbol(typed)


def test_a_one_letter_exchange_suffix_is_refused_only_after_a_dot():
    with pytest.raises(ValueError, match="London"):
        normalize_symbol("VOD.L")
    assert normalize_symbol("VOD-L") == "VOD-L"          # already in FMP's share-class form: taken as typed
