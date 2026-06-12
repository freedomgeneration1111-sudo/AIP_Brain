"""Versioned artifact store — preserves every version.

Each write appends a new version; no version is ever overwritten.
Uses SQLite for persistence.
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

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_ARTIFACTS = """
    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT NOT NULL,
        version INTEGER NOT NULL,
        content TEXT NOT NULL,
        metadata_json TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (id, version)
    )
"""

_DDL_IDX_ARTIFACTS_ID = """
    CREATE INDEX IF NOT EXISTS idx_artifacts_id
    ON artifacts(id)
"""


class VersionedArtifactStore(StoreHealthMixin):
    """ArtifactStore implementation with version preservation.

    Every version is preserved for provenance.
    Generated != canonical — versions support separation.
    Artifact hash is not approval; supersession marks old entries, does not delete them.

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
        """Create artifacts table and index on the given connection."""
        await conn.execute(_DDL_ARTIFACTS)
        await conn.execute(_DDL_IDX_ARTIFACTS_ID)
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
        except Exception:
            await self._reset_conn()
            raise

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
        except KeyError:
            raise
        except Exception:
            await self._reset_conn()
            raise

    async def list_versions(self, id: str) -> list[int]:
        """List all version numbers for an artifact, ascending order."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT version FROM artifacts WHERE id = ? ORDER BY version ASC", (id,))
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def read_metadata(self, id: str, version: int | None = None) -> dict:
        """Read artifact metadata by id and optional version.

        version=None: returns latest version metadata.
        version=N: returns specific version metadata.
        Raises KeyError if artifact or version not found.
        """
        conn = await self._get_conn()
        try:
            if version is None:
                cursor = await conn.execute(
                    "SELECT metadata_json FROM artifacts WHERE id = ? ORDER BY version DESC LIMIT 1",
                    (id,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT metadata_json FROM artifacts WHERE id = ? AND version = ?",
                    (id, version),
                )
            row = await cursor.fetchone()
            if row is None:
                raise KeyError(f"Artifact {id!r} version {version} not found")
            return json.loads(row[0]) if row[0] else {}
        except KeyError:
            raise
        except Exception:
            await self._reset_conn()
            raise

    async def read_with_metadata(self, id: str, version: int | None = None) -> tuple[str, dict]:
        """Read artifact content and metadata together.

        Returns (content, metadata) tuple.
        Raises KeyError if artifact or version not found.
        """
        conn = await self._get_conn()
        try:
            if version is None:
                cursor = await conn.execute(
                    "SELECT content, metadata_json FROM artifacts WHERE id = ? ORDER BY version DESC LIMIT 1",
                    (id,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT content, metadata_json FROM artifacts WHERE id = ? AND version = ?",
                    (id, version),
                )
            row = await cursor.fetchone()
            if row is None:
                raise KeyError(f"Artifact {id!r} version {version} not found")
            return row[0], json.loads(row[1]) if row[1] else {}
        except KeyError:
            raise
        except Exception:
            await self._reset_conn()
            raise

    async def read_metadata_batch(self, ids: list[str]) -> dict[str, dict]:
        """Read metadata for multiple artifacts in a single query.

        Returns a dict mapping artifact_id -> metadata dict.
        Artifacts not found are silently omitted from the result.
        Always reads the latest version of each artifact.
        """
        if not ids:
            return {}
        conn = await self._get_conn()
        try:
            placeholders = ",".join("?" for _ in ids)
            sql = (
                f"SELECT a.id, a.metadata_json FROM artifacts a "
                f"INNER JOIN ("
                f"  SELECT id, MAX(version) as max_ver FROM artifacts GROUP BY id"
                f") latest ON a.id = latest.id AND a.version = latest.max_ver "
                f"WHERE a.id IN ({placeholders})"
            )
            cursor = await conn.execute(sql, ids)
            rows = await cursor.fetchall()
            return {row[0]: json.loads(row[1]) if row[1] else {} for row in rows}
        except Exception:
            await self._reset_conn()
            raise

    async def read_with_metadata_batch(self, ids: list[str]) -> dict[str, tuple[str, dict]]:
        """Read content and metadata for multiple artifacts in a single query.

        Returns a dict mapping artifact_id -> (content, metadata) tuple.
        Artifacts not found are silently omitted from the result.
        Always reads the latest version of each artifact.
        """
        if not ids:
            return {}
        conn = await self._get_conn()
        try:
            placeholders = ",".join("?" for _ in ids)
            sql = (
                f"SELECT a.id, a.content, a.metadata_json FROM artifacts a "
                f"INNER JOIN ("
                f"  SELECT id, MAX(version) as max_ver FROM artifacts GROUP BY id"
                f") latest ON a.id = latest.id AND a.version = latest.max_ver "
                f"WHERE a.id IN ({placeholders})"
            )
            cursor = await conn.execute(sql, ids)
            rows = await cursor.fetchall()
            return {row[0]: (row[1], json.loads(row[2]) if row[2] else {}) for row in rows}
        except Exception:
            await self._reset_conn()
            raise

    async def list_artifacts_by_metadata(
        self,
        key: str,
        value: str,
        artifact_type: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """List artifacts where metadata_json contains a key-value pair.

        Queries the latest version of each artifact. Returns list of dicts
        with id, content, metadata, created_at fields.
        """
        conn = await self._get_conn()
        try:
            if artifact_type:
                sql = """
                    SELECT a.id, a.content, a.metadata_json, a.created_at
                    FROM artifacts a
                    INNER JOIN (
                        SELECT id, MAX(version) as max_ver FROM artifacts GROUP BY id
                    ) latest ON a.id = latest.id AND a.version = latest.max_ver
                    WHERE a.metadata_json LIKE ?
                    AND a.metadata_json LIKE ?
                    ORDER BY a.created_at DESC
                    LIMIT ?
                """
                pattern_kv = f'%"{key}": "{value}"%'
                pattern_type = f'%"artifact_type": "{artifact_type}"%'
                cursor = await conn.execute(sql, (pattern_kv, pattern_type, limit))
            else:
                sql = """
                    SELECT a.id, a.content, a.metadata_json, a.created_at
                    FROM artifacts a
                    INNER JOIN (
                        SELECT id, MAX(version) as max_ver FROM artifacts GROUP BY id
                    ) latest ON a.id = latest.id AND a.version = latest.max_ver
                    WHERE a.metadata_json LIKE ?
                    ORDER BY a.created_at DESC
                    LIMIT ?
                """
                pattern_kv = f'%"{key}": "{value}"%'
                cursor = await conn.execute(sql, (pattern_kv, limit))

            rows = await cursor.fetchall()
            results = []
            for row in rows:
                metadata = json.loads(row[2]) if row[2] else {}
                results.append(
                    {
                        "id": row[0],
                        "content": row[1],
                        "metadata": metadata,
                        "created_at": row[3],
                    }
                )
            return results
        except Exception:
            await self._reset_conn()
            raise
