"""SQLite database layer — WAL-mode SQLite with async executor dispatch."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from config import config

logger = logging.getLogger(__name__)

_db_executor: Optional[ThreadPoolExecutor] = None
_db_path: str = ""
_db_ready: asyncio.Event = asyncio.Event()

SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS vip_users (
    user_id    INTEGER PRIMARY KEY,
    expiry     REAL,
    first_seen REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS all_users (
    user_id    INTEGER PRIMARY KEY,
    first_seen REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_seen  REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS cards (
    code       TEXT PRIMARY KEY,
    card_type  TEXT NOT NULL DEFAULT 'forever',
    days       INTEGER,
    used       INTEGER NOT NULL DEFAULT 0,
    used_by    INTEGER,
    used_at    REAL,
    created_by INTEGER,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    inviter_id INTEGER NOT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS stats_daily (
    date       TEXT PRIMARY KEY,
    new_users  INTEGER NOT NULL DEFAULT 0,
    card_activations INTEGER NOT NULL DEFAULT 0,
    searches   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS search_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    keyword    TEXT NOT NULL,
    searched_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_history_user ON search_history(user_id, searched_at DESC);
CREATE INDEX IF NOT EXISTS idx_cards_used ON cards(used);
"""


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def _init_db(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = _dict_factory
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


_local: threading.local | None = None
_connections: set[sqlite3.Connection] = set()
_connections_lock: threading.Lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    assert _local is not None, "Database not initialized; call start_database() first"
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_db_path, check_same_thread=False)
        conn.row_factory = _dict_factory
        _local.conn = conn
        with _connections_lock:
            _connections.add(conn)
    return conn


async def _run(fn, *args):
    """Run a sync function in the DB thread pool executor.
    
    NOTE: asyncio.get_running_loop().run_in_executor() does NOT accept kwargs,
    so this function intentionally does not pass through **kwargs.
    """
    await _db_ready.wait()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_db_executor, fn, *args)


async def _exec(sql: str, params=()):
    def _do():
        c = _conn()
        c.execute(sql, params)
        c.commit()
    await _run(_do)


async def _exec_rowcount(sql: str, params=()) -> int:
    def _do():
        c = _conn()
        cur = c.execute(sql, params)
        rc = cur.rowcount
        c.commit()
        return rc
    return await _run(_do)


async def _fetch_all(sql: str, params=()) -> list[dict]:
    def _do():
        c = _conn()
        return c.execute(sql, params).fetchall()
    return await _run(_do)


async def _fetch_one(sql: str, params=()) -> Optional[dict]:
    def _do():
        c = _conn()
        return c.execute(sql, params).fetchone()
    return await _run(_do)


async def _fetch_val(sql: str, params=()):
    def _do():
        c = _conn()
        row = c.execute(sql, params).fetchone()
        return row and next(iter(row.values())) if row else None
    return await _run(_do)


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

async def start_database():
    global _db_executor, _db_path, _local
    _local = threading.local()
    _db_path = config.DB_PATH
    _db_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="db")
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
    await asyncio.get_running_loop().run_in_executor(_db_executor, _init_db, _db_path)
    _db_ready.set()
    logger.info("Database started: %s", _db_path)


async def stop_database():
    global _local
    _db_ready.clear()
    with _connections_lock:
        for conn in list(_connections):
            try:
                conn.close()
            except Exception:
                pass
        _connections.clear()
    if _db_executor:
        _db_executor.shutdown(wait=True)
    logger.info("Database stopped")


# ---------------------------------------------------------------------------
# VIP users
# ---------------------------------------------------------------------------

async def db_load_vip() -> dict[int, Optional[float]]:
    rows = await _fetch_all("SELECT user_id, expiry FROM vip_users")
    return {r["user_id"]: r["expiry"] for r in rows}


async def db_save_vip(user_id: int, expiry: Optional[float]):
    await _exec(
        "INSERT INTO vip_users (user_id, expiry, first_seen) VALUES (?, ?, strftime('%s')) "
        "ON CONFLICT(user_id) DO UPDATE SET expiry=excluded.expiry",
        (user_id, expiry),
    )


async def db_delete_expired_vip() -> int:
    now = time.time()

    def _do():
        c = _conn()
        c.execute("DELETE FROM vip_users WHERE expiry IS NOT NULL AND expiry < ?", (now,))
        c.commit()
        return c.rowcount
    return await _run(_do)


async def db_vip_count() -> int:
    return await _fetch_val("SELECT COUNT(*) FROM vip_users") or 0


# ---------------------------------------------------------------------------
# All users
# ---------------------------------------------------------------------------

async def db_load_users() -> set[int]:
    rows = await _fetch_all("SELECT user_id FROM all_users")
    return {r["user_id"] for r in rows}


async def db_add_user(user_id: int):
    await _exec(
        "INSERT OR IGNORE INTO all_users (user_id, first_seen, last_seen) VALUES (?, strftime('%s'), strftime('%s'))",
        (user_id,),
    )


async def db_user_count() -> int:
    return await _fetch_val("SELECT COUNT(*) FROM all_users") or 0


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

async def db_save_card(code: str, card_type: str, days: Optional[int], created_by: int):
    await _exec(
        "INSERT OR IGNORE INTO cards (code, card_type, days, created_by) VALUES (?, ?, ?, ?)",
        (code, card_type, days, created_by),
    )


async def db_activate_card(code: str, user_id: int) -> bool:
    return (await _exec_rowcount(
        "UPDATE cards SET used=1, used_by=?, used_at=strftime('%s') WHERE code=? AND used=0",
        (user_id, code),
    )) > 0


async def db_card_count_used() -> int:
    return await _fetch_val("SELECT COUNT(*) FROM cards WHERE used=1") or 0


async def db_card_count_total() -> int:
    return await _fetch_val("SELECT COUNT(*) FROM cards") or 0


async def db_list_unused_cards() -> list[dict]:
    return await _fetch_all("SELECT code, card_type, days FROM cards WHERE used=0 ORDER BY code")


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------

async def db_load_invites() -> dict[str, str]:
    rows = await _fetch_all("SELECT code, inviter_id FROM invites")
    return {r["code"]: str(r["inviter_id"]) for r in rows}


async def db_save_invite(code: str, inviter_id: int):
    await _exec(
        "INSERT OR IGNORE INTO invites (code, inviter_id) VALUES (?, ?)",
        (code, inviter_id),
    )


async def db_find_invite(code: str) -> Optional[str]:
    row = await _fetch_one("SELECT inviter_id FROM invites WHERE code = ?", (code,))
    return str(row["inviter_id"]) if row else None


# ---------------------------------------------------------------------------
# Daily stats
# ---------------------------------------------------------------------------

_VALID_STAT_COLUMNS = {"new_users", "searches", "card_activations"}


async def db_bump_stat(date_str: str, column: str, delta: int = 1):
    if column not in _VALID_STAT_COLUMNS:
        raise ValueError(f"Invalid stat column: {column!r}")
    await _exec(
        f"INSERT INTO stats_daily (date, {column}) VALUES (?, ?) "
        f"ON CONFLICT(date) DO UPDATE SET {column} = {column} + excluded.{column}",
        (date_str, delta),
    )


async def db_get_stats_last_days(days: int = 7) -> list[dict]:
    return await _fetch_all(
        "SELECT * FROM stats_daily WHERE date >= date('now', ?) ORDER BY date DESC",
        (f"-{days} days",),
    )


async def db_add_search_history(user_id: int, keyword: str):
    await _exec(
        "INSERT OR IGNORE INTO search_history (user_id, keyword) VALUES (?, ?)",
        (user_id, keyword.lower()),
    )


async def db_get_user_history(user_id: int, limit: int = 6) -> list[str]:
    rows = await _fetch_all(
        "SELECT keyword, MAX(searched_at) AS last_search FROM search_history "
        "WHERE user_id = ? GROUP BY keyword ORDER BY last_search DESC LIMIT ?",
        (user_id, limit),
    )
    return [r["keyword"] for r in rows]
