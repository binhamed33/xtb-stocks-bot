"""Yahoo Finance via yfinance: prices, quotes, fundamentals (info + statements), news.

Free, delayed (~15 min), unofficial. Every figure is marked delayed=True.
"""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import timezone
from typing import Any

from .base import Candle, Fundamentals, NewsItem, Quote

log = logging.getLogger(__name__)
NAME = "Yahoo Finance"


def _f(x: Any) -> float | None:
    try:
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def _flatten(df: Any) -> Any:
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


def _stmt_value(df: Any, *labels: str, col: int = 0) -> float | None:
    """First matching row label from a yfinance statement DataFrame (latest column by default)."""
    if df is None or getattr(df, "empty", True):
        return None
    for lab in labels:
        if lab in df.index:
            try:
                return _f(df.loc[lab].iloc[col])
            except Exception:  # noqa: BLE001
                continue
    return None


class YahooProvider:
    name = NAME

    # ---- prices --------------------------------------------------------------
    def history(self, ticker: str, period: str = "1y") -> list[Candle]:
        import yfinance as yf

        df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True, threads=False, timeout=30)
        if df is None or df.empty:
            return []
        df = _flatten(df)
        out: list[Candle] = []
        for ts, r in df.dropna(subset=["Close"]).iterrows():
            out.append(Candle(ts.to_pydatetime().replace(tzinfo=timezone.utc).timestamp(),
                              float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"]),
                              float(r.get("Volume", 0) or 0)))
        return out

    def history_many(self, tickers: list[str], period: str = "1y") -> dict[str, list[Candle]]:
        import yfinance as yf

        out: dict[str, list[Candle]] = {}
        if not tickers:
            return out
        df = yf.download(tickers, period=period, interval="1d", group_by="ticker", progress=False,
                         auto_adjust=True, threads=True, timeout=30)
        if df is None or df.empty:
            return out
        for t in tickers:
            try:
                sub = df[t] if len(tickers) > 1 else _flatten(df)
            except KeyError:
                continue
            rows = []
            for ts, r in sub.dropna(subset=["Close"]).iterrows():
                rows.append(Candle(ts.to_pydatetime().replace(tzinfo=timezone.utc).timestamp(),
                                   float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"]),
                                   float(r.get("Volume", 0) or 0)))
            if rows:
                out[t] = rows
        return out

    def quote(self, ticker: str) -> Quote | None:
        import yfinance as yf

        try:
            tk = yf.Ticker(ticker)
            fi = getattr(tk, "fast_info", None)
            price = _f(getattr(fi, "last_price", None)) if fi is not None else None
            prev = _f(getattr(fi, "previous_close", None)) if fi is not None else None
            currency = str(getattr(fi, "currency", "") or "") if fi is not None else ""
            exchange = str(getattr(fi, "exchange", "") or "") if fi is not None else ""
            info: dict = {}
            if price is None or not currency:
                info = tk.info or {}
                price = price or _f(info.get("currentPrice") or info.get("regularMarketPrice"))
                prev = prev or _f(info.get("previousClose"))
                currency = currency or str(info.get("currency") or "USD")
                exchange = exchange or str(info.get("exchange") or "")
            if price is None:
                return None
            name = str(info.get("shortName") or info.get("longName") or "") if info else ""
            return Quote(ticker=ticker.upper(), price=price, prev_close=prev, currency=currency or "USD",
                         exchange=exchange, name=name, market_state=str(info.get("marketState", "") if info else ""),
                         quote_type=str(info.get("quoteType", "") if info else ""))
        except Exception as exc:  # noqa: BLE001
            log.warning("%s quote failed: %s", ticker, exc)
            return None

    # ---- fundamentals --------------------------------------------------------
    def fundamentals(self, ticker: str) -> Fundamentals | None:
        import yfinance as yf

        try:
            tk = yf.Ticker(ticker)
            info = tk.info or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("%s info failed: %s", ticker, exc)
            return None
        if not info or (info.get("quoteType") not in (None, "", "EQUITY") and not info.get("sector")):
            # ETFs, crypto, futures: not a company
            if info.get("quoteType") and info.get("quoteType") != "EQUITY":
                f = Fundamentals(ticker=ticker.upper(), name=str(info.get("shortName") or ticker))
                f.exchange = str(info.get("exchange") or "")
                f.currency = str(info.get("currency") or "USD")
                f.description = f"quoteType={info.get('quoteType')}"
                return f
        f = Fundamentals(
            ticker=ticker.upper(),
            name=str(info.get("shortName") or info.get("longName") or ticker),
            sector=str(info.get("sector") or ""), industry=str(info.get("industry") or ""),
            country=str(info.get("country") or ""), exchange=str(info.get("exchange") or ""),
            currency=str(info.get("currency") or info.get("financialCurrency") or "USD"),
            description=str(info.get("longBusinessSummary") or "")[:1500],
            market_cap=_f(info.get("marketCap")), revenue=_f(info.get("totalRevenue")),
            revenue_growth=_f(info.get("revenueGrowth")), earnings_growth=_f(info.get("earningsGrowth")),
            eps_ttm=_f(info.get("trailingEps")), eps_forward=_f(info.get("forwardEps")),
            pe=_f(info.get("trailingPE")), forward_pe=_f(info.get("forwardPE")), pb=_f(info.get("priceToBook")),
            ps=_f(info.get("priceToSalesTrailing12Months")), free_cash_flow=_f(info.get("freeCashflow")),
            operating_cash_flow=_f(info.get("operatingCashflow")), debt_to_equity=_f(info.get("debtToEquity")),
            gross_margin=_f(info.get("grossMargins")), operating_margin=_f(info.get("operatingMargins")),
            profit_margin=_f(info.get("profitMargins")), roe=_f(info.get("returnOnEquity")),
            total_debt=_f(info.get("totalDebt")), total_cash=_f(info.get("totalCash")),
            shares_outstanding=_f(info.get("sharesOutstanding")), beta=_f(info.get("beta")),
            insider_pct=_f(info.get("heldPercentInsiders")), institutions_pct=_f(info.get("heldPercentInstitutions")),
            short_pct_float=_f(info.get("shortPercentOfFloat")), analyst_target_mean=_f(info.get("targetMeanPrice")),
            analyst_recommendation=str(info.get("recommendationKey") or ""),
            analyst_count=int(info["numberOfAnalystOpinions"]) if _f(info.get("numberOfAnalystOpinions")) else None,
        )
        dy = _f(info.get("dividendYield"))
        f.dividend_yield = dy / 100 if dy is not None and dy > 1 else dy
        # balance sheet / income statement for the Shariah denominators
        try:
            bs = tk.balance_sheet
            f.total_assets = _stmt_value(bs, "Total Assets")
            f.receivables = _stmt_value(bs, "Accounts Receivable", "Receivables", "Net Receivables")
            if f.total_debt is None:
                f.total_debt = _stmt_value(bs, "Total Debt")
            if f.total_cash is None:
                f.total_cash = _stmt_value(bs, "Cash Cash Equivalents And Short Term Investments",
                                           "Cash And Cash Equivalents")
            if bs is not None and not bs.empty:
                f.fiscal_period_end = str(bs.columns[0].date()) if hasattr(bs.columns[0], "date") else str(bs.columns[0])
        except Exception as exc:  # noqa: BLE001
            log.debug("%s balance sheet unavailable: %s", ticker, exc)
        try:
            inc = tk.income_stmt
            f.interest_income = _stmt_value(inc, "Interest Income Non Operating", "Interest Income",
                                            "Net Non Operating Interest Income Expense")
            if f.revenue is None:
                f.revenue = _stmt_value(inc, "Total Revenue")
        except Exception as exc:  # noqa: BLE001
            log.debug("%s income statement unavailable: %s", ticker, exc)
        try:
            cal = tk.calendar or {}
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if ed:
                first = ed[0] if isinstance(ed, (list, tuple)) else ed
                f.earnings_date = str(first.date() if hasattr(first, "date") else first)
        except Exception:  # noqa: BLE001
            pass
        return f

    # ---- news ----------------------------------------------------------------
    def news(self, ticker: str, limit: int = 10) -> list[NewsItem]:
        import yfinance as yf

        out: list[NewsItem] = []
        try:
            items = yf.Ticker(ticker).news or []
        except Exception as exc:  # noqa: BLE001
            log.warning("%s news failed: %s", ticker, exc)
            return out
        for n in items[:limit]:
            c = n.get("content", n) if isinstance(n, dict) else {}
            title = c.get("title") or n.get("title")
            url = ""
            if isinstance(c.get("canonicalUrl"), dict):
                url = c["canonicalUrl"].get("url", "")
            elif isinstance(c.get("clickThroughUrl"), dict):
                url = c["clickThroughUrl"].get("url", "")
            url = url or n.get("link", "")
            if not title or not url:
                continue
            pub = None
            if c.get("pubDate"):
                try:
                    from datetime import datetime
                    pub = datetime.fromisoformat(str(c["pubDate"]).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    pub = None
            elif n.get("providerPublishTime"):
                pub = float(n["providerPublishTime"])
            provider_name = ""
            if isinstance(c.get("provider"), dict):
                provider_name = c["provider"].get("displayName", "")
            provider_name = provider_name or n.get("publisher", "") or "Yahoo Finance"
            nid = hashlib.sha1(url.encode()).hexdigest()[:16]
            out.append(NewsItem(id=nid, ticker=ticker.upper(), title=str(title)[:300], url=url, source=provider_name,
                                provider=NAME, published_at=pub, summary=str(c.get("summary") or "")[:600]))
        return out
