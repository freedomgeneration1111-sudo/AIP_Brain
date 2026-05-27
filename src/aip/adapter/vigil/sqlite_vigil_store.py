"""SqliteVigilStore — implements VigilStore (CHUNK-9.1).

Per spec: health table for canonicals + vigil_checks audit log.
Read-only actor support (populated by 9.2 canonical pipeline).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from aip.foundation.protocols import VigilStore


class SqliteVigilStore(VigilStore):
    """SQLite implementation of VigilStore Protocol (Phase 7)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_tables(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS canonical_health (
                    artifact_id TEXT PRIMARY KEY,
                    last_evaluated TEXT,
                    model_slot_used TEXT,
                    faithfulness_score REAL,
                    domain_coherence_score REAL,
                    created_at TEXT,
                    status TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vigil_checks (
                    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    check_time TEXT NOT NULL,
                    canonical_count INTEGER NOT NULL,
                    stale_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    re_evaluate_count INTEGER DEFAULT 0,
                    entity_issues_found INTEGER DEFAULT 0
                )
            """)
            conn.commit()
        finally:
            conn.close()

    async def initialize(self) -> None:
        self._ensure_tables()

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def get_canonical_health(self, artifact_id: str) -> dict:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM canonical_health WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if not row:
                return {}
            return dict(row)
        finally:
            pass

    async def list_stale_canonicals(self, threshold_days: int) -> list[dict]:
        conn = self._get_conn()
        try:
            # Simplified staleness: last_evaluated older than threshold_days
            cutoff = (datetime.utcnow() - __import__('datetime').timedelta(days=threshold_days)).isoformat() + "Z"
            rows = conn.execute(
                "SELECT * FROM canonical_health WHERE last_evaluated < ? OR last_evaluated IS NULL",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            pass

    async def record_vigil_check(self, canonical_count: int, stale_count: int, status: str) -> None:
        conn = self._get_conn()
        try:
            now = datetime.utcnow().isoformat() + "Z"
            conn.execute(
                "INSERT INTO vigil_checks (check_time, canonical_count, stale_count, status) VALUES (?, ?, ?, ?)",
                (now, canonical_count, stale_count, status),
            )
            conn.commit()
        finally:
            pass

    async def get_last_vigil_check(self) -> dict | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM vigil_checks ORDER BY check_time DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            pass
