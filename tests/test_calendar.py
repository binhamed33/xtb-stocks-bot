from datetime import date, datetime
from zoneinfo import ZoneInfo

from stockbot import market_calendar as cal

ET = cal.ET


def test_weekend_and_holiday_closed():
    assert not cal.is_trading_day(date(2026, 11, 1))   # Sunday
    assert not cal.is_trading_day(date(2026, 11, 26))  # Thanksgiving
    assert cal.is_trading_day(date(2026, 11, 2))


def test_early_close():
    s = cal.session(date(2026, 11, 27))
    assert s and s[1].hour == 13


def test_market_state_transitions():
    d = date(2026, 11, 2)
    assert cal.market_state(datetime(2026, 11, 2, 8, 0, tzinfo=ET)) == "pre"
    assert cal.market_state(datetime(2026, 11, 2, 10, 0, tzinfo=ET)) == "open"
    assert cal.market_state(datetime(2026, 11, 2, 16, 0, tzinfo=ET)) == "after"
    assert cal.market_state(datetime(2026, 11, 1, 10, 0, tzinfo=ET)) == "closed"
    assert cal.session(d)[0] == datetime(2026, 11, 2, 9, 30, tzinfo=ET)


def test_next_session_skips_weekend_in_muscat_time():
    sat = datetime(2026, 10, 31, 12, 0, tzinfo=ZoneInfo("Asia/Muscat"))
    o, c = cal.next_session(sat)
    assert o.date() == date(2026, 11, 2)
    assert o.astimezone(ZoneInfo("Asia/Muscat")).hour == 18  # 09:30 ET = 18:30 Muscat (EST)
