"""SQLite implementation of CanonicalStore Protocol.

Per prose + ANNEX (exact).
Enforces "approved_by == 'definer'" on write (DEFINER sovereignty).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols import CanonicalStore


class SqliteCanonicalStore(CanonicalStore):
    """SQLite-backed CanonicalStore.

    Stores only DEFINER-approved canonical artifacts (distinct from versioned generated artifacts).
    """

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

    async def initialize(self) -> None:
        self._ensure_table()

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def read_canonical(self, artifact_id: str) -> dict | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT content, approved_by, domain, created_at, superseded_by FROM canonical_artifacts "
                "WHERE artifact_id = ? AND superseded_by IS NULL",
                (artifact_id,),
            ).fetchone()
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
            conn.close()
            self._conn = None

    async def write_canonical(
        self, artifact_id: str, content: dict, approved_by: str
    ) -> None:
        if approved_by != "definer":
            # Only DEFINER may create canonicals
            raise PermissionError(
                f"write_canonical requires approved_by='definer', got {approved_by!r}"
            )

        conn = self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            content_json = json.dumps(content or {})
            conn.execute(
                """
                INSERT OR REPLACE INTO canonical_artifacts
                    (artifact_id, content, approved_by, domain, created_at, superseded_by)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (artifact_id, content_json, approved_by, content.get("domain", "") if isinstance(content, dict) else "", now),
            )
            conn.commit()
        finally:
            conn.close()
            self._conn = None

    async def list_canonical(self, domain: str | None = None) -> list[dict]:
        conn = self._get_conn()
        try:
            if domain:
                rows = conn.execute(
                    "SELECT artifact_id, content, approved_by, domain, created_at, superseded_by "
                    "FROM canonical_artifacts WHERE domain = ? AND superseded_by IS NULL "
                    "ORDER BY created_at DESC",
                    (domain,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT artifact_id, content, approved_by, domain, created_at, superseded_by "
                    "FROM canonical_artifacts WHERE superseded_by IS NULL "
                    "ORDER BY created_at DESC"
                ).fetchall()

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
            conn.close()
            self._conn = None
