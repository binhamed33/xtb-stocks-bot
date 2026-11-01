"""Offline preview of every card the bot sends. `python -m stockbot preview`.
Uses canned data; posts to Discord if DISCORD_WEBHOOK_URL is set, otherwise prints."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import market_calendar as cal
from .analyst import DailyBrief, Pick, CloseSummary
from .bot import State, StocksBot
from .config import Settings
from .data import StockSnapshot
from .notify import Discord


class _PrintingDiscord(Discord):
    def _post(self, body):
        for e in body.get("embeds", []):
            print("\n┌─", e.get("title", ""))
            if e.get("description"):
                print("│ ", e["description"].replace("\n", "\n│  "))
            for f in e.get("fields", []):
                print(f"│  {f['name']}: {f['value']}".replace("\n", "\n│     "))
            if e.get("footer"):
                print("│  —", e["footer"]["text"])
            print("└" + "─" * 70)
        return super()._post(body) if self.webhook else True


class _CannedAnalyst:
    def daily_brief(self, snaps, indices, today, previous_picks=None):
        return DailyBrief(
            market_mood="السوق في اتجاه صاعد هادئ؛ المؤشرات فوق متوسطاتها والتقلب منخفض. اليوم بيانات التضخم الساعة 08:30 نيويورك.",
            key_events=["CPI الأمريكي 08:30 نيويورك", "نتائج AMD بعد الإغلاق"],
            buys=[Pick(ticker="MSFT", action="BUY", conviction=0.62, entry_zone="412-416", stop_loss=398.0, target=448.0,
                       horizon="2-4 أسابيع", reason="ارتداد من متوسط 50 يوم مع حجم أعلى من المتوسط، RSI عند 56، وأعلى قمة شهرية عند 448.",
                       risk_note="إغلاق يومي تحت 398 يلغي الفكرة.")],
            sells=[Pick(ticker="PLTR", action="SELL", conviction=0.6, reason="وصل الهدف السابق 48 وRSI فوق 78؛ جني أرباح.")],
            avoid=[Pick(ticker="AMD", action="AVOID", conviction=0.7, reason="نتائج الأرباح الليلة؛ تحرك 8-10% محتمل في أي اتجاه.")],
            top_idea=Pick(ticker="AAPL", action="BUY", conviction=0.66, entry_zone="سعر السوق", stop_loss=222.0, target=252.0,
                          horizon="3-6 أسابيع", reason="اختراق قمة 52 أسبوع بحجم ضعف المتوسط، والقطاع كله قوي.",
                          risk_note="عودة تحت 230 تعني اختراقاً كاذباً."),
            holdings_advice=[],
        )

    def close_summary(self, indices, perf, movers, today):
        return CloseSummary(summary="جلسة إيجابية؛ القيادة من أشباه الموصلات.", lessons=["الاختراقات بحجم مرتفع استمرت"],
                            tomorrow_watch=["رد فعل AMD على النتائج", "عوائد السندات"])


def _snap(t, name, price, sector, industry, debt, cash, cap, rsi=55.0):
    s = StockSnapshot(ticker=t, name=name, price=price, prev_close=price * 0.99, change_1d_pct=1.0, change_1m_pct=4.0,
                      rsi14=rsi, sector=sector, industry=industry, market_cap=cap, total_debt=debt, total_cash=cash)
    return s


def run_preview(settings: Settings) -> int:
    import stockbot.bot as botmod

    snaps = [
        _snap("AAPL", "Apple", 236.1, "Technology", "Consumer Electronics", 100e9, 60e9, 3.5e12),
        _snap("MSFT", "Microsoft", 414.2, "Technology", "Software - Infrastructure", 70e9, 80e9, 3.1e12),
        _snap("AMD", "AMD", 162.3, "Technology", "Semiconductors", 2e9, 5e9, 260e9),
        _snap("PLTR", "Palantir", 48.9, "Technology", "Software - Infrastructure", 0.2e9, 4e9, 110e9, rsi=79),
        _snap("JPM", "JPMorgan", 215.0, "Financial Services", "Banks - Diversified", 400e9, 500e9, 600e9),
        _snap("DIS", "Disney", 95.0, "Communication Services", "Entertainment", 45e9, 6e9, 170e9),
        _snap("XYZ", "Overleveraged Co", 50.0, "Industrials", "Airlines", 40e9, 2e9, 60e9),
    ]
    quotes = {"MSFT": 426.0, "AAPL": 241.0}
    botmod.fetch_universe = lambda tickers, with_fundamentals=True: snaps  # type: ignore[assignment]
    botmod.fetch_quotes = lambda tickers: {t: quotes.get(t, 100.0) for t in tickers}  # type: ignore[assignment]
    botmod.index_overview = lambda: {"SP500_last": 5890.1, "SP500_chg_pct": 0.42, "NASDAQ_chg_pct": 0.77,  # type: ignore[assignment]
                                     "DOW_chg_pct": 0.11, "VIX_last": 14.2, "VIX_chg_pct": -3.1, "OIL_chg_pct": -0.8, "GOLD_chg_pct": 0.3}

    d = _PrintingDiscord(settings.discord_webhook)
    day = datetime(2026, 11, 2, 8, 0, tzinfo=cal.ET)
    clock = {"now": day}
    bot = StocksBot(settings, d, _CannedAnalyst(), State(), now_fn=lambda: clock["now"])
    print(">>> 09:00 بتوقيتك: تنبيه الصباح"); clock["now"] = day.replace(hour=0, minute=5); bot.tick()
    print("\n>>> قبل الفتح بـ 45 دقيقة: النشرة"); clock["now"] = day.replace(hour=8, minute=46); bot.tick()
    print("\n>>> 09:30 نيويورك: الفتح"); clock["now"] = day.replace(hour=9, minute=30); bot.tick()
    print("\n>>> 12:00 نيويورك: منتصف الجلسة"); clock["now"] = day.replace(hour=12); bot.tick()
    print("\n>>> 16:00 نيويورك: الإغلاق"); clock["now"] = day.replace(hour=16); bot.tick()
    print("\n>>> يوم عطلة"); clock["now"] = datetime(2026, 11, 1, 10, 0, tzinfo=settings.user_tz); bot.tick()
    return 0
