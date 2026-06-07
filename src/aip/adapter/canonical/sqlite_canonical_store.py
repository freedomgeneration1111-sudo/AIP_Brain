"""SQLite implementation of CanonicalStore Protocol.

Enforces "approved_by == 'definer'" on write (DEFINER sovereignty).
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
from aip.foundation.protocols import CanonicalStore

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_CANONICAL_ARTIFACTS = """
    CREATE TABLE IF NOT EXISTS canonical_artifacts (
        artifact_id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        approved_by TEXT NOT NULL,
        domain TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        superseded_by TEXT
    )
"""

_DDL_IDX_CANONICAL_DOMAIN = """
    CREATE INDEX IF NOT EXISTS idx_canonical_domain
    ON canonical_artifacts(domain)
"""


class SqliteCanonicalStore(CanonicalStore, StoreHealthMixin):
    """SQLite-backed CanonicalStore.

    Stores only DEFINER-approved canonical artifacts (distinct from versioned generated artifacts).
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
        """Create canonical_artifacts table and index on the given connection."""
        await conn.execute(_DDL_CANONICAL_ARTIFACTS)
        await conn.execute(_DDL_IDX_CANONICAL_DOMAIN)
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

    async def read_canonical(self, artifact_id: str) -> dict | None:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT content, approved_by, domain, created_at, superseded_by FROM canonical_artifacts "
                "WHERE artifact_id = ? AND superseded_by IS NULL",
                (artifact_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "artifact_id": artifact_id,
                "content": json.loads(row["content"]),
                "approved_by": row["approved_by"],
                "domain": row["domain"],
                "created_at": row["created_at"],
                "superseded_by": row["superseded_by"],
            }
        except Exception:
            await self._reset_conn()
            raise

    async def write_canonical(self, artifact_id: str, content: dict, approved_by: str) -> None:
        if approved_by != "definer":
            raise PermissionError(f"write_canonical requires approved_by='definer', got {approved_by!r}")

        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            content_json = json.dumps(content or {})
            await conn.execute(
                """
                INSERT OR REPLACE INTO canonical_artifacts
                    (artifact_id, content, approved_by, domain, created_at, superseded_by)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    artifact_id,
                    content_json,
                    approved_by,
                    content.get("domain", "") if isinstance(content, dict) else "",
                    now,
                ),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def list_canonical(self, domain: str | None = None) -> list[dict]:
        conn = await self._get_conn()
        try:
            if domain:
                cursor = await conn.execute(
                    "SELECT artifact_id, content, approved_by, domain, created_at, superseded_by "
                    "FROM canonical_artifacts WHERE domain = ? AND superseded_by IS NULL "
                    "ORDER BY created_at DESC",
                    (domain,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT artifact_id, content, approved_by, domain, created_at, superseded_by "
                    "FROM canonical_artifacts WHERE superseded_by IS NULL "
                    "ORDER BY created_at DESC",
                )

            rows = await cursor.fetchall()
            results = []
            for row in rows:
                results.append(
                    {
                        "artifact_id": row["artifact_id"],
                        "content": json.loads(row["content"]),
                        "approved_by": row["approved_by"],
                        "domain": row["domain"],
                        "created_at": row["created_at"],
                        "superseded_by": row["superseded_by"],
                    },
                )
            return results
        except Exception:
            await self._reset_conn()
            raise
