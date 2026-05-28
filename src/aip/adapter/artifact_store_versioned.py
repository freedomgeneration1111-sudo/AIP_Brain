"""Versioned artifact store — preserves every version.

Each write appends a new version; no version is ever overwritten.
Uses SQLite for persistence.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


class VersionedArtifactStore:
    """ArtifactStore implementation with version preservation.

    Every version is preserved for provenance.
    Generated ≠ canonical — versions support separation.
    Per Appendix D: artifact hash ≠ approval; supersession ≠ deletion.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        cur = self._conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (id, version)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_artifacts_id
            ON artifacts(id)
        """)
        self._conn.commit()

    async def write(self, id: str, content: str, metadata: dict) -> None:
        """Write artifact content, appending a new version.

        Version number is auto-incremented per artifact id.
        Metadata is merged with version and timestamp.
        """
        cur = self._conn.cursor()
        cur.execute("SELECT MAX(version) FROM artifacts WHERE id = ?", (id,))
        row = cur.fetchone()
        next_version = (row[0] or 0) + 1

        now = datetime.now(timezone.utc).isoformat()
        enriched_metadata = {**(metadata or {}), "version": next_version, "created_at": now}
        meta_json = json.dumps(enriched_metadata)

        cur.execute(
            "INSERT INTO artifacts (id, version, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (id, next_version, content, meta_json, now),
        )
        self._conn.commit()

    async def read(self, id: str, version: int | None = None) -> str:
        """Read artifact content by id and optional version.

        version=None: returns latest version.
        version=N: returns specific version.
        Raises KeyError if artifact or version not found.
        """
        cur = self._conn.cursor()
        if version is None:
            cur.execute(
                "SELECT content FROM artifacts WHERE id = ? ORDER BY version DESC LIMIT 1",
                (id,),
            )
        else:
            cur.execute(
                "SELECT content FROM artifacts WHERE id = ? AND version = ?",
                (id, version),
            )
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"Artifact {id!r} version {version} not found")
        return row[0]

    async def list_versions(self, id: str) -> list[int]:
        """List all version numbers for an artifact, ascending order."""
        cur = self._conn.cursor()
        cur.execute("SELECT version FROM artifacts WHERE id = ? ORDER BY version ASC", (id,))
        return [row[0] for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
