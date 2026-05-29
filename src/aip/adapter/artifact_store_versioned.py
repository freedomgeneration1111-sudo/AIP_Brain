"""Versioned artifact store — preserves every version.

Each write appends a new version; no version is ever overwritten.
Uses SQLite for persistence.
Uses aiosqlite for async-safe database access.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import aiosqlite


class VersionedArtifactStore:
    """ArtifactStore implementation with version preservation.

    Every version is preserved for provenance.
    Generated ≠ canonical — versions support separation.
    Per architecture spec: artifact hash is not approval; supersession marks old entries, does not delete them.

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
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (id, version)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_artifacts_id
                ON artifacts(id)
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
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (id, version)
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_artifacts_id
                ON artifacts(id)
            """)
            await conn.commit()
        finally:
            await conn.close()

    async def initialize(self) -> None:
        """Async initialization — ensures tables exist."""
        await self._ensure_table()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def write(self, id: str, content: str, metadata: dict) -> None:
        """Write artifact content, appending a new version.

        Version number is auto-incremented per artifact id.
        Metadata is merged with version and timestamp.
        """
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT MAX(version) FROM artifacts WHERE id = ?", (id,))
            row = await cursor.fetchone()
            next_version = (row[0] if row and row[0] is not None else 0) + 1

            now = datetime.now(timezone.utc).isoformat()
            enriched_metadata = {**(metadata or {}), "version": next_version, "created_at": now}
            meta_json = json.dumps(enriched_metadata)

            await conn.execute(
                "INSERT INTO artifacts (id, version, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (id, next_version, content, meta_json, now),
            )
            await conn.commit()
        finally:
            await conn.close()
            self._conn = None

    async def read(self, id: str, version: int | None = None) -> str:
        """Read artifact content by id and optional version.

        version=None: returns latest version.
        version=N: returns specific version.
        Raises KeyError if artifact or version not found.
        """
        conn = await self._get_conn()
        try:
            if version is None:
                cursor = await conn.execute(
                    "SELECT content FROM artifacts WHERE id = ? ORDER BY version DESC LIMIT 1",
                    (id,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT content FROM artifacts WHERE id = ? AND version = ?",
                    (id, version),
                )
            row = await cursor.fetchone()
            if row is None:
                raise KeyError(f"Artifact {id!r} version {version} not found")
            return row[0]
        finally:
            await conn.close()
            self._conn = None

    async def list_versions(self, id: str) -> list[int]:
        """List all version numbers for an artifact, ascending order."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT version FROM artifacts WHERE id = ? ORDER BY version ASC", (id,))
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
        finally:
            await conn.close()
            self._conn = None
