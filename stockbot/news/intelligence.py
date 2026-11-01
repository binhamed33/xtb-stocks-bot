"""News intelligence: aggregate real items, classify event type, score sentiment, never invent.

Inputs are NewsItem (publisher + URL) and SEC Filings (official). Classification
is keyword-based and transparent; Gemini (when configured) may add a short
summary/impact note on top of the real items, never new facts.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from ..providers.base import Filing, NewsItem

EVENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "earnings": ("earnings", "quarterly results", "q1 ", "q2 ", "q3 ", "q4 ", "eps", "revenue beat", "revenue miss", "profit"),
    "guidance": ("guidance", "outlook", "forecast", "raises", "cuts forecast", "lowers outlook"),
    "fda": ("fda", "approval", "clinical", "phase 3", "phase iii", "trial"),
    "lawsuit": ("lawsuit", "sued", "settlement", "class action", "court", "antitrust", "doj", "ftc"),
    "acquisition": ("acquire", "acquisition", "merger", "buyout", "takeover", "to buy", "deal to"),
    "management": ("ceo", "cfo", "chief executive", "steps down", "resigns", "appoints", "names new"),
    "regulatory": ("regulator", "regulatory", "sec ", "probe", "investigation", "fine", "ban", "tariff", "export control"),
    "contract": ("contract", "wins", "awarded", "partnership", "agreement", "order"),
    "insider": ("insider", "form 4", "sold shares", "bought shares", "stake"),
    "dividend": ("dividend", "buyback", "repurchase", "split"),
    "layoffs": ("layoff", "job cuts", "restructuring", "workforce"),
    "product": ("launch", "unveil", "new product", "release", "chip", "model"),
    "analyst": ("upgrade", "downgrade", "price target", "analyst", "rating"),
}
POSITIVE = ("beat", "beats", "record", "surge", "soar", "jumps", "rally", "upgrade", "raises", "strong", "growth",
            "approval", "approved", "wins", "win", "partnership", "buyback", "profit rises", "outperform", "expands",
            "higher", "tops", "exceeds", "bullish", "momentum")
NEGATIVE = ("miss", "misses", "plunge", "falls", "drop", "slump", "downgrade", "cuts", "lawsuit", "probe", "investigation",
            "recall", "layoff", "weak", "warning", "warns", "decline", "loss", "losses", "fine", "fraud", "delay",
            "halt", "bankrupt", "default", "lower", "below", "bearish", "concern", "risk")


@dataclass
class ClassifiedNews:
    item: NewsItem
    event_type: str
    sentiment: str                 # positive / negative / neutral
    impact: str                    # short Arabic note
    age_days: float | None

    def line(self) -> str:
        icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}[self.sentiment]
        when = time.strftime("%Y-%m-%d", time.gmtime(self.item.published_at)) if self.item.published_at else "تاريخ غير معروف"
        return f"{icon} [{self.item.title}]({self.item.url}) — {self.item.source} · {when} · {self.event_type}"


@dataclass
class NewsView:
    items: list[ClassifiedNews] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    score: int = 10                     # 0-20 for the opportunity scorer
    notes: list[str] = field(default_factory=list)
    ai_summary: str = ""
    sources: list[str] = field(default_factory=list)
    as_of: float = field(default_factory=time.time)

    @property
    def has_recent(self) -> bool:
        return any(c.age_days is not None and c.age_days <= 7 for c in self.items)


EVENT_AR = {"earnings": "نتائج أرباح", "guidance": "توجيهات مستقبلية", "fda": "قرار FDA / تجارب", "lawsuit": "دعوى قضائية",
            "acquisition": "استحواذ / اندماج", "management": "تغيير إدارة", "regulatory": "مخاطر تنظيمية",
            "contract": "عقد / شراكة", "insider": "تعاملات داخلية", "dividend": "توزيعات / إعادة شراء",
            "layoffs": "تسريح / إعادة هيكلة", "product": "منتج", "analyst": "تقييم محللين", "general": "عام"}


def classify(item: NewsItem, now: float | None = None) -> ClassifiedNews:
    text = f"{item.title} {item.summary}".lower()
    event = "general"
    for ev, pats in EVENT_PATTERNS.items():
        if any(re.search(rf"(?<![a-z]){re.escape(p.strip())}(?![a-z])", text) for p in pats):
            event = ev
            break
    pos = sum(1 for w in POSITIVE if re.search(rf"\b{re.escape(w)}\b", text))
    neg = sum(1 for w in NEGATIVE if re.search(rf"\b{re.escape(w)}\b", text))
    sentiment = "positive" if pos > neg else "negative" if neg > pos else "neutral"
    age = None if item.published_at is None else ((now or time.time()) - item.published_at) / 86400
    impact = {
        "earnings": "قد يحرك السهم بقوة خلال يوم أو يومين",
        "guidance": "تغيّر التوجيهات يعيد تسعير التوقعات",
        "fda": "حدث ثنائي النتيجة: أثر كبير في الاتجاهين",
        "lawsuit": "خطر مالي وسمعة؛ غالباً ممتد زمنياً",
        "acquisition": "يغيّر تقييم الشركة والقطاع",
        "management": "تغيير القيادة قد يبدل الاستراتيجية",
        "regulatory": "ضغط محتمل على الإيرادات أو الهوامش",
        "contract": "إيرادات مستقبلية إضافية",
        "insider": "إشارة عن ثقة الإدارة",
        "dividend": "دعم للسعر وعائد للمساهم",
        "layoffs": "ضغط على الأرباح قصيراً وتحسين الهوامش لاحقاً",
        "product": "محفز نمو إن نجح",
        "analyst": "أثر قصير الأجل على المعنويات",
        "general": "أثر محدود غالباً",
    }[event]
    return ClassifiedNews(item=item, event_type=event, sentiment=sentiment, impact=impact, age_days=age)


def build(items: list[NewsItem], filings: list[Filing], sources: list[str], now: float | None = None) -> NewsView:
    now = now or time.time()
    v = NewsView(sources=sources, filings=filings[:8])
    v.items = [classify(i, now) for i in items]
    recent = [c for c in v.items if c.age_days is not None and c.age_days <= 14]
    sc = 10.0
    for c in recent:
        w = 2.0 if c.age_days <= 3 else 1.0
        if c.sentiment == "positive":
            sc += w
        elif c.sentiment == "negative":
            sc -= w
        if c.event_type in ("lawsuit", "regulatory") and c.sentiment != "positive":
            sc -= 1
        if c.event_type in ("contract", "fda") and c.sentiment == "positive":
            sc += 1
    v.score = int(max(0, min(20, round(sc))))
    if not v.items:
        v.notes.append("لا توجد أخبار حديثة متاحة من المصادر المتصلة")
    else:
        kinds = {}
        for c in recent:
            kinds[c.event_type] = kinds.get(c.event_type, 0) + 1
        if kinds:
            top = sorted(kinds.items(), key=lambda x: -x[1])[:3]
            v.notes.append("أحداث حديثة: " + "، ".join(f"{EVENT_AR[k]} ({n})" for k, n in top))
        pos = sum(1 for c in recent if c.sentiment == "positive")
        neg = sum(1 for c in recent if c.sentiment == "negative")
        v.notes.append(f"نبرة الأخبار (14 يوماً): {pos} إيجابي / {neg} سلبي / {len(recent) - pos - neg} محايد")
    recent_filings = [f for f in filings if f.form.startswith("8-K") or f.form.startswith("4")]
    if recent_filings:
        v.notes.append(f"إفصاحات SEC حديثة: {len(recent_filings)} (آخرها {recent_filings[0].form} بتاريخ {recent_filings[0].filed_at})")
    return v
