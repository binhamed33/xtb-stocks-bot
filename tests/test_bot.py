"""Scheduler with fake data, fake analyst and a capturing Discord."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from stockbot import bot as botmod
from stockbot import market_calendar as cal
from stockbot.analyst import DailyBrief, Pick, CloseSummary
from stockbot.bot import State, StocksBot
from stockbot.config import load_settings, parse_holdings, ConfigError
from stockbot.data import StockSnapshot, snapshot_from_rows
from stockbot.notify import Discord


class FakeDiscord(Discord):
    def __init__(self):
        super().__init__("https://discord.com/api/webhooks/fake")
        self.posts = []

    def _post(self, body):
        self.posts.append(body)
        return True

    def titles(self):
        out = []
        for p in self.posts:
            for e in p.get("embeds", []):
                if "title" in e:
                    out.append(e["title"])
        return out


class FakeAnalyst:
    def __init__(self):
        self.calls = 0

    def daily_brief(self, snaps, indices, today, previous_picks=None):
        self.calls += 1
        return DailyBrief(
            market_mood="سوق هادئ", key_events=["CPI 08:30"],
            buys=[Pick(ticker="AAPL", action="BUY", conviction=0.62, entry_zone="market", stop_loss=95.0,
                       target=112.0, horizon="2 weeks", reason="فوق المتوسطات", risk_note="كسر 95")],
            sells=[], avoid=[Pick(ticker="TSLA", action="AVOID", conviction=0.7, reason="نتائج غداً")],
            top_idea=None, holdings_advice=[],
        )

    def close_summary(self, indices, perf, movers, today):
        return CloseSummary(summary="يوم جيد", lessons=["الصبر"], tomorrow_watch=["NVDA"])


def snap(t, price=100.0, industry="Semiconductors", debt=1e9, cash=1e9):
    rows = [{"ts": i, "open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 1e6} for i in range(260)]
    s = snapshot_from_rows(t, rows)
    s.name, s.sector, s.industry = t, "Technology", industry
    s.market_cap, s.total_debt, s.total_cash = 100e9, debt, cash
    return s


@pytest.fixture
def settings(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/fake")
    monkeypatch.setenv("STOCK_UNIVERSE", "AAPL,TSLA")
    monkeypatch.setenv("BOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SEND_MIDDAY_UPDATE", "true")
    monkeypatch.delenv("HOLDINGS", raising=False)
    return load_settings()


@pytest.fixture
def patched(monkeypatch):
    prices = {"AAPL": 100.0, "TSLA": 200.0}
    monkeypatch.setattr(botmod, "fetch_universe", lambda tickers, with_fundamentals=True: [snap(t, prices[t]) for t in tickers])
    monkeypatch.setattr(botmod, "fetch_quotes", lambda tickers: {t: prices[t] for t in tickers if t in prices})
    monkeypatch.setattr(botmod, "index_overview", lambda: {"SP500_last": 5000.0, "SP500_chg_pct": 0.5})
    return prices


def make(settings, when):
    d = FakeDiscord()
    clock = {"now": when}
    bot = StocksBot(settings, d, FakeAnalyst(), State(), now_fn=lambda: clock["now"])
    return bot, d, clock


def test_full_trading_day_sequence(settings, patched):
    day = datetime(2026, 11, 2, 8, 0, tzinfo=cal.ET)
    bot, d, clock = make(settings, day)
    bot.tick()
    assert d.titles() == ["⏰ السوق الأمريكي يفتح اليوم"]   # 08:00 ET = 17:00 Muscat: morning note only
    assert "1 ساعة و30 دقيقة" in d.posts[0]["embeds"][0]["description"]
    bot.tick()
    assert len(d.titles()) == 1                   # not repeated
    clock["now"] = day.replace(hour=8, minute=46)
    bot.tick()
    assert any("ما قبل الافتتاح" in t for t in d.titles())
    brief = [e for p in d.posts for e in p.get("embeds", []) if "ما قبل الافتتاح" in e.get("title", "")][0]
    assert any(f["name"] == "🕌 فحص الحلال" for f in brief["fields"])
    assert any("شراء: AAPL" in t for t in d.titles()) and any("لا تشترِ" in t for t in d.titles())
    assert bot.state.open_ideas[0]["ticker"] == "AAPL" and bot.state.open_ideas[0]["entry"] == 100.0
    bot.tick()                                    # same minute again -> no duplicate
    assert sum("ما قبل الافتتاح" in t for t in d.titles()) == 1
    clock["now"] = day.replace(hour=9, minute=30)
    bot.tick()
    assert any("فتح الآن" in t for t in d.titles())
    clock["now"] = day.replace(hour=12, minute=0)
    patched["AAPL"] = 104.0
    bot.tick()
    assert any("منتصف الجلسة" in t for t in d.titles())
    clock["now"] = day.replace(hour=16, minute=0)
    patched["AAPL"] = 113.0                       # target 112 hit
    bot.tick()
    close = [e for p in d.posts for e in p.get("embeds", []) if "أغلق" in e.get("title", "")][0]
    perf = next(f["value"] for f in close["fields"] if f["name"] == "أداء أفكار اليوم")
    assert "🎯" in perf and "+13.00%" in perf
    assert bot.state.open_ideas == []             # resolved
    assert bot.analyst.calls == 1                 # one brief per day
    assert sorted(bot.state.sent["2026-11-02"]) == ["close", "midday", "morning", "open", "premarket"]
    open_card = [e for p in d.posts for e in p.get("embeds", []) if "فتح الآن" in e.get("title", "")][0]
    assert "6 ساعة و30 دقيقة" in open_card["description"]


def test_restart_does_not_repost(settings, patched):
    day = datetime(2026, 11, 2, 9, 0, tzinfo=cal.ET)
    bot, d, clock = make(settings, day)
    bot.tick()
    n = len(d.posts)
    bot2 = StocksBot(settings, d, FakeAnalyst(), State.load(settings.state_file), now_fn=lambda: clock["now"])
    bot2.tick()
    assert len(d.posts) == n


def test_closed_day_single_note(settings, patched):
    sunday = datetime(2026, 11, 1, 10, 0, tzinfo=settings.user_tz)
    bot, d, clock = make(settings, sunday)
    bot.tick(); bot.tick()
    assert d.titles().count("📴 السوق الأمريكي مغلق اليوم") == 1
    assert "2026-11-02" in d.posts[0]["embeds"][0]["description"]


def test_share_sizing_in_pick_embed(settings, patched):
    bot, d, clock = make(settings, datetime(2026, 11, 2, 9, 0, tzinfo=cal.ET))
    bot.tick()
    pick = [e for p in d.posts for e in p.get("embeds", []) if e.get("title", "").startswith("🟢 شراء: AAPL")][0]
    f = {x["name"]: x["value"] for x in pick["fields"]}
    assert f["عدد الأسهم المقترح"].startswith("20 ")      # 1% of 10000 = 100$ / (100-95) = 20 shares
    assert f["عائد/مخاطرة"] == "2.4 : 1"
    assert f["🕌 الحلال"].startswith("✅")


def test_halal_filter_removes_non_compliant_before_analysis(settings, monkeypatch):
    prices = {"AAPL": 100.0, "TSLA": 200.0}
    seen = {}

    class SpyAnalyst(FakeAnalyst):
        def daily_brief(self, snaps, indices, today, previous_picks=None):
            seen["tickers"] = [x.ticker for x in snaps]
            return super().daily_brief(snaps, indices, today, previous_picks)

    bank = snap("TSLA", 200.0, industry="Banks - Diversified")
    monkeypatch.setattr(botmod, "fetch_universe", lambda tickers, with_fundamentals=True: [snap("AAPL"), bank])
    monkeypatch.setattr(botmod, "fetch_quotes", lambda tickers: {t: prices[t] for t in tickers if t in prices})
    monkeypatch.setattr(botmod, "index_overview", lambda: {})
    d = FakeDiscord()
    bot = StocksBot(settings, d, SpyAnalyst(), State(), now_fn=lambda: datetime(2026, 11, 2, 9, 0, tzinfo=cal.ET))
    bot.tick()
    assert seen["tickers"] == ["AAPL"]
    brief = [e for p in d.posts for e in p.get("embeds", []) if "ما قبل الافتتاح" in e.get("title", "")][0]
    halal = next(f["value"] for f in brief["fields"] if f["name"] == "🕌 فحص الحلال")
    assert "TSLA" in halal and "مستبعد" in halal


def test_holdings_parse():
    h = parse_holdings("AAPL:10@180.5, msft:5@400")
    assert h[0].ticker == "AAPL" and h[0].qty == 10 and h[1].ticker == "MSFT"
    with pytest.raises(ConfigError):
        parse_holdings("AAPL-10")


def test_snapshot_indicators():
    rows = [{"ts": i, "open": 100 + i * 0.1, "high": 101 + i * 0.1, "low": 99 + i * 0.1, "close": 100 + i * 0.1, "volume": 1e6}
            for i in range(300)]
    s = snapshot_from_rows("X", rows)
    assert s.sma200 and s.rsi14 and s.rsi14 > 50 and s.high_52w >= s.price and s.volume_ratio == pytest.approx(1.0)
    assert s.as_prompt_dict()["ticker"] == "X"
