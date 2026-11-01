"""Optional keyed providers: Finnhub (news, basic financials) and Financial Modeling Prep (profile, ratios).

Both are skipped silently when their key is missing. Free tiers are rate-limited
(Finnhub ~60 calls/min, FMP ~250 calls/day) so the hub caches aggressively.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.parse
import urllib.request

from .base import Fundamentals, NewsItem

log = logging.getLogger(__name__)


def _get_json(url: str, timeout: float = 15.0) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "xtb-stocks-bot/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class FinnhubProvider:
    name = "Finnhub"

    def __init__(self, api_key: str) -> None:
        self.key = api_key

    def news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        end = time.strftime("%Y-%m-%d", time.gmtime())
        start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 14 * 86400))
        url = ("https://finnhub.io/api/v1/company-news?" +
               urllib.parse.urlencode({"symbol": ticker.upper(), "from": start, "to": end, "token": self.key}))
        try:
            data = _get_json(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Finnhub news failed: %s", exc)
            return []
        out: list[NewsItem] = []
        for n in data[:limit] if isinstance(data, list) else []:
            url_ = n.get("url", "")
            if not url_ or not n.get("headline"):
                continue
            out.append(NewsItem(id=hashlib.sha1(url_.encode()).hexdigest()[:16], ticker=ticker.upper(),
                                title=str(n["headline"])[:300], url=url_, source=str(n.get("source") or "Finnhub"),
                                provider=self.name, published_at=float(n.get("datetime") or 0) or None,
                                summary=str(n.get("summary") or "")[:600]))
        return out

    def fundamentals(self, ticker: str) -> Fundamentals | None:
        url = "https://finnhub.io/api/v1/stock/metric?" + urllib.parse.urlencode(
            {"symbol": ticker.upper(), "metric": "all", "token": self.key})
        try:
            data = _get_json(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Finnhub metrics failed: %s", exc)
            return None
        m = (data or {}).get("metric", {}) if isinstance(data, dict) else {}
        if not m:
            return None
        f = Fundamentals(ticker=ticker.upper())
        f.pe = m.get("peTTM"); f.pb = m.get("pbQuarterly"); f.ps = m.get("psTTM")
        f.revenue_growth = (m.get("revenueGrowthTTMYoy") or 0) / 100 if m.get("revenueGrowthTTMYoy") is not None else None
        f.earnings_growth = (m.get("epsGrowthTTMYoy") or 0) / 100 if m.get("epsGrowthTTMYoy") is not None else None
        f.eps_ttm = m.get("epsTTM"); f.roe = (m.get("roeTTM") or 0) / 100 if m.get("roeTTM") is not None else None
        f.profit_margin = (m.get("netProfitMarginTTM") or 0) / 100 if m.get("netProfitMarginTTM") is not None else None
        f.gross_margin = (m.get("grossMarginTTM") or 0) / 100 if m.get("grossMarginTTM") is not None else None
        f.operating_margin = (m.get("operatingMarginTTM") or 0) / 100 if m.get("operatingMarginTTM") is not None else None
        f.debt_to_equity = m.get("totalDebt/totalEquityQuarterly")
        f.beta = m.get("beta")
        return f


class FmpProvider:
    name = "Financial Modeling Prep"

    def __init__(self, api_key: str) -> None:
        self.key = api_key

    def fundamentals(self, ticker: str) -> Fundamentals | None:
        try:
            prof = _get_json(f"https://financialmodelingprep.com/api/v3/profile/{ticker.upper()}?apikey={self.key}")
            ratios = _get_json(f"https://financialmodelingprep.com/api/v3/ratios-ttm/{ticker.upper()}?apikey={self.key}")
        except Exception as exc:  # noqa: BLE001
            log.warning("FMP failed: %s", exc)
            return None
        p = prof[0] if isinstance(prof, list) and prof else {}
        r = ratios[0] if isinstance(ratios, list) and ratios else {}
        if not p:
            return None
        f = Fundamentals(ticker=ticker.upper(), name=p.get("companyName", ""), sector=p.get("sector", ""),
                         industry=p.get("industry", ""), country=p.get("country", ""), exchange=p.get("exchangeShortName", ""),
                         currency=p.get("currency", "USD"), description=str(p.get("description") or "")[:1500],
                         market_cap=p.get("mktCap"), beta=p.get("beta"))
        f.pe = r.get("peRatioTTM"); f.pb = r.get("priceToBookRatioTTM"); f.ps = r.get("priceToSalesRatioTTM")
        f.debt_to_equity = (r.get("debtEquityRatioTTM") or 0) * 100 if r.get("debtEquityRatioTTM") is not None else None
        f.roe = r.get("returnOnEquityTTM"); f.profit_margin = r.get("netProfitMarginTTM")
        f.gross_margin = r.get("grossProfitMarginTTM"); f.operating_margin = r.get("operatingProfitMarginTTM")
        f.dividend_yield = r.get("dividendYielTTM") or r.get("dividendYieldTTM")
        return f
