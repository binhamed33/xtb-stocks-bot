"""Sharia Compliance Engine.

Three documented methodologies, never mixed. Each verdict carries the method,
thresholds, the ratios actually computed, the data sources, the screening date,
and an explicit list of what could NOT be verified.

Business screen (all methods): the company's main activity must be permissible.
Financial screens:

  AAOIFI (Shari'ah Standard No. 21):
    interest-bearing debt            / market cap (avg)      < 30%
    interest-bearing deposits+secs   / market cap (avg)      < 30%
    non-permissible income           / total revenue         < 5%
  MSCI Islamic Index Series:
    total debt                       / total assets          < 33.33%
    cash + interest-bearing secs     / total assets          < 33.33%
    accounts receivable + cash       / total assets          < 33.33%
    non-permissible revenue          / total revenue         < 5%
  FTSE Shariah Global Equity Index:
    total debt                       / total assets          < 33.333%
    cash + interest-bearing items    / total assets          < 33.333%
    accounts receivable + cash       / total assets          < 50%
    non-permissible income           / total revenue         < 5%

Statuses: HALAL (all screens passed with data), NON_COMPLIANT (a screen failed),
QUESTIONABLE (passes available screens but a material item needs review, e.g.
mixed-activity industry), UNVERIFIED (not enough data to rule).

A HALAL verdict is never issued without data for every ratio the method needs,
except the non-permissible income ratio when the statements do not report it:
that case is downgraded to QUESTIONABLE with the gap stated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..providers.base import Fundamentals

HALAL, NON_COMPLIANT, QUESTIONABLE, UNVERIFIED = "HALAL", "NON_COMPLIANT", "QUESTIONABLE", "UNVERIFIED"

METHODS: dict[str, dict] = {
    "AAOIFI": {
        "label": "AAOIFI Shari'ah Standard No. 21",
        "denominator": "market_cap",
        "debt": 0.30, "cash": 0.30, "receivables": None, "income": 0.05,
        "reference": "https://aaoifi.org/shariah-standards/?lang=en",
    },
    "MSCI": {
        "label": "MSCI Islamic Index Series Methodology",
        "denominator": "total_assets",
        "debt": 1 / 3, "cash": 1 / 3, "receivables": 1 / 3, "income": 0.05,
        "reference": "https://www.msci.com/our-solutions/indexes/islamic-indexes",
    },
    "FTSE": {
        "label": "FTSE Shariah Global Equity Index Series",
        "denominator": "total_assets",
        "debt": 1 / 3, "cash": 1 / 3, "receivables": 0.50, "income": 0.05,
        "reference": "https://www.lseg.com/en/ftse-russell/indices/shariah",
    },
}

# Business screen ---------------------------------------------------------------
EXCLUDED_SECTORS = {"financial services", "financial"}
# keyword -> short reason (matched against Yahoo/FMP industry names, lower-case)
EXCLUDED_INDUSTRY = {
    "bank": "conventional banking", "insurance": "conventional insurance", "credit services": "interest-based lending",
    "capital markets": "conventional finance", "asset management": "conventional finance", "mortgage": "interest-based lending",
    "financial data": "conventional finance services", "lending": "interest-based lending", "consumer finance": "interest-based lending",
    "reit": "conventional REIT (interest-based financing)",
    "alcohol": "alcohol", "beverages - brewers": "alcohol", "beverages - wineries": "alcohol", "distill": "alcohol",
    "tobacco": "tobacco", "gambling": "gambling", "casino": "gambling", "lottery": "gambling", "betting": "gambling",
    "pork": "pork", "adult": "adult content",
    "entertainment": "conventional entertainment", "broadcasting": "conventional media", "media": "conventional media",
    "music": "music", "cinema": "cinema",
    "aerospace & defense": "weapons / defence", "defense": "weapons / defence", "weapons": "weapons", "firearms": "weapons",
    "cannabis": "cannabis", "marijuana": "cannabis", "crypto": "crypto exchange / speculation", "bitcoin": "crypto",
}
# Industries that collide with a keyword but are widely treated as permissible; they become QUESTIONABLE, not failed.
REVIEW_INDUSTRY = {
    "electronic gaming & multimedia": "video games: permissible by most boards, content should be reviewed",
    "internet content & information": "mixed content/advertising revenue: minor non-permissible exposure possible",
    "restaurants": "check alcohol sales share",
    "grocery stores": "check alcohol/tobacco sales share",
    "airlines": "in-flight alcohol sales are usually a minor share",
    "lodging": "hotels: check alcohol/entertainment revenue share",
    "resorts & casinos": "casino revenue",
    "leisure": "check gambling/alcohol exposure",
    "advertising agencies": "mixed clients",
}


@dataclass
class ShariaVerdict:
    ticker: str
    method: str
    status: str = UNVERIFIED
    reasons: list[str] = field(default_factory=list)
    ratios: dict[str, float | None] = field(default_factory=dict)      # name -> value (fraction)
    thresholds: dict[str, float | None] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    screened_at: float = field(default_factory=time.time)
    business_note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == HALAL

    @property
    def investable(self) -> bool:
        """HALAL only. QUESTIONABLE/UNVERIFIED are never presented as investable."""
        return self.status == HALAL

    def label(self) -> str:
        icon = {HALAL: "✅", NON_COMPLIANT: "⛔", QUESTIONABLE: "⚠️", UNVERIFIED: "❓"}[self.status]
        ar = {HALAL: "متوافق مع الشريعة", NON_COMPLIANT: "غير متوافق", QUESTIONABLE: "يحتاج مراجعة", UNVERIFIED: "غير مؤكد"}[self.status]
        return f"{icon} {ar} ({self.method})"

    def ratio_lines(self) -> list[str]:
        names = {"debt": "الدين الربوي", "cash": "النقد والأوراق الربوية", "receivables": "الذمم + النقد",
                 "income": "الدخل غير الجائز"}
        out = []
        for k, v in self.ratios.items():
            th = self.thresholds.get(k)
            if v is None:
                out.append(f"{names.get(k, k)}: غير متوفر" + (f" (الحد {th:.1%})" if th else ""))
            else:
                flag = "✓" if th is None or v < th else "✗"
                out.append(f"{names.get(k, k)}: {v:.1%} {flag}" + (f" (الحد {th:.1%})" if th else ""))
        return out


def business_screen(sector: str | None, industry: str | None, description: str = "") -> tuple[str, str]:
    """Return (status, note) where status is HALAL / NON_COMPLIANT / QUESTIONABLE / UNVERIFIED."""
    sec = (sector or "").strip().lower()
    ind = (industry or "").strip().lower()
    if not sec and not ind:
        return UNVERIFIED, "sector/industry unavailable"
    if sec in EXCLUDED_SECTORS:
        return NON_COMPLIANT, f"main activity: {sector}"
    if ind in REVIEW_INDUSTRY:
        return QUESTIONABLE, REVIEW_INDUSTRY[ind]
    for kw, why in EXCLUDED_INDUSTRY.items():
        if kw in ind:
            return NON_COMPLIANT, f"main activity: {why} ({industry})"
    desc = (description or "").lower()
    for kw in ("casino", "brewery", "distillery", "tobacco products", "firearms", "adult entertainment"):
        if kw in desc:
            return QUESTIONABLE, f"description mentions '{kw}': revenue share needs review"
    return HALAL, "permissible main activity"


def screen(f: Fundamentals, method: str = "AAOIFI", sources: list[str] | None = None,
           whitelist: frozenset[str] = frozenset(), blacklist: frozenset[str] = frozenset()) -> ShariaVerdict:
    method = method.upper()
    spec = METHODS[method]
    v = ShariaVerdict(ticker=f.ticker.upper(), method=method, sources=list(sources or []))
    v.thresholds = {"debt": spec["debt"], "cash": spec["cash"], "receivables": spec["receivables"], "income": spec["income"]}

    if v.ticker in blacklist:
        v.status, v.reasons = NON_COMPLIANT, ["manually blacklisted (HALAL_BLACKLIST)"]
        return v
    if v.ticker in whitelist:
        v.status, v.reasons = HALAL, ["manually whitelisted (HALAL_WHITELIST): certified by the user's own screener"]
        v.sources.append("user whitelist")
        return v

    if f.description.startswith("quoteType=") and not f.sector:
        v.status, v.reasons = UNVERIFIED, [f"not a single company ({f.description}); fund/ETF holdings are not screened"]
        return v

    b_status, b_note = business_screen(f.sector, f.industry, f.description)
    v.business_note = b_note
    if b_status == NON_COMPLIANT:
        v.status, v.reasons = NON_COMPLIANT, [f"business screen failed: {b_note}"]
        return v
    if b_status == UNVERIFIED:
        v.status, v.reasons = UNVERIFIED, ["business screen: " + b_note]
        v.unverified.append("main business activity")
        return v

    denom_name = spec["denominator"]
    denom = f.market_cap if denom_name == "market_cap" else f.total_assets
    if not denom:
        v.status = UNVERIFIED
        v.reasons.append(f"{denom_name.replace('_', ' ')} unavailable: financial screen impossible")
        v.unverified.append(denom_name.replace("_", " "))
        v.ratios = {"debt": None, "cash": None, "receivables": None if spec["receivables"] else None, "income": None}
        return v

    def ratio(num: float | None) -> float | None:
        return None if num is None else max(0.0, num) / denom

    v.ratios["debt"] = ratio(f.total_debt)
    v.ratios["cash"] = ratio(f.total_cash)
    if spec["receivables"] is not None:
        rc = None if (f.receivables is None and f.total_cash is None) else (f.receivables or 0.0) + (f.total_cash or 0.0)
        v.ratios["receivables"] = ratio(rc)
    if f.interest_income is not None and f.revenue:
        v.ratios["income"] = max(0.0, f.interest_income) / f.revenue
    else:
        v.ratios["income"] = None

    failed: list[str] = []
    missing: list[str] = []
    labels = {"debt": "interest-bearing debt", "cash": "cash & interest-bearing securities",
              "receivables": "receivables + cash", "income": "non-permissible income"}
    for k in ("debt", "cash", "receivables", "income"):
        th = v.thresholds.get(k)
        if th is None:
            continue
        r = v.ratios.get(k)
        if r is None:
            missing.append(k)
        elif r >= th:
            failed.append(f"{labels[k]} {r:.1%} ≥ {th:.1%} ({denom_name.replace('_', ' ')} basis)")

    if failed:
        v.status, v.reasons = NON_COMPLIANT, failed
        return v
    hard_missing = [m for m in missing if m != "income"]
    if hard_missing:
        v.status = UNVERIFIED
        v.reasons.append("missing data for: " + ", ".join(labels[m] for m in hard_missing))
        v.unverified.extend(labels[m] for m in hard_missing)
        return v
    if "income" in missing:
        v.unverified.append("non-permissible income share (not separately reported in free statements)")
    if b_status == QUESTIONABLE:
        v.status = QUESTIONABLE
        v.reasons.append("business review: " + b_note)
        v.reasons.append("financial ratios within limits")
        return v
    if "income" in missing:
        v.status = QUESTIONABLE
        v.reasons.append("financial ratios within limits; non-permissible income share could not be verified from free data")
        return v
    v.status = HALAL
    v.reasons.append("business permissible; all financial ratios within the method's limits")
    return v


def method_note(method: str) -> str:
    spec = METHODS[method.upper()]
    parts = [f"debt < {spec['debt']:.1%}", f"cash/interest items < {spec['cash']:.1%}"]
    if spec["receivables"]:
        parts.append(f"receivables+cash < {spec['receivables']:.1%}")
    parts.append(f"non-permissible income < {spec['income']:.0%}")
    return f"{spec['label']} — basis: {spec['denominator'].replace('_', ' ')} — " + ", ".join(parts)
