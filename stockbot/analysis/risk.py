"""Risk metrics: volatility, max drawdown, beta-based market risk, liquidity, and plain-language risk notes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..providers.base import Candle, Fundamentals


@dataclass
class RiskView:
    annual_volatility: float | None = None     # fraction
    max_drawdown_1y: float | None = None       # fraction (negative)
    worst_day_1y: float | None = None          # fraction
    avg_dollar_volume: float | None = None
    beta: float | None = None
    level: str = "unknown"                     # low / medium / high / very high
    notes: list[str] = field(default_factory=list)
    loss_probability_note: str = ""


def analyze(candles: list[Candle], f: Fundamentals | None = None) -> RiskView:
    r = RiskView()
    if len(candles) < 30:
        return r
    closes = [c.close for c in candles[-252:]]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - mean) ** 2 for x in rets) / max(1, len(rets) - 1))
    r.annual_volatility = sd * math.sqrt(252)
    peak = closes[0]
    mdd = 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    r.max_drawdown_1y = mdd
    r.worst_day_1y = min(rets)
    w = candles[-20:]
    r.avg_dollar_volume = sum(c.close * c.volume for c in w) / len(w)
    r.beta = f.beta if f else None

    vol = r.annual_volatility
    if vol < 0.25:
        r.level = "low"
    elif vol < 0.40:
        r.level = "medium"
    elif vol < 0.65:
        r.level = "high"
    else:
        r.level = "very high"
    r.notes.append(f"تقلب سنوي {vol:.0%}، أسوأ تراجع في سنة {mdd:.0%}، أسوأ يوم {r.worst_day_1y:+.1%}")
    if r.beta is not None:
        r.notes.append(f"بيتا {r.beta:.2f}: " + ("يتحرك أكثر من السوق" if r.beta > 1.2 else "أقل حساسية للسوق" if r.beta < 0.8 else "يتبع السوق تقريباً"))
    if r.avg_dollar_volume is not None and r.avg_dollar_volume < 5e6:
        r.notes.append("سيولة منخفضة: قد يصعب البيع بسعر عادل بكميات كبيرة")
    if f:
        if f.short_pct_float and f.short_pct_float > 0.15:
            r.notes.append(f"نسبة بيع على المكشوف مرتفعة {f.short_pct_float:.0%}: السوق يراهن ضده")
        if f.earnings_date:
            r.notes.append(f"نتائج الأرباح القادمة {f.earnings_date}: تحرك حاد محتمل")
        if f.free_cash_flow is not None and f.free_cash_flow < 0:
            r.notes.append("مخاطر مالية: الشركة تحرق نقداً وقد تحتاج تمويلاً")
        if f.debt_to_equity is not None and f.debt_to_equity > 150:
            r.notes.append("مخاطر مالية: مديونية ثقيلة تزيد الحساسية لأسعار الفائدة")
    # 1-sigma annual loss chance (normal approximation): P(return < 0) ≈ Φ(-mu/sigma)
    mu = mean * 252
    z = (0 - mu) / vol if vol else 0
    p_loss = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    r.loss_probability_note = (f"إحصائياً (على أساس تذبذب آخر سنة) احتمال أن تكون القيمة أقل من اليوم بعد سنة ≈ {p_loss:.0%}. "
                               "هذا تقدير إحصائي وليس توقعاً.")
    return r
