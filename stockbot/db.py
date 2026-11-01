"""SQLite storage: cache, sharia history, news, reports, watchlist, portfolio, alerts, API usage.

One file, WAL mode, thread-safe through a lock. No ORM: small explicit helpers.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL, source TEXT, fetched_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS stocks (
  ticker TEXT PRIMARY KEY, name TEXT, exchange TEXT, currency TEXT, sector TEXT, industry TEXT,
  country TEXT, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sharia_screens (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL,
  reasons TEXT NOT NULL, ratios TEXT NOT NULL, sources TEXT NOT NULL, unverified TEXT NOT NULL,
  screened_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sharia_ticker ON sharia_screens(ticker, method, screened_at);
CREATE TABLE IF NOT EXISTS news (
  id TEXT PRIMARY KEY, ticker TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, source TEXT NOT NULL,
  published_at REAL, event_type TEXT, sentiment TEXT, summary TEXT, fetched_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_news_ticker ON news(ticker, published_at);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS watchlist (
  user_id INTEGER NOT NULL, ticker TEXT NOT NULL, note TEXT, added_at REAL NOT NULL,
  PRIMARY KEY (user_id, ticker));
CREATE TABLE IF NOT EXISTS portfolio (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, ticker TEXT NOT NULL, qty REAL NOT NULL,
  avg_price REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'USD', bought_at TEXT, invested REAL,
  note TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_portfolio_user ON portfolio(user_id);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, ticker TEXT NOT NULL, kind TEXT NOT NULL,
  threshold REAL, enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS alert_log (
  dedupe_key TEXT PRIMARY KEY, user_id INTEGER NOT NULL, ticker TEXT NOT NULL, kind TEXT NOT NULL,
  message TEXT NOT NULL, fired_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS api_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, endpoint TEXT NOT NULL, ok INTEGER NOT NULL,
  ms INTEGER, day TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_usage_day ON api_usage(provider, day);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, universe_size INTEGER NOT NULL, results TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS xtb_instruments (
  ticker TEXT PRIMARY KEY, status TEXT NOT NULL, instrument_type TEXT, market TEXT, currency TEXT,
  min_qty REAL, fee_note TEXT, source TEXT NOT NULL, verified_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def x(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.lastrowid or cur.rowcount

    # ---- cache -------------------------------------------------------------
    def cache_get(self, key: str) -> tuple[Any, dict] | None:
        row = self.one("SELECT value, expires_at, source, fetched_at FROM cache WHERE key=?", (key,))
        if row is None or row["expires_at"] < time.time():
            return None
        return json.loads(row["value"]), {"source": row["source"], "fetched_at": row["fetched_at"], "cached": True}

    def cache_set(self, key: str, value: Any, ttl: float, source: str = "") -> None:
        now = time.time()
        self.x("INSERT OR REPLACE INTO cache(key, value, expires_at, source, fetched_at) VALUES (?,?,?,?,?)",
               (key, json.dumps(value, ensure_ascii=False, default=str), now + ttl, source, now))

    def cache_purge(self) -> int:
        return self.x("DELETE FROM cache WHERE expires_at < ?", (time.time(),))

    # ---- kv -----------------------------------------------------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        self.x("INSERT OR REPLACE INTO kv(key, value) VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False, default=str)))

    # ---- api usage ----------------------------------------------------------
    def usage_record(self, provider: str, endpoint: str, ok: bool, ms: int = 0) -> None:
        now = time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        self.x("INSERT INTO api_usage(provider, endpoint, ok, ms, day, at) VALUES (?,?,?,?,?,?)",
               (provider, endpoint, 1 if ok else 0, ms, day, now))

    def usage_today(self, provider: str) -> int:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        row = self.one("SELECT COUNT(*) AS n FROM api_usage WHERE provider=? AND day=?", (provider, day))
        return int(row["n"]) if row else 0

    def usage_summary(self, days: int = 7) -> list[dict]:
        since = time.time() - days * 86400
        return [dict(r) for r in self.q(
            "SELECT provider, day, COUNT(*) AS calls, SUM(ok) AS ok FROM api_usage WHERE at>=? GROUP BY provider, day ORDER BY day DESC",
            (since,))]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
