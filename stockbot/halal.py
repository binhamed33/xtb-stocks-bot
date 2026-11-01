"""Backward-compatible wrapper around analysis.sharia (AAOIFI) for the scheduled brief and older tests.

New code should use `stockbot.analysis.sharia` directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from .analysis import sharia as sh
from .providers.base import Fundamentals


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
           whitelist: frozenset[str] = frozenset(), blacklist: frozenset[str] = frozenset(),
           method: str = "AAOIFI") -> HalalVerdict:
    f = Fundamentals(ticker=ticker, sector=sector or "", industry=industry or "", market_cap=market_cap,
                     total_debt=total_debt, total_cash=total_cash, total_assets=None)
    v = sh.screen(f, method, whitelist=whitelist, blacklist=blacklist)
    status = {sh.HALAL: "pass", sh.NON_COMPLIANT: "fail", sh.QUESTIONABLE: "unknown", sh.UNVERIFIED: "unknown"}[v.status]
    # the scheduled brief only tracks debt/cash; a QUESTIONABLE purely for unmeasured income is treated as pass there
    if v.status == sh.QUESTIONABLE and v.unverified and all("income" in u for u in v.unverified) and not v.business_note.startswith("check") and v.business_note == "permissible main activity":
        status = "pass"
    return HalalVerdict(status, tuple(v.reasons), v.ratios.get("debt"), v.ratios.get("cash"))
