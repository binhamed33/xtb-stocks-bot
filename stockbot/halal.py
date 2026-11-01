"""Shariah (halal) screening, AAOIFI-style, applied to every stock before any recommendation.

1. Business screen: companies whose main activity is non-permissible are excluded
   (conventional banking/insurance/interest-based finance, alcohol, tobacco, gambling,
   pork, adult entertainment, conventional entertainment/music/cinema, weapons/defence,
   cannabis, crypto exchanges).
2. Financial screen (AAOIFI thresholds):
   - interest-bearing debt / market cap  < 33%
   - cash + interest-bearing securities / market cap < 33%
   (non-permissible income < 5% cannot be measured from free data; see note.)

This is an automated approximation from Yahoo Finance data. For certainty, verify
with a certified screener (Zoya, Musaffa, Islamicly) - HALAL_WHITELIST / HALAL_BLACKLIST
let you override the automatic verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

DEBT_LIMIT = 0.33
CASH_LIMIT = 0.33

EXCLUDED_SECTORS = {"financial services", "financial"}
EXCLUDED_INDUSTRY_KEYWORDS = (
    "bank", "insurance", "credit services", "capital markets", "asset management", "mortgage",
    "financial data", "lending", "consumer finance", "reit",
    "alcohol", "beverages - brewers", "beverages - wineries", "distill", "tobacco",
    "gambling", "casino", "lottery", "betting",
    "pork", "adult",
    "entertainment", "broadcasting", "media", "music", "cinema", "gaming",
    "aerospace & defense", "defense", "weapons", "firearms",
    "cannabis", "marijuana",
    "crypto", "bitcoin",
)
# Industries that match a keyword above but are actually fine (keyword collision).
INDUSTRY_ALLOW_OVERRIDE = ("electronic gaming & multimedia",)  # video games: treated as permissible by most screens


@dataclass(frozen=True)
class HalalVerdict:
    status: str            # "pass" | "fail" | "unknown"
    reasons: tuple[str, ...]
    debt_ratio: float | None = None
    cash_ratio: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    def label(self) -> str:
        if self.status == "pass":
            r = []
            if self.debt_ratio is not None:
                r.append(f"دين {self.debt_ratio:.0%}")
            if self.cash_ratio is not None:
                r.append(f"نقد {self.cash_ratio:.0%}")
            return "✅ متوافق مع الشريعة" + (f" ({'، '.join(r)})" if r else "")
        if self.status == "fail":
            return "⛔ غير متوافق: " + "؛ ".join(self.reasons)
        return "❓ لم يتأكد: " + "؛ ".join(self.reasons)


def screen(ticker: str, sector: str | None, industry: str | None, market_cap: float | None,
           total_debt: float | None, total_cash: float | None,
           whitelist: frozenset[str] = frozenset(), blacklist: frozenset[str] = frozenset()) -> HalalVerdict:
    t = ticker.upper()
    if t in blacklist:
        return HalalVerdict("fail", ("في القائمة السوداء اليدوية",))
    if t in whitelist:
        return HalalVerdict("pass", ("في القائمة البيضاء اليدوية",))

    reasons: list[str] = []
    sec = (sector or "").strip().lower()
    ind = (industry or "").strip().lower()
    if sec in EXCLUDED_SECTORS:
        reasons.append(f"قطاع مالي تقليدي ({sector})")
    elif ind and ind not in INDUSTRY_ALLOW_OVERRIDE:
        hit = next((k for k in EXCLUDED_INDUSTRY_KEYWORDS if k in ind), None)
        if hit:
            reasons.append(f"نشاط غير جائز ({industry})")
    if reasons:
        return HalalVerdict("fail", tuple(reasons))

    if not sector and not industry:
        return HalalVerdict("unknown", ("بيانات القطاع غير متوفرة",))
    if not market_cap:
        return HalalVerdict("unknown", ("القيمة السوقية غير متوفرة",))

    debt_ratio = (total_debt or 0.0) / market_cap
    cash_ratio = (total_cash or 0.0) / market_cap
    if total_debt is None:
        reasons.append("بيانات الدين غير متوفرة")
    elif debt_ratio >= DEBT_LIMIT:
        return HalalVerdict("fail", (f"الدين {debt_ratio:.0%} من القيمة السوقية (الحد 33%)",), debt_ratio, cash_ratio)
    if total_cash is None:
        reasons.append("بيانات النقد غير متوفرة")
    elif cash_ratio >= CASH_LIMIT:
        return HalalVerdict("fail", (f"النقد والأوراق الربوية {cash_ratio:.0%} (الحد 33%)",), debt_ratio, cash_ratio)
    if reasons:
        return HalalVerdict("unknown", tuple(reasons), debt_ratio if total_debt is not None else None,
                            cash_ratio if total_cash is not None else None)
    return HalalVerdict("pass", (), debt_ratio, cash_ratio)
