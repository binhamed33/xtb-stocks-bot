from stockbot.analysis import fundamental as fa
from stockbot.analysis import risk as ra
from stockbot.analysis import scoring
from stockbot.analysis import sharia as sh
from stockbot.analysis import technical as ta
from tests.conftest import make_candles, make_fund


def test_technical_full_view():
    c = make_candles(400)
    v = ta.analyze(c)
    assert v and v.ema20 and v.ema200 and v.rsi14 is not None and v.macd_hist is not None
    assert v.bb_low < v.bb_mid < v.bb_high and v.atr14 > 0 and v.vwap20
    assert v.trend_long in ("up", "sideways", "down")
    assert v.stop and v.stop < v.price < v.target1 <= v.target2 and v.reward_risk and v.reward_risk > 0
    assert v.entry_low <= v.price <= v.entry_high or v.entry_low < v.entry_high
    assert 0 <= v.score <= 100 and 0 <= v.confidence <= 1
    assert v.signals and v.summary_ar()


def test_technical_short_series_returns_none():
    assert ta.analyze(make_candles(10)) is None


def test_golden_cross_detection():
    fast = [1, 1, 1, 2, 3]
    slow = [2, 2, 2, 2, 2]
    assert ta.cross_recent(fast, slow, 3) == "golden"
    assert ta.cross_recent(slow, fast, 3) == "death"


def test_fundamental_health_and_drop_diagnosis():
    c = make_candles(400)
    t = ta.analyze(c)
    good = fa.analyze(make_fund(), t)
    assert good.health_score > 60 and good.growth_score >= 50 and "تدفق نقدي حر موجب" in good.strengths
    assert good.drop_diagnosis == "n/a"
    t.from_52w_high_pct = -35
    bad = fa.analyze(make_fund(revenue_growth=-0.2, earnings_growth=-0.5, free_cash_flow=-5e9, debt_to_equity=300), t)
    assert bad.drop_diagnosis == "warning" and bad.health_score < 50
    opp = fa.analyze(make_fund(), t)
    assert opp.drop_diagnosis == "opportunity"


def test_risk_metrics():
    r = ra.analyze(make_candles(300), make_fund(beta=1.6, short_pct_float=0.2, earnings_date="2026-11-20"))
    assert r.annual_volatility and r.max_drawdown_1y <= 0 and r.level in ("low", "medium", "high", "very high")
    assert any("بيتا" in n for n in r.notes) and any("المكشوف" in n for n in r.notes)
    assert "ليس توقعاً" in r.loss_probability_note


def _views(fund_over=None, drop=-25.0):
    c = make_candles(400)
    t = ta.analyze(c)
    t.from_52w_high_pct = drop
    f = fa.analyze(make_fund(**(fund_over or {})), t)
    r = ra.analyze(c, make_fund())
    return t, f, r


def test_scoring_gates_dominate():
    t, f, r = _views()
    halal = sh.screen(make_fund(), "AAOIFI")
    bad = sh.screen(make_fund(industry="Banks"), "AAOIFI")
    o = scoring.score("AAPL", t, f, r, halal, "REAL_STOCK", news_score=14)
    assert o.total == sum(o.parts.values()) and o.gates["sharia"] == "HALAL"
    assert o.category in (scoring.STRONG, scoring.RECOVERY, scoring.WATCH, scoring.HRHR)
    o2 = scoring.score("AAPL", t, f, r, bad, "REAL_STOCK", news_score=20)
    assert o2.category == scoring.AVOID
    o3 = scoring.score("AAPL", t, f, r, halal, "UNVERIFIED", news_score=20)
    assert o3.category == scoring.UNVERIFIED and "XTB" in o3.warnings[0]
    o4 = scoring.score("AAPL", t, f, r, halal, "CFD_ONLY", news_score=20)
    assert o4.category == scoring.UNVERIFIED


def test_scoring_never_strong_on_deep_drop_or_bad_fundamentals():
    t, f, r = _views(drop=-40.0)
    halal = sh.screen(make_fund(), "AAOIFI")
    o = scoring.score("AAPL", t, f, r, halal, "REAL_STOCK", news_score=20)
    assert o.category != scoring.STRONG
    t, f, r = _views({"revenue_growth": -0.3, "earnings_growth": -0.6, "free_cash_flow": -1e9, "debt_to_equity": 400})
    o = scoring.score("AAPL", t, f, r, halal, "REAL_STOCK", news_score=20)
    assert o.category == scoring.AVOID and "الأساسيات" in o.warnings[-1]
    assert o.category_ar()
