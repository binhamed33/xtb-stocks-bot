"""Rendering: StockReport and friends -> Discord embed dicts (and plain text for the CLI).

Embeds are plain dicts so the same output goes to the webhook and to discord.py
(`discord.Embed.from_dict`). Long content is split across several embeds rather
than crammed into one.
"""

from __future__ import annotations

import time
from typing import Any

from .analysis import sharia as sh
from .analysis.scoring import Opportunity
from .engine import StockReport
from .notify import BLUE, GREEN, GREY, ORANGE, PURPLE, RED
from .portfolio import PortfolioView

DISCLAIMER = "أسهم حقيقية فقط (لا CFD ولا رافعة) · أبحاث آلية وليست نصيحة استثمارية ولا أوامر تنفيذ · لا يوجد ربح مضمون"


def _f(x: float | None, d: int = 2, suffix: str = "") -> str:
    return "-" if x is None else f"{x:,.{d}f}{suffix}"


def _pct(x: float | None, d: int = 1) -> str:
    return "-" if x is None else f"{x:+.{d}f}%"


def _frac(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:+.1f}%"


def _big(x: float | None) -> str:
    if x is None:
        return "-"
    for div, s in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(x) >= div:
            return f"{x / div:.2f}{s}"
    return f"{x:,.0f}"


def _color(r: StockReport) -> int:
    if not r.investable:
        return GREY
    return {"Strong Opportunity": GREEN, "Potential Recovery": BLUE, "High Risk / High Reward": ORANGE,
            "Watchlist": PURPLE}.get(r.opportunity.category, RED)


def _gate_line(r: StockReport) -> str:
    s = r.sharia.label() if r.sharia else "❓ لم يُفحص شرعياً"
    return f"{s}\n{r.xtb.label()}"


# ---- blocks --------------------------------------------------------------------------
def header_embed(r: StockReport, title_prefix: str = "📊") -> dict[str, Any]:
    q = r.quote.value if r.quote else None
    chg = _pct((q.price / q.prev_close - 1) * 100) if q and q.prev_close else "-"
    desc = _gate_line(r)
    fields = [
        {"name": "السعر", "value": f"{_f(r.price)} {r.currency} ({chg})", "inline": True},
        {"name": "التقييم الإجمالي", "value": f"{r.opportunity.total}/100 · {r.opportunity.category_ar()}", "inline": True},
        {"name": "من قمة 52 أسبوع", "value": _pct(r.tech.from_52w_high_pct) if r.tech else "-", "inline": True},
    ]
    if r.fundamentals:
        f = r.fundamentals.value
        fields.append({"name": "الشركة", "value": f"{f.sector or '-'} · {f.industry or '-'} · {f.country or '-'} · {f.exchange or '-'}", "inline": False})
    if r.warnings:
        fields.append({"name": "⚠️ تنبيهات", "value": "\n".join(r.warnings)[:1000], "inline": False})
    return {"title": f"{title_prefix} {r.ticker} — {r.name}"[:256], "description": desc, "color": _color(r), "fields": fields,
            "footer": {"text": f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(r.generated_at))} · {DISCLAIMER}"}}


def sharia_embed(r: StockReport, history: list[dict] | None = None) -> dict[str, Any]:
    v = r.sharia
    if v is None:
        return {"title": f"🕌 الفحص الشرعي — {r.ticker}", "description": "لا بيانات كافية للفحص", "color": GREY}
    spec = sh.METHODS[v.method]
    fields = [
        {"name": "الحكم", "value": v.label(), "inline": False},
        {"name": "المنهجية", "value": f"{spec['label']}\nالأساس: {spec['denominator'].replace('_', ' ')}\n[المرجع]({spec['reference']})", "inline": False},
        {"name": "النسب المالية", "value": "\n".join(v.ratio_lines()) or "-", "inline": False},
        {"name": "الأسباب", "value": "\n".join(f"• {x}" for x in v.reasons)[:1000] or "-", "inline": False},
    ]
    if v.business_note:
        fields.append({"name": "فحص النشاط", "value": v.business_note[:500], "inline": False})
    if v.unverified:
        fields.append({"name": "❓ لم يُتحقق منه", "value": "\n".join(f"• {x}" for x in v.unverified)[:1000], "inline": False})
    fields.append({"name": "مصادر البيانات", "value": "\n".join(v.sources)[:500] or "-", "inline": False})
    fields.append({"name": "تاريخ الفحص", "value": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(v.screened_at)), "inline": True})
    if history and len(history) > 1:
        fields.append({"name": "سجل الفحوص", "value": "\n".join(f"{time.strftime('%Y-%m-%d', time.gmtime(h['screened_at']))}: {h['status']} ({h['method']})" for h in history[:5]), "inline": False})
    color = {sh.HALAL: GREEN, sh.NON_COMPLIANT: RED, sh.QUESTIONABLE: ORANGE, sh.UNVERIFIED: GREY}[v.status]
    return {"title": f"🕌 الفحص الشرعي — {r.ticker}", "color": color, "fields": fields,
            "footer": {"text": "فحص آلي من بيانات مالية مجانية؛ نسبة الدخل غير الجائز لا تُقاس منها غالباً. للتأكد استخدم فاحصاً معتمداً (Zoya/Musaffa)."}}


def technical_embed(r: StockReport) -> dict[str, Any]:
    t = r.tech
    if t is None:
        return {"title": f"📈 التحليل الفني — {r.ticker}", "description": "لا بيانات سعرية", "color": GREY}
    fields = [
        {"name": "الاتجاه", "value": t.summary_ar(), "inline": False},
        {"name": "EMA 20/50/200", "value": f"{_f(t.ema20)} / {_f(t.ema50)} / {_f(t.ema200)}", "inline": True},
        {"name": "RSI 14", "value": _f(t.rsi14, 0), "inline": True},
        {"name": "MACD hist", "value": _f(t.macd_hist, 3), "inline": True},
        {"name": "بولينجر", "value": f"{_f(t.bb_low)} – {_f(t.bb_high)}", "inline": True},
        {"name": "ATR 14", "value": f"{_f(t.atr14)} ({_f(t.atr_pct, 1, '%')})", "inline": True},
        {"name": "VWAP 20", "value": _f(t.vwap20), "inline": True},
        {"name": "الحجم / المتوسط", "value": _f(t.vol_ratio, 2, "×"), "inline": True},
        {"name": "الأداء", "value": f"يوم {_pct(t.change_1d)} · أسبوع {_pct(t.change_1w)} · شهر {_pct(t.change_1m)} · 3ش {_pct(t.change_3m)} · سنة {_pct(t.change_1y)}", "inline": False},
        {"name": "دعوم", "value": ", ".join(_f(x) for x in t.supports) or "-", "inline": True},
        {"name": "مقاومات", "value": ", ".join(_f(x) for x in t.resistances) or "-", "inline": True},
        {"name": "الإشارات", "value": "\n".join(f"• {s}" for s in t.signals)[:1000] or "-", "inline": False},
        {"name": "درجة الإشارة", "value": f"{t.score}/100 · توافق المؤشرات {t.confidence:.0%}", "inline": True},
    ]
    if t.stop:
        fields.append({"name": "خطة محتملة", "value": f"دخول {_f(t.entry_low)}–{_f(t.entry_high)} · وقف {_f(t.stop)} · هدف1 {_f(t.target1)} · هدف2 {_f(t.target2)} · عائد/مخاطرة {_f(t.reward_risk, 1)}:1\n{t.plan_note}", "inline": False})
    return {"title": f"📈 التحليل الفني — {r.ticker}", "color": BLUE, "fields": fields}


def fundamental_embed(r: StockReport) -> dict[str, Any]:
    if not r.fundamentals or not r.fund:
        return {"title": f"🏦 التحليل الأساسي — {r.ticker}", "description": "لا بيانات أساسية", "color": GREY}
    f, v = r.fundamentals.value, r.fund
    fields = [
        {"name": "الصحة المالية", "value": f"{v.health_score}/100", "inline": True},
        {"name": "النمو", "value": f"{v.growth_score}/100", "inline": True},
        {"name": "التقييم", "value": f"{v.valuation_score}/100", "inline": True},
        {"name": "القيمة السوقية", "value": _big(f.market_cap), "inline": True},
        {"name": "الإيرادات (سنوي)", "value": f"{_big(f.revenue)} ({_frac(f.revenue_growth)} نمو)", "inline": True},
        {"name": "نمو الأرباح", "value": _frac(f.earnings_growth), "inline": True},
        {"name": "EPS / متوقع", "value": f"{_f(f.eps_ttm)} / {_f(f.eps_forward)}", "inline": True},
        {"name": "P/E / Forward", "value": f"{_f(f.pe, 1)} / {_f(f.forward_pe, 1)}", "inline": True},
        {"name": "P/B · P/S", "value": f"{_f(f.pb, 1)} · {_f(f.ps, 1)}", "inline": True},
        {"name": "تدفق نقدي حر", "value": _big(f.free_cash_flow), "inline": True},
        {"name": "D/E", "value": _f(f.debt_to_equity, 0, "%"), "inline": True},
        {"name": "هوامش (إجمالي/تشغيلي/صافي)", "value": f"{_frac(f.gross_margin)} / {_frac(f.operating_margin)} / {_frac(f.profit_margin)}", "inline": True},
        {"name": "ROE", "value": _frac(f.roe), "inline": True},
        {"name": "توزيعات", "value": _frac(f.dividend_yield), "inline": True},
        {"name": "نتائج قادمة", "value": f.earnings_date or "-", "inline": True},
        {"name": "التقييم", "value": v.valuation_note[:500], "inline": False},
    ]
    if v.strengths:
        fields.append({"name": "✅ نقاط قوة", "value": "\n".join(f"• {x}" for x in v.strengths[:5]), "inline": False})
    if v.weaknesses:
        fields.append({"name": "⚠️ نقاط ضعف", "value": "\n".join(f"• {x}" for x in v.weaknesses[:5]), "inline": False})
    if v.drop_diagnosis != "n/a":
        lab = {"opportunity": "🟢 الهبوط يبدو فرصة", "warning": "🔴 الهبوط تحذير", "mixed": "🟡 صورة مختلطة"}[v.drop_diagnosis]
        fields.append({"name": "لماذا انخفض السهم؟", "value": f"{lab}\n" + "\n".join(f"• {x}" for x in v.drop_reasons), "inline": False})
    if v.missing:
        fields.append({"name": "بيانات غير متوفرة", "value": ", ".join(v.missing)[:300], "inline": False})
    return {"title": f"🏦 التحليل الأساسي — {r.ticker}", "color": BLUE, "fields": fields,
            "footer": {"text": r.fundamentals.meta()}}


def news_embed(r: StockReport) -> dict[str, Any]:
    n = r.news
    lines = [c.line() for c in n.items[:8]]
    fields = []
    if lines:
        fields.append({"name": "آخر الأخبار (تاريخ النشر)", "value": "\n".join(lines)[:1000], "inline": False})
    for c in n.items[:4]:
        fields.append({"name": f"{c.item.title[:90]}", "value": f"الأثر المحتمل: {c.impact}\n{(c.item.summary or '')[:300]}", "inline": False})
    if n.filings:
        fields.append({"name": "إفصاحات SEC (رسمية)", "value": "\n".join(f"• [{f.form}]({f.url}) {f.filed_at} — {f.description}" for f in n.filings[:6])[:1000], "inline": False})
    if n.notes:
        fields.append({"name": "قراءة", "value": "\n".join(f"• {x}" for x in n.notes)[:500], "inline": False})
    if r.news_digest:
        d = r.news_digest
        fields.append({"name": "🤖 ملخص Gemini (من العناصر أعلاه فقط)", "value": f"{d.summary}\nالنبرة: {d.overall_tone}"[:1000], "inline": False})
        if d.key_risks:
            fields.append({"name": "مخاطر من الأخبار", "value": "\n".join(f"• {x}" for x in d.key_risks[:4])[:500], "inline": False})
        if d.key_catalysts:
            fields.append({"name": "محفزات من الأخبار", "value": "\n".join(f"• {x}" for x in d.key_catalysts[:4])[:500], "inline": False})
    desc = "" if lines else "لا توجد أخبار حديثة من المصادر المتصلة (Yahoo Finance" + (", Finnhub" if "Finnhub" in n.sources else "") + "). لم يُختلق شيء."
    return {"title": f"📰 الأخبار والأحداث — {r.ticker}", "description": desc, "color": BLUE, "fields": fields,
            "footer": {"text": "الروابط تؤدي للمصدر الأصلي. تاريخ النشر قد يختلف عن تاريخ الحدث."}}


def risk_embed(r: StockReport) -> dict[str, Any]:
    k = r.risk
    if k is None:
        return {"title": f"⚠️ المخاطر — {r.ticker}", "description": "لا بيانات", "color": GREY}
    lvl = {"low": "منخفض", "medium": "متوسط", "high": "مرتفع", "very high": "مرتفع جداً", "unknown": "غير محدد"}[k.level]
    fields = [
        {"name": "مستوى المخاطرة", "value": lvl, "inline": True},
        {"name": "التقلب السنوي", "value": _frac(k.annual_volatility), "inline": True},
        {"name": "أقصى تراجع (سنة)", "value": _frac(k.max_drawdown_1y), "inline": True},
        {"name": "سيولة يومية", "value": _big(k.avg_dollar_volume) + " $", "inline": True},
        {"name": "ملاحظات", "value": "\n".join(f"• {x}" for x in k.notes)[:1000] or "-", "inline": False},
        {"name": "احتمال الخسارة", "value": k.loss_probability_note[:500], "inline": False},
    ]
    return {"title": f"⚠️ تقييم المخاطر — {r.ticker}", "color": ORANGE, "fields": fields}


def verdict_embed(r: StockReport) -> dict[str, Any]:
    o = r.opportunity
    parts = " · ".join(f"{k} {v}/20" for k, v in {"صحة": o.parts.get("health", 0), "فني": o.parts.get("technical", 0),
                                                   "نمو": o.parts.get("growth", 0), "تقييم": o.parts.get("valuation", 0),
                                                   "أخبار": o.parts.get("news", 0)}.items())
    fields = [{"name": "النتيجة", "value": f"**{o.total}/100** — {o.category_ar()}\n{parts}", "inline": False},
              {"name": "البوابات الإلزامية", "value": _gate_line(r), "inline": False}]
    if r.tech and r.tech.stop and r.investable:
        fields.append({"name": "مناطق الدخول / الوقف / الأهداف", "value": f"دخول {_f(r.tech.entry_low)}–{_f(r.tech.entry_high)} · وقف {_f(r.tech.stop)} · هدف {_f(r.tech.target1)} / {_f(r.tech.target2)} · R/R {_f(r.tech.reward_risk, 1)}:1\nالأفق: استثمار 3-12 شهراً (ليس مضاربة يومية)", "inline": False})
    if o.reasons:
        fields.append({"name": "لماذا", "value": "\n".join(f"• {x}" for x in o.reasons[:5])[:800], "inline": False})
    if o.warnings:
        fields.append({"name": "تحذيرات", "value": "\n".join(f"• {x}" for x in o.warnings[:5])[:800], "inline": False})
    if r.narrative:
        n = r.narrative
        fields.append({"name": "🤖 قراءة Gemini (تفسير فقط، البيانات أعلاه)", "value": f"{n.overview}\n\n**لماذا تحرك:** {n.why_moved}"[:1000], "inline": False})
        if n.bull_case:
            fields.append({"name": "الحجة الإيجابية", "value": "\n".join(f"• {x}" for x in n.bull_case[:4])[:600], "inline": True})
        if n.bear_case:
            fields.append({"name": "الحجة السلبية", "value": "\n".join(f"• {x}" for x in n.bear_case[:4])[:600], "inline": True})
        fields.append({"name": "الخلاصة", "value": f"{n.verdict}\n(ثقة النموذج {n.confidence:.0%})"[:1000], "inline": False})
    fields.append({"name": "المراجع والمصادر", "value": "\n".join(f"• {s}" for s in r.sources())[:1000], "inline": False})
    return {"title": f"🧭 الخلاصة — {r.ticker}", "color": _color(r), "fields": fields, "footer": {"text": DISCLAIMER}}


def full_report(r: StockReport, history: list[dict] | None = None) -> list[dict[str, Any]]:
    return [header_embed(r, "📑"), sharia_embed(r, history), technical_embed(r), fundamental_embed(r), news_embed(r),
            risk_embed(r), verdict_embed(r)]


def quick_analysis(r: StockReport) -> list[dict[str, Any]]:
    return [header_embed(r), verdict_embed(r)]


def stock_embed(r: StockReport) -> dict[str, Any]:
    e = header_embed(r, "💹")
    if r.tech:
        e["fields"].append({"name": "الاتجاه", "value": r.tech.summary_ar(), "inline": False})
        e["fields"].append({"name": "الأداء", "value": f"يوم {_pct(r.tech.change_1d)} · شهر {_pct(r.tech.change_1m)} · سنة {_pct(r.tech.change_1y)}", "inline": False})
    if r.fundamentals:
        f = r.fundamentals.value
        e["fields"].append({"name": "أساسيات", "value": f"P/E {_f(f.pe, 1)} · قيمة سوقية {_big(f.market_cap)} · توزيعات {_frac(f.dividend_yield)}", "inline": False})
    return e


def opportunities_embeds(reports: list[StockReport]) -> list[dict[str, Any]]:
    if not reports:
        return [{"title": "🔎 الفرص الاستثمارية", "description": "لم يُعثر على فرص تجتاز الفلاتر حالياً.", "color": GREY}]
    out = []
    for r in reports:
        o = r.opportunity
        fields = [
            {"name": "التقييم", "value": f"{o.total}/100 · {o.category_ar()}", "inline": True},
            {"name": "السعر", "value": f"{_f(r.price)} {r.currency} ({_pct(o.drop_pct)} من القمة)", "inline": True},
            {"name": "البوابات", "value": _gate_line(r), "inline": False},
        ]
        if r.fund and r.fund.drop_diagnosis != "n/a":
            fields.append({"name": "لماذا انخفض", "value": "\n".join(f"• {x}" for x in r.fund.drop_reasons[:3])[:500], "inline": False})
        if o.reasons:
            fields.append({"name": "نقاط لصالحه", "value": "\n".join(f"• {x}" for x in o.reasons[:3])[:500], "inline": False})
        if o.warnings:
            fields.append({"name": "تحذيرات", "value": "\n".join(f"• {x}" for x in o.warnings[:3])[:500], "inline": False})
        if r.tech and r.tech.stop and r.investable:
            fields.append({"name": "خطة", "value": f"دخول {_f(r.tech.entry_low)}–{_f(r.tech.entry_high)} · وقف {_f(r.tech.stop)} · هدف {_f(r.tech.target1)}", "inline": False})
        out.append({"title": f"{r.ticker} — {r.name}"[:256], "color": _color(r), "fields": fields})
    out.append({"description": DISCLAIMER, "color": GREY})
    return out


def compare_embed(a: StockReport, b: StockReport, ai_res=None) -> dict[str, Any]:  # noqa: ANN001
    def row(label: str, fa: str, fb: str) -> dict:
        return {"name": label, "value": f"{a.ticker}: {fa}\n{b.ticker}: {fb}", "inline": True}
    fa_, fb_ = (a.fundamentals.value if a.fundamentals else None), (b.fundamentals.value if b.fundamentals else None)
    fields = [
        row("التقييم الإجمالي", f"{a.opportunity.total}/100", f"{b.opportunity.total}/100"),
        row("الشريعة", a.sharia.status if a.sharia else "-", b.sharia.status if b.sharia else "-"),
        row("XTB", a.xtb.status, b.xtb.status),
        row("السعر", f"{_f(a.price)} {a.currency}", f"{_f(b.price)} {b.currency}"),
        row("P/E", _f(fa_.pe, 1) if fa_ else "-", _f(fb_.pe, 1) if fb_ else "-"),
        row("نمو الإيرادات", _frac(fa_.revenue_growth) if fa_ else "-", _frac(fb_.revenue_growth) if fb_ else "-"),
        row("هامش صافٍ", _frac(fa_.profit_margin) if fa_ else "-", _frac(fb_.profit_margin) if fb_ else "-"),
        row("D/E", _f(fa_.debt_to_equity, 0, "%") if fa_ else "-", _f(fb_.debt_to_equity, 0, "%") if fb_ else "-"),
        row("الصحة / النمو / التقييم", f"{a.fund.health_score}/{a.fund.growth_score}/{a.fund.valuation_score}" if a.fund else "-",
            f"{b.fund.health_score}/{b.fund.growth_score}/{b.fund.valuation_score}" if b.fund else "-"),
        row("فني", f"{a.tech.score}/100" if a.tech else "-", f"{b.tech.score}/100" if b.tech else "-"),
        row("سنة", _pct(a.tech.change_1y) if a.tech else "-", _pct(b.tech.change_1y) if b.tech else "-"),
        row("مخاطرة", a.risk.level if a.risk else "-", b.risk.level if b.risk else "-"),
    ]
    if ai_res:
        fields.append({"name": "🤖 رأي Gemini", "value": f"الأفضل: {ai_res.winner}\n{ai_res.rationale}"[:1000], "inline": False})
    return {"title": f"⚖️ مقارنة {a.ticker} مقابل {b.ticker}", "color": PURPLE, "fields": fields, "footer": {"text": DISCLAIMER}}


def market_embed(idx: dict[str, Any], state_line: str) -> dict[str, Any]:
    names = {"SP500": "S&P 500", "NASDAQ": "Nasdaq", "DOW": "Dow", "VIX": "VIX", "US10Y": "عائد 10 سنوات", "OIL": "نفط", "GOLD": "ذهب", "BTC": "بيتكوين"}
    fields = []
    for k, label in names.items():
        last, chg = idx.get(f"{k}_last"), idx.get(f"{k}_chg_pct")
        if last is not None:
            fields.append({"name": label, "value": f"{last:,.2f} ({_pct(chg)})", "inline": True})
    return {"title": "🌐 ملخص الأسواق", "description": state_line, "color": BLUE, "fields": fields or [{"name": "-", "value": "لا بيانات", "inline": False}],
            "footer": {"text": "Yahoo Finance · متأخر ~15 دقيقة"}}


def portfolio_embeds(v: PortfolioView, currency_note: str = "") -> list[dict[str, Any]]:
    if not v.positions:
        return [{"title": "💼 المحفظة", "description": "لا مراكز بعد. أضف بـ `/portfolio add`.", "color": GREY}]
    lines = []
    for p in v.positions:
        s = {"HALAL": "✅", "NON_COMPLIANT": "⛔", "QUESTIONABLE": "⚠️", "UNVERIFIED": "❓", "": "❓"}[p.sharia]
        if p.price is not None:
            lines.append(f"{s} **{p.ticker}** {p.qty:g} × {p.avg_price:.2f} → {p.price:.2f} = {p.value:,.2f} ({_pct(p.pnl_pct)}) · {p.weight:.0%}")
        else:
            lines.append(f"{s} **{p.ticker}** {p.qty:g} × {p.avg_price:.2f} → سعر غير متاح")
    color = GREEN if v.total_pnl >= 0 else RED
    lvl = {"low": "منخفض", "medium": "متوسط", "high": "مرتفع"}[v.risk_level]
    fields = [
        {"name": "القيمة الإجمالية", "value": f"{v.total_value:,.2f}", "inline": True},
        {"name": "التكلفة", "value": f"{v.total_cost:,.2f}", "inline": True},
        {"name": "الربح/الخسارة", "value": f"{v.total_pnl:+,.2f} ({v.total_pnl_pct:+.2f}%)", "inline": True},
        {"name": "المراكز", "value": "\n".join(lines)[:1000], "inline": False},
        {"name": "التوزيع القطاعي", "value": "\n".join(f"{k}: {w:.0%}" for k, w in sorted(v.allocation.items(), key=lambda x: -x[1]))[:500] or "-", "inline": False},
        {"name": "مستوى المخاطرة", "value": f"{lvl}\n" + "\n".join(f"• {x}" for x in v.risk_notes)[:500], "inline": False},
    ]
    return [{"title": "💼 المحفظة", "color": color, "fields": fields,
             "footer": {"text": (currency_note + " · " if currency_note else "") + "الأسعار متأخرة ~15 دقيقة · لا تنفيذ تلقائي"}}]


def text(embeds: list[dict[str, Any]]) -> str:
    """Plain-text rendering for the CLI."""
    out = []
    for e in embeds:
        out.append("\n┌─ " + e.get("title", ""))
        if e.get("description"):
            out.append("│  " + e["description"].replace("\n", "\n│  "))
        for f in e.get("fields", []):
            out.append(f"│  {f['name']}: " + str(f["value"]).replace("\n", "\n│     "))
        if e.get("footer"):
            out.append("│  — " + e["footer"]["text"])
        out.append("└" + "─" * 70)
    return "\n".join(out)
