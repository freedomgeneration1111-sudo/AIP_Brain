"""SQLite implementation of ProjectStore Protocol.

Minimal implementation: provides list_projects() so Beast and other
actors can iterate over projects for corpus maintenance and health checks.

Phase 3 addition: fills the gap where ProjectStore had only a Protocol
and no concrete adapter, leaving container.project_store as None.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.foundation.protocols import ProjectStore


class SqliteProjectStore(ProjectStore):
    """SQLite-backed ProjectStore.

    Stores project metadata (id, name, status, domain, timestamps).
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
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    domain TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
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
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    domain TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await conn.commit()
        finally:
            await conn.close()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
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
                    "FROM projects ORDER BY updated_at DESC"
                )

            rows = await cursor.fetchall()
            results = []
            for row in rows:
                results.append({
                    "project_id": row["project_id"],
                    "name": row["name"],
                    "status": row["status"],
                    "domain": row["domain"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
            return results
        finally:
            await conn.close()
            self._conn = None

    async def create_project(self, project_id: str, name: str, domain: str = "") -> dict:
        """Create a new project. Returns the created project dict."""
        conn = await self._get_conn()
        try:
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
        finally:
            await conn.close()
            self._conn = None
