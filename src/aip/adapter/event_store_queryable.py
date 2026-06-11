"""Queryable event store — timeline reconstruction.

Append-only: events are never modified or deleted.
Supports query by artifact_id and event_type for review,
DEFINER audit, and Sexton failure analysis.
Uses aiosqlite for async-safe database access.

Constructor is lightweight (stores path only). Call ``initialize()``
(async) to create tables before first use, or rely on lazy creation
via ``_get_conn()``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import aiosqlite

from aip.adapter.store_health import StoreHealthMixin
from aip.foundation.schemas import Event

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_EVENTS = """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        actor TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        from_state TEXT,
        to_state TEXT,
        metadata_json TEXT,
        created_at TEXT NOT NULL
    )
"""

_DDL_IDX_EVENTS_ARTIFACT = """
    CREATE INDEX IF NOT EXISTS idx_events_artifact
    ON events(artifact_id)
"""

_DDL_IDX_EVENTS_TYPE = """
    CREATE INDEX IF NOT EXISTS idx_events_type
    ON events(event_type)
"""

_DDL_IDX_EVENTS_CREATED = """
    CREATE INDEX IF NOT EXISTS idx_events_created
    ON events(created_at)
"""


class QueryableEventStore(StoreHealthMixin):
    """EventStore with query support for timeline reconstruction.

    Uses a persistent aiosqlite connection per instance with error recovery.
    Includes connection health metrics via StoreHealthMixin.
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
            # Sprint 6.3: busy_timeout to handle concurrent write contention
            await self._conn.execute("PRAGMA busy_timeout=5000")
            self._health_track_connect()
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create events table and indexes on the given connection."""
        await conn.execute(_DDL_EVENTS)
        await conn.execute(_DDL_IDX_EVENTS_ARTIFACT)
        await conn.execute(_DDL_IDX_EVENTS_TYPE)
        await conn.execute(_DDL_IDX_EVENTS_CREATED)
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

    async def write_event(
        self,
        event_type: str,
        actor: str,
        artifact_id: str,
        from_state: str | None = None,
        to_state: str | None = None,
        **kwargs,
    ) -> None:
        """Write an event. Append-only — never modifies or deletes."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            meta_json = json.dumps(kwargs) if kwargs else "{}"
            await conn.execute(
                "INSERT INTO events "
                "(event_type, actor, artifact_id, from_state, to_state, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_type, actor, artifact_id, from_state, to_state, meta_json, now),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def query(
        self,
        artifact_id: str | None = None,
        event_type: str | None = None,
        actor: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events by filters, most recent first."""
        conn = await self._get_conn()
        try:
            conditions = []
            params: list = []

            if artifact_id is not None:
                conditions.append("artifact_id = ?")
                params.append(artifact_id)
            if event_type is not None:
                conditions.append("event_type = ?")
                params.append(event_type)
            if actor is not None:
                conditions.append("actor = ?")
                params.append(actor)

            where = " AND ".join(conditions) if conditions else "1=1"
            sql = (
                f"SELECT id, event_type, actor, artifact_id, from_state, to_state, "
                f"metadata_json, created_at FROM events WHERE {where} "
                f"ORDER BY created_at DESC LIMIT ?"
            )
            params.append(limit)

            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                id_, et, actor, aid, fs, ts, mj, ca = row
                results.append(
                    Event(
                        id=id_,
                        event_type=et,
                        actor=actor,
                        artifact_id=aid,
                        from_state=fs,
                        to_state=ts,
                        timestamp=ca,
                        metadata=json.loads(mj) if mj else {},
                    ),
                )
            return results
        except Exception:
            await self._reset_conn()
            raise

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
