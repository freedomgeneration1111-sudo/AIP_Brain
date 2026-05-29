"""SqliteVigilStore — implements VigilStore.

Per spec: health table for canonicals + vigil_checks audit log.
Read-only actor support (populated by 9.2 canonical pipeline).
Phase 3: migrated from blocking sqlite3 to aiosqlite to avoid event loop blocking.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from aip.foundation.protocols import VigilStore


class SqliteVigilStore(VigilStore):
    """SQLite implementation of VigilStore Protocol (Phase 7).

    Uses aiosqlite for async-compatible database access.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._ensure_tables_sync()

    def _ensure_tables_sync(self) -> None:
        """Synchronous table creation during init (runs once at startup)."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS canonical_health (
                    artifact_id TEXT PRIMARY KEY,
                    last_evaluated TEXT,
                    model_slot_used TEXT,
                    faithfulness_score REAL,
                    domain_coherence_score REAL,
                    created_at TEXT,
                    status TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vigil_checks (
                    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    check_time TEXT NOT NULL,
                    canonical_count INTEGER NOT NULL,
                    stale_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    re_evaluate_count INTEGER DEFAULT 0,
                    entity_issues_found INTEGER DEFAULT 0
                )
            """)
            conn.commit()
        finally:
            conn.close()

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def _ensure_tables(self) -> None:
        conn = await aiosqlite.connect(self._db_path)
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS canonical_health (
                    artifact_id TEXT PRIMARY KEY,
                    last_evaluated TEXT,
                    model_slot_used TEXT,
                    faithfulness_score REAL,
                    domain_coherence_score REAL,
                    created_at TEXT,
                    status TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS vigil_checks (
                    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    check_time TEXT NOT NULL,
                    canonical_count INTEGER NOT NULL,
                    stale_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    re_evaluate_count INTEGER DEFAULT 0,
                    entity_issues_found INTEGER DEFAULT 0
                )
            """)
            await conn.commit()
        finally:
            await conn.close()

    async def initialize(self) -> None:
        await self._ensure_tables()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def get_canonical_health(self, artifact_id: str) -> dict | None:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM canonical_health WHERE artifact_id = ?",
                (artifact_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return dict(row)
        finally:
            await conn.close()
            self._conn = None

    async def list_stale_canonicals(self, threshold_days: int) -> list[dict]:
        conn = await self._get_conn()
        try:
            # Simplified staleness: last_evaluated older than threshold_days
            cutoff = (datetime.now(timezone.utc) - timedelta(days=threshold_days)).isoformat().replace("+00:00", "Z")
            cursor = await conn.execute(
                "SELECT * FROM canonical_health WHERE last_evaluated < ? OR last_evaluated IS NULL",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            await conn.close()
            self._conn = None

    async def record_vigil_check(self, canonical_count: int, stale_count: int, status: str) -> None:
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            await conn.execute(
                "INSERT INTO vigil_checks (check_time, canonical_count, stale_count, status) VALUES (?, ?, ?, ?)",
                (now, canonical_count, stale_count, status),
            )
            await conn.commit()
        finally:
            await conn.close()
            self._conn = None

    async def get_last_vigil_check(self) -> dict | None:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT * FROM vigil_checks ORDER BY check_time DESC LIMIT 1")
            row = await cursor.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()
            self._conn = None
