from stockbot.halal import screen


def test_business_screen():
    assert screen("JPM", "Financial Services", "Banks - Diversified", 1e12, 1e9, 1e9).status == "fail"
    assert screen("DIS", "Communication Services", "Entertainment", 1e11, 1e9, 1e9).status == "fail"
    assert screen("BUD", "Consumer Defensive", "Beverages - Brewers", 1e11, 1e9, 1e9).status == "fail"
    assert screen("LMT", "Industrials", "Aerospace & Defense", 1e11, 1e9, 1e9).status == "fail"
    # video games are flagged for review (QUESTIONABLE) by the new engine -> legacy 'unknown'
    assert screen("EA", "Communication Services", "Electronic Gaming & Multimedia", 1e11, 1e9, 1e9).status == "unknown"


def test_financial_ratios():
    ok = screen("AAPL", "Technology", "Consumer Electronics", 3e12, 100e9, 60e9)
    assert ok.status == "pass" and ok.debt_ratio < 0.05 and "✅" in ok.label()
    assert "debt" in screen("X", "Industrials", "Airlines", 60e9, 40e9, 1e9).reasons[0]
    assert "cash" in screen("Y", "Technology", "Software", 100e9, 1e9, 50e9).reasons[0]


def test_unknown_when_data_missing_and_overrides():
    assert screen("Z", "", "", None, None, None).status == "unknown"
    assert screen("Z", "Technology", "Software", 1e11, None, 1e9).status == "unknown"
    assert screen("JPM", "Financial Services", "Banks", 1e12, 1e9, 1e9, whitelist=frozenset({"JPM"})).status == "pass"
    assert screen("AAPL", "Technology", "Consumer Electronics", 3e12, 1e9, 1e9, blacklist=frozenset({"AAPL"})).status == "fail"
