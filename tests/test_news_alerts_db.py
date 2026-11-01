import time

from stockbot.alerts import Alerts
from stockbot.db import Database
from stockbot.news import intelligence as ni
from stockbot.providers.base import Filing, NewsItem


def _n(title, days=1, summary=""):
    return NewsItem(id=str(abs(hash(title)))[:10], ticker="X", title=title, url="https://example.com/" + str(abs(hash(title))),
                    source="CNBC", provider="Fake", published_at=time.time() - days * 86400, summary=summary)


def test_news_classification_and_score():
    items = [_n("Company beats earnings, raises guidance", 1), _n("FDA approves new drug", 2),
             _n("Shares plunge after lawsuit filed", 1), _n("CEO steps down", 10), _n("Old news", 40)]
    v = ni.build(items, [Filing("8-K", "2026-10-30", "material event (8-K)", "https://sec.gov/a")], ["Yahoo Finance"])
    types = [c.event_type for c in v.items]
    assert types[0] == "earnings" and types[1] == "fda" and types[2] == "lawsuit" and types[3] == "management"
    assert v.items[0].sentiment == "positive" and v.items[2].sentiment == "negative"
    assert 0 <= v.score <= 20 and any("إفصاحات SEC" in n for n in v.notes) and v.has_recent


def test_news_empty_is_explicit():
    v = ni.build([], [], ["Yahoo Finance"])
    assert v.score == 10 and "لا توجد أخبار" in v.notes[0]


def test_db_cache_and_usage(tmp_path):
    db = Database(tmp_path / "t.sqlite3")
    db.cache_set("k", {"a": 1}, ttl=60, source="src")
    val, meta = db.cache_get("k")
    assert val == {"a": 1} and meta["source"] == "src" and meta["cached"]
    db.cache_set("old", 1, ttl=-1)
    assert db.cache_get("old") is None and db.cache_purge() >= 1
    db.usage_record("gemini", "x", True); db.usage_record("gemini", "y", False)
    assert db.usage_today("gemini") == 2 and db.usage_summary()[0]["calls"] == 2
    db.kv_set("a", [1, 2]); assert db.kv_get("a") == [1, 2]


def test_alert_rules_and_dedupe(tmp_path):
    db = Database(tmp_path / "t.sqlite3")
    al = Alerts(db)
    i = al.add(1, "aapl", "target", 150)
    al.add(1, "AAPL", "drop", 3)
    assert len(al.list(1)) == 2
    ev = al.evaluate(1, "AAPL", price=151, prev_close=160, drop_pct_default=5, rules=al.list(1))
    assert {e.kind for e in ev} == {"target", "drop"}
    assert al.evaluate(1, "AAPL", price=151, prev_close=160, drop_pct_default=5, rules=al.list(1)) == []
    al.remove(1, i)
    assert len(al.list(1)) == 1
    # sharia change + trend flip
    al.evaluate(1, "AAPL", price=None, prev_close=None, drop_pct_default=5, rules=[], sharia_status="HALAL", trend_medium="up", auto_monitor=True)
    ev = al.evaluate(1, "AAPL", price=None, prev_close=None, drop_pct_default=5, rules=[], sharia_status="NON_COMPLIANT", trend_medium="down", auto_monitor=True)
    assert {e.kind for e in ev} == {"sharia", "trend"}
    try:
        al.add(1, "AAPL", "target")
        assert False
    except ValueError:
        pass
