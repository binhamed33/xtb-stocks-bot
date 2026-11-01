"""Real-stock verification for XTB.

XTB has no public API (xAPI was discontinued in March 2025) and does not publish
a machine-readable instrument list, so availability cannot be confirmed
automatically. This registry therefore holds *evidence*, never assumptions:

  REAL_STOCK     confirmed cash equity on XTB (manual confirmation or imported list)
  CFD_ONLY       the user confirmed only a CFD exists for this name
  NOT_AVAILABLE  confirmed not tradable on XTB
  NOT_A_STOCK    the instrument is not a company share (ETF, crypto, index, future)
  UNVERIFIED     nothing confirmed yet (default)

Only REAL_STOCK counts as investable. The optional heuristic (`XTB_TRUST_HEURISTIC=true`)
upgrades 'EQUITY listed on an exchange XTB serves for real shares' to REAL_STOCK with
the source clearly labelled as a heuristic, so the user can decide to accept that risk.
"""

from __future__ import annotations

import csv
import io
import os
import time
from dataclasses import dataclass

from ..db import Database
from ..providers.base import Fundamentals, Quote

REAL_STOCK, CFD_ONLY, NOT_AVAILABLE, NOT_A_STOCK, UNVERIFIED = (
    "REAL_STOCK", "CFD_ONLY", "NOT_AVAILABLE", "NOT_A_STOCK", "UNVERIFIED")

# Exchanges where XTB offers real shares (per XTB's public product pages; verify for your entity).
XTB_STOCK_EXCHANGES = {
    # Yahoo exchange codes
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE", "ASE": "NYSE American", "PCX": "NYSE Arca",
    "LSE": "London", "GER": "Xetra", "FRA": "Frankfurt", "PAR": "Euronext Paris", "AMS": "Euronext Amsterdam",
    "BRU": "Euronext Brussels", "LIS": "Euronext Lisbon", "MIL": "Borsa Italiana", "MCE": "BME Madrid",
    "EBS": "SIX Swiss", "STO": "Nasdaq Stockholm", "HEL": "Nasdaq Helsinki", "CPH": "Nasdaq Copenhagen",
    "OSL": "Oslo", "WSE": "Warsaw", "VIE": "Vienna", "PRA": "Prague",
}
CFD_LIKE_TYPES = {"INDEX", "FUTURE", "CURRENCY", "CRYPTOCURRENCY", "COMMODITY", "OPTION"}


@dataclass
class XtbStatus:
    ticker: str
    status: str
    instrument_type: str = ""
    market: str = ""
    currency: str = ""
    min_qty: float | None = None
    fee_note: str = ""
    source: str = ""
    verified_at: float | None = None
    note: str = ""

    @property
    def investable(self) -> bool:
        return self.status == REAL_STOCK

    def label(self) -> str:
        icon = {REAL_STOCK: "✅", CFD_ONLY: "⛔", NOT_AVAILABLE: "⛔", NOT_A_STOCK: "⛔", UNVERIFIED: "❓"}[self.status]
        ar = {REAL_STOCK: "سهم حقيقي متاح في XTB", CFD_ONLY: "متاح كـ CFD فقط (مستبعد)", NOT_AVAILABLE: "غير متاح في XTB",
              NOT_A_STOCK: "ليس سهم شركة (مستبعد)", UNVERIFIED: "لم يُتحقق من توفره كسهم حقيقي في XTB"}[self.status]
        s = f"{icon} {ar}"
        if self.market:
            s += f" · {self.market}"
        if self.currency:
            s += f" · {self.currency}"
        if self.source:
            s += f" · المصدر: {self.source}"
        if self.verified_at:
            s += f" · {time.strftime('%Y-%m-%d', time.gmtime(self.verified_at))}"
        return s


class XtbRegistry:
    def __init__(self, db: Database, trust_heuristic: bool | None = None) -> None:
        self.db = db
        self.trust_heuristic = (os.environ.get("XTB_TRUST_HEURISTIC", "false").lower() in {"1", "true", "yes"}
                                if trust_heuristic is None else trust_heuristic)

    def get(self, ticker: str) -> XtbStatus | None:
        row = self.db.one("SELECT * FROM xtb_instruments WHERE ticker=?", (ticker.upper(),))
        if not row:
            return None
        return XtbStatus(ticker=row["ticker"], status=row["status"], instrument_type=row["instrument_type"] or "",
                         market=row["market"] or "", currency=row["currency"] or "", min_qty=row["min_qty"],
                         fee_note=row["fee_note"] or "", source=row["source"], verified_at=row["verified_at"])

    def set(self, ticker: str, status: str, source: str, instrument_type: str = "", market: str = "",
            currency: str = "", min_qty: float | None = None, fee_note: str = "") -> XtbStatus:
        if status not in (REAL_STOCK, CFD_ONLY, NOT_AVAILABLE, NOT_A_STOCK, UNVERIFIED):
            raise ValueError(f"unknown status {status}")
        self.db.x("INSERT OR REPLACE INTO xtb_instruments(ticker,status,instrument_type,market,currency,min_qty,fee_note,source,verified_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (ticker.upper(), status, instrument_type, market, currency, min_qty, fee_note, source, time.time()))
        return self.get(ticker.upper())  # type: ignore[return-value]

    def clear(self, ticker: str) -> None:
        self.db.x("DELETE FROM xtb_instruments WHERE ticker=?", (ticker.upper(),))

    def all(self) -> list[XtbStatus]:
        return [self.get(r["ticker"]) for r in self.db.q("SELECT ticker FROM xtb_instruments ORDER BY ticker")]  # type: ignore[misc]

    def import_csv(self, text: str, source: str = "xStation export") -> int:
        """Import 'symbol,type[,market,currency,min_qty]' rows. type: STOCK|STC|CFD|ETF ... Returns rows imported."""
        n = 0
        for row in csv.DictReader(io.StringIO(text)):
            sym = (row.get("symbol") or row.get("ticker") or row.get("Symbol") or "").strip().upper()
            typ = (row.get("type") or row.get("Type") or row.get("category") or "").strip().upper()
            if not sym:
                continue
            sym = sym.split(".")[0]  # XTB uses e.g. AAPL.US
            if typ in {"STOCK", "STC", "STOCKS", "SHARE", "SHARES", "EQUITY"}:
                status = REAL_STOCK
            elif typ in {"CFD", "STOCK CFD", "STC CFD"}:
                status = CFD_ONLY
            elif typ in {"ETF", "ETC", "INDEX", "IND", "FX", "CMD", "CRYPTO"}:
                status = NOT_A_STOCK
            else:
                status = UNVERIFIED
            existing = self.get(sym)
            if existing and existing.status == REAL_STOCK and status == CFD_ONLY:
                continue  # a real stock listing beats a CFD listing for the same name
            self.set(sym, status, source, instrument_type=typ, market=(row.get("market") or "").strip(),
                     currency=(row.get("currency") or "").strip().upper(),
                     min_qty=float(row["min_qty"]) if row.get("min_qty") else None)
            n += 1
        return n

    def resolve(self, ticker: str, quote: Quote | None = None, fund: Fundamentals | None = None) -> XtbStatus:
        """Registry first; otherwise classify from instrument data without claiming availability."""
        known = self.get(ticker)
        if known:
            return known
        qt = (quote.quote_type if quote else "") or ""
        if fund and fund.description.startswith("quoteType="):
            qt = fund.description.split("=", 1)[1]
        if qt and qt.upper() != "EQUITY":
            return XtbStatus(ticker.upper(), NOT_A_STOCK, instrument_type=qt.upper(), source="instrument type from data provider",
                             note="not a company share")
        exch = (quote.exchange if quote else "") or (fund.exchange if fund else "")
        market = XTB_STOCK_EXCHANGES.get(exch, "")
        cur = (quote.currency if quote else "") or (fund.currency if fund else "")
        if self.trust_heuristic and market:
            return XtbStatus(ticker.upper(), REAL_STOCK, instrument_type="EQUITY", market=market, currency=cur,
                             source="heuristic: equity on an exchange XTB serves for real shares (XTB_TRUST_HEURISTIC=true)",
                             note="not confirmed with XTB")
        note = (f"listed on {market}: XTB usually offers such names as real shares, but availability for your account "
                f"is unconfirmed. Confirm with /xtb verify {ticker.upper()} after checking xStation."
                if market else "exchange not in XTB's real-share coverage list, or unknown")
        return XtbStatus(ticker.upper(), UNVERIFIED, instrument_type="EQUITY" if qt else "", market=market, currency=cur,
                         source="", note=note)
