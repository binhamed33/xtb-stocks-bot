"""Portfolio manager: manual positions per Discord user, valuation, allocation, risk, Sharia status."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .db import Database


@dataclass
class Position:
    id: int
    user_id: int
    ticker: str
    qty: float
    avg_price: float
    currency: str
    bought_at: str | None
    invested: float | None
    note: str
    # valuation (filled by value())
    price: float | None = None
    value: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    weight: float | None = None
    sharia: str = ""
    price_source: str = ""


@dataclass
class PortfolioView:
    positions: list[Position]
    total_value: float
    total_cost: float
    total_pnl: float
    total_pnl_pct: float
    risk_level: str
    risk_notes: list[str] = field(default_factory=list)
    allocation: dict[str, float] = field(default_factory=dict)   # sector -> weight


class Portfolio:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, user_id: int, ticker: str, qty: float, avg_price: float, currency: str = "USD",
            bought_at: str | None = None, invested: float | None = None, note: str = "") -> int:
        if qty <= 0 or avg_price <= 0:
            raise ValueError("qty and avg_price must be positive")
        return self.db.x("INSERT INTO portfolio(user_id,ticker,qty,avg_price,currency,bought_at,invested,note,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (user_id, ticker.upper(), qty, avg_price, currency.upper(), bought_at,
                          invested if invested is not None else qty * avg_price, note, time.time()))

    def remove(self, user_id: int, ticker: str) -> int:
        return self.db.x("DELETE FROM portfolio WHERE user_id=? AND ticker=?", (user_id, ticker.upper()))

    def positions(self, user_id: int) -> list[Position]:
        rows = self.db.q("SELECT * FROM portfolio WHERE user_id=? ORDER BY ticker", (user_id,))
        # merge duplicate tickers into one weighted position
        merged: dict[str, Position] = {}
        for r in rows:
            p = Position(r["id"], r["user_id"], r["ticker"], r["qty"], r["avg_price"], r["currency"], r["bought_at"],
                         r["invested"], r["note"] or "")
            if p.ticker in merged:
                m = merged[p.ticker]
                cost = m.qty * m.avg_price + p.qty * p.avg_price
                m.qty += p.qty
                m.avg_price = cost / m.qty
                m.invested = (m.invested or 0) + (p.invested or 0)
            else:
                merged[p.ticker] = p
        return list(merged.values())

    def tickers(self, user_id: int) -> list[str]:
        return [p.ticker for p in self.positions(user_id)]

    def value(self, user_id: int, quotes: dict[str, tuple[float, str]], sectors: dict[str, str] | None = None,
              sharia: dict[str, str] | None = None, volatility: dict[str, float] | None = None) -> PortfolioView:
        """quotes: ticker -> (price, source). Prices assumed in the position's currency."""
        pos = self.positions(user_id)
        total_value = total_cost = 0.0
        for p in pos:
            q = quotes.get(p.ticker)
            p.sharia = (sharia or {}).get(p.ticker, "")
            if q:
                p.price, p.price_source = q
                p.value = p.qty * p.price
                p.pnl = p.value - p.qty * p.avg_price
                p.pnl_pct = (p.price / p.avg_price - 1) * 100
                total_value += p.value
            total_cost += p.qty * p.avg_price
        alloc: dict[str, float] = {}
        notes: list[str] = []
        for p in pos:
            if p.value is not None and total_value > 0:
                p.weight = p.value / total_value
                sec = (sectors or {}).get(p.ticker, "غير معروف")
                alloc[sec] = alloc.get(sec, 0) + p.weight
        # risk: concentration + volatility + non-halal exposure
        risk = "low"
        if pos:
            top = max((p.weight or 0) for p in pos)
            if top > 0.5:
                risk = "high"; notes.append(f"تركّز مرتفع: مركز واحد يشكل {top:.0%} من المحفظة")
            elif top > 0.3:
                risk = "medium"; notes.append(f"تركّز متوسط: أكبر مركز {top:.0%}")
            if alloc:
                sec_top = max(alloc.items(), key=lambda x: x[1])
                if sec_top[1] > 0.6:
                    risk = "high"; notes.append(f"قطاع واحد ({sec_top[0]}) يشكل {sec_top[1]:.0%}")
            if volatility:
                w_vol = sum((p.weight or 0) * volatility.get(p.ticker, 0.3) for p in pos)
                if w_vol > 0.45:
                    risk = "high"; notes.append(f"تقلب مرجح مرتفع {w_vol:.0%}")
                elif w_vol > 0.3 and risk == "low":
                    risk = "medium"
            bad = [p.ticker for p in pos if p.sharia and p.sharia != "HALAL"]
            if bad:
                notes.append("مراكز تحتاج مراجعة شرعية: " + "، ".join(bad))
        total_pnl = total_value - total_cost if total_value else 0.0
        return PortfolioView(positions=pos, total_value=total_value, total_cost=total_cost, total_pnl=total_pnl,
                             total_pnl_pct=(total_pnl / total_cost * 100) if total_cost else 0.0,
                             risk_level=risk, risk_notes=notes, allocation=alloc)


class Watchlist:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, user_id: int, ticker: str, note: str = "") -> None:
        self.db.x("INSERT OR REPLACE INTO watchlist(user_id,ticker,note,added_at) VALUES (?,?,?,?)",
                  (user_id, ticker.upper(), note, time.time()))

    def remove(self, user_id: int, ticker: str) -> int:
        return self.db.x("DELETE FROM watchlist WHERE user_id=? AND ticker=?", (user_id, ticker.upper()))

    def list(self, user_id: int) -> list[tuple[str, str]]:
        return [(r["ticker"], r["note"] or "") for r in self.db.q("SELECT ticker, note FROM watchlist WHERE user_id=? ORDER BY added_at", (user_id,))]

    def all_tickers(self) -> list[str]:
        return [r["ticker"] for r in self.db.q("SELECT DISTINCT ticker FROM watchlist")]
