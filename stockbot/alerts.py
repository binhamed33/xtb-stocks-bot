"""Alerts: user rules + automatic portfolio/watchlist monitoring, with de-duplication.

Kinds:
  drop      price fell >= threshold % vs previous close (default ALERT_DROP_PCT)
  target    price reached threshold (absolute)
  below     price fell below threshold (absolute)
  news      a material event (earnings/guidance/fda/lawsuit/acquisition/regulatory/management) published in the last 24h
  sharia    the Sharia status changed since the last screening
  earnings  earnings date within 3 days
  trend     medium-term trend flipped since the last check
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .db import Database

MATERIAL_EVENTS = {"earnings", "guidance", "fda", "lawsuit", "acquisition", "regulatory", "management"}


@dataclass
class AlertEvent:
    user_id: int
    ticker: str
    kind: str
    message: str
    dedupe_key: str


class Alerts:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- rules ---------------------------------------------------------------
    def add(self, user_id: int, ticker: str, kind: str, threshold: float | None = None) -> int:
        if kind not in {"drop", "target", "below", "news", "sharia", "earnings", "trend"}:
            raise ValueError("unknown alert kind")
        if kind in {"target", "below", "drop"} and threshold is None:
            raise ValueError(f"{kind} needs a threshold")
        return self.db.x("INSERT INTO alerts(user_id,ticker,kind,threshold,created_at) VALUES (?,?,?,?,?)",
                         (user_id, ticker.upper(), kind, threshold, time.time()))

    def remove(self, user_id: int, alert_id: int) -> int:
        return self.db.x("DELETE FROM alerts WHERE user_id=? AND id=?", (user_id, alert_id))

    def list(self, user_id: int) -> list[dict]:
        return [dict(r) for r in self.db.q("SELECT * FROM alerts WHERE user_id=? AND enabled=1 ORDER BY ticker, kind", (user_id,))]

    def all_rules(self) -> list[dict]:
        return [dict(r) for r in self.db.q("SELECT * FROM alerts WHERE enabled=1")]

    # ---- dedupe --------------------------------------------------------------
    def _fire(self, ev: AlertEvent) -> bool:
        if self.db.one("SELECT 1 FROM alert_log WHERE dedupe_key=?", (ev.dedupe_key,)):
            return False
        self.db.x("INSERT INTO alert_log(dedupe_key,user_id,ticker,kind,message,fired_at) VALUES (?,?,?,?,?,?)",
                  (ev.dedupe_key, ev.user_id, ev.ticker, ev.kind, ev.message, time.time()))
        return True

    # ---- evaluation ------------------------------------------------------------
    def evaluate(self, user_id: int, ticker: str, *, price: float | None, prev_close: float | None,
                 drop_pct_default: float, rules: list[dict], news_events: list[tuple[str, str, str]] | None = None,
                 sharia_status: str | None = None, earnings_date: str | None = None, trend_medium: str | None = None,
                 auto_monitor: bool = False, now: float | None = None) -> list[AlertEvent]:
        """Return the events that fired (already recorded for dedupe).

        news_events: (event_type, title, url) published within 24h.
        auto_monitor: apply default drop/news/sharia/earnings monitoring (portfolio & watchlist).
        """
        now = now or time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        out: list[AlertEvent] = []
        t = ticker.upper()

        def emit(kind: str, msg: str, key_suffix: str) -> None:
            ev = AlertEvent(user_id, t, kind, msg, f"{user_id}:{t}:{kind}:{key_suffix}")
            if self._fire(ev):
                out.append(ev)

        kinds = {r["kind"]: r for r in rules}
        if price is not None and prev_close:
            chg = (price / prev_close - 1) * 100
            thr = kinds["drop"]["threshold"] if "drop" in kinds else (drop_pct_default if auto_monitor else None)
            if thr is not None and chg <= -thr:
                emit("drop", f"📉 {t} هبط {chg:.1f}% اليوم إلى {price:.2f} (الحد {thr:.0f}%)", day)
        if price is not None:
            if "target" in kinds and price >= kinds["target"]["threshold"]:
                emit("target", f"🎯 {t} وصل الهدف {kinds['target']['threshold']:.2f} (السعر {price:.2f})", f"{kinds['target']['threshold']}")
            if "below" in kinds and price <= kinds["below"]["threshold"]:
                emit("below", f"⚠️ {t} كسر {kinds['below']['threshold']:.2f} (السعر {price:.2f})", f"{kinds['below']['threshold']}")
        if (auto_monitor or "news" in kinds) and news_events:
            for ev_type, title, url in news_events:
                if ev_type in MATERIAL_EVENTS:
                    emit("news", f"📰 {t}: خبر جوهري ({ev_type}) — [{title}]({url})", url)
        if (auto_monitor or "sharia" in kinds) and sharia_status:
            last = self.db.kv_get(f"sharia_last:{user_id}:{t}")
            if last and last != sharia_status:
                emit("sharia", f"🕌 {t}: تغيّر التصنيف الشرعي من {last} إلى {sharia_status}", f"{last}->{sharia_status}")
            self.db.kv_set(f"sharia_last:{user_id}:{t}", sharia_status)
        if (auto_monitor or "earnings" in kinds) and earnings_date:
            try:
                ed = time.mktime(time.strptime(earnings_date, "%Y-%m-%d"))
                days = (ed - now) / 86400
                if 0 <= days <= 3:
                    emit("earnings", f"📊 {t}: نتائج الأرباح خلال {int(days) + 1} يوم ({earnings_date})", earnings_date)
            except ValueError:
                pass
        if (auto_monitor or "trend" in kinds) and trend_medium:
            last = self.db.kv_get(f"trend_last:{user_id}:{t}")
            if last and last != trend_medium and trend_medium in ("up", "down"):
                emit("trend", f"📈 {t}: الاتجاه المتوسط تغيّر من {last} إلى {trend_medium}", f"{last}->{trend_medium}:{day}")
            self.db.kv_set(f"trend_last:{user_id}:{t}", trend_medium)
        return out
