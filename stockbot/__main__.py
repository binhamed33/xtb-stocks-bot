"""CLI.

Interactive Discord bot (slash commands + alerts + scheduler):
    python -m stockbot discord

Web dashboard (tdawl.riyami.om; same engine and database):
    python -m stockbot web

Scheduled webhook posts only (no slash commands):
    python -m stockbot run           # open/close alerts, pre-market brief, close summary
    python -m stockbot now           # build and send a brief immediately
    python -m stockbot test-discord  # send a test message to the webhook
    python -m stockbot preview       # example scheduled cards offline

Research from the terminal (same engine as the slash commands):
    python -m stockbot analyze SYMBOL
    python -m stockbot report SYMBOL
    python -m stockbot sharia SYMBOL [AAOIFI|MSCI|FTSE]
    python -m stockbot news SYMBOL
    python -m stockbot scan [MIN_DROP] [LIMIT]
    python -m stockbot compare A B
    python -m stockbot market
    python -m stockbot xtb-verify SYMBOL | xtb-import FILE.csv | xtb-list
    python -m stockbot alerts-run    # evaluate alerts once and print them
    python -m stockbot status
    python -m stockbot calendar
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from . import market_calendar as cal
from .config import ConfigError, load_settings
from .notify import BLUE, Discord


def _logging(log_file) -> None:  # noqa: ANN001
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        from logging.handlers import RotatingFileHandler

        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    for noisy in ("yfinance", "peewee", "httpx", "discord", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    import warnings

    warnings.simplefilter("ignore")                            # pandas/yfinance deprecation noise in the service log
    warnings.filterwarnings("ignore", module="yfinance")
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)


def main(argv: list[str]) -> int:
    load_dotenv()
    cmd = argv[1] if len(argv) > 1 else "calendar"
    args = argv[2:]
    try:
        s = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    _logging(s.log_file)
    log = logging.getLogger("stockbot")
    discord = Discord(s.discord_webhook)
    now = datetime.now(timezone.utc)

    if cmd == "calendar":
        nxt = cal.next_session(now)
        print(f"now         : {now.astimezone(s.user_tz):%Y-%m-%d %H:%M} ({s.user_tz.key})  |  {now.astimezone(cal.ET):%H:%M} ET")
        print(f"state       : {cal.market_state(now)}")
        print(f"next open   : {nxt[0].astimezone(s.user_tz):%A %Y-%m-%d %H:%M}  ({nxt[0]:%H:%M} ET)")
        print(f"next close  : {nxt[1].astimezone(s.user_tz):%A %Y-%m-%d %H:%M}  ({nxt[1]:%H:%M} ET)")
        print(f"universe    : {len(s.universe)} tickers (scan universe {len(s.scan_universe)})")
        print(f"webhook     : {'configured' if discord.enabled else 'NOT configured'}")
        print(f"bot token   : {'present' if s.discord_bot_token else 'missing (slash commands disabled)'}")
        print(f"gemini      : {'present (' + s.gemini_model + ')' if s.gemini_enabled else 'missing -> programmatic analysis'}")
        print(f"sharia      : {s.sharia_method}")
        return 0

    if cmd == "test-discord":
        ok = discord.embed("✅ اختبار بوت الأسهم", description="البوت يستطيع الإرسال إلى هذه القناة.",
                           fields=[("الأسهم المراقبة", str(len(s.universe)), True), ("توقيتك", s.user_tz.key, True)], color=BLUE)
        print("sent" if ok else "FAILED (check DISCORD_WEBHOOK_URL)")
        return 0 if ok else 1

    if cmd == "preview":
        from .preview import run_preview

        return run_preview(s)

    # ---- everything below needs the engine ------------------------------------------
    from . import render
    from .engine import Engine

    engine = Engine(s)

    if cmd == "discord":
        from .discord_bot import run_discord

        run_discord(s, engine)
        return 0

    if cmd == "web":
        from .web.app import run_web

        run_web(s, engine)
        return 0

    if cmd in ("run", "now"):
        from .analyst import build_analyst
        from .bot import State, StocksBot

        if not discord.enabled:
            print("config error: DISCORD_WEBHOOK_URL is required", file=sys.stderr)
            return 2
        bot = StocksBot(s, discord, build_analyst(s, engine.ai), State.load(s.state_file))
        if cmd == "now":
            bot.brief_now()
            return 0

        def _stop(signum, _f):  # noqa: ANN001
            log.warning("signal %s, stopping", signum)
            bot.stop_event.set()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        bot.run_forever()
        return 0

    if cmd in ("analyze", "report", "stock", "news"):
        if not args:
            print("usage: SYMBOL", file=sys.stderr); return 2
        r = engine.analyze(args[0], with_ai=engine.ai.enabled, with_news=cmd != "stock")
        if cmd == "report":
            print(render.text(render.full_report(r, engine.sharia_history(args[0]))))
        elif cmd == "news":
            print(render.text([render.news_embed(r)]))
        elif cmd == "stock":
            print(render.text([render.stock_embed(r)]))
        else:
            print(render.text(render.quick_analysis(r) + [render.technical_embed(r), render.fundamental_embed(r), render.risk_embed(r)]))
        return 0

    if cmd == "sharia":
        if not args:
            print("usage: SYMBOL [METHOD]", file=sys.stderr); return 2
        method = args[1].upper() if len(args) > 1 else s.sharia_method
        f = engine.hub.fundamentals(args[0])
        v = engine.sharia(args[0], f, method, force=True)
        r = engine.analyze(args[0], with_ai=False, with_news=False)
        r.sharia = v
        print(render.text([render.sharia_embed(r, engine.sharia_history(args[0]))]))
        return 0

    if cmd == "scan":
        min_drop = float(args[0]) if args else 15.0
        limit = int(args[1]) if len(args) > 1 else 8
        reports = engine.scan(None, limit, min_drop, progress=lambda i, n, t: print(f"  [{i}/{n}] {t}", flush=True))
        print(render.text(render.opportunities_embeds(reports)))
        return 0

    if cmd == "compare":
        if len(args) < 2:
            print("usage: compare A B", file=sys.stderr); return 2
        a, b, res = engine.compare(args[0], args[1])
        print(render.text([render.compare_embed(a, b, res)]))
        return 0

    if cmd == "market":
        idx = engine.market()
        print(render.text([render.market_embed(idx, cal.market_state(now))]))
        return 0

    if cmd == "xtb-verify":
        from .xtb import instruments as xi

        st = engine.xtb.set(args[0], xi.REAL_STOCK, "manual confirmation (CLI)")
        print(st.label()); return 0
    if cmd == "xtb-import":
        n = engine.xtb.import_csv(open(args[0], encoding="utf-8").read(), f"CSV import {os.path.basename(args[0])}")
        print(f"imported {n}"); return 0
    if cmd == "xtb-list":
        for st in engine.xtb.all():
            print(st.label())
        return 0

    if cmd == "alerts-run":
        for ev in engine.run_alerts():
            print(f"[{ev.user_id}] {ev.message}")
        return 0

    if cmd == "status":
        st = engine.status()
        for k, v in st["providers"].items():
            print(f"{k:26} {v}")
        print(f"{'AI':26} {st['ai']}")
        print(f"{'Sharia':26} {st['sharia_method']}")
        print(f"{'XTB verified':26} {st['xtb_verified']}")
        print(f"{'DB':26} {st['db']}")
        for u in st["usage_7d"][:15]:
            print(f"  {u['day']} {u['provider']:10} {u['calls']} calls ({u['ok']} ok)")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
