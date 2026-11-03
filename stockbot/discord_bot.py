"""Discord slash-command bot (discord.py 2.x).

Commands: /analyze /stock /sharia /news /report /opportunities /scan /market /compare
          /portfolio (show|add|remove) /watchlist (show|add|remove) /alerts (show|add|remove)
          /xtb (status|verify|cfd|unavailable|import) /ask /status
Background: the existing open/close scheduler (webhook) and the alert monitor.

Security: admin-only for portfolio/alerts of other users, xtb verification and /status;
every reply with personal data is ephemeral; tokens never leave the environment.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Any

import discord
from discord import app_commands

from .analyst import build_analyst
from .bot import State, StocksBot
from .config import Settings
from .engine import Engine
from .notify import BLUE, GREEN, GREY, ORANGE, RED, Discord as Webhook
from . import help as helptext
from . import render
from .xtb import instruments as xi

log = logging.getLogger(__name__)
MAX_EMBEDS = 10


def _embeds(dicts: list[dict[str, Any]]) -> list[discord.Embed]:
    return [discord.Embed.from_dict(d) for d in dicts[:MAX_EMBEDS]]


class Pager(discord.ui.View):
    """Buttons to page through a list of embeds (one per page)."""

    def __init__(self, pages: list[list[discord.Embed]], timeout: float = 600) -> None:
        super().__init__(timeout=timeout)
        self.pages = pages
        self.i = 0

    async def _show(self, inter: discord.Interaction) -> None:
        await inter.response.edit_message(embeds=self.pages[self.i], view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, inter: discord.Interaction, _b: discord.ui.Button) -> None:
        self.i = (self.i - 1) % len(self.pages)
        await self._show(inter)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, inter: discord.Interaction, _b: discord.ui.Button) -> None:
        self.i = (self.i + 1) % len(self.pages)
        await self._show(inter)


class HelpButton(discord.ui.Button):
    def __init__(self, key: str, label: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.success if key == "about" else discord.ButtonStyle.secondary,
                         custom_id=f"help:{key}")
        self.key = key

    async def callback(self, inter: discord.Interaction) -> None:
        await inter.response.edit_message(embed=discord.Embed.from_dict(helptext.embed(self.key)), view=self.view)


class HelpView(discord.ui.View):
    """One button per help section; clicking swaps the embed in place."""

    def __init__(self, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        for key, label in helptext.SECTIONS:
            self.add_item(HelpButton(key, label))


class StocksDiscordBot(discord.Client):
    def __init__(self, settings: Settings, engine: Engine) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.s = settings
        self.engine = engine
        self.tree = app_commands.CommandTree(self)
        self._register()
        self._alert_task: asyncio.Task | None = None
        self._sched_task: asyncio.Task | None = None

    # ---- helpers ---------------------------------------------------------------
    def is_admin(self, inter: discord.Interaction) -> bool:
        if not self.s.admin_user_ids:
            return True  # no admin list configured: everyone in the server can use everything
        return inter.user.id in self.s.admin_user_ids

    async def run_blocking(self, fn, *args):  # noqa: ANN001
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    async def send_pages(self, inter: discord.Interaction, dicts: list[dict[str, Any]], per_page: int = 3, ephemeral: bool = False) -> None:
        embeds = _embeds(dicts) if len(dicts) <= MAX_EMBEDS else [discord.Embed.from_dict(d) for d in dicts]
        if len(embeds) <= per_page:
            await inter.followup.send(embeds=embeds, ephemeral=ephemeral)
            return
        pages = [embeds[i:i + per_page] for i in range(0, len(embeds), per_page)]
        view = Pager(pages)
        await inter.followup.send(embeds=pages[0], view=view, ephemeral=ephemeral)

    async def fail(self, inter: discord.Interaction, msg: str) -> None:
        e = discord.Embed(title="⚠️", description=msg[:2000], color=ORANGE)
        if inter.response.is_done():
            await inter.followup.send(embed=e, ephemeral=True)
        else:
            await inter.response.send_message(embed=e, ephemeral=True)

    # ---- lifecycle ---------------------------------------------------------------
    async def setup_hook(self) -> None:
        if self.s.discord_guild_id:
            g = discord.Object(id=self.s.discord_guild_id)
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        else:
            await self.tree.sync()
        self._alert_task = asyncio.create_task(self._alert_loop())
        self._sched_task = asyncio.create_task(self._scheduler_loop())

    async def on_ready(self) -> None:
        log.info("Discord bot ready as %s (© Abdulrhman AL-Riyami)", self.user)

    async def _post_channel(self, dicts: list[dict[str, Any]]) -> None:
        if not self.s.discord_channel_id:
            return
        ch = self.get_channel(self.s.discord_channel_id) or await self.fetch_channel(self.s.discord_channel_id)
        for i in range(0, len(dicts), MAX_EMBEDS):
            await ch.send(embeds=_embeds(dicts[i:i + MAX_EMBEDS]))  # type: ignore[union-attr]

    async def _alert_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                fired = await self.run_blocking(self.engine.run_alerts)
                for ev in fired:
                    try:
                        user = await self.fetch_user(ev.user_id)
                        await user.send(embed=discord.Embed(title="🔔 تنبيه", description=ev.message, color=ORANGE))
                    except Exception as exc:  # noqa: BLE001
                        log.warning("alert DM failed for %s: %s", ev.user_id, exc)
                        if self.s.discord_channel_id:
                            await self._post_channel([{"title": "🔔 تنبيه", "description": f"<@{ev.user_id}> {ev.message}", "color": ORANGE}])
            except Exception:  # noqa: BLE001
                log.exception("alert loop failed")
            await asyncio.sleep(self.s.alert_check_minutes * 60)

    async def _scheduler_loop(self) -> None:
        """Reuse the existing open/close scheduler; it posts through the webhook when configured."""
        if not self.s.discord_webhook:
            log.info("DISCORD_WEBHOOK_URL not set: scheduled open/close posts disabled (slash commands still work)")
            return
        await self.wait_until_ready()
        analyst = build_analyst(self.s, self.engine.ai)
        bot = StocksBot(self.s, Webhook(self.s.discord_webhook), analyst, State.load(self.s.state_file))
        while not self.is_closed():
            try:
                await self.run_blocking(bot.tick)
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            await asyncio.sleep(60)

    # ---- commands ---------------------------------------------------------------------
    def _register(self) -> None:
        tree = self.tree
        engine = self.engine
        bot = self

        @tree.command(name="analyze", description="تحليل كامل للسهم: شرعي، فني، أساسي، أخبار، مخاطر، خلاصة")
        @app_commands.describe(symbol="رمز السهم مثل AAPL")
        async def analyze(inter: discord.Interaction, symbol: str) -> None:
            await inter.response.defer(thinking=True)
            r = await bot.run_blocking(engine.analyze, symbol)
            await bot.send_pages(inter, render.quick_analysis(r) + [render.technical_embed(r), render.fundamental_embed(r), render.news_embed(r), render.risk_embed(r)], per_page=2)

        @tree.command(name="report", description="تقرير استثماري شامل (18 قسماً) عن السهم")
        async def report(inter: discord.Interaction, symbol: str) -> None:
            await inter.response.defer(thinking=True)
            r = await bot.run_blocking(engine.analyze, symbol)
            hist = engine.sharia_history(symbol)
            await bot.send_pages(inter, render.full_report(r, hist), per_page=2)

        @tree.command(name="stock", description="معلومات السهم وسعره")
        async def stock(inter: discord.Interaction, symbol: str) -> None:
            await inter.response.defer(thinking=True)
            r = await bot.run_blocking(lambda: engine.analyze(symbol, with_ai=False, with_news=False))
            await inter.followup.send(embed=discord.Embed.from_dict(render.stock_embed(r)))

        @tree.command(name="sharia", description="الفحص الشرعي للسهم مع المنهجية والنسب والمصادر")
        @app_commands.describe(symbol="رمز السهم", method="AAOIFI أو MSCI أو FTSE", rescreen="إعادة الفحص الآن بدل النتيجة المحفوظة")
        async def sharia(inter: discord.Interaction, symbol: str, method: str | None = None, rescreen: bool = False) -> None:
            await inter.response.defer(thinking=True)
            m = (method or engine.s.sharia_method).upper()
            if m not in ("AAOIFI", "MSCI", "FTSE"):
                await bot.fail(inter, "المنهجية يجب أن تكون AAOIFI أو MSCI أو FTSE"); return
            def work():
                f = engine.hub.fundamentals(symbol)
                v = engine.sharia(symbol, f, m, force=rescreen)
                r = engine.analyze(symbol, with_ai=False, with_news=False)
                r.sharia = v
                return r
            r = await bot.run_blocking(work)
            await inter.followup.send(embed=discord.Embed.from_dict(render.sharia_embed(r, engine.sharia_history(symbol))))

        @tree.command(name="news", description="آخر أخبار الشركة وإفصاحات SEC مع الروابط")
        async def news(inter: discord.Interaction, symbol: str) -> None:
            await inter.response.defer(thinking=True)
            r = await bot.run_blocking(lambda: engine.analyze(symbol, with_ai=engine.ai.enabled, with_news=True))
            await inter.followup.send(embed=discord.Embed.from_dict(render.news_embed(r)))

        @tree.command(name="opportunities", description="أفضل الفرص من آخر مسح")
        async def opportunities(inter: discord.Interaction) -> None:
            await inter.response.defer(thinking=True)
            last = engine.last_scan()
            if not last:
                await inter.followup.send("لا يوجد مسح محفوظ بعد. استخدم /scan."); return
            lines = [f"**{x['ticker']}** {x['score']}/100 · {x['category']} · شرعي {x['sharia']} · XTB {x['xtb']} · {x['drop_pct']:+.1f}% من القمة" if x.get('drop_pct') is not None else f"**{x['ticker']}** {x['score']}/100 · {x['category']}" for x in last[:15]]
            await inter.followup.send(embed=discord.Embed(title="🔎 آخر الفرص المكتشفة", description="\n".join(lines)[:4000], color=BLUE))

        @tree.command(name="scan", description="مسح القائمة للبحث عن أسهم هبطت ولديها أساسيات جيدة (يأخذ دقائق)")
        @app_commands.describe(min_drop="أدنى هبوط من قمة 52 أسبوع بالنسبة المئوية", limit="عدد النتائج")
        async def scan(inter: discord.Interaction, min_drop: float = 15.0, limit: int = 8) -> None:
            await inter.response.defer(thinking=True)
            reports = await bot.run_blocking(lambda: engine.scan(None, max(1, min(limit, 15)), min_drop))
            await bot.send_pages(inter, render.opportunities_embeds(reports), per_page=3)

        @tree.command(name="market", description="ملخص الأسواق وحالة الجلسة")
        async def market(inter: discord.Interaction) -> None:
            await inter.response.defer(thinking=True)
            from . import market_calendar as cal
            from datetime import datetime, timezone
            idx = await bot.run_blocking(engine.market)
            now = datetime.now(timezone.utc)
            st = cal.market_state(now)
            nxt = cal.next_session(now)
            state_line = {"open": "🟢 السوق الأمريكي مفتوح الآن", "pre": "🟡 قبل الافتتاح", "after": "🔵 بعد الإغلاق", "closed": "📴 مغلق"}[st]
            state_line += f" · الافتتاح القادم {nxt[0].astimezone(engine.s.user_tz):%A %H:%M} بتوقيتك"
            await inter.followup.send(embed=discord.Embed.from_dict(render.market_embed(idx, state_line)))

        @tree.command(name="compare", description="مقارنة شركتين")
        async def compare(inter: discord.Interaction, symbol1: str, symbol2: str) -> None:
            await inter.response.defer(thinking=True)
            a, b, res = await bot.run_blocking(engine.compare, symbol1, symbol2)
            await inter.followup.send(embed=discord.Embed.from_dict(render.compare_embed(a, b, res)))

        @tree.command(name="ask", description="اسأل عن سهم؛ الإجابة من البيانات الحقيقية فقط (يحتاج Gemini)")
        async def ask(inter: discord.Interaction, question: str, symbol: str | None = None) -> None:
            await inter.response.defer(thinking=True, ephemeral=True)
            if not engine.ai.enabled:
                await bot.fail(inter, "Gemini غير مفعّل (GEMINI_API_KEY). الأوامر الأخرى تعمل بدون ذكاء اصطناعي."); return
            ans = await bot.run_blocking(engine.ask, question, symbol)
            if ans is None:
                await bot.fail(inter, "تعذر الحصول على إجابة الآن."); return
            e = discord.Embed(title="🤖 إجابة Gemini", description=ans.answer[:4000], color=BLUE)
            if ans.data_used:
                e.add_field(name="البيانات المستخدمة", value="\n".join(f"• {x}" for x in ans.data_used[:6])[:1000], inline=False)
            if ans.caveats:
                e.add_field(name="تحفظات", value="\n".join(f"• {x}" for x in ans.caveats[:5])[:1000], inline=False)
            await inter.followup.send(embed=e, ephemeral=True)

        # ---- portfolio -------------------------------------------------------------------
        pf = app_commands.Group(name="portfolio", description="محفظتك الاستثمارية")

        @pf.command(name="show", description="عرض المحفظة مع الأرباح والمخاطر والتوافق الشرعي")
        async def pf_show(inter: discord.Interaction) -> None:
            await inter.response.defer(thinking=True, ephemeral=True)
            v = await bot.run_blocking(engine.portfolio_view, inter.user.id)
            await inter.followup.send(embeds=_embeds(render.portfolio_embeds(v)), ephemeral=True)

        @pf.command(name="add", description="إضافة مركز: الرمز، العدد، متوسط سعر الشراء")
        @app_commands.describe(symbol="الرمز", qty="عدد الأسهم", avg_price="متوسط سعر الشراء", currency="العملة", bought_at="تاريخ الشراء YYYY-MM-DD")
        async def pf_add(inter: discord.Interaction, symbol: str, qty: float, avg_price: float, currency: str = "USD", bought_at: str | None = None) -> None:
            try:
                engine.portfolio.add(inter.user.id, symbol, qty, avg_price, currency, bought_at)
            except ValueError as exc:
                await bot.fail(inter, str(exc)); return
            await inter.response.send_message(f"✅ أُضيف {symbol.upper()}: {qty:g} × {avg_price:.2f} {currency.upper()}", ephemeral=True)

        @pf.command(name="remove", description="حذف مركز")
        async def pf_remove(inter: discord.Interaction, symbol: str) -> None:
            n = engine.portfolio.remove(inter.user.id, symbol)
            await inter.response.send_message(f"🗑️ حُذف {symbol.upper()}" if n else "لم يُعثر على المركز", ephemeral=True)

        tree.add_command(pf)

        # ---- watchlist ------------------------------------------------------------------------
        wl = app_commands.Group(name="watchlist", description="الأسهم التي تتابعها")

        @wl.command(name="show", description="عرض قائمة المتابعة مع السعر والتقييم")
        async def wl_show(inter: discord.Interaction) -> None:
            await inter.response.defer(thinking=True, ephemeral=True)
            items = engine.watchlist.list(inter.user.id)
            if not items:
                await inter.followup.send("القائمة فارغة. أضف بـ `/watchlist add`.", ephemeral=True); return
            lines = []
            for t, note in items:
                r = await bot.run_blocking(lambda t=t: engine.analyze(t, with_ai=False, with_news=False))
                s = r.sharia.status if r.sharia else "?"
                lines.append(f"**{t}** {render._f(r.price)} {r.currency} · {render._pct(r.tech.change_1d) if r.tech else '-'} اليوم · {r.opportunity.total}/100 · {s} · XTB {r.xtb.status}" + (f" · {note}" if note else ""))
            await inter.followup.send(embed=discord.Embed(title="👀 قائمة المتابعة", description="\n".join(lines)[:4000], color=BLUE), ephemeral=True)

        @wl.command(name="add", description="إضافة سهم للمتابعة")
        async def wl_add(inter: discord.Interaction, symbol: str, note: str = "") -> None:
            engine.watchlist.add(inter.user.id, symbol, note)
            await inter.response.send_message(f"👀 تتم متابعة {symbol.upper()} (تنبيهات هبوط/أخبار/شريعة/نتائج تلقائية)", ephemeral=True)

        @wl.command(name="remove", description="إزالة سهم من المتابعة")
        async def wl_remove(inter: discord.Interaction, symbol: str) -> None:
            n = engine.watchlist.remove(inter.user.id, symbol)
            await inter.response.send_message("🗑️ أُزيل" if n else "غير موجود", ephemeral=True)

        tree.add_command(wl)

        # ---- alerts -------------------------------------------------------------------------------
        al = app_commands.Group(name="alerts", description="تنبيهاتك")

        @al.command(name="show", description="عرض التنبيهات")
        async def al_show(inter: discord.Interaction) -> None:
            rules = engine.alerts.list(inter.user.id)
            auto = sorted(set(engine.portfolio.tickers(inter.user.id)) | {t for t, _ in engine.watchlist.list(inter.user.id)})
            lines = [f"#{r['id']} {r['ticker']} {r['kind']}" + (f" {r['threshold']:g}" if r['threshold'] is not None else "") for r in rules]
            desc = ("**قواعدك:**\n" + ("\n".join(lines) if lines else "لا قواعد") +
                    f"\n\n**مراقبة تلقائية** (هبوط ≥{engine.s.alert_drop_pct:g}%، خبر جوهري، تغير شرعي، نتائج خلال 3 أيام، انعكاس اتجاه): " + (", ".join(auto) if auto else "لا شيء بعد"))
            await inter.response.send_message(embed=discord.Embed(title="🔔 التنبيهات", description=desc[:4000], color=BLUE), ephemeral=True)

        @al.command(name="add", description="إضافة تنبيه: drop (نسبة) / target (سعر) / below (سعر) / news / sharia / earnings / trend")
        async def al_add(inter: discord.Interaction, symbol: str, kind: str, threshold: float | None = None) -> None:
            try:
                i = engine.alerts.add(inter.user.id, symbol, kind.lower(), threshold)
            except ValueError as exc:
                await bot.fail(inter, str(exc)); return
            await inter.response.send_message(f"🔔 تنبيه #{i} أُضيف لـ {symbol.upper()} ({kind})", ephemeral=True)

        @al.command(name="remove", description="حذف تنبيه برقمه")
        async def al_remove(inter: discord.Interaction, alert_id: int) -> None:
            n = engine.alerts.remove(inter.user.id, alert_id)
            await inter.response.send_message("🗑️ حُذف" if n else "غير موجود", ephemeral=True)

        tree.add_command(al)

        # ---- xtb verification -------------------------------------------------------------------------
        xg = app_commands.Group(name="xtb", description="التحقق من توفر السهم الحقيقي في XTB")

        @xg.command(name="status", description="حالة التحقق لسهم")
        async def xtb_status(inter: discord.Interaction, symbol: str) -> None:
            await inter.response.defer(thinking=True)
            r = await bot.run_blocking(lambda: engine.analyze(symbol, with_ai=False, with_news=False))
            e = discord.Embed(title=f"XTB — {symbol.upper()}", description=r.xtb.label() + ("\n" + r.xtb.note if r.xtb.note else ""), color=GREEN if r.xtb.investable else GREY)
            e.set_footer(text="لا توجد واجهة رسمية من XTB؛ التحقق يدوي من xStation أو باستيراد قائمة الأدوات.")
            await inter.followup.send(embed=e)

        @xg.command(name="verify", description="تأكيد أن السهم متاح كسهم حقيقي (بعد التحقق في xStation)")
        @app_commands.describe(symbol="الرمز", market="السوق مثل NASDAQ", currency="العملة", min_qty="أدنى كمية", fee_note="ملاحظة الرسوم")
        async def xtb_verify(inter: discord.Interaction, symbol: str, market: str = "", currency: str = "", min_qty: float | None = None, fee_note: str = "") -> None:
            if not bot.is_admin(inter):
                await bot.fail(inter, "للمشرفين فقط"); return
            st = engine.xtb.set(symbol, xi.REAL_STOCK, f"manual confirmation by {inter.user.name} in xStation", "EQUITY", market, currency, min_qty, fee_note)
            await inter.response.send_message(f"✅ {st.label()}", ephemeral=True)

        @xg.command(name="cfd", description="تسجيل أن الاسم متاح كـ CFD فقط (يُستبعد من التوصيات)")
        async def xtb_cfd(inter: discord.Interaction, symbol: str) -> None:
            if not bot.is_admin(inter):
                await bot.fail(inter, "للمشرفين فقط"); return
            st = engine.xtb.set(symbol, xi.CFD_ONLY, f"manual by {inter.user.name}")
            await inter.response.send_message(f"⛔ {st.label()}", ephemeral=True)

        @xg.command(name="unavailable", description="تسجيل أن السهم غير متاح في XTB")
        async def xtb_unavail(inter: discord.Interaction, symbol: str) -> None:
            if not bot.is_admin(inter):
                await bot.fail(inter, "للمشرفين فقط"); return
            st = engine.xtb.set(symbol, xi.NOT_AVAILABLE, f"manual by {inter.user.name}")
            await inter.response.send_message(f"⛔ {st.label()}", ephemeral=True)

        @xg.command(name="import", description="استيراد قائمة أدوات (CSV: symbol,type[,market,currency,min_qty])")
        async def xtb_import(inter: discord.Interaction, file: discord.Attachment) -> None:
            if not bot.is_admin(inter):
                await bot.fail(inter, "للمشرفين فقط"); return
            data = await file.read()
            n = engine.xtb.import_csv(data.decode("utf-8", errors="ignore"), f"CSV import by {inter.user.name}: {file.filename}")
            await inter.response.send_message(f"📥 استُوردت {n} أداة", ephemeral=True)

        tree.add_command(xg)

        @tree.command(name="help", description="شرح البوت: ما فائدته وكيف تستخدمه (مع أزرار للأقسام)")
        async def help_cmd(inter: discord.Interaction) -> None:
            await inter.response.send_message(embed=discord.Embed.from_dict(helptext.embed("about")), view=HelpView())

        @tree.command(name="status", description="حالة البوت: المصادر، Gemini، الاستهلاك")
        async def status(inter: discord.Interaction) -> None:
            if not bot.is_admin(inter):
                await bot.fail(inter, "للمشرفين فقط"); return
            st = engine.status()
            e = discord.Embed(title="⚙️ حالة النظام", color=BLUE)
            e.set_footer(text=f"تطوير وحقوق: Abdulrhman AL-Riyami · {render.AUTHOR}")
            e.add_field(name="مصادر البيانات", value="\n".join(f"• {k}: {v}" for k, v in st["providers"].items())[:1000], inline=False)
            e.add_field(name="الذكاء الاصطناعي", value=st["ai"], inline=False)
            e.add_field(name="المنهجية الشرعية", value=st["sharia_method"][:1000], inline=False)
            e.add_field(name="أسهم XTB مؤكدة", value=str(st["xtb_verified"]), inline=True)
            usage = st["usage_7d"][:10]
            if usage:
                e.add_field(name="استهلاك API (7 أيام)", value="\n".join(f"{u['day']} {u['provider']}: {u['calls']} ({u['ok']} ok)" for u in usage)[:1000], inline=False)
            await inter.response.send_message(embed=e, ephemeral=True)

        @tree.error
        async def on_error(inter: discord.Interaction, error: app_commands.AppCommandError) -> None:
            log.exception("command error: %s", error)
            await bot.fail(inter, f"حدث خطأ: {type(error).__name__}. راجع السجل.")


def run_discord(settings: Settings, engine: Engine) -> None:
    if not settings.discord_bot_token:
        raise SystemExit("DISCORD_BOT_TOKEN is required for the interactive bot")
    client = StocksDiscordBot(settings, engine)
    client.run(settings.discord_bot_token, log_handler=None)
