"""FastAPI application for the web dashboard.

Same Engine, same SQLite, same user ids as Discord: a member who logs in with
Discord sees the portfolio / watchlist / alerts they created in the chat.

Auth modes (WEB_AUTH):
  discord  OAuth2 login with the Discord application; membership of DISCORD_GUILD_ID required when set
  code     a shared access code (WEB_ACCESS_CODE); one shared "guest" account for personal features
  open     no login (research only; personal features disabled)

Secrets never reach the browser: tokens are exchanged server-side and the
session is a signed, HttpOnly cookie holding only id / name / avatar / admin.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from .. import help as helptext
from .. import market_calendar as cal
from .. import render
from ..analysis import sharia as sh
from ..analysis import technical as ta
from ..config import Settings
from ..engine import Engine, StockReport
from ..providers.base import to_jsonable
from ..xtb import instruments as xi

log = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")
SESSION_COOKIE = "tdawl_session"
SESSION_MAX_AGE = 14 * 86400
DISCORD_API = "https://discord.com/api/v10"
GUEST_ID = 1  # shared account in "code" mode


# ---- helpers ------------------------------------------------------------------------
class RateLimiter:
    """Fixed-window per-key limiter (in memory; one process)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str, cost: int = 1) -> None:
        now = time.time()
        window = [t for t in self._hits.get(key, []) if now - t < 60]
        if len(window) + cost > self.per_minute:
            raise HTTPException(429, "طلبات كثيرة خلال دقيقة؛ انتظر قليلاً ثم أعد المحاولة")
        window.extend([now] * cost)
        self._hits[key] = window


def _clean_symbol(sym: str) -> str:
    s = (sym or "").strip().upper()
    if not s or len(s) > 12 or not all(c.isalnum() or c in ".-^=" for c in s):
        raise HTTPException(400, "رمز غير صالح")
    return s


def _chart(r: StockReport, n: int = 260) -> dict[str, Any]:
    c = r.candles.value[-n:] if r.candles and r.candles.value else []
    closes_all = [x.close for x in (r.candles.value or [])]
    out: dict[str, Any] = {"dates": [time.strftime("%Y-%m-%d", time.gmtime(x.ts)) for x in c],
                           "close": [round(x.close, 4) for x in c], "volume": [x.volume for x in c]}
    for p in (20, 50, 200):
        if len(closes_all) >= p:
            out[f"ema{p}"] = [round(v, 4) for v in ta.ema_series(closes_all, p)[-len(c):]]
    if r.tech:
        out["levels"] = {"supports": r.tech.supports, "resistances": r.tech.resistances, "stop": r.tech.stop,
                         "target1": r.tech.target1, "target2": r.tech.target2, "entry_low": r.tech.entry_low,
                         "entry_high": r.tech.entry_high}
    return out


def report_payload(engine: Engine, r: StockReport, full: bool = True) -> dict[str, Any]:
    q = r.quote.value if r.quote else None
    change = (q.price / q.prev_close - 1) * 100 if q and q.prev_close else None
    summary = {
        "ticker": r.ticker, "name": r.name, "price": r.price, "currency": r.currency, "change_pct": change,
        "sharia": r.sharia.status if r.sharia else "UNVERIFIED", "sharia_label": r.sharia.label() if r.sharia else "",
        "sharia_method": r.sharia.method if r.sharia else None,
        "xtb": r.xtb.status, "xtb_label": r.xtb.label(), "investable": r.investable,
        "score": r.opportunity.total, "parts": r.opportunity.parts, "category": r.opportunity.category,
        "category_ar": r.opportunity.category_ar(), "drop_pct": r.tech.from_52w_high_pct if r.tech else None,
        "tech_score": r.tech.score if r.tech else None, "trend": r.tech.summary_ar() if r.tech else "",
        "health": r.fund.health_score if r.fund else None, "growth": r.fund.growth_score if r.fund else None,
        "valuation": r.fund.valuation_score if r.fund else None, "risk": r.risk.level if r.risk else None,
        "sector": r.fundamentals.value.sector if r.fundamentals else "", "industry": r.fundamentals.value.industry if r.fundamentals else "",
        "exchange": r.fundamentals.value.exchange if r.fundamentals else (q.exchange if q else ""),
        "generated_at": r.generated_at, "sources": r.sources(), "warnings": r.warnings,
        "ai": bool(r.narrative),
    }
    embeds = render.full_report(r, engine.sharia_history(r.ticker)) if full else render.quick_analysis(r)
    return {"summary": summary, "embeds": embeds, "chart": _chart(r),
            "news": [{"title": c.item.title, "url": c.item.url, "source": c.item.source, "published_at": c.item.published_at,
                      "event": c.event_type, "event_ar": helptext_event(c.event_type), "sentiment": c.sentiment,
                      "impact": c.impact, "summary": c.item.summary} for c in r.news.items[:12]],
            "filings": [to_jsonable(f) for f in r.news.filings[:8]],
            "narrative": r.narrative.model_dump() if r.narrative else None,
            "news_digest": r.news_digest.model_dump() if r.news_digest else None}


def helptext_event(ev: str) -> str:
    from ..news.intelligence import EVENT_AR

    return EVENT_AR.get(ev, ev)


# ---- app factory ------------------------------------------------------------------------
class PortfolioIn(BaseModel):
    symbol: str
    qty: float = Field(gt=0)
    avg_price: float = Field(gt=0)
    currency: str = "USD"
    bought_at: str | None = None


class WatchIn(BaseModel):
    symbol: str
    note: str = ""


class AlertIn(BaseModel):
    symbol: str
    kind: str
    threshold: float | None = None


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    symbol: str | None = None


class CodeIn(BaseModel):
    code: str


class XtbIn(BaseModel):
    symbol: str
    status: str
    market: str = ""
    currency: str = ""


def create_app(settings: Settings, engine: Engine) -> FastAPI:
    app = FastAPI(title="تداول · أبحاث الأسهم الحلال", docs_url=None, redoc_url=None, openapi_url=None)
    signer = URLSafeTimedSerializer(settings.web_secret, salt="tdawl-session")
    limiter = RateLimiter(settings.web_rate_per_minute)
    oauth_states: dict[str, float] = {}

    # ---- session ----------------------------------------------------------------------
    def session_of(request: Request) -> dict[str, Any] | None:
        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            return None
        try:
            return signer.loads(raw, max_age=SESSION_MAX_AGE)
        except BadSignature:
            return None

    def set_session(resp: JSONResponse | RedirectResponse, data: dict[str, Any]) -> None:
        resp.set_cookie(SESSION_COOKIE, signer.dumps(data), max_age=SESSION_MAX_AGE, httponly=True, samesite="lax",
                        secure=settings.web_public_url.startswith("https://"))

    def current_user(request: Request) -> dict[str, Any]:
        """Who is asking. In 'open' mode research works anonymously; personal features need a login."""
        s = session_of(request)
        if s:
            return s
        if settings.web_auth == "open":
            return {"id": 0, "name": "زائر", "avatar": None, "admin": False, "anon": True}
        raise HTTPException(401, "سجّل الدخول أولاً")

    def personal_user(request: Request) -> dict[str, Any]:
        u = current_user(request)
        if u.get("anon"):
            raise HTTPException(401, "المحفظة والتنبيهات تحتاج تسجيل الدخول")
        return u

    def admin_user(request: Request) -> dict[str, Any]:
        u = current_user(request)
        if not u.get("admin"):
            raise HTTPException(403, "للمشرفين فقط")
        return u

    def limit(request: Request, cost: int = 1) -> None:
        key = (session_of(request) or {}).get("id") or (request.client.host if request.client else "?")
        limiter.check(str(key), cost)

    # ---- auth routes -------------------------------------------------------------------
    @app.get("/api/me")
    def me(request: Request) -> dict[str, Any]:
        s = session_of(request)
        return {"auth": settings.web_auth, "user": s, "guild": settings.discord_guild_id,
                "ai": settings.gemini_enabled, "sharia_method": settings.sharia_method,
                "login_url": "/auth/login" if settings.web_auth == "discord" else None}

    @app.get("/auth/login")
    def login() -> RedirectResponse:
        if settings.web_auth != "discord":
            raise HTTPException(404)
        state = secrets.token_urlsafe(24)
        oauth_states[state] = time.time()
        for k, t in list(oauth_states.items()):
            if time.time() - t > 600:
                oauth_states.pop(k, None)
        params = {"client_id": settings.discord_client_id, "response_type": "code",
                  "redirect_uri": f"{settings.web_public_url}/auth/callback", "scope": "identify guilds", "state": state, "prompt": "none"}
        return RedirectResponse(f"https://discord.com/oauth2/authorize?{urllib.parse.urlencode(params)}")

    def _discord_post(url: str, data: dict[str, str]) -> dict[str, Any]:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "tdawl.riyami.om"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    def _discord_get(url: str, token: str) -> Any:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "tdawl.riyami.om"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    @app.get("/auth/callback")
    async def callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        if settings.web_auth != "discord":
            raise HTTPException(404)
        if error or not code or state not in oauth_states:
            return RedirectResponse("/?login=failed")
        oauth_states.pop(state, None)

        def exchange() -> dict[str, Any]:
            tok = _discord_post(f"{DISCORD_API}/oauth2/token", {
                "client_id": settings.discord_client_id or "", "client_secret": settings.discord_client_secret or "",
                "grant_type": "authorization_code", "code": code, "redirect_uri": f"{settings.web_public_url}/auth/callback"})
            access = tok["access_token"]
            me_ = _discord_get(f"{DISCORD_API}/users/@me", access)
            guilds = _discord_get(f"{DISCORD_API}/users/@me/guilds", access) if settings.discord_guild_id else []
            return {"me": me_, "guilds": guilds}

        try:
            data = await run_in_threadpool(exchange)
        except Exception as exc:  # noqa: BLE001
            log.warning("discord oauth failed: %s", exc)
            return RedirectResponse("/?login=failed")
        if settings.discord_guild_id and not any(str(g.get("id")) == str(settings.discord_guild_id) for g in data["guilds"]):
            return RedirectResponse("/?login=notmember")
        me_ = data["me"]
        uid = int(me_["id"])
        avatar = f"https://cdn.discordapp.com/avatars/{me_['id']}/{me_['avatar']}.png?size=64" if me_.get("avatar") else None
        resp = RedirectResponse("/")
        set_session(resp, {"id": uid, "name": me_.get("global_name") or me_.get("username"), "avatar": avatar,
                           "admin": (uid in settings.admin_user_ids) or not settings.admin_user_ids})
        return resp

    @app.post("/auth/code")
    def code_login(body: CodeIn, request: Request) -> JSONResponse:
        if settings.web_auth != "code":
            raise HTTPException(404)
        limit(request, 3)
        if not settings.web_access_code or not secrets.compare_digest(body.code.strip(), settings.web_access_code):
            raise HTTPException(401, "رمز الدخول غير صحيح")
        resp = JSONResponse({"ok": True})
        set_session(resp, {"id": GUEST_ID, "name": "عضو", "avatar": None, "admin": not settings.admin_user_ids})
        return resp

    @app.post("/auth/logout")
    def logout() -> JSONResponse:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    # ---- research --------------------------------------------------------------------------
    @app.get("/api/analyze/{symbol}")
    async def analyze(symbol: str, request: Request, ai: int = 1, user: dict = Depends(current_user)) -> dict[str, Any]:
        limit(request, 2)
        t = _clean_symbol(symbol)
        r = await run_in_threadpool(engine.analyze, t, bool(ai) and engine.ai.enabled, True)
        return report_payload(engine, r)

    @app.get("/api/stock/{symbol}")
    async def stock(symbol: str, request: Request, user: dict = Depends(current_user)) -> dict[str, Any]:
        limit(request)
        t = _clean_symbol(symbol)
        r = await run_in_threadpool(engine.analyze, t, False, False)
        p = report_payload(engine, r, full=False)
        p["embeds"] = [render.stock_embed(r)]
        return p

    @app.get("/api/sharia/{symbol}")
    async def sharia(symbol: str, request: Request, method: str | None = None, rescreen: int = 0,
                     user: dict = Depends(current_user)) -> dict[str, Any]:
        limit(request)
        t = _clean_symbol(symbol)
        m = (method or settings.sharia_method).upper()
        if m not in sh.METHODS:
            raise HTTPException(400, "المنهجية يجب أن تكون AAOIFI أو MSCI أو FTSE")

        def work() -> StockReport:
            f = engine.hub.fundamentals(t)
            v = engine.sharia(t, f, m, force=bool(rescreen))
            r = engine.analyze(t, with_ai=False, with_news=False)
            r.sharia = v
            return r

        r = await run_in_threadpool(work)
        v = r.sharia
        return {"summary": report_payload(engine, r, full=False)["summary"],
                "verdict": {"status": v.status, "label": v.label(), "method": v.method, "method_label": sh.METHODS[v.method]["label"],
                            "reference": sh.METHODS[v.method]["reference"], "basis": sh.METHODS[v.method]["denominator"],
                            "reasons": v.reasons, "ratios": v.ratios, "thresholds": v.thresholds, "ratio_lines": v.ratio_lines(),
                            "sources": v.sources, "unverified": v.unverified, "screened_at": v.screened_at, "business_note": v.business_note},
                "history": engine.sharia_history(t), "embeds": [render.sharia_embed(r, engine.sharia_history(t))],
                "methods": {k: {"label": m_["label"], "note": sh.method_note(k)} for k, m_ in sh.METHODS.items()}}

    @app.get("/api/news/{symbol}")
    async def news(symbol: str, request: Request, user: dict = Depends(current_user)) -> dict[str, Any]:
        limit(request)
        t = _clean_symbol(symbol)
        r = await run_in_threadpool(engine.analyze, t, engine.ai.enabled, True)
        p = report_payload(engine, r, full=False)
        p["embeds"] = [render.news_embed(r)]
        return p

    @app.get("/api/compare")
    async def compare(a: str, b: str, request: Request, user: dict = Depends(current_user)) -> dict[str, Any]:
        limit(request, 3)
        ra_, rb_, res = await run_in_threadpool(engine.compare, _clean_symbol(a), _clean_symbol(b))
        return {"a": report_payload(engine, ra_, full=False)["summary"], "b": report_payload(engine, rb_, full=False)["summary"],
                "embeds": [render.compare_embed(ra_, rb_, res)], "ai": res.model_dump() if res else None}

    @app.get("/api/market")
    async def market(request: Request, user: dict = Depends(current_user)) -> dict[str, Any]:
        limit(request)
        idx = await run_in_threadpool(engine.market)
        now = datetime.now(timezone.utc)
        st = cal.market_state(now)
        nxt = cal.next_session(now)
        return {"indices": idx, "state": st,
                "state_ar": {"open": "السوق الأمريكي مفتوح الآن", "pre": "قبل الافتتاح", "after": "بعد الإغلاق", "closed": "السوق مغلق"}[st],
                "next_open": nxt[0].timestamp(), "next_close": nxt[1].timestamp(), "tz": settings.user_tz.key,
                "now": now.timestamp(), "embeds": [render.market_embed(idx, cal.market_state(now))]}

    @app.get("/api/opportunities")
    def opportunities(request: Request, user: dict = Depends(current_user)) -> dict[str, Any]:
        row = engine.db.one("SELECT results, created_at, universe_size FROM scans ORDER BY created_at DESC LIMIT 1")
        if not row:
            return {"results": [], "created_at": None, "universe_size": 0}
        return {"results": json.loads(row["results"]), "created_at": row["created_at"], "universe_size": row["universe_size"]}

    @app.post("/api/scan")
    async def scan(request: Request, min_drop: float = 15.0, limit_: int = 8, user: dict = Depends(admin_user)) -> dict[str, Any]:
        limit(request, 5)
        reps = await run_in_threadpool(engine.scan, None, max(1, min(limit_, 15)), max(1.0, min(min_drop, 80.0)))
        return {"results": [engine.summary_dict(r) for r in reps], "embeds": render.opportunities_embeds(reps), "created_at": time.time()}

    @app.post("/api/ask")
    async def ask(body: AskIn, request: Request, user: dict = Depends(current_user)) -> dict[str, Any]:
        limit(request, 3)
        if not engine.ai.enabled:
            raise HTTPException(503, "Gemini غير مفعّل على الخادم")
        sym = _clean_symbol(body.symbol) if body.symbol else None
        ans = await run_in_threadpool(engine.ask, body.question.strip(), sym)
        if ans is None:
            raise HTTPException(502, "تعذر الحصول على إجابة الآن")
        return ans.model_dump()

    @app.get("/api/xtb/{symbol}")
    async def xtb_status(symbol: str, request: Request, user: dict = Depends(current_user)) -> dict[str, Any]:
        limit(request)
        t = _clean_symbol(symbol)
        r = await run_in_threadpool(engine.analyze, t, False, False)
        return {"ticker": t, **to_jsonable(r.xtb), "label": r.xtb.label(), "investable": r.xtb.investable}

    @app.post("/api/xtb")
    def xtb_set(body: XtbIn, user: dict = Depends(admin_user)) -> dict[str, Any]:
        st = body.status.upper()
        if st not in (xi.REAL_STOCK, xi.CFD_ONLY, xi.NOT_AVAILABLE, xi.NOT_A_STOCK):
            raise HTTPException(400, "الحالة غير معروفة")
        x = engine.xtb.set(_clean_symbol(body.symbol), st, f"manual confirmation by {user['name']} (web)",
                           "EQUITY" if st == xi.REAL_STOCK else "", body.market, body.currency)
        return {**to_jsonable(x), "label": x.label()}

    @app.get("/api/xtb")
    def xtb_all(user: dict = Depends(current_user)) -> list[dict[str, Any]]:
        return [{**to_jsonable(x), "label": x.label()} for x in engine.xtb.all()]

    # ---- personal ----------------------------------------------------------------------------
    @app.get("/api/portfolio")
    async def portfolio(request: Request, user: dict = Depends(personal_user)) -> dict[str, Any]:
        limit(request)
        v = await run_in_threadpool(engine.portfolio_view, user["id"])
        return {**to_jsonable(v), "embeds": render.portfolio_embeds(v)}

    @app.post("/api/portfolio")
    def portfolio_add(body: PortfolioIn, user: dict = Depends(personal_user)) -> dict[str, Any]:
        try:
            engine.portfolio.add(user["id"], _clean_symbol(body.symbol), body.qty, body.avg_price, body.currency, body.bought_at)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/portfolio/{symbol}")
    def portfolio_remove(symbol: str, user: dict = Depends(personal_user)) -> dict[str, Any]:
        return {"removed": engine.portfolio.remove(user["id"], _clean_symbol(symbol))}

    @app.get("/api/watchlist")
    async def watchlist(request: Request, user: dict = Depends(personal_user)) -> dict[str, Any]:
        limit(request)
        items = engine.watchlist.list(user["id"])

        def work() -> list[dict[str, Any]]:
            out = []
            for t, note in items:
                try:
                    r = engine.analyze(t, with_ai=False, with_news=False)
                    out.append({**report_payload(engine, r, full=False)["summary"], "note": note})
                except Exception as exc:  # noqa: BLE001
                    out.append({"ticker": t, "note": note, "error": str(exc)})
            return out

        return {"items": await run_in_threadpool(work)}

    @app.post("/api/watchlist")
    def watchlist_add(body: WatchIn, user: dict = Depends(personal_user)) -> dict[str, Any]:
        engine.watchlist.add(user["id"], _clean_symbol(body.symbol), body.note[:200])
        return {"ok": True}

    @app.delete("/api/watchlist/{symbol}")
    def watchlist_remove(symbol: str, user: dict = Depends(personal_user)) -> dict[str, Any]:
        return {"removed": engine.watchlist.remove(user["id"], _clean_symbol(symbol))}

    @app.get("/api/alerts")
    def alerts(user: dict = Depends(personal_user)) -> dict[str, Any]:
        auto = sorted(set(engine.portfolio.tickers(user["id"])) | {t for t, _ in engine.watchlist.list(user["id"])})
        log_rows = [dict(r) for r in engine.db.q("SELECT ticker,kind,message,fired_at FROM alert_log WHERE user_id=? ORDER BY fired_at DESC LIMIT 30", (user["id"],))]
        return {"rules": engine.alerts.list(user["id"]), "auto": auto, "drop_pct": settings.alert_drop_pct, "log": log_rows}

    @app.post("/api/alerts")
    def alerts_add(body: AlertIn, user: dict = Depends(personal_user)) -> dict[str, Any]:
        try:
            i = engine.alerts.add(user["id"], _clean_symbol(body.symbol), body.kind.lower(), body.threshold)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": i}

    @app.delete("/api/alerts/{alert_id}")
    def alerts_remove(alert_id: int, user: dict = Depends(personal_user)) -> dict[str, Any]:
        return {"removed": engine.alerts.remove(user["id"], alert_id)}

    # ---- misc ------------------------------------------------------------------------------
    @app.get("/api/help")
    def help_() -> dict[str, Any]:
        return {"sections": [{"key": k, "label": lbl, "embed": helptext.embed(k)} for k, lbl in helptext.SECTIONS]}

    @app.get("/api/status")
    def status(user: dict = Depends(admin_user)) -> dict[str, Any]:
        return engine.status()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "time": time.time()}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/static/{name}")
    def static(name: str) -> FileResponse:
        p = (STATIC / name).resolve()
        if STATIC.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    @app.exception_handler(Exception)
    async def on_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("web error on %s: %s", request.url.path, exc)
        return JSONResponse({"detail": "حدث خطأ غير متوقع؛ راجع السجل"}, status_code=500)

    return app


def run_web(settings: Settings, engine: Engine) -> None:
    import uvicorn

    app = create_app(settings, engine)
    log.info("web dashboard on http://%s:%d (public: %s, auth=%s)", settings.web_host, settings.web_port, settings.web_public_url, settings.web_auth)
    uvicorn.run(app, host=settings.web_host, port=settings.web_port, log_level="warning", proxy_headers=True, forwarded_allow_ips="127.0.0.1")
