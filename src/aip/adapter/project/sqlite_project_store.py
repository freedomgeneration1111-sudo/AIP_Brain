"""SQLite implementation of ProjectStore Protocol.

Minimal implementation: provides list_projects() so Beast and other
actors can iterate over projects for corpus maintenance and health checks.

Constructor is lightweight (stores path only). Call ``initialize()``
(async) to create tables before first use, or rely on lazy creation
via ``_get_conn()``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import aiosqlite

from aip.foundation.protocols import ProjectStore

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_PROJECTS = """
    CREATE TABLE IF NOT EXISTS projects (
        project_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        domain TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""


class SqliteProjectStore(ProjectStore):
    """SQLite-backed ProjectStore.

    Stores project metadata (id, name, status, domain, timestamps).

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
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create projects table on the given connection."""
        await conn.execute(_DDL_PROJECTS)
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

    async def list_projects(self, status: str | None = None) -> list[dict]:
        """List projects, optionally filtered by status.

        Returns list of dicts with project_id, name, status, domain.
        """
        conn = await self._get_conn()
        try:
            if status:
                cursor = await conn.execute(
                    "SELECT project_id, name, status, domain, created_at, updated_at "
                    "FROM projects WHERE status = ? ORDER BY updated_at DESC",
                    (status,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT project_id, name, status, domain, created_at, updated_at "
                    "FROM projects ORDER BY updated_at DESC",
                )

            rows = await cursor.fetchall()
            results = []
            for row in rows:
                results.append(
                    {
                        "project_id": row["project_id"],
                        "name": row["name"],
                        "status": row["status"],
                        "domain": row["domain"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    },
                )
            return results
        except Exception:
            await self._reset_conn()
            raise

    async def create_project(self, project_id: str, name: str, domain: str = "") -> dict:
        """Create a new project. Returns the created project dict.

        If the project already exists, returns the existing project without error.
        """
        conn = await self._get_conn()
        try:
            # Check if project already exists
            cursor = await conn.execute(
                "SELECT project_id, name, status, domain, created_at, updated_at FROM projects WHERE project_id = ?",
                (project_id,),
            )
            existing = await cursor.fetchone()
            if existing is not None:
                return {
                    "project_id": existing["project_id"],
                    "name": existing["name"],
                    "status": existing["status"],
                    "domain": existing["domain"],
                    "created_at": existing["created_at"],
                    "updated_at": existing["updated_at"],
                }

            now = datetime.now(timezone.utc).isoformat() + "Z"
            await conn.execute(
                "INSERT INTO projects (project_id, name, domain, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, name, domain, now, now),
            )
            await conn.commit()
            return {
                "project_id": project_id,
                "name": name,
                "status": "active",
                "domain": domain,
                "created_at": now,
                "updated_at": now,
            }
        except Exception:
            await self._reset_conn()
            raise
