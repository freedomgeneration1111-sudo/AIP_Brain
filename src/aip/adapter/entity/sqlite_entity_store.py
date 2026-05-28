"""SQLite implementation of EntityStore Protocol.

Per CHUNK-8.0b prose + ANNEX (exact).
Separate from ProjectStore per §5 / Appendix D.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols import EntityStore


class SqliteEntityStore(EntityStore):
    """SQLite-backed EntityStore (basic CRUD for entities)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_table(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    entity_type TEXT,
                    name TEXT,
                    metadata TEXT NOT NULL,  -- JSON
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.commit()
        finally:
            conn.close()

    async def initialize(self) -> None:
        self._ensure_table()

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def get_entity(self, entity_id: str) -> dict | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT entity_id, entity_type, name, metadata, created_at, updated_at FROM entities WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
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
        finally:
            pass

    async def list_entities(self, entity_type: str | None = None) -> list[dict]:
        conn = self._get_conn()
        try:
            if entity_type:
                rows = conn.execute(
                    "SELECT entity_id, entity_type, name, metadata, created_at, updated_at "
                    "FROM entities WHERE entity_type = ? ORDER BY updated_at DESC",
                    (entity_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT entity_id, entity_type, name, metadata, created_at, updated_at "
                    "FROM entities ORDER BY updated_at DESC"
                ).fetchall()

            results = []
            for row in rows:
                results.append({
                    "entity_id": row["entity_id"],
                    "entity_type": row["entity_type"],
                    "name": row["name"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
            return results
        finally:
            pass

    async def update_entity(self, entity_id: str, updates: dict) -> None:
        conn = self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            # Simple upsert-style update (merge metadata if present)
            existing = await self.get_entity(entity_id)
            if existing:
                meta = existing.get("metadata", {})
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

                conn.execute(
                    f"UPDATE entities SET {', '.join(set_clauses)} WHERE entity_id = ?",
                    params,
                )
            else:
                # Create if not exists (lenient for L2 scope)
                meta = updates.get("metadata", {})
                conn.execute(
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
            conn.commit()
        finally:
            pass
