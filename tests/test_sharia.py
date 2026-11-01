from stockbot.analysis import sharia as sh
from tests.conftest import make_fund


def test_aaoifi_pass_and_market_cap_basis():
    v = sh.screen(make_fund(), "AAOIFI")
    assert v.status == sh.HALAL
    assert abs(v.ratios["debt"] - 100e9 / 3.0e12) < 1e-9
    assert v.thresholds["debt"] == 0.30 and v.thresholds["receivables"] is None
    assert "AAOIFI" in v.label() and v.sources == []


def test_methods_are_not_mixed():
    f = make_fund(total_debt=120e9, total_assets=350e9, market_cap=1.0e12)   # debt 12% of cap, 34% of assets
    assert sh.screen(f, "AAOIFI").status == sh.HALAL
    msci = sh.screen(f, "MSCI")
    assert msci.status == sh.NON_COMPLIANT and "total assets basis" in msci.reasons[0]
    assert sh.screen(f, "FTSE").status == sh.NON_COMPLIANT


def test_ftse_receivables_limit_is_50_pct_but_msci_33():
    f = make_fund(total_debt=10e9, total_cash=50e9, receivables=100e9, total_assets=350e9)  # rc+cash = 43%
    assert sh.screen(f, "FTSE").status == sh.HALAL
    v = sh.screen(f, "MSCI")
    assert v.status == sh.NON_COMPLIANT and "receivables" in v.reasons[0]


def test_business_screen_fails_banks_and_flags_review_industries():
    assert sh.screen(make_fund(sector="Financial Services", industry="Banks - Diversified"), "AAOIFI").status == sh.NON_COMPLIANT
    assert sh.screen(make_fund(industry="Entertainment"), "AAOIFI").status == sh.NON_COMPLIANT
    v = sh.screen(make_fund(industry="Restaurants"), "AAOIFI")
    assert v.status == sh.QUESTIONABLE and "alcohol" in v.business_note


def test_never_halal_without_data():
    v = sh.screen(make_fund(market_cap=None), "AAOIFI")
    assert v.status == sh.UNVERIFIED and "market cap" in v.unverified[0]
    v = sh.screen(make_fund(total_debt=None), "AAOIFI")
    assert v.status == sh.UNVERIFIED and any("debt" in u for u in v.unverified)
    v = sh.screen(make_fund(interest_income=None), "AAOIFI")
    assert v.status == sh.QUESTIONABLE and any("income" in u for u in v.unverified)
    v = sh.screen(make_fund(sector="", industry=""), "AAOIFI")
    assert v.status == sh.UNVERIFIED


def test_income_ratio_fails_at_5_pct():
    v = sh.screen(make_fund(interest_income=25e9, revenue=390e9), "AAOIFI")
    assert v.status == sh.NON_COMPLIANT and "non-permissible income" in v.reasons[0]


def test_overrides_and_etf():
    assert sh.screen(make_fund(industry="Banks"), "AAOIFI", whitelist=frozenset({"AAPL"})).status == sh.HALAL
    assert sh.screen(make_fund(), "AAOIFI", blacklist=frozenset({"AAPL"})).status == sh.NON_COMPLIANT
    etf = make_fund(ticker="SPY", sector="", industry="", description="quoteType=ETF")
    assert sh.screen(etf, "AAOIFI").status == sh.UNVERIFIED


def test_verdict_text_helpers():
    v = sh.screen(make_fund(), "MSCI")
    assert any("✓" in line for line in v.ratio_lines())
    assert "MSCI" in sh.method_note("MSCI") and "total assets" in sh.method_note("MSCI")
