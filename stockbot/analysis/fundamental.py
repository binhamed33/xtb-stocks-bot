"""Fundamental analysis: health score, valuation read, and 'why is it down' diagnosis."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..providers.base import Fundamentals
from .technical import TechnicalView


@dataclass
class FundamentalView:
    health_score: int = 0            # 0-100
    valuation_score: int = 0         # 0-100 (higher = cheaper vs. quality)
    growth_score: int = 0            # 0-100
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    valuation_note: str = ""
    drop_diagnosis: str = ""         # "opportunity" | "warning" | "mixed" | "n/a"
    drop_reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    metrics: dict[str, float | None] = field(default_factory=dict)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def analyze(f: Fundamentals, tech: TechnicalView | None = None) -> FundamentalView:
    v = FundamentalView()
    v.metrics = {
        "market_cap": f.market_cap, "revenue": f.revenue, "revenue_growth": f.revenue_growth,
        "earnings_growth": f.earnings_growth, "eps_ttm": f.eps_ttm, "eps_forward": f.eps_forward, "pe": f.pe,
        "forward_pe": f.forward_pe, "pb": f.pb, "ps": f.ps, "free_cash_flow": f.free_cash_flow,
        "debt_to_equity": f.debt_to_equity, "gross_margin": f.gross_margin, "operating_margin": f.operating_margin,
        "profit_margin": f.profit_margin, "roe": f.roe, "dividend_yield": f.dividend_yield, "beta": f.beta,
    }
    v.missing = [k for k, val in v.metrics.items() if val is None]

    # ---- health (profitability, cash, leverage) -----------------------------
    h = 50.0
    if f.profit_margin is not None:
        if f.profit_margin > 0.15:
            h += 12; v.strengths.append(f"هامش ربح صافٰ مرتفع {_pct(f.profit_margin)}")
        elif f.profit_margin > 0.05:
            h += 5
        elif f.profit_margin < 0:
            h -= 15; v.weaknesses.append(f"خسائر صافية {_pct(f.profit_margin)}")
    if f.operating_margin is not None and f.operating_margin < 0:
        h -= 8; v.weaknesses.append("هامش تشغيلي سالب")
    if f.free_cash_flow is not None:
        if f.free_cash_flow > 0:
            h += 10; v.strengths.append("تدفق نقدي حر موجب")
        else:
            h -= 12; v.weaknesses.append("تدفق نقدي حر سالب (تحرق نقداً)")
    if f.debt_to_equity is not None:
        if f.debt_to_equity < 50:
            h += 8; v.strengths.append(f"مديونية منخفضة (D/E {f.debt_to_equity:.0f}%)")
        elif f.debt_to_equity > 150:
            h -= 12; v.weaknesses.append(f"مديونية مرتفعة (D/E {f.debt_to_equity:.0f}%)")
    if f.roe is not None:
        if f.roe > 0.15:
            h += 8; v.strengths.append(f"عائد على حقوق الملكية {_pct(f.roe)}")
        elif f.roe < 0:
            h -= 6
    if f.total_cash and f.total_debt is not None and f.total_cash > f.total_debt:
        h += 5; v.strengths.append("النقد يغطي كامل الدين")
    v.health_score = int(max(0, min(100, round(h))))

    # ---- growth --------------------------------------------------------------
    g = 50.0
    if f.revenue_growth is not None:
        if f.revenue_growth > 0.20:
            g += 20; v.strengths.append(f"نمو إيرادات قوي {_pct(f.revenue_growth)}")
        elif f.revenue_growth > 0.08:
            g += 10
        elif f.revenue_growth < 0:
            g -= 15; v.weaknesses.append(f"الإيرادات تتراجع {_pct(f.revenue_growth)}")
    if f.earnings_growth is not None:
        if f.earnings_growth > 0.20:
            g += 15
        elif f.earnings_growth < -0.20:
            g -= 15; v.weaknesses.append(f"الأرباح تتراجع {_pct(f.earnings_growth)}")
    if f.eps_forward and f.eps_ttm and f.eps_ttm > 0 and f.eps_forward > f.eps_ttm * 1.1:
        g += 8; v.strengths.append("توقعات الأرباح المستقبلية أعلى من الحالية")
    v.growth_score = int(max(0, min(100, round(g))))

    # ---- valuation -----------------------------------------------------------
    val = 50.0
    notes = []
    pe = f.forward_pe or f.pe
    if pe is not None:
        if pe <= 0:
            val -= 10; notes.append("لا توجد أرباح (P/E غير معرّف)")
        elif pe < 15:
            val += 15; notes.append(f"P/E {pe:.1f} منخفض")
        elif pe < 25:
            val += 5; notes.append(f"P/E {pe:.1f} معقول")
        elif pe > 45:
            val -= 15; notes.append(f"P/E {pe:.1f} مرتفع: مسعّر للنمو")
        else:
            notes.append(f"P/E {pe:.1f}")
    if pe and f.earnings_growth and f.earnings_growth > 0:
        peg = pe / (f.earnings_growth * 100)
        if peg < 1:
            val += 10; notes.append(f"PEG {peg:.2f} جذاب")
        elif peg > 2.5:
            val -= 8; notes.append(f"PEG {peg:.2f} مرتفع")
    if f.pb is not None:
        if f.pb < 1.5:
            val += 8
        elif f.pb > 15:
            val -= 5
    if f.analyst_target_mean and tech and tech.price:
        up = (f.analyst_target_mean / tech.price - 1) * 100
        notes.append(f"متوسط أهداف المحللين {f.analyst_target_mean:.2f} ({up:+.0f}%)" + (f"، {f.analyst_count} محلل" if f.analyst_count else ""))
        if up > 20:
            val += 6
        elif up < -10:
            val -= 6
    v.valuation_score = int(max(0, min(100, round(val))))
    v.valuation_note = "؛ ".join(notes) if notes else "بيانات التقييم غير كافية"

    # ---- why is it down? -------------------------------------------------------
    if tech and tech.from_52w_high_pct is not None and tech.from_52w_high_pct <= -15:
        reasons = []
        bad = 0
        good = 0
        if f.revenue_growth is not None and f.revenue_growth < 0:
            reasons.append("تراجع الإيرادات"); bad += 1
        if f.earnings_growth is not None and f.earnings_growth < -0.2:
            reasons.append("تراجع الأرباح"); bad += 1
        if f.free_cash_flow is not None and f.free_cash_flow < 0:
            reasons.append("حرق نقدي"); bad += 1
        if f.debt_to_equity is not None and f.debt_to_equity > 150:
            reasons.append("مديونية ثقيلة"); bad += 1
        if f.profit_margin is not None and f.profit_margin > 0.1:
            good += 1
        if f.revenue_growth is not None and f.revenue_growth > 0.05:
            good += 1
        if f.free_cash_flow is not None and f.free_cash_flow > 0:
            good += 1
        if tech.patterns and "high_volume_selloff" in tech.patterns:
            reasons.append("بيع عنيف حديث بحجم مرتفع (خبر أو نتائج؟)")
        if bad == 0 and good >= 2:
            v.drop_diagnosis = "opportunity"
            reasons.append("الأساسيات سليمة: الهبوط يبدو تقييمياً أو قطاعياً لا جوهرياً")
        elif bad >= 2:
            v.drop_diagnosis = "warning"
            reasons.append("الهبوط مبرر بتدهور في الأساسيات")
        else:
            v.drop_diagnosis = "mixed"
            reasons.append("صورة مختلطة: يحتاج قراءة الأخبار والتقارير")
        v.drop_reasons = reasons
    else:
        v.drop_diagnosis = "n/a"
    return v
