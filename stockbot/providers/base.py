"""Provider layer: every number carries its provenance.

`Sourced` wraps a value with source, timestamp, currency, and whether it is
delayed or estimated, so reports can say exactly where each figure came from.
Providers implement small interfaces; `DataHub` (registry.py) picks the first
provider that can answer, caches the result, and records API usage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

T = TypeVar("T")


@dataclass
class Sourced(Generic[T]):
    value: T
    source: str
    as_of: float = field(default_factory=time.time)   # unix seconds the data refers to / was fetched
    currency: str | None = None
    delayed: bool = True                               # free feeds are delayed; only mark False when the provider says real-time
    estimated: bool = False                            # derived / modelled rather than reported
    note: str = ""

    def meta(self) -> str:
        bits = [self.source, time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(self.as_of))]
        if self.currency:
            bits.append(self.currency)
        bits.append("delayed" if self.delayed else "real-time")
        if self.estimated:
            bits.append("estimated")
        return " · ".join(bits)


@dataclass
class Candle:
    ts: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Quote:
    ticker: str
    price: float
    prev_close: float | None
    currency: str
    exchange: str
    name: str
    market_state: str = ""          # e.g. REGULAR, PRE, POST, CLOSED
    quote_type: str = ""            # EQUITY, ETF, CRYPTOCURRENCY, ...


@dataclass
class Fundamentals:
    """Reported figures (None when the provider has no value)."""
    ticker: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    country: str = ""
    exchange: str = ""
    currency: str = "USD"
    description: str = ""
    market_cap: float | None = None
    revenue: float | None = None
    revenue_growth: float | None = None      # fraction, yoy
    earnings_growth: float | None = None     # fraction, yoy
    eps_ttm: float | None = None
    eps_forward: float | None = None
    pe: float | None = None
    forward_pe: float | None = None
    pb: float | None = None
    ps: float | None = None
    free_cash_flow: float | None = None
    operating_cash_flow: float | None = None
    debt_to_equity: float | None = None      # percent as Yahoo reports (e.g. 45.3)
    gross_margin: float | None = None
    operating_margin: float | None = None
    profit_margin: float | None = None
    roe: float | None = None
    total_debt: float | None = None
    total_cash: float | None = None
    total_assets: float | None = None
    receivables: float | None = None
    interest_income: float | None = None     # non-operating interest income, when reported
    shares_outstanding: float | None = None
    dividend_yield: float | None = None
    beta: float | None = None
    earnings_date: str | None = None
    insider_pct: float | None = None
    institutions_pct: float | None = None
    short_pct_float: float | None = None
    analyst_target_mean: float | None = None
    analyst_recommendation: str = ""
    analyst_count: int | None = None
    fiscal_period_end: str | None = None


@dataclass
class NewsItem:
    id: str
    ticker: str
    title: str
    url: str
    source: str                  # publisher name
    provider: str                # which API gave it to us
    published_at: float | None   # unix seconds the article was published
    summary: str = ""
    event_date: str | None = None  # the date of the event itself when different from publication


@dataclass
class Filing:
    form: str
    filed_at: str                # YYYY-MM-DD
    description: str
    url: str


class PriceProvider(Protocol):
    name: str
    def history(self, ticker: str, period: str = "1y") -> list[Candle]: ...
    def quote(self, ticker: str) -> Quote | None: ...


class FundamentalsProvider(Protocol):
    name: str
    def fundamentals(self, ticker: str) -> Fundamentals | None: ...


class NewsProvider(Protocol):
    name: str
    def news(self, ticker: str, limit: int = 10) -> list[NewsItem]: ...


class FilingsProvider(Protocol):
    name: str
    def filings(self, ticker: str, limit: int = 10) -> list[Filing]: ...


def to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, list):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj
