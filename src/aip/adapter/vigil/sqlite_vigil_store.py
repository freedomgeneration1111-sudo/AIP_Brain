"""SqliteVigilStore — implements VigilStore.

Health table for canonicals + vigil_checks audit log.
Read-only actor support (populated by canonical pipeline).
Uses aiosqlite for async-safe database access.

Constructor is lightweight (stores path only). Call ``initialize()``
(async) to create tables before first use, or rely on lazy creation
via ``_get_conn()``.
"""

from __future__ import annotations

import sqlite3

import aiosqlite

from aip.adapter.store_health import StoreHealthMixin
from aip.foundation.protocols import VigilStore

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_CANONICAL_HEALTH = """
    CREATE TABLE IF NOT EXISTS canonical_health (
        artifact_id TEXT PRIMARY KEY,
        last_evaluated TEXT,
        model_slot_used TEXT,
        faithfulness_score REAL,
        domain_coherence_score REAL,
        created_at TEXT,
        status TEXT
    )
"""

_DDL_VIGIL_CHECKS = """
    CREATE TABLE IF NOT EXISTS vigil_checks (
        check_id INTEGER PRIMARY KEY AUTOINCREMENT,
        check_time TEXT NOT NULL,
        canonical_count INTEGER NOT NULL,
        stale_count INTEGER NOT NULL,
        status TEXT NOT NULL,
        re_evaluate_count INTEGER DEFAULT 0,
        entity_issues_found INTEGER DEFAULT 0
    )
"""


class SqliteVigilStore(VigilStore, StoreHealthMixin):
    """SQLite implementation of VigilStore Protocol.

    Uses a persistent aiosqlite connection per instance with error recovery.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._tables_ready = False

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return a persistent connection, creating one if needed.

        Lazily ensures tables on first connection so that callers
        who bypass ``initialize()`` still get a working schema.
        """
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            self._health_track_connect()
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create canonical_health and vigil_checks tables on the given connection."""
        await conn.execute(_DDL_CANONICAL_HEALTH)
        await conn.execute(_DDL_VIGIL_CHECKS)
        await conn.commit()

    async def initialize(self) -> None:
        """Idempotent table creation (called by lifespan / DI container).

        Uses a short-lived connection to create tables, then discards it.
        Subsequent operations use the persistent connection from _get_conn().
        """
        if self._tables_ready:
            return
        conn = await aiosqlite.connect(self._db_path)
        try:
            await self._create_tables(conn)
            self._tables_ready = True
        finally:
            await conn.close()

    async def close(self) -> None:
        """Close the persistent connection."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def _reset_conn(self) -> None:
        """Reset the persistent connection (called on errors)."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._health_track_reset()

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
        except Exception:
            await self._reset_conn()
            raise

    async def list_stale_canonicals(self, threshold_days: int) -> list[dict]:
        from datetime import datetime, timedelta, timezone

        conn = await self._get_conn()
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=threshold_days)).isoformat().replace("+00:00", "Z")
            cursor = await conn.execute(
                "SELECT * FROM canonical_health WHERE last_evaluated < ? OR last_evaluated IS NULL",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def record_vigil_check(self, canonical_count: int, stale_count: int, status: str) -> None:
        from datetime import datetime, timezone

        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            await conn.execute(
                "INSERT INTO vigil_checks (check_time, canonical_count, stale_count, status) VALUES (?, ?, ?, ?)",
                (now, canonical_count, stale_count, status),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def get_last_vigil_check(self) -> dict | None:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT * FROM vigil_checks ORDER BY check_time DESC LIMIT 1")
            row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception:
            await self._reset_conn()
            raise
