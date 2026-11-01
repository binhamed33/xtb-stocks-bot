"""CLI.

    python -m stockbot run           # scheduler loop (open/close alerts, pre-market brief, close summary)
    python -m stockbot now           # build and send a brief immediately (costs one Claude call)
    python -m stockbot test-discord  # send a test message to the webhook
    python -m stockbot calendar      # show today's state and the next session in your timezone
    python -m stockbot status        # sent events and open ideas
    python -m stockbot halal         # run the Shariah screen on the watch-list and print verdicts
    python -m stockbot preview       # print example cards offline (no network, no keys)
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from . import market_calendar as cal
from .analyst import Analyst
from .bot import State, StocksBot
from .config import ConfigError, load_settings
from .notify import BLUE, Discord


def _logging(log_file) -> None:  # noqa: ANN001
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        from logging.handlers import RotatingFileHandler

        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    logging.getLogger("peewee").setLevel(logging.ERROR)


def main(argv: list[str]) -> int:
    load_dotenv()
    cmd = argv[1] if len(argv) > 1 else "calendar"
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
        print(f"universe    : {len(s.universe)} tickers")
        print(f"discord     : {'configured' if discord.enabled else 'NOT configured'}")
        print(f"claude key  : {'present (AI analysis)' if os.environ.get('ANTHROPIC_API_KEY') else 'missing -> rule-based picks'}")
        return 0

    if cmd == "test-discord":
        ok = discord.embed("✅ اختبار بوت الأسهم", description="البوت يستطيع الإرسال إلى هذه القناة.",
                           fields=[("الأسهم المراقبة", str(len(s.universe)), True), ("توقيتك", s.user_tz.key, True)], color=BLUE)
        print("sent" if ok else "FAILED (check DISCORD_WEBHOOK_URL)")
        return 0 if ok else 1

    if cmd == "halal":
        from .data import fetch_universe
        from .halal import screen

        for sn in fetch_universe(list(s.universe)):
            v = screen(sn.ticker, sn.sector, sn.industry, sn.market_cap, sn.total_debt, sn.total_cash,
                       s.halal_whitelist, s.halal_blacklist)
            print(f"{sn.ticker:6} {v.status:7} {sn.industry[:32]:32} {v.label()}")
        return 0

    if cmd == "preview":
        from .preview import run_preview

        return run_preview(s)

    state = State.load(s.state_file)
    if cmd == "status":
        for day, ev in sorted(state.sent.items())[-7:]:
            print(f"{day}: {', '.join(ev)}")
        print(f"open ideas: {[(o['ticker'], o['entry']) for o in state.open_ideas]}")
        return 0

    if not discord.enabled:
        print("config error: DISCORD_WEBHOOK_URL is required", file=sys.stderr)
        return 2
    if os.environ.get("ANTHROPIC_API_KEY"):
        analyst = Analyst(s)
    else:
        from .rules import RulesAnalyst

        log.warning("ANTHROPIC_API_KEY not set: using rule-based picks (no AI analysis)")
        analyst = RulesAnalyst(s.max_buy_picks, s.max_avoid_picks, s.holdings)

    bot = StocksBot(s, discord, analyst, state)
    if cmd == "now":
        bot.brief_now()
        return 0
    if cmd == "run":
        def _stop(signum, _f):  # noqa: ANN001
            log.warning("signal %s, stopping", signum)
            bot.stop_event.set()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        bot.run_forever()
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
