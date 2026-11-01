"""NYSE / NASDAQ trading calendar: regular hours, holidays, early closes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Full-day closures (NYSE official list).
HOLIDAYS: set[date] = {
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}
# 1:00 pm ET closes.
EARLY_CLOSES: set[date] = {
    date(2026, 11, 27), date(2026, 12, 24),
    date(2027, 11, 26),
}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def close_time(d: date) -> time:
    return EARLY_CLOSE if d in EARLY_CLOSES else CLOSE


def session(d: date) -> tuple[datetime, datetime] | None:
    if not is_trading_day(d):
        return None
    return datetime.combine(d, OPEN, ET), datetime.combine(d, close_time(d), ET)


def next_session(after: datetime) -> tuple[datetime, datetime]:
    """First session whose close is after `after`."""
    d = after.astimezone(ET).date()
    for _ in range(14):
        s = session(d)
        if s and s[1] > after:
            return s
        d += timedelta(days=1)
    raise RuntimeError("no session found in 14 days")


def market_state(now: datetime) -> str:
    """'open', 'pre' (trading day, before open), 'after' (trading day, after close) or 'closed'."""
    now = now.astimezone(ET)
    s = session(now.date())
    if s is None:
        return "closed"
    if now < s[0]:
        return "pre"
    if now < s[1]:
        return "open"
    return "after"
