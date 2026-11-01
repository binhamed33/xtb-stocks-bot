import time

from stockbot.ai import AIClient
from stockbot.engine import Engine
from stockbot.providers.base import Filing, NewsItem
from stockbot import render
from stockbot.xtb import instruments as xi
from tests.conftest import FakeHub, make_candles, make_fund


def _news(t, title, days_ago=1, summary=""):
    return NewsItem(id=title[:8], ticker=t, title=title, url=f"https://example.com/{t}/{abs(hash(title))}", source="Reuters",
                    provider="Fake", published_at=time.time() - days_ago * 86400, summary=summary)


def make_engine(settings, db, **kw):
    funds = {"AAPL": make_fund(), "JPM": make_fund(ticker="JPM", name="JPMorgan", sector="Financial Services", industry="Banks - Diversified"),
             "DOWN": make_fund(ticker="DOWN", name="Fallen Co", revenue_growth=0.1, free_cash_flow=5e9, debt_to_equity=40, market_cap=50e9, total_debt=2e9, total_cash=5e9)}
    candles = {"AAPL": make_candles(400), "JPM": make_candles(400), "DOWN": make_candles(400)}
    # DOWN: push price 30% below its 52w high
    for c in candles["DOWN"][-30:]:
        c.close *= 0.7; c.high *= 0.7; c.low *= 0.7; c.open *= 0.7
    news = {"AAPL": [_news("AAPL", "Apple beats earnings estimates, raises guidance", 0.4, "Revenue grew."),
                     _news("AAPL", "Regulators open antitrust probe into App Store", 3)]}
    filings = {"AAPL": [Filing("8-K", "2026-10-30", "material event (8-K)", "https://www.sec.gov/x")]}
    hub = FakeHub(db, funds, candles, news, filings)
    hub.s = settings
    eng = Engine(settings, db, hub=hub, ai=AIClient(), **kw)
    return eng, hub


def test_analyze_pipeline_and_report_rendering(settings, db):
    eng, hub = make_engine(settings, db)
    r = eng.analyze("aapl")
    assert r.ticker == "AAPL" and r.sharia.status == "HALAL" and r.xtb.status == xi.UNVERIFIED
    assert r.opportunity.category == "Unverified"            # not confirmed on XTB -> never investable
    assert r.news.items and r.news.items[0].event_type == "earnings" and r.news.items[0].sentiment == "positive"
    assert any("Reuters" in c.line() for c in r.news.items) and r.news.filings
    embeds = render.full_report(r, eng.sharia_history("AAPL"))
    assert len(embeds) == 7 and all("title" in e for e in embeds)
    txt = render.text(embeds)
    assert "AAOIFI" in txt and "https://example.com/AAPL" in txt and "لم يُتحقق" in txt and "CFD" in txt
    # persisted
    assert db.one("SELECT COUNT(*) AS n FROM reports")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM sharia_screens")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM news")["n"] == 2


def test_xtb_verification_unlocks_investable(settings, db):
    eng, hub = make_engine(settings, db)
    eng.xtb.set("AAPL", xi.REAL_STOCK, "manual confirmation in xStation", "EQUITY", "NASDAQ", "USD")
    r = eng.analyze("AAPL")
    assert r.xtb.investable and r.investable and r.opportunity.category != "Unverified"
    eng.xtb.set("AAPL", xi.CFD_ONLY, "manual")
    r = eng.analyze("AAPL")
    assert not r.investable and "CFD" in r.xtb.label()


def test_xtb_heuristic_and_csv_import(settings, db):
    eng, hub = make_engine(settings, db)
    eng.xtb.trust_heuristic = True
    st = eng.xtb.resolve("AAPL", hub.quote("AAPL").value, hub.fundamentals("AAPL").value)
    assert st.status == xi.REAL_STOCK and "heuristic" in st.source
    n = eng.xtb.import_csv("symbol,type,market,currency\nAAPL.US,STC,NASDAQ,USD\nNVDA.US,CFD,,USD\nSPY.US,ETF,,USD\n", "test")
    assert n == 3
    assert eng.xtb.get("AAPL").status == xi.REAL_STOCK and eng.xtb.get("NVDA").status == xi.CFD_ONLY and eng.xtb.get("SPY").status == xi.NOT_A_STOCK


def test_sharia_cached_then_rescreened(settings, db):
    eng, hub = make_engine(settings, db)
    v1 = eng.sharia("AAPL")
    calls = len(hub.calls)
    v2 = eng.sharia("AAPL")
    assert v2.screened_at == v1.screened_at and len(hub.calls) == calls   # cached: no provider call
    v3 = eng.sharia("AAPL", force=True)
    assert v3.screened_at >= v1.screened_at and len(eng.sharia_history("AAPL")) == 2
    assert eng.sharia("AAPL", method="MSCI").method == "MSCI"


def test_scan_ranks_and_gates(settings, db):
    eng, hub = make_engine(settings, db)
    eng.xtb.set("DOWN", xi.REAL_STOCK, "manual")
    reps = eng.scan(["AAPL", "JPM", "DOWN"], limit=5, min_drop_pct=10)
    tickers = [r.ticker for r in reps]
    assert "DOWN" in tickers and "JPM" not in tickers or all(r.opportunity.category == "Avoid" for r in reps if r.ticker == "JPM")
    down = next(r for r in reps if r.ticker == "DOWN")
    assert down.opportunity.category in ("Potential Recovery", "Strong Opportunity", "Watchlist", "High Risk / High Reward")
    assert eng.last_scan() and eng.last_scan()[0]["ticker"]
    embeds = render.opportunities_embeds(reps)
    assert embeds[-1]["description"].startswith("أسهم حقيقية")


def test_portfolio_valuation_and_alerts(settings, db):
    eng, hub = make_engine(settings, db)
    eng.portfolio.add(7, "AAPL", 10, 50.0)
    eng.portfolio.add(7, "AAPL", 10, 70.0)      # merged -> avg 60
    eng.portfolio.add(7, "JPM", 5, 100.0)
    v = eng.portfolio_view(7)
    assert len(v.positions) == 2 and abs(v.positions[0].avg_price - 60) < 1e-9
    assert v.total_value > 0 and v.positions[0].pnl_pct is not None
    assert any(p.sharia == "NON_COMPLIANT" for p in v.positions) and any("شرعية" in n for n in v.risk_notes)
    emb = render.portfolio_embeds(v)
    assert "المحفظة" in emb[0]["title"]
    # alerts: force a 6% daily drop on AAPL and an earnings date
    hub.funds["AAPL"].earnings_date = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 2 * 86400))
    c = hub.candles_map["AAPL"]
    c[-1].close = c[-2].close * 0.93
    eng.alerts.add(7, "AAPL", "below", 1.0)   # price way above 1 -> no fire
    fired = eng.run_alerts()
    kinds = sorted({e.kind for e in fired if e.ticker == "AAPL"})
    assert "drop" in kinds and "earnings" in kinds and "news" in kinds
    assert eng.run_alerts() == []              # deduped


def test_compare_market_status_and_ask_without_ai(settings, db):
    eng, hub = make_engine(settings, db)
    a, b, res = eng.compare("AAPL", "JPM")
    assert res is None
    e = render.compare_embed(a, b, res)
    assert "AAPL" in e["title"] and "JPM" in e["title"]
    assert eng.ask("hello", "AAPL") is None
    st = eng.status()
    assert "disabled" in st["ai"] and st["xtb_verified"] == 0
