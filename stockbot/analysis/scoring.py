"""Opportunity scoring (0-100) with two hard gates: Sharia compliance and real-stock availability.

    Financial Health   20
    Technical          20
    Growth Potential   20
    Valuation          20
    News & Catalysts   20

Categories: Strong Opportunity / Potential Recovery / High Risk-High Reward / Watchlist / Avoid.
A gate failure forces 'Avoid' (or 'Unverified') regardless of the numeric score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fundamental import FundamentalView
from .risk import RiskView
from .sharia import HALAL, ShariaVerdict
from .technical import TechnicalView

STRONG, RECOVERY, HRHR, WATCH, AVOID, UNVERIFIED = (
    "Strong Opportunity", "Potential Recovery", "High Risk / High Reward", "Watchlist", "Avoid", "Unverified")


@dataclass
class Opportunity:
    ticker: str
    total: int
    parts: dict[str, int]
    category: str
    gates: dict[str, str]            # "sharia": HALAL/..., "real_stock": status
    drop_pct: float | None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def category_ar(self) -> str:
        return {STRONG: "فرصة قوية", RECOVERY: "تعافٍ محتمل", HRHR: "مخاطرة عالية / عائد عالٍ",
                WATCH: "قائمة متابعة", AVOID: "تجنّب", UNVERIFIED: "غير مؤكد"}[self.category]


def score(ticker: str, tech: TechnicalView | None, fund: FundamentalView | None, risk: RiskView | None,
          sharia: ShariaVerdict | None, real_stock_status: str, news_score: int | None = None,
          news_notes: list[str] | None = None) -> Opportunity:
    parts: dict[str, int] = {}
    reasons: list[str] = []
    warnings: list[str] = []

    # Financial health 20
    if fund:
        parts["health"] = round(fund.health_score / 5)
        reasons.extend(fund.strengths[:2])
        warnings.extend(fund.weaknesses[:2])
    else:
        parts["health"] = 0
        warnings.append("لا بيانات أساسية")
    # Technical 20 — for recovery candidates reward oversold-in-uptrend and reversal patterns
    if tech:
        t = tech.score / 5
        if "oversold_in_uptrend" in tech.patterns or "possible_trend_reversal" in tech.patterns or "pullback_to_ema20_in_uptrend" in tech.patterns:
            t = min(20, t + 4)
        if "death_cross" in tech.patterns or tech.trend_long == "down" and tech.trend_medium == "down":
            t = max(0, t - 4)
        parts["technical"] = round(t)
    else:
        parts["technical"] = 0
    # Growth 20
    parts["growth"] = round(fund.growth_score / 5) if fund else 0
    # Valuation 20
    parts["valuation"] = round(fund.valuation_score / 5) if fund else 0
    # News & catalysts 20 (neutral 10 when unknown)
    parts["news"] = int(max(0, min(20, news_score))) if news_score is not None else 10
    if news_notes:
        reasons.extend(news_notes[:2])

    total = sum(parts.values())
    drop = tech.from_52w_high_pct if tech else None
    gates = {"sharia": sharia.status if sharia else "UNVERIFIED", "real_stock": real_stock_status}

    # category
    if gates["sharia"] != HALAL:
        cat = AVOID if gates["sharia"] == "NON_COMPLIANT" else UNVERIFIED
        warnings.append("لم يجتز فحص الشريعة" if cat == AVOID else "التوافق الشرعي غير مؤكد")
    elif real_stock_status not in ("REAL_STOCK",):
        cat = UNVERIFIED
        warnings.append("لم يُتحقق أنه سهم حقيقي في XTB")
    elif fund and fund.drop_diagnosis == "warning":
        cat = AVOID
        warnings.append("الهبوط مبرر بتدهور الأساسيات")
    elif total >= 70 and (fund and fund.health_score >= 55) and (risk is None or risk.level in ("low", "medium")):
        cat = STRONG
    elif total >= 60 and drop is not None and drop <= -20 and fund and fund.drop_diagnosis in ("opportunity", "mixed"):
        cat = RECOVERY
    elif total >= 55 and risk and risk.level in ("high", "very high"):
        cat = HRHR
    elif total >= 45:
        cat = WATCH
    else:
        cat = AVOID
    if cat == STRONG and drop is not None and drop <= -30:
        cat = RECOVERY  # a 30%+ drawdown is never 'strong' without a confirmed recovery
    return Opportunity(ticker=ticker.upper(), total=int(total), parts=parts, category=cat, gates=gates,
                       drop_pct=drop, reasons=reasons, warnings=warnings)
