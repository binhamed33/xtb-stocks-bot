"""CLI.

    python -m stockbot run           # scheduler loop (open/close alerts, pre-market brief, close summary)
    python -m stockbot now           # build and send a brief immediately (costs one Claude call)
    python -m stockbot test-discord  # send a test message to the webhook
    python -m stockbot calendar      # show today's state and the next session in your timezone
    python -m stockbot status        # sent events and open ideas
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
        print(f"claude key  : {'present' if os.environ.get('ANTHROPIC_API_KEY') else 'MISSING'}")
        return 0

    if cmd == "test-discord":
        ok = discord.embed("✅ اختبار بوت الأسهم", description="البوت يستطيع الإرسال إلى هذه القناة.",
                           fields=[("الأسهم المراقبة", str(len(s.universe)), True), ("توقيتك", s.user_tz.key, True)], color=BLUE)
        print("sent" if ok else "FAILED (check DISCORD_WEBHOOK_URL)")
        return 0 if ok else 1

    state = State.load(s.state_file)
    if cmd == "status":
        for day, ev in sorted(state.sent.items())[-7:]:
            print(f"{day}: {', '.join(ev)}")
        print(f"open ideas: {[(o['ticker'], o['entry']) for o in state.open_ideas]}")
        return 0

    if not discord.enabled:
        print("config error: DISCORD_WEBHOOK_URL is required", file=sys.stderr)
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("config error: ANTHROPIC_API_KEY is required", file=sys.stderr)
        return 2

    bot = StocksBot(s, discord, Analyst(s), state)
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
