"""Market data via yfinance: prices, basic fundamentals, upcoming earnings, headlines."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .indicators import pct, rsi, sma

log = logging.getLogger(__name__)


@dataclass
class StockSnapshot:
    ticker: str
    name: str = ""
    sector: str = ""
    price: float = 0.0
    prev_close: float = 0.0
    change_1d_pct: float | None = None
    change_5d_pct: float | None = None
    change_1m_pct: float | None = None
    change_3m_pct: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    rsi14: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    from_52w_high_pct: float | None = None
    avg_volume_20d: float | None = None
    last_volume: float | None = None
    volume_ratio: float | None = None
    atr14_pct: float | None = None
    pe: float | None = None
    forward_pe: float | None = None
    market_cap: float | None = None
    dividend_yield_pct: float | None = None
    earnings_date: str | None = None
    days_to_earnings: int | None = None
    headlines: list[str] = field(default_factory=list)

    def as_prompt_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if v not in (None, "", [])}
        for k, v in list(d.items()):
            if isinstance(v, float):
                d[k] = round(v, 2)
        return d


def _clean(x: Any) -> float | None:
    try:
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _flatten_columns(df: Any) -> Any:
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


def fetch_history(tickers: list[str], period: str = "1y") -> dict[str, list[dict[str, float]]]:
    """Daily OHLCV per ticker as list of dicts (oldest first)."""
    import yfinance as yf

    out: dict[str, list[dict[str, float]]] = {}
    df = yf.download(tickers, period=period, interval="1d", group_by="ticker", progress=False,
                     auto_adjust=True, threads=True)
    if df is None or df.empty:
        return out
    for t in tickers:
        try:
            sub = df[t] if len(tickers) > 1 else _flatten_columns(df)
        except KeyError:
            continue
        rows = []
        for ts, r in sub.dropna(subset=["Close"]).iterrows():
            rows.append({"ts": ts.to_pydatetime().replace(tzinfo=timezone.utc).timestamp(),
                         "open": float(r["Open"]), "high": float(r["High"]), "low": float(r["Low"]),
                         "close": float(r["Close"]), "volume": float(r.get("Volume", 0) or 0)})
        if rows:
            out[t] = rows
    return out


def snapshot_from_rows(ticker: str, rows: list[dict[str, float]]) -> StockSnapshot:
    closes = [r["close"] for r in rows]
    vols = [r["volume"] for r in rows]
    s = StockSnapshot(ticker=ticker, price=closes[-1], prev_close=closes[-2] if len(closes) > 1 else closes[-1])
    s.change_1d_pct = pct(closes[-1], s.prev_close)
    s.change_5d_pct = pct(closes[-1], closes[-6]) if len(closes) > 6 else None
    s.change_1m_pct = pct(closes[-1], closes[-22]) if len(closes) > 22 else None
    s.change_3m_pct = pct(closes[-1], closes[-64]) if len(closes) > 64 else None
    s.sma20, s.sma50, s.sma200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    s.rsi14 = rsi(closes, 14)
    year = rows[-252:]
    s.high_52w = max(r["high"] for r in year)
    s.low_52w = min(r["low"] for r in year)
    s.from_52w_high_pct = pct(closes[-1], s.high_52w)
    if len(vols) >= 21:
        s.avg_volume_20d = sum(vols[-21:-1]) / 20
        s.last_volume = vols[-1]
        s.volume_ratio = vols[-1] / s.avg_volume_20d if s.avg_volume_20d else None
    if len(rows) > 15:
        trs = [max(r["high"] - r["low"], abs(r["high"] - p["close"]), abs(r["low"] - p["close"]))
               for p, r in zip(rows[-15:-1], rows[-14:])]
        s.atr14_pct = (sum(trs) / 14) / closes[-1] * 100
    return s


def enrich_fundamentals(s: StockSnapshot, today: date | None = None) -> None:
    """Best-effort: yfinance .info and .calendar can fail or be slow; never raise."""
    import yfinance as yf

    today = today or datetime.now(timezone.utc).date()
    try:
        tk = yf.Ticker(s.ticker)
        info = tk.info or {}
        s.name = str(info.get("shortName") or info.get("longName") or s.ticker)
        s.sector = str(info.get("sector") or "")
        s.pe = _clean(info.get("trailingPE"))
        s.forward_pe = _clean(info.get("forwardPE"))
        s.market_cap = _clean(info.get("marketCap"))
        dy = _clean(info.get("dividendYield"))
        s.dividend_yield_pct = dy * 100 if dy is not None and dy < 1 else dy
        try:
            cal = tk.calendar or {}
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if ed:
                first = ed[0] if isinstance(ed, (list, tuple)) else ed
                d = first.date() if hasattr(first, "date") else first
                s.earnings_date = str(d)
                s.days_to_earnings = (d - today).days
        except Exception:  # noqa: BLE001
            pass
        try:
            news = tk.news or []
            heads = []
            for n in news[:5]:
                c = n.get("content", n)
                title = c.get("title") or n.get("title")
                if title:
                    heads.append(str(title)[:140])
            s.headlines = heads
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        log.debug("%s: fundamentals unavailable: %s", s.ticker, exc)


def fetch_universe(tickers: list[str], with_fundamentals: bool = True) -> list[StockSnapshot]:
    hist = fetch_history(tickers)
    snaps: list[StockSnapshot] = []
    for t in tickers:
        rows = hist.get(t)
        if not rows or len(rows) < 30:
            log.warning("%s: insufficient history, skipped", t)
            continue
        s = snapshot_from_rows(t, rows)
        if with_fundamentals:
            enrich_fundamentals(s)
            time.sleep(0.2)  # be polite to Yahoo
        snaps.append(s)
    return snaps


def fetch_quotes(tickers: list[str]) -> dict[str, float]:
    """Latest prices (intraday when the market is open)."""
    import yfinance as yf

    out: dict[str, float] = {}
    try:
        df = yf.download(tickers, period="5d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
        for t in tickers:
            try:
                sub = df[t] if len(tickers) > 1 else _flatten_columns(df)
                closes = sub["Close"].dropna()
                if len(closes):
                    out[t] = float(closes.iloc[-1])
            except KeyError:
                continue
    except Exception as exc:  # noqa: BLE001
        log.warning("quotes failed: %s", exc)
    return out


def index_overview() -> dict[str, float | None]:
    """S&P 500, Nasdaq, VIX, 10y yield, oil, gold: last daily change %."""
    symbols = {"SP500": "^GSPC", "NASDAQ": "^IXIC", "DOW": "^DJI", "VIX": "^VIX", "US10Y": "^TNX",
               "OIL": "CL=F", "GOLD": "GC=F", "BTC": "BTC-USD"}
    hist = fetch_history(list(symbols.values()), period="1mo")
    out: dict[str, float | None] = {}
    for name, sym in symbols.items():
        rows = hist.get(sym)
        if rows and len(rows) >= 2:
            out[f"{name}_last"] = round(rows[-1]["close"], 2)
            out[f"{name}_chg_pct"] = round(pct(rows[-1]["close"], rows[-2]["close"]) or 0.0, 2)
    return out
