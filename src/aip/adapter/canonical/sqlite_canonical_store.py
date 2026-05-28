"""SQLite implementation of CanonicalStore Protocol.

Per prose + ANNEX (exact).
Enforces "approved_by == 'definer'" on write (DEFINER sovereignty).
Phase 3: migrated from blocking sqlite3 to aiosqlite to avoid event loop blocking.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.foundation.protocols import CanonicalStore


class SqliteCanonicalStore(CanonicalStore):
    """SQLite-backed CanonicalStore.

    Stores only DEFINER-approved canonical artifacts (distinct from versioned generated artifacts).
    Uses aiosqlite for async-compatible database access.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._ensure_table_sync()

    def _ensure_table_sync(self) -> None:
        """Synchronous table creation during init (runs once at startup)."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS canonical_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,  -- JSON
                    approved_by TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    superseded_by TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_canonical_domain
                ON canonical_artifacts(domain)
            """)
            conn.commit()
        finally:
            conn.close()

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def _ensure_table(self) -> None:
        conn = await aiosqlite.connect(self._db_path)
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS canonical_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,  -- JSON
                    approved_by TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    superseded_by TEXT
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_canonical_domain
                ON canonical_artifacts(domain)
            """)
            await conn.commit()
        finally:
            await conn.close()

    async def initialize(self) -> None:
        await self._ensure_table()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

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
        finally:
            await conn.close()
            self._conn = None

    async def write_canonical(
        self, artifact_id: str, content: dict, approved_by: str
    ) -> None:
        if approved_by != "definer":
            # Only DEFINER may create canonicals
            raise PermissionError(
                f"write_canonical requires approved_by='definer', got {approved_by!r}"
            )

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
                (artifact_id, content_json, approved_by, content.get("domain", "") if isinstance(content, dict) else "", now),
            )
            await conn.commit()
        finally:
            await conn.close()
            self._conn = None

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
                    "ORDER BY created_at DESC"
                )

            rows = await cursor.fetchall()
            results = []
            for row in rows:
                results.append({
                    "artifact_id": row["artifact_id"],
                    "content": json.loads(row["content"]),
                    "approved_by": row["approved_by"],
                    "domain": row["domain"],
                    "created_at": row["created_at"],
                    "superseded_by": row["superseded_by"],
                })
            return results
        finally:
            await conn.close()
            self._conn = None
