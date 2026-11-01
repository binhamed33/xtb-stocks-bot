"""Scheduler + message composer.

Events on each NYSE trading day (all times ET, shown to the user in their own timezone too):
  pre-market brief   OPEN - PREMARKET_MINUTES_BEFORE_OPEN   buy / sell / avoid / holdings / events
  market open        09:30                                  short alert
  midday update      MIDDAY_HOUR_ET (optional)              how today's ideas are doing
  market close       16:00 (13:00 early days)               closing summary + idea performance
Weekends / holidays: one "market closed today" note in the morning (user time).
Sent events are recorded per date so a restart never double-posts.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import market_calendar as cal
from .analyst import Analyst, DailyBrief, Pick
from .config import Settings
from .data import StockSnapshot, fetch_quotes, fetch_universe, index_overview
from .halal import screen
from .notify import BLUE, GREEN, GREY, ORANGE, PURPLE, RED, Discord

FOOTER = "أسهم فعلية فقط (لا CFD ولا رافعة). أبحاث آلية وليست نصيحة استثمارية. الأسعار من Yahoo Finance."

log = logging.getLogger(__name__)


@dataclass
class State:
    sent: dict[str, list[str]] = field(default_factory=dict)      # date -> [event names]
    ideas: dict[str, list[dict]] = field(default_factory=dict)    # date -> picks with entry price
    open_ideas: list[dict] = field(default_factory=list)          # BUY ideas not yet resolved

    @classmethod
    def load(cls, p: Path) -> "State":
        if not p.exists():
            return cls()
        d = json.loads(p.read_text())
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, p: Path) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1))
        tmp.replace(p)


def _money(x: float | None) -> str:
    if x is None:
        return "-"
    if x >= 1e12:
        return f"{x / 1e12:.2f}T"
    if x >= 1e9:
        return f"{x / 1e9:.1f}B"
    return f"{x:,.0f}"


class StocksBot:
    def __init__(self, settings: Settings, discord: Discord, analyst: Analyst, state: State,
                 now_fn=lambda: datetime.now(timezone.utc)) -> None:  # noqa: ANN001
        self.s = settings
        self.discord = discord
        self.analyst = analyst
        self.state = state
        self.now = now_fn
        self.stop_event = threading.Event()
        self._snaps_cache: tuple[str, list[StockSnapshot]] | None = None

    # ---- helpers ---------------------------------------------------------
    def _t(self, dt: datetime) -> str:
        et = dt.astimezone(cal.ET).strftime("%H:%M")
        local = dt.astimezone(self.s.user_tz).strftime("%H:%M")
        return f"{local} بتوقيتك ({et} نيويورك)"

    @staticmethod
    def _countdown(until: datetime, now: datetime) -> str:
        secs = max(0, int((until - now).total_seconds()))
        h, m = divmod(secs // 60, 60)
        return f"{h} ساعة و{m} دقيقة" if h else f"{m} دقيقة"

    def _apply_halal(self, snaps: list[StockSnapshot]) -> tuple[list[StockSnapshot], list[StockSnapshot]]:
        """Stamp every snapshot with its verdict; return (allowed, rejected)."""
        allowed, rejected = [], []
        for sn in snaps:
            v = screen(sn.ticker, sn.sector, sn.industry, sn.market_cap, sn.total_debt, sn.total_cash,
                       self.s.halal_whitelist, self.s.halal_blacklist)
            sn.halal_status, sn.halal_note = v.status, v.label()
            if self.s.halal_only and not v.ok:
                rejected.append(sn)
            else:
                allowed.append(sn)
        return allowed, rejected

    def _mark(self, day: str, event: str) -> None:
        self.state.sent.setdefault(day, []).append(event)
        self.state.save(self.s.state_file)

    def _done(self, day: str, event: str) -> bool:
        return event in self.state.sent.get(day, [])

    # ---- main loop -------------------------------------------------------
    def run_forever(self) -> None:
        log.info("Stocks bot running; universe=%d tickers", len(self.s.universe))
        self.discord.embed("🤖 بوت الأسهم اشتغل", fields=[
            ("الأسهم المراقبة", f"{len(self.s.universe)}", True),
            ("التنبيه قبل الفتح", f"{self.s.premarket_minutes_before_open} دقيقة", True),
            ("توقيتك", str(self.s.user_tz.key), True)], color=BLUE)
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            self.stop_event.wait(60)

    def tick(self) -> None:
        now = self.now()
        et = now.astimezone(cal.ET)
        day = et.date().isoformat()
        sess = cal.session(et.date())
        if sess is None:
            # closed day: one morning note in user's timezone
            local = now.astimezone(self.s.user_tz)
            if local.hour >= 9 and not self._done(day, "closed"):
                nxt = cal.next_session(now)
                self.discord.embed("📴 السوق الأمريكي مغلق اليوم",
                                   description=f"الافتتاح القادم: {nxt[0].astimezone(self.s.user_tz):%A %Y-%m-%d} الساعة {self._t(nxt[0])}",
                                   color=GREY)
                self._mark(day, "closed")
            return
        open_dt, close_dt = sess
        pre_dt = open_dt - timedelta(minutes=self.s.premarket_minutes_before_open)
        mid_dt = open_dt.replace(hour=self.s.midday_hour_et, minute=0)
        local = now.astimezone(self.s.user_tz)

        if (local.hour >= self.s.morning_hour_local and now < pre_dt and not self._done(day, "morning")):
            self.discord.embed("⏰ السوق الأمريكي يفتح اليوم",
                               description=f"يفتح بعد **{self._countdown(open_dt, now)}** ({self._t(open_dt)})\n"
                                           f"النشرة تصلك قبل الفتح بـ {self.s.premarket_minutes_before_open} دقيقة.",
                               color=GREY)
            self._mark(day, "morning")
        if now >= pre_dt and not self._done(day, "premarket"):
            self.send_premarket(day, open_dt, close_dt)
        if now >= open_dt and not self._done(day, "open"):
            self.send_open(day, close_dt)
        if self.s.send_midday and mid_dt < close_dt and now >= mid_dt and not self._done(day, "midday"):
            self.send_midday(day)
        if now >= close_dt and not self._done(day, "close"):
            self.send_close(day, close_dt)

    # ---- events ----------------------------------------------------------
    def send_premarket(self, day: str, open_dt: datetime, close_dt: datetime) -> None:
        log.info("Building pre-market brief for %s", day)
        snaps = fetch_universe(list(self.s.universe))
        if not snaps:
            self.discord.embed("⚠️ تعذر جلب بيانات الأسهم", color=ORANGE)
            return
        snaps, rejected = self._apply_halal(snaps)
        self._snaps_cache = (day, snaps)
        indices = index_overview()
        brief = self.analyst.daily_brief(snaps, indices, day, self.state.open_ideas)
        self._last_rejected = rejected
        price = {s.ticker: s.price for s in snaps}

        # record today's ideas for performance tracking
        tracked = list(brief.buys)
        if brief.top_idea and brief.top_idea.action == "BUY" and all(p.ticker != brief.top_idea.ticker for p in tracked):
            tracked.insert(0, brief.top_idea)
        ideas = [{"ticker": p.ticker, "action": p.action, "entry": price.get(p.ticker), "stop": p.stop_loss,
                  "target": p.target, "date": day} for p in tracked if p.stop_loss and p.target]
        self.state.ideas[day] = ideas
        for i in ideas:
            if i["entry"] and not any(o["ticker"] == i["ticker"] for o in self.state.open_ideas):
                self.state.open_ideas.append(i)
        self._mark(day, "premarket")

        self._post_brief(brief, snaps, indices, open_dt, close_dt, day)

    def _post_brief(self, brief: DailyBrief, snaps: list[StockSnapshot], indices: dict,
                    open_dt: datetime, close_dt: datetime, day: str) -> None:
        by = {s.ticker: s for s in snaps}
        idx = " | ".join(f"{k.replace('_chg_pct', '')} {v:+.2f}%" for k, v in indices.items() if k.endswith("_chg_pct"))
        embeds: list[dict] = [{
            "title": f"🌅 نشرة ما قبل الافتتاح — {day}",
            "color": BLUE,
            "description": brief.market_mood[:2000],
            "fields": [
                {"name": "يفتح السوق", "value": self._t(open_dt), "inline": True},
                {"name": "يغلق", "value": self._t(close_dt), "inline": True},
                {"name": "المؤشرات (أمس)", "value": idx or "-", "inline": False},
                {"name": "أحداث مهمة اليوم", "value": "\n".join(f"• {e}" for e in brief.key_events[:6]) or "-", "inline": False},
                {"name": "🕌 فحص الحلال", "value": self._halal_summary(snaps), "inline": False},
            ],
        }]
        if brief.top_idea:
            embeds.append(self._pick_embed(brief.top_idea, by, "⭐ أفضل فكرة اليوم", PURPLE))
        for p in brief.buys:
            if brief.top_idea and p.ticker == brief.top_idea.ticker:
                continue
            embeds.append(self._pick_embed(p, by, "🟢 شراء", GREEN))
        if brief.sells:
            embeds.append({"title": "🔴 بيع / جني أرباح", "color": RED,
                           "fields": [{"name": f"{p.ticker} ({p.conviction:.0%})", "value": p.reason[:1000], "inline": False} for p in brief.sells]})
        if brief.avoid:
            embeds.append({"title": "⛔ لا تشترِ اليوم", "color": ORANGE,
                           "fields": [{"name": p.ticker, "value": (p.reason + (f"\n⚠️ {p.risk_note}" if p.risk_note else ""))[:1000], "inline": False} for p in brief.avoid]})
        if brief.holdings_advice:
            embeds.append({"title": "💼 محفظتك", "color": GREY,
                           "fields": [{"name": f"{p.ticker}: {'احتفظ' if p.action == 'HOLD' else 'بع'}", "value": p.reason[:1000], "inline": False} for p in brief.holdings_advice]})
        embeds.append({"description": FOOTER, "color": GREY})
        self.discord.embeds(embeds)

    def _halal_summary(self, snaps: list[StockSnapshot]) -> str:
        rejected = getattr(self, "_last_rejected", [])
        passed = sum(1 for x in snaps if x.halal_status == "pass")
        unknown = [x.ticker for x in snaps if x.halal_status != "pass"]
        txt = f"{passed} سهماً اجتاز فحص AAOIFI (نشاط مباح، دين < 33%، نقد < 33%)."
        if rejected:
            txt += "\nمستبعد: " + "، ".join(f"{x.ticker} ({x.halal_note.split(': ', 1)[-1]})" for x in rejected[:8])
        if unknown:
            txt += "\nلم يتأكد: " + "، ".join(unknown[:8])
        return txt[:1000]

    def _pick_embed(self, p: Pick, by: dict[str, StockSnapshot], label: str, color: int) -> dict:
        s = by.get(p.ticker)
        fields = []
        if s:
            fields += [{"name": "السعر", "value": f"{s.price:.2f}", "inline": True},
                       {"name": "اليوم / الشهر", "value": f"{(s.change_1d_pct or 0):+.1f}% / {(s.change_1m_pct or 0):+.1f}%", "inline": True},
                       {"name": "RSI", "value": f"{s.rsi14:.0f}" if s.rsi14 else "-", "inline": True}]
        fields += [{"name": "منطقة الدخول", "value": p.entry_zone or "سعر السوق", "inline": True}]
        if p.stop_loss and p.target and s and s.price:
            risk = s.price - p.stop_loss
            rr = (p.target - s.price) / risk if risk > 0 else 0
            risk_usd = self.s.portfolio_size * self.s.risk_per_trade_pct / 100
            shares = int(risk_usd / risk) if risk > 0 else 0
            fields += [{"name": "وقف الخسارة", "value": f"{p.stop_loss:.2f} ({(p.stop_loss / s.price - 1) * 100:+.1f}%)", "inline": True},
                       {"name": "الهدف", "value": f"{p.target:.2f} ({(p.target / s.price - 1) * 100:+.1f}%)", "inline": True},
                       {"name": "عائد/مخاطرة", "value": f"{rr:.1f} : 1", "inline": True},
                       {"name": "عدد الأسهم المقترح", "value": f"{shares} (مخاطرة {risk_usd:.0f}$ من {self.s.portfolio_size:,.0f}$)", "inline": True}]
        fields += [{"name": "المدة", "value": p.horizon or "-", "inline": True},
                   {"name": "الثقة", "value": f"{p.conviction:.0%}", "inline": True},
                   {"name": "🕌 الحلال", "value": (s.halal_note if s and s.halal_note else "❓ لم يُفحص")[:1024], "inline": False},
                   {"name": "السبب", "value": p.reason[:1000], "inline": False}]
        if p.risk_note:
            fields.append({"name": "⚠️ ما يلغي الفكرة", "value": p.risk_note[:1000], "inline": False})
        if s and s.days_to_earnings is not None and 0 <= s.days_to_earnings <= 10:
            fields.append({"name": "📊 نتائج الأرباح", "value": f"بعد {s.days_to_earnings} يوم ({s.earnings_date})", "inline": False})
        title = f"{label}: {p.ticker}" + (f" — {s.name}" if s and s.name and s.name != p.ticker else "")
        return {"title": title[:256], "color": color, "fields": fields}

    def send_open(self, day: str, close_dt: datetime) -> None:
        now = self.now()
        self.discord.embed("🔔 السوق الأمريكي فتح الآن",
                           description=f"يغلق بعد **{self._countdown(close_dt, now)}** ({self._t(close_dt)})", color=GREEN)
        self._mark(day, "open")

    def _ideas_performance(self, day: str) -> list[dict]:
        ideas = self.state.ideas.get(day, [])
        if not ideas:
            return []
        quotes = fetch_quotes([i["ticker"] for i in ideas])
        out = []
        for i in ideas:
            q = quotes.get(i["ticker"])
            if q and i.get("entry"):
                out.append({**i, "now": q, "change_pct": round((q / i["entry"] - 1) * 100, 2),
                            "hit_stop": bool(i.get("stop") and q <= i["stop"]),
                            "hit_target": bool(i.get("target") and q >= i["target"])})
        return out

    def _perf_lines(self, perf: list[dict]) -> str:
        if not perf:
            return "لا توجد أفكار شراء اليوم"
        lines = []
        for p in perf:
            flag = "🎯" if p["hit_target"] else ("🛑" if p["hit_stop"] else ("🟢" if p["change_pct"] >= 0 else "🔴"))
            lines.append(f"{flag} **{p['ticker']}** {p['entry']:.2f} → {p['now']:.2f} ({p['change_pct']:+.2f}%)")
        return "\n".join(lines)

    def send_midday(self, day: str) -> None:
        perf = self._ideas_performance(day)
        self.discord.embed("🕛 تحديث منتصف الجلسة", description=self._perf_lines(perf), color=BLUE)
        self._mark(day, "midday")

    def send_close(self, day: str, close_dt: datetime) -> None:
        perf = self._ideas_performance(day)
        indices = index_overview()
        # resolve open ideas that hit stop/target
        quotes = fetch_quotes([o["ticker"] for o in self.state.open_ideas]) if self.state.open_ideas else {}
        resolved, still = [], []
        for o in self.state.open_ideas:
            q = quotes.get(o["ticker"])
            if q and ((o.get("stop") and q <= o["stop"]) or (o.get("target") and q >= o["target"])):
                resolved.append({**o, "exit": q, "result_pct": round((q / o["entry"] - 1) * 100, 2)})
            else:
                still.append(o)
        self.state.open_ideas = still
        movers = self._movers()
        try:
            summary = self.analyst.close_summary(indices, perf, movers, day)
            text, lessons, watch = summary.summary, summary.lessons, summary.tomorrow_watch
        except Exception as exc:  # noqa: BLE001
            log.warning("close summary failed: %s", exc)
            text, lessons, watch = "تعذر توليد الملخص النصي.", [], []
        idx = " | ".join(f"{k.replace('_chg_pct', '')} {v:+.2f}%" for k, v in indices.items() if k.endswith("_chg_pct"))
        fields = [("المؤشرات", idx or "-", False), ("أداء أفكار اليوم", self._perf_lines(perf), False)]
        if resolved:
            fields.append(("أفكار سابقة وصلت الوقف/الهدف", "\n".join(
                f"{'🎯' if r['result_pct'] > 0 else '🛑'} {r['ticker']} من {r['date']}: {r['result_pct']:+.2f}%" for r in resolved), False))
        if movers:
            fields.append(("أكبر التحركات في قائمتك", "\n".join(f"{m['ticker']} {m['change_pct']:+.2f}%" for m in movers[:8]), False))
        if lessons:
            fields.append(("دروس اليوم", "\n".join(f"• {x}" for x in lessons), False))
        if watch:
            fields.append(("راقب غداً", "\n".join(f"• {x}" for x in watch), False))
        nxt = cal.next_session(self.now() + timedelta(minutes=1))
        fields.append(("الافتتاح القادم", f"{nxt[0].astimezone(self.s.user_tz):%A %Y-%m-%d} الساعة {self._t(nxt[0])}", False))
        self.discord.embed(f"🔕 السوق أغلق — {day}", description=text, fields=fields,
                           color=RED if "-" in idx.split("|")[0] else GREEN, footer=FOOTER)
        self._mark(day, "close")

    def _movers(self) -> list[dict]:
        quotes_now = fetch_quotes(list(self.s.universe))
        snaps = self._snaps_cache[1] if self._snaps_cache else []
        prev = {s.ticker: s.price for s in snaps}
        out = []
        for t, q in quotes_now.items():
            p = prev.get(t)
            if p:
                out.append({"ticker": t, "change_pct": round((q / p - 1) * 100, 2)})
        out.sort(key=lambda m: abs(m["change_pct"]), reverse=True)
        return out

    # ---- on-demand -------------------------------------------------------
    def brief_now(self) -> None:
        now = self.now()
        nxt = cal.next_session(now)
        day = now.astimezone(cal.ET).date().isoformat()
        snaps = fetch_universe(list(self.s.universe))
        snaps, self._last_rejected = self._apply_halal(snaps)
        indices = index_overview()
        brief = self.analyst.daily_brief(snaps, indices, day, self.state.open_ideas)
        self._post_brief(brief, snaps, indices, nxt[0], nxt[1], day)
