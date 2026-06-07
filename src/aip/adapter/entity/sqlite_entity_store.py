"""SQLite implementation of EntityStore Protocol.

Separate from ProjectStore for clarity and single-responsibility.
Uses aiosqlite for async-safe database access.

Constructor is lightweight (stores path only). Call ``initialize()``
(async) to create tables before first use, or rely on lazy creation
via ``_get_conn()``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.adapter.store_health import StoreHealthMixin
from aip.foundation.protocols import EntityStore

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_ENTITIES = """
    CREATE TABLE IF NOT EXISTS entities (
        entity_id TEXT PRIMARY KEY,
        entity_type TEXT,
        name TEXT,
        metadata TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""


class SqliteEntityStore(EntityStore, StoreHealthMixin):
    """SQLite-backed EntityStore (basic CRUD for entities).

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
            self._health_track_connect()
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create entities table on the given connection."""
        await conn.execute(_DDL_ENTITIES)
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

    async def get_entity(self, entity_id: str) -> dict | None:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT entity_id, entity_type, name, metadata, created_at, updated_at "
                "FROM entities WHERE entity_id = ?",
                (entity_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "entity_id": row["entity_id"],
                "entity_type": row["entity_type"],
                "name": row["name"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        except Exception:
            await self._reset_conn()
            raise

    async def list_entities(self, entity_type: str | None = None) -> list[dict]:
        conn = await self._get_conn()
        try:
            if entity_type:
                cursor = await conn.execute(
                    "SELECT entity_id, entity_type, name, metadata, created_at, updated_at "
                    "FROM entities WHERE entity_type = ? ORDER BY updated_at DESC",
                    (entity_type,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT entity_id, entity_type, name, metadata, created_at, updated_at "
                    "FROM entities ORDER BY updated_at DESC",
                )

            rows = await cursor.fetchall()
            results = []
            for row in rows:
                results.append(
                    {
                        "entity_id": row["entity_id"],
                        "entity_type": row["entity_type"],
                        "name": row["name"],
                        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    },
                )
            return results
        except Exception:
            await self._reset_conn()
            raise

    async def update_entity(self, entity_id: str, updates: dict) -> None:
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            cursor = await conn.execute(
                "SELECT metadata FROM entities WHERE entity_id = ?",
                (entity_id,),
            )
            existing = await cursor.fetchone()

            if existing:
                meta = json.loads(existing["metadata"]) if existing["metadata"] else {}
                if "metadata" in updates:
                    meta.update(updates["metadata"])
                    updates = {**updates, "metadata": meta}

                set_clauses = []
                params: list[Any] = []
                for k, v in updates.items():
                    if k in ("entity_id", "created_at"):
                        continue
                    if k == "metadata":
                        set_clauses.append("metadata = ?")
                        params.append(json.dumps(v))
                    else:
                        set_clauses.append(f"{k} = ?")
                        params.append(v)
                set_clauses.append("updated_at = ?")
                params.append(now)
                params.append(entity_id)

                await conn.execute(
                    f"UPDATE entities SET {', '.join(set_clauses)} WHERE entity_id = ?",
                    params,
                )
            else:
                meta = updates.get("metadata", {})
                await conn.execute(
                    """
                    INSERT INTO entities (entity_id, entity_type, name, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        updates.get("entity_type"),
                        updates.get("name"),
                        json.dumps(meta),
                        now,
                        now,
                    ),
                )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise
