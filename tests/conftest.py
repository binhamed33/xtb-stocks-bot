"""Shared fakes: an offline DataHub so the engine runs without network."""
import time

import pytest

from stockbot.config import load_settings
from stockbot.db import Database
from stockbot.providers.base import Candle, Filing, Fundamentals, NewsItem, Quote, Sourced


def make_candles(n=300, start=100.0, drift=0.0005, vol_spike_last=False, drop_last_pct=None):
    out, p = [], start
    for i in range(n):
        p = p * (1 + drift + (0.004 if i % 7 == 0 else -0.003 if i % 5 == 0 else 0.0))
        out.append(Candle(time.time() - (n - i) * 86400, p * 0.995, p * 1.01, p * 0.985, p, 1_000_000))
    if drop_last_pct:
        last = out[-1]
        last.close = last.close * (1 - drop_last_pct / 100); last.low = min(last.low, last.close)
    if vol_spike_last:
        out[-1].volume = 3_000_000
    return out


def make_fund(ticker="AAPL", **over):
    base = dict(ticker=ticker, name="Apple Inc.", sector="Technology", industry="Consumer Electronics", country="United States",
                exchange="NMS", currency="USD", description="Apple designs consumer electronics.", market_cap=3.0e12,
                revenue=390e9, revenue_growth=0.06, earnings_growth=0.1, eps_ttm=6.5, eps_forward=7.2, pe=30.0,
                forward_pe=27.0, pb=45.0, ps=7.5, free_cash_flow=100e9, operating_cash_flow=110e9, debt_to_equity=140.0,
                gross_margin=0.45, operating_margin=0.30, profit_margin=0.25, roe=1.5, total_debt=100e9, total_cash=60e9,
                total_assets=350e9, receivables=60e9, interest_income=3e9, shares_outstanding=15e9, dividend_yield=0.005,
                beta=1.2, earnings_date=None)
    base.update(over)
    return Fundamentals(**base)


class FakeHub:
    def __init__(self, db, funds=None, candles=None, news=None, filings=None, quotes=None):
        self.db = db
        self.s = None
        self.funds = funds or {}
        self.candles_map = candles or {}
        self.news_map = news or {}
        self.filings_map = filings or {}
        self.quotes_map = quotes or {}
        self.calls = []

    def quote(self, t):
        self.calls.append(("quote", t))
        t = t.upper()
        if t in self.quotes_map:
            q = self.quotes_map[t]
            return Sourced(q, "FakeQuotes", currency=q.currency)
        c = self.candles_map.get(t)
        if not c:
            return None
        f = self.funds.get(t)
        q = Quote(t, c[-1].close, c[-2].close, f.currency if f else "USD", f.exchange if f else "NMS", f.name if f else t, "CLOSED", "EQUITY")
        return Sourced(q, "FakeQuotes", currency=q.currency)

    def history(self, t, period="1y"):
        self.calls.append(("history", t))
        return Sourced(list(self.candles_map.get(t.upper(), [])), "FakeHistory")

    def history_many(self, tickers, period="1y"):
        return {t.upper(): Sourced(list(self.candles_map[t.upper()]), "FakeHistory") for t in tickers if t.upper() in self.candles_map}

    def fundamentals(self, t):
        self.calls.append(("fund", t))
        f = self.funds.get(t.upper())
        return Sourced(f, "FakeFundamentals", currency=f.currency) if f else None

    def news(self, t, limit=12):
        return Sourced(list(self.news_map.get(t.upper(), [])), "FakeNews")

    def filings(self, t, limit=8):
        return Sourced(list(self.filings_map.get(t.upper(), [])), "SEC EDGAR")

    def providers_status(self):
        return {"Fake": "active"}


@pytest.fixture
def settings(monkeypatch, tmp_path):
    for k in list(__import__("os").environ):
        if k.startswith(("DISCORD", "GEMINI", "HALAL", "SHARIA", "XTB", "STOCK_", "SCAN_", "HOLDINGS", "ADMIN")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/fake")
    return load_settings()


@pytest.fixture
def db(settings):
    return Database(settings.db_path)
