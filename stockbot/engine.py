"""Engine: composes providers, analysis, Sharia, XTB verification, news, AI and storage per ticker.

Everything the Discord commands and the CLI need goes through here, so the
same code path is tested offline with fake providers.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .ai import AIClient, CompareResult, NewsDigest, QAAnswer, StockNarrative, build_ai
from .alerts import AlertEvent, Alerts
from .analysis import fundamental as fa
from .analysis import risk as ra
from .analysis import scoring
from .analysis import sharia as sh
from .analysis import technical as ta
from .config import Settings
from .db import Database
from .news import intelligence as ni
from .portfolio import Portfolio, PortfolioView, Watchlist
from .providers.base import Candle, Fundamentals, Quote, Sourced, to_jsonable
from .providers.registry import DataHub
from .xtb.instruments import XtbRegistry, XtbStatus

log = logging.getLogger(__name__)


@dataclass
class StockReport:
    ticker: str
    generated_at: float
    quote: Sourced[Quote] | None
    fundamentals: Sourced[Fundamentals] | None
    candles: Sourced[list[Candle]]
    tech: ta.TechnicalView | None
    fund: fa.FundamentalView | None
    risk: ra.RiskView | None
    sharia: sh.ShariaVerdict | None
    xtb: XtbStatus
    news: ni.NewsView
    opportunity: scoring.Opportunity
    narrative: StockNarrative | None = None
    news_digest: NewsDigest | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        if self.fundamentals and self.fundamentals.value.name:
            return self.fundamentals.value.name
        return self.quote.value.name if self.quote and self.quote.value.name else self.ticker

    @property
    def price(self) -> float | None:
        if self.quote:
            return self.quote.value.price
        return self.tech.price if self.tech else None

    @property
    def currency(self) -> str:
        return self.quote.value.currency if self.quote else (self.fundamentals.value.currency if self.fundamentals else "USD")

    @property
    def investable(self) -> bool:
        return bool(self.sharia and self.sharia.investable and self.xtb.investable)

    def sources(self) -> list[str]:
        out: list[str] = []
        if self.quote:
            out.append(f"السعر: {self.quote.meta()}")
        if self.fundamentals:
            out.append(f"البيانات المالية: {self.fundamentals.meta()}")
        if self.candles.value:
            out.append(f"التاريخ السعري: {self.candles.meta()}")
        if self.news.items:
            out.append(f"الأخبار: {' + '.join(self.news.sources)} · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(self.news.as_of))}")
        if self.news.filings:
            out.append("الإفصاحات: SEC EDGAR (رسمي)")
        if self.sharia:
            out.append(f"الفحص الشرعي: {sh.METHODS[self.sharia.method]['label']} · {time.strftime('%Y-%m-%d', time.gmtime(self.sharia.screened_at))}")
        out.append(f"XTB: {self.xtb.source or 'لم يُتحقق'}")
        return out


class Engine:
    def __init__(self, settings: Settings, db: Database | None = None, hub: DataHub | None = None,
                 ai: AIClient | None = None) -> None:
        self.s = settings
        self.db = db or Database(settings.db_path)
        self.hub = hub or DataHub(settings, self.db)
        self.ai = ai or build_ai(settings, self.db)
        self.xtb = XtbRegistry(self.db)
        self.portfolio = Portfolio(self.db)
        self.watchlist = Watchlist(self.db)
        self.alerts = Alerts(self.db)

    # ---- sharia with history ---------------------------------------------------
    def sharia(self, ticker: str, fund: Sourced[Fundamentals] | None = None, method: str | None = None,
               force: bool = False) -> sh.ShariaVerdict | None:
        method = (method or self.s.sharia_method).upper()
        t = ticker.upper()
        if not force:
            row = self.db.one("SELECT * FROM sharia_screens WHERE ticker=? AND method=? ORDER BY screened_at DESC LIMIT 1", (t, method))
            if row and time.time() - row["screened_at"] < self.s.sharia_rescreen_days * 86400:
                return sh.ShariaVerdict(ticker=t, method=method, status=row["status"], reasons=json.loads(row["reasons"]),
                                        ratios=json.loads(row["ratios"]), thresholds={k: v for k, v in sh.METHODS[method].items() if k in ("debt", "cash", "receivables", "income")},
                                        sources=json.loads(row["sources"]), unverified=json.loads(row["unverified"]),
                                        screened_at=row["screened_at"])
        fund = fund or self.hub.fundamentals(t)
        if fund is None:
            v = sh.ShariaVerdict(ticker=t, method=method, status=sh.UNVERIFIED, reasons=["no fundamental data available"],
                                 unverified=["everything"])
        else:
            v = sh.screen(fund.value, method, [fund.meta()], self.s.halal_whitelist, self.s.halal_blacklist)
        self.db.x("INSERT INTO sharia_screens(ticker,method,status,reasons,ratios,sources,unverified,screened_at) VALUES (?,?,?,?,?,?,?,?)",
                  (t, method, v.status, json.dumps(v.reasons, ensure_ascii=False), json.dumps(v.ratios),
                   json.dumps(v.sources, ensure_ascii=False), json.dumps(v.unverified, ensure_ascii=False), v.screened_at))
        return v

    def sharia_history(self, ticker: str, limit: int = 5) -> list[dict]:
        return [dict(r) for r in self.db.q("SELECT method,status,screened_at FROM sharia_screens WHERE ticker=? ORDER BY screened_at DESC LIMIT ?",
                                           (ticker.upper(), limit))]

    # ---- full analysis -------------------------------------------------------------
    def analyze(self, ticker: str, with_ai: bool = True, with_news: bool = True) -> StockReport:
        t = ticker.upper().strip()
        warnings: list[str] = []
        quote = self.hub.quote(t)
        fund = self.hub.fundamentals(t)
        candles = self.hub.history(t, "2y")
        if not candles.value:
            warnings.append("لا توجد بيانات سعرية لهذا الرمز")
        tech = ta.analyze(candles.value) if candles.value else None
        fview = fa.analyze(fund.value, tech) if fund else None
        risk = ra.analyze(candles.value, fund.value if fund else None) if candles.value else None
        verdict = self.sharia(t, fund)
        xtb = self.xtb.resolve(t, quote.value if quote else None, fund.value if fund else None)
        if with_news:
            news_s = self.hub.news(t)
            filings = self.hub.filings(t)
            news = ni.build(news_s.value, filings.value, news_s.source.split(" + "))
            news.as_of = news_s.as_of
            for c in news.items:
                self.db.x("INSERT OR IGNORE INTO news(id,ticker,title,url,source,published_at,event_type,sentiment,summary,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (c.item.id, t, c.item.title, c.item.url, c.item.source, c.item.published_at, c.event_type, c.sentiment, c.item.summary, time.time()))
        else:
            news = ni.NewsView()
        opp = scoring.score(t, tech, fview, risk, verdict, xtb.status, news.score if with_news else None, news.notes)
        rep = StockReport(ticker=t, generated_at=time.time(), quote=quote, fundamentals=fund, candles=candles, tech=tech,
                          fund=fview, risk=risk, sharia=verdict, xtb=xtb, news=news, opportunity=opp, warnings=warnings)
        if with_ai and self.ai.enabled and (tech or fund):
            rep.narrative = self.ai.generate("Write the stock narrative from the data only.", self._ai_payload(rep), StockNarrative, self.s.language)
            if news.items:
                rep.news_digest = self.ai.generate("Summarise ONLY these news items; do not add facts.",
                                                   {"ticker": t, "items": [{"title": c.item.title, "source": c.item.source, "url": c.item.url,
                                                                            "published": c.item.published_at, "summary": c.item.summary,
                                                                            "event": c.event_type} for c in news.items[:12]]},
                                                   NewsDigest, self.s.language)
        self.db.x("INSERT INTO reports(ticker,kind,payload,created_at) VALUES (?,?,?,?)",
                  (t, "analysis", json.dumps(self.summary_dict(rep), ensure_ascii=False, default=str), time.time()))
        return rep

    def _ai_payload(self, r: StockReport) -> dict[str, Any]:
        return {
            "ticker": r.ticker, "name": r.name, "price": r.price, "currency": r.currency,
            "price_source": r.quote.meta() if r.quote else None,
            "fundamentals": {k: v for k, v in to_jsonable(r.fundamentals.value).items() if v not in (None, "")} if r.fundamentals else None,
            "fundamentals_source": r.fundamentals.meta() if r.fundamentals else None,
            "technical": {k: v for k, v in to_jsonable(r.tech).items() if v not in (None, [], "")} if r.tech else None,
            "fundamental_view": to_jsonable(r.fund) if r.fund else None,
            "risk": to_jsonable(r.risk) if r.risk else None,
            "sharia": {"status": r.sharia.status, "method": r.sharia.method, "reasons": r.sharia.reasons, "ratios": r.sharia.ratios} if r.sharia else None,
            "xtb_real_stock": r.xtb.status,
            "news": [{"title": c.item.title, "source": c.item.source, "url": c.item.url, "published_at": c.item.published_at,
                      "event": c.event_type, "sentiment": c.sentiment} for c in r.news.items[:10]],
            "filings": [to_jsonable(f) for f in r.news.filings[:5]],
            "opportunity": {"total": r.opportunity.total, "parts": r.opportunity.parts, "category": r.opportunity.category},
        }

    def summary_dict(self, r: StockReport) -> dict[str, Any]:
        return {"ticker": r.ticker, "name": r.name, "price": r.price, "currency": r.currency,
                "sharia": r.sharia.status if r.sharia else None, "xtb": r.xtb.status,
                "score": r.opportunity.total, "category": r.opportunity.category, "parts": r.opportunity.parts,
                "tech_score": r.tech.score if r.tech else None, "health": r.fund.health_score if r.fund else None,
                "drop_pct": r.tech.from_52w_high_pct if r.tech else None, "generated_at": r.generated_at}

    # ---- scanner ----------------------------------------------------------------------
    def scan(self, tickers: list[str] | None = None, limit: int = 10, min_drop_pct: float = 10.0,
             progress=None) -> list[StockReport]:  # noqa: ANN001
        tickers = [t.upper() for t in (tickers or self.s.scan_universe)]
        hist = self.hub.history_many(tickers, "2y")
        candidates: list[tuple[str, float]] = []
        for t in tickers:
            c = hist.get(t)
            if not c or len(c.value) < 60:
                continue
            tv = ta.analyze(c.value)
            if tv and tv.from_52w_high_pct is not None and tv.from_52w_high_pct <= -min_drop_pct:
                candidates.append((t, tv.from_52w_high_pct))
        candidates.sort(key=lambda x: x[1])
        reports: list[StockReport] = []
        for i, (t, _) in enumerate(candidates[: max(limit * 3, 15)]):
            if progress:
                progress(i + 1, len(candidates[: max(limit * 3, 15)]), t)
            try:
                reports.append(self.analyze(t, with_ai=False, with_news=True))
            except Exception as exc:  # noqa: BLE001
                log.warning("scan: %s failed: %s", t, exc)
        order = {scoring.STRONG: 0, scoring.RECOVERY: 1, scoring.HRHR: 2, scoring.WATCH: 3, scoring.UNVERIFIED: 4, scoring.AVOID: 5}
        reports.sort(key=lambda r: (order[r.opportunity.category], -r.opportunity.total))
        self.db.x("INSERT INTO scans(universe_size,results,created_at) VALUES (?,?,?)",
                  (len(tickers), json.dumps([self.summary_dict(r) for r in reports], ensure_ascii=False, default=str), time.time()))
        return reports[:limit]

    def last_scan(self) -> list[dict] | None:
        row = self.db.one("SELECT results, created_at FROM scans ORDER BY created_at DESC LIMIT 1")
        return json.loads(row["results"]) if row else None

    # ---- compare / market / QA --------------------------------------------------------
    def compare(self, a: str, b: str) -> tuple[StockReport, StockReport, CompareResult | None]:
        ra_, rb_ = self.analyze(a, with_ai=False), self.analyze(b, with_ai=False)
        res = None
        if self.ai.enabled:
            res = self.ai.generate("Compare the two companies strictly from the data; pick the better long-term risk/reward.",
                                   {"a": self._ai_payload(ra_), "b": self._ai_payload(rb_)}, CompareResult, self.s.language)
        return ra_, rb_, res

    def market(self) -> dict[str, Any]:
        from .data import index_overview  # existing helper (Yahoo)
        key = "market:overview"
        hit = self.db.cache_get(key)
        if hit:
            return hit[0]
        try:
            idx = index_overview()
        except Exception as exc:  # noqa: BLE001
            log.warning("index overview failed: %s", exc)
            idx = {}
        self.db.cache_set(key, idx, 15 * 60, "Yahoo Finance")
        return idx

    def ask(self, question: str, ticker: str | None = None) -> QAAnswer | None:
        if not self.ai.enabled:
            return None
        payload: dict[str, Any] = {"question": question}
        if ticker:
            payload["stock"] = self._ai_payload(self.analyze(ticker, with_ai=False))
        return self.ai.generate("Answer the user's question using only the attached data.", payload, QAAnswer, self.s.language)

    # ---- portfolio -------------------------------------------------------------------
    def portfolio_view(self, user_id: int) -> PortfolioView:
        tickers = self.portfolio.tickers(user_id)
        quotes: dict[str, tuple[float, str]] = {}
        sectors: dict[str, str] = {}
        sharia: dict[str, str] = {}
        vol: dict[str, float] = {}
        for t in tickers:
            q = self.hub.quote(t)
            if q:
                quotes[t] = (q.value.price, q.meta())
            f = self.hub.fundamentals(t)
            if f:
                sectors[t] = f.value.sector or "غير معروف"
            v = self.sharia(t, f)
            if v:
                sharia[t] = v.status
            c = self.hub.history(t, "1y")
            rv = ra.analyze(c.value) if c.value else None
            if rv and rv.annual_volatility:
                vol[t] = rv.annual_volatility
        return self.portfolio.value(user_id, quotes, sectors, sharia, vol)

    # ---- alerts ------------------------------------------------------------------------
    def run_alerts(self) -> list[AlertEvent]:
        """Evaluate every user's rules plus automatic monitoring of portfolio & watchlist tickers."""
        fired: list[AlertEvent] = []
        rules = self.alerts.all_rules()
        by_user: dict[int, dict[str, list[dict]]] = {}
        for r in rules:
            by_user.setdefault(r["user_id"], {}).setdefault(r["ticker"], []).append(r)
        auto: dict[int, set[str]] = {}
        for row in self.db.q("SELECT DISTINCT user_id, ticker FROM portfolio"):
            auto.setdefault(row["user_id"], set()).add(row["ticker"])
        for row in self.db.q("SELECT DISTINCT user_id, ticker FROM watchlist"):
            auto.setdefault(row["user_id"], set()).add(row["ticker"])
        users = set(by_user) | set(auto)
        for uid in users:
            tickers = set(by_user.get(uid, {})) | auto.get(uid, set())
            for t in tickers:
                try:
                    q = self.hub.quote(t)
                    f = self.hub.fundamentals(t)
                    v = self.sharia(t, f)
                    news = self.hub.news(t, 8)
                    recent = [(ni.classify(n).event_type, n.title, n.url) for n in news.value
                              if n.published_at and time.time() - n.published_at < 86400]
                    c = self.hub.history(t, "1y")
                    tv = ta.analyze(c.value) if c.value else None
                    fired.extend(self.alerts.evaluate(
                        uid, t, price=q.value.price if q else None, prev_close=q.value.prev_close if q else None,
                        drop_pct_default=self.s.alert_drop_pct, rules=by_user.get(uid, {}).get(t, []),
                        news_events=recent, sharia_status=v.status if v else None,
                        earnings_date=f.value.earnings_date if f else None,
                        trend_medium=tv.trend_medium if tv else None, auto_monitor=t in auto.get(uid, set())))
                except Exception as exc:  # noqa: BLE001
                    log.warning("alerts: %s/%s failed: %s", uid, t, exc)
        return fired

    def status(self) -> dict[str, Any]:
        return {"providers": self.hub.providers_status(),
                "ai": f"{self.ai.name} ({self.s.gemini_model}, {getattr(self.ai, 'remaining_today', lambda: 0)()} calls left today)" if self.ai.enabled else "disabled (GEMINI_API_KEY not set): programmatic analysis only",
                "sharia_method": sh.method_note(self.s.sharia_method),
                "xtb_verified": len([x for x in self.xtb.all() if x.status == "REAL_STOCK"]),
                "db": str(self.db.path), "usage_7d": self.db.usage_summary(7)}
