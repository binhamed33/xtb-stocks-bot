"""DataHub: one entry point for every data need, with cache, fallbacks, retries and usage accounting.

    hub = DataHub(settings, db)
    hub.quote("AAPL")          -> Sourced[Quote]
    hub.history("AAPL")        -> Sourced[list[Candle]]
    hub.fundamentals("AAPL")   -> Sourced[Fundamentals]  (Yahoo, enriched by FMP/Finnhub when keys exist)
    hub.news("AAPL")           -> Sourced[list[NewsItem]] (Yahoo + Finnhub merged, deduped by URL)
    hub.filings("AAPL")        -> Sourced[list[Filing]]  (SEC EDGAR)

A provider failure never raises out of the hub: the next provider is tried, and
an empty-but-sourced result is returned when all fail.
"""

from __future__ import annotations

import logging
import time
from dataclasses import fields
from typing import Any, Callable, TypeVar

from ..config import Settings
from ..db import Database
from .base import Candle, Filing, Fundamentals, NewsItem, Quote, Sourced, to_jsonable
from .keyed import FinnhubProvider, FmpProvider
from .sec_edgar import SecEdgarProvider
from .yahoo import YahooProvider

log = logging.getLogger(__name__)
T = TypeVar("T")

TTL_QUOTE = 5 * 60
TTL_HISTORY = 60 * 60
TTL_FUNDAMENTALS = 12 * 60 * 60
TTL_NEWS = 30 * 60
TTL_FILINGS = 6 * 60 * 60


def _retry(fn: Callable[[], T], attempts: int = 2, delay: float = 1.0) -> T:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i + 1 < attempts:
                time.sleep(delay * (i + 1))
    assert last is not None
    raise last


class DataHub:
    def __init__(self, settings: Settings, db: Database, yahoo: YahooProvider | None = None,
                 sec: SecEdgarProvider | None = None, finnhub: FinnhubProvider | None = None,
                 fmp: FmpProvider | None = None) -> None:
        self.s = settings
        self.db = db
        self.yahoo = yahoo or YahooProvider()
        self.sec = sec or SecEdgarProvider(settings.sec_user_agent)
        self.finnhub = finnhub or (FinnhubProvider(settings.finnhub_api_key) if settings.finnhub_api_key else None)
        self.fmp = fmp or (FmpProvider(settings.fmp_api_key) if settings.fmp_api_key else None)

    # ---- plumbing --------------------------------------------------------------
    def _timed(self, provider: str, endpoint: str, fn: Callable[[], T]) -> T:
        t0 = time.monotonic()
        ok = False
        try:
            out = _retry(fn)
            ok = True
            return out
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            self.db.usage_record(provider, endpoint, ok, ms)
            log.info("%s %s: %s in %dms", provider, endpoint, 'ok' if ok else 'FAILED', ms)

    def _cached(self, key: str, ttl: float, source: str, fn: Callable[[], Any], decode: Callable[[Any], T]) -> Sourced[T] | None:
        hit = self.db.cache_get(key)
        if hit is not None:
            value, meta = hit
            return Sourced(decode(value), meta["source"] or source, as_of=meta["fetched_at"], note="cached")
        try:
            raw = fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("%s failed for %s: %s", source, key, exc)
            return None
        if raw is None:
            return None
        self.db.cache_set(key, to_jsonable(raw), ttl, source)
        return Sourced(raw, source)

    # ---- quotes / prices --------------------------------------------------------
    def quote(self, ticker: str) -> Sourced[Quote] | None:
        t = ticker.upper()
        res = self._cached(f"quote:{t}", TTL_QUOTE, self.yahoo.name,
                           lambda: self._timed("yahoo", "quote", lambda: self.yahoo.quote(t)),
                           lambda d: Quote(**d))
        if res:
            res.currency = res.value.currency
        return res

    def history(self, ticker: str, period: str = "1y") -> Sourced[list[Candle]]:
        t = ticker.upper()
        res = self._cached(f"hist:{t}:{period}", TTL_HISTORY, self.yahoo.name,
                           lambda: self._timed("yahoo", "history", lambda: self.yahoo.history(t, period) or None),
                           lambda d: [Candle(**c) for c in d])
        return res or Sourced([], self.yahoo.name, note="no data")

    def history_many(self, tickers: list[str], period: str = "1y") -> dict[str, Sourced[list[Candle]]]:
        out: dict[str, Sourced[list[Candle]]] = {}
        missing: list[str] = []
        for t in (x.upper() for x in tickers):
            hit = self.db.cache_get(f"hist:{t}:{period}")
            if hit:
                value, meta = hit
                out[t] = Sourced([Candle(**c) for c in value], meta["source"], as_of=meta["fetched_at"], note="cached")
            else:
                missing.append(t)
        if missing:
            try:
                fetched = self._timed("yahoo", "history_many", lambda: self.yahoo.history_many(missing, period))
            except Exception as exc:  # noqa: BLE001
                log.warning("history_many failed: %s", exc)
                fetched = {}
            for t, rows in fetched.items():
                self.db.cache_set(f"hist:{t}:{period}", to_jsonable(rows), TTL_HISTORY, self.yahoo.name)
                out[t] = Sourced(rows, self.yahoo.name)
        return out

    # ---- fundamentals -------------------------------------------------------------
    def fundamentals(self, ticker: str) -> Sourced[Fundamentals] | None:
        t = ticker.upper()
        cached = self.db.cache_get(f"fund:{t}")
        if cached:
            value, meta = cached
            return Sourced(Fundamentals(**value), meta["source"], as_of=meta["fetched_at"], note="cached")
        base: Fundamentals | None = None
        sources: list[str] = []
        try:
            base = self._timed("yahoo", "fundamentals", lambda: self.yahoo.fundamentals(t))
            if base:
                sources.append(self.yahoo.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("Yahoo fundamentals failed for %s: %s", t, exc)
        for prov in (self.fmp, self.finnhub):
            if prov is None:
                continue
            try:
                extra = self._timed(prov.name.lower().split()[0], "fundamentals", lambda p=prov: p.fundamentals(t))
            except Exception as exc:  # noqa: BLE001
                log.warning("%s fundamentals failed for %s: %s", prov.name, t, exc)
                continue
            if extra is None:
                continue
            if base is None:
                base = extra
            else:
                for fld in fields(Fundamentals):
                    if getattr(base, fld.name) in (None, "", 0) and getattr(extra, fld.name) not in (None, ""):
                        setattr(base, fld.name, getattr(extra, fld.name))
            sources.append(prov.name)
        if base is None:
            return None
        src = " + ".join(sources) or "unknown"
        self.db.cache_set(f"fund:{t}", to_jsonable(base), TTL_FUNDAMENTALS, src)
        self.db.x("INSERT OR REPLACE INTO stocks(ticker,name,exchange,currency,sector,industry,country,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                  (t, base.name, base.exchange, base.currency, base.sector, base.industry, base.country, time.time()))
        return Sourced(base, src, currency=base.currency)

    # ---- news / filings ---------------------------------------------------------------
    def news(self, ticker: str, limit: int = 12) -> Sourced[list[NewsItem]]:
        t = ticker.upper()
        cached = self.db.cache_get(f"news:{t}")
        if cached:
            value, meta = cached
            return Sourced([NewsItem(**n) for n in value], meta["source"], as_of=meta["fetched_at"], note="cached")
        items: list[NewsItem] = []
        sources: list[str] = []
        for prov in (self.yahoo, self.finnhub):
            if prov is None:
                continue
            try:
                got = self._timed(prov.name.lower().split()[0], "news", lambda p=prov: p.news(t, limit))
            except Exception as exc:  # noqa: BLE001
                log.warning("%s news failed for %s: %s", prov.name, t, exc)
                continue
            if got:
                items.extend(got)
                sources.append(prov.name)
        seen: set[str] = set()
        unique: list[NewsItem] = []
        for n in sorted(items, key=lambda x: x.published_at or 0, reverse=True):
            k = n.url.split("?")[0].rstrip("/").lower()
            if k in seen:
                continue
            seen.add(k)
            unique.append(n)
        unique = unique[:limit]
        src = " + ".join(sources) or self.yahoo.name
        self.db.cache_set(f"news:{t}", to_jsonable(unique), TTL_NEWS, src)
        return Sourced(unique, src)

    def filings(self, ticker: str, limit: int = 8) -> Sourced[list[Filing]]:
        t = ticker.upper()
        res = self._cached(f"filings:{t}", TTL_FILINGS, self.sec.name,
                           lambda: self._timed("sec", "filings", lambda: self.sec.filings(t, limit)),
                           lambda d: [Filing(**x) for x in d])
        return res or Sourced([], self.sec.name, note="unavailable")

    def providers_status(self) -> dict[str, str]:
        return {
            "Yahoo Finance": "active (free, delayed ~15 min)",
            "SEC EDGAR": "active (official filings, free)",
            "Finnhub": "active" if self.finnhub else "disabled (FINNHUB_API_KEY not set)",
            "Financial Modeling Prep": "active" if self.fmp else "disabled (FMP_API_KEY not set)",
            "Alpha Vantage": "disabled (ALPHAVANTAGE_API_KEY not set)" if not self.s.alphavantage_api_key else "key present (reserved for future use)",
        }
