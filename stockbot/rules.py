"""Rule-based fallback analyst used when no ANTHROPIC_API_KEY is set.

No AI: picks come from plain, transparent rules on the local indicators.
Weaker than the Claude brief, but the bot still delivers a daily plan.
"""

from __future__ import annotations

from .analyst import CloseSummary, DailyBrief, Pick
from .data import StockSnapshot


def _score(s: StockSnapshot) -> float:
    sc = 0.0
    if s.sma50 and s.sma200 and s.price > s.sma50 > s.sma200:
        sc += 2
    elif s.sma50 and s.price > s.sma50:
        sc += 1
    if s.sma20 and s.price > s.sma20:
        sc += 0.5
    if s.rsi14 is not None:
        if 45 <= s.rsi14 <= 65:
            sc += 1
        elif s.rsi14 > 75:
            sc -= 1.5
        elif s.rsi14 < 30:
            sc -= 0.5
    if s.change_1m_pct is not None and s.change_1m_pct > 0:
        sc += 0.5
    if s.from_52w_high_pct is not None and -12 <= s.from_52w_high_pct <= -2:
        sc += 0.5
    if s.volume_ratio and s.volume_ratio > 1.5 and (s.change_1d_pct or 0) > 0:
        sc += 0.5
    if s.days_to_earnings is not None and 0 <= s.days_to_earnings <= 2:
        sc -= 3
    return sc


def _reason(s: StockSnapshot) -> str:
    bits = []
    if s.sma50 and s.sma200 and s.price > s.sma50 > s.sma200:
        bits.append("اتجاه صاعد: السعر فوق متوسط 50 و200 يوم")
    elif s.sma50 and s.price > s.sma50:
        bits.append("السعر فوق متوسط 50 يوم")
    if s.rsi14 is not None:
        bits.append(f"RSI عند {s.rsi14:.0f}")
    if s.change_1m_pct is not None:
        bits.append(f"تغير الشهر {s.change_1m_pct:+.1f}%")
    if s.from_52w_high_pct is not None:
        bits.append(f"{s.from_52w_high_pct:+.1f}% من قمة 52 أسبوع")
    return "، ".join(bits) + "."


class RulesAnalyst:
    def __init__(self, max_buys: int = 5, max_avoid: int = 5, holdings: tuple = ()) -> None:
        self.max_buys = max_buys
        self.max_avoid = max_avoid
        self.holdings = holdings

    def daily_brief(self, snaps: list[StockSnapshot], indices: dict, today: str,
                    previous_picks: list[dict] | None = None) -> DailyBrief:
        ranked = sorted(snaps, key=_score, reverse=True)
        buys: list[Pick] = []
        for s in ranked:
            if len(buys) >= self.max_buys or _score(s) < 3 or not s.atr14_pct:
                break
            stop = round(s.price * (1 - max(2.0, 2.0 * s.atr14_pct) / 100), 2)
            target = round(s.price + 2.2 * (s.price - stop), 2)
            buys.append(Pick(ticker=s.ticker, action="BUY", conviction=min(0.65, 0.45 + 0.04 * _score(s)),
                             entry_zone="سعر السوق", stop_loss=stop, target=target, horizon="2-4 أسابيع",
                             reason=_reason(s), risk_note=f"إغلاق يومي تحت {stop:.2f} يلغي الفكرة."))
        avoid: list[Pick] = []
        for s in snaps:
            why = []
            if s.days_to_earnings is not None and 0 <= s.days_to_earnings <= 3:
                why.append(f"نتائج الأرباح بعد {s.days_to_earnings} يوم")
            if s.rsi14 and s.rsi14 > 75:
                why.append(f"تشبع شرائي RSI {s.rsi14:.0f}")
            if s.sma200 and s.price < s.sma200 and (s.change_1m_pct or 0) < -8:
                why.append("اتجاه هابط تحت متوسط 200 يوم")
            if why and len(avoid) < self.max_avoid:
                avoid.append(Pick(ticker=s.ticker, action="AVOID", conviction=0.6, reason="، ".join(why) + "."))
        held = {h.ticker for h in self.holdings}
        advice = []
        for s in snaps:
            if s.ticker in held:
                bad = s.sma50 and s.price < s.sma50 and (s.change_1m_pct or 0) < -5
                advice.append(Pick(ticker=s.ticker, action="SELL" if bad else "HOLD", conviction=0.55,
                                   reason=_reason(s)))
        sp = indices.get("SP500_chg_pct")
        vix = indices.get("VIX_last")
        mood = "قراءة آلية بدون ذكاء اصطناعي (لا يوجد مفتاح Claude). "
        if sp is not None:
            mood += f"S&P 500 أمس {sp:+.2f}%. "
        if vix is not None:
            mood += "تقلب مرتفع، قلّل الحجم. " if vix > 25 else "تقلب طبيعي. "
        events = []
        for s in snaps:
            if s.days_to_earnings is not None and 0 <= s.days_to_earnings <= 1:
                events.append(f"{s.ticker}: نتائج أرباح {s.earnings_date}")
        return DailyBrief(market_mood=mood, key_events=events[:6], buys=buys, sells=[], avoid=avoid,
                          top_idea=buys[0] if buys else None, holdings_advice=advice)

    def close_summary(self, indices: dict, picks_perf: list[dict], movers: list[dict], today: str) -> CloseSummary:
        wins = sum(1 for p in picks_perf if p["change_pct"] > 0)
        return CloseSummary(
            summary=f"أغلق السوق. من {len(picks_perf)} فكرة اليوم، {wins} في الربح.",
            lessons=[], tomorrow_watch=[m["ticker"] for m in movers[:3]],
        )
