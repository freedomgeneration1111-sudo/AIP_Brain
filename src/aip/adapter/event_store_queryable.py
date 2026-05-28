"""Queryable event store — timeline reconstruction.

Append-only: events are never modified or deleted.
Supports query by artifact_id and event_type for review,
DEFINER audit, and Sexton failure analysis.
Phase 3: migrated from blocking sqlite3 to aiosqlite to avoid event loop blocking.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import aiosqlite

from aip.foundation.schemas import Event


class QueryableEventStore:
    """EventStore with query support for timeline reconstruction.

    Uses aiosqlite for async-compatible database access.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        # Initialize tables synchronously during __init__ for backward compat
        self._init_tables_sync()

    def _init_tables_sync(self) -> None:
        """Synchronous table creation during init (runs once at startup)."""
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
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
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_artifact
                ON events(artifact_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_type
                ON events(event_type)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_created
                ON events(created_at)
            """)
            conn.commit()
        finally:
            conn.close()

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def initialize(self) -> None:
        """Async initialization — ensures tables exist."""
        conn = await aiosqlite.connect(self._db_path)
        try:
            await conn.execute("""
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
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_artifact
                ON events(artifact_id)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_type
                ON events(event_type)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_created
                ON events(created_at)
            """)
            await conn.commit()
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
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(kwargs) if kwargs else "{}"
        await conn.execute(
            "INSERT INTO events (event_type, actor, artifact_id, from_state, to_state, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_type, actor, artifact_id, from_state, to_state, meta_json, now),
        )
        await conn.commit()

    async def query(
        self,
        artifact_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events by filters, most recent first."""
        conn = await self._get_conn()
        conditions = []
        params: list = []

        if artifact_id is not None:
            conditions.append("artifact_id = ?")
            params.append(artifact_id)
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT id, event_type, actor, artifact_id, from_state, to_state, metadata_json, created_at FROM events WHERE {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            id_, et, actor, aid, fs, ts, mj, ca = row
            results.append(Event(
                id=id_,
                event_type=et,
                actor=actor,
                artifact_id=aid,
                from_state=fs,
                to_state=ts,
                timestamp=ca,
                metadata=json.loads(mj) if mj else {},
            ))
        return results

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
