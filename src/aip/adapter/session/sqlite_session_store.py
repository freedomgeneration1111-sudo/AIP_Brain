"""SQLite implementation of SessionStore Protocol.

Persists chat session metadata (role, model slot, turn count, etc.)
so sessions survive process restarts. Falls back to in-memory dict
when the database is unavailable.

Constructor is lightweight (stores path only). Call ``initialize()``
(async) to create tables before first use, or rely on lazy creation
via ``_get_conn()``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.foundation.protocols import SessionStore

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_SESSIONS = """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        project_id TEXT DEFAULT '',
        role TEXT DEFAULT '',
        model_slot TEXT DEFAULT 'synthesis',
        mode TEXT DEFAULT 'normal',
        turn_count INTEGER DEFAULT 0,
        context_tokens_estimate INTEGER DEFAULT 0,
        artifacts_produced TEXT DEFAULT '[]',
        metadata_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""

_DDL_IDX_SESSIONS_UPDATED = """
    CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
    ON sessions(updated_at DESC)
"""


class SqliteSessionStore(SessionStore):
    """SQLite-backed SessionStore for chat session persistence.

    Uses a persistent aiosqlite connection per instance with error recovery.
    Stores session metadata as JSON in TEXT columns.
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
        """Create sessions table and index on the given connection."""
        await conn.execute(_DDL_SESSIONS)
        await conn.execute(_DDL_IDX_SESSIONS_UPDATED)
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

    def _row_to_dict(self, row: sqlite3.Row | aiosqlite.Row) -> dict[str, Any]:
        """Convert a database row to a session dict."""
        result: dict[str, Any] = {
            "id": row["session_id"],
            "project_id": row["project_id"],
            "role": row["role"],
            "model_slot": row["model_slot"],
            "mode": row["mode"],
            "turn_count": row["turn_count"],
            "context_tokens_estimate": row["context_tokens_estimate"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        # Parse JSON columns
        artifacts = row["artifacts_produced"]
        if isinstance(artifacts, str):
            try:
                result["artifacts_produced"] = json.loads(artifacts)
            except (json.JSONDecodeError, TypeError):
                result["artifacts_produced"] = []
        else:
            result["artifacts_produced"] = artifacts or []

        metadata = row["metadata_json"]
        if isinstance(metadata, str):
            try:
                meta = json.loads(metadata)
                # Merge top-level keys from metadata_json into the result
                result.update(meta)
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    async def create_session(self, session_id: str, metadata: dict) -> None:
        """Create a new session with the given metadata."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"

            # Extract known columns; put the rest into metadata_json
            known_keys = {
                "session_id", "project_id", "role", "model_slot", "mode",
                "turn_count", "context_tokens_estimate", "artifacts_produced",
                "metadata_json", "created_at", "updated_at", "id",
            }
            extra_meta = {k: v for k, v in metadata.items() if k not in known_keys}

            await conn.execute(
                """
                INSERT OR REPLACE INTO sessions
                    (session_id, project_id, role, model_slot, mode,
                     turn_count, context_tokens_estimate, artifacts_produced,
                     metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    metadata.get("project_id", ""),
                    metadata.get("role", ""),
                    metadata.get("model_slot", "synthesis"),
                    metadata.get("mode", "normal"),
                    metadata.get("turn_count", 0),
                    metadata.get("context_tokens_estimate", 0),
                    json.dumps(metadata.get("artifacts_produced", [])),
                    json.dumps(extra_meta),
                    metadata.get("created_at", now),
                    now,
                ),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def get_session(self, session_id: str) -> dict | None:
        """Get session metadata by ID. Returns None if not found."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT session_id, project_id, role, model_slot, mode, "
                "turn_count, context_tokens_estimate, artifacts_produced, "
                "metadata_json, created_at, updated_at "
                "FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_dict(row)
        except Exception:
            await self._reset_conn()
            raise

    async def list_sessions(self, limit: int = 100) -> list[dict]:
        """List sessions, most recently updated first."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT session_id, project_id, role, model_slot, mode, "
                "turn_count, context_tokens_estimate, artifacts_produced, "
                "metadata_json, created_at, updated_at "
                "FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def update_session(self, session_id: str, updates: dict) -> None:
        """Update session fields. Creates the session if it does not exist."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"

            # Check if session exists
            cursor = await conn.execute(
                "SELECT metadata_json FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            existing = await cursor.fetchone()

            if existing:
                # Parse existing metadata_json
                try:
                    existing_meta = json.loads(existing["metadata_json"]) if existing["metadata_json"] else {}
                except (json.JSONDecodeError, TypeError):
                    existing_meta = {}

                # Build SET clauses
                set_clauses: list[str] = []
                params: list[Any] = []

                known_column_map = {
                    "project_id": "project_id",
                    "role": "role",
                    "model_slot": "model_slot",
                    "mode": "mode",
                    "turn_count": "turn_count",
                    "context_tokens_estimate": "context_tokens_estimate",
                }

                extra_meta = dict(existing_meta)
                for k, v in updates.items():
                    if k in ("session_id", "id", "created_at"):
                        continue
                    if k in known_column_map:
                        set_clauses.append(f"{k} = ?")
                        params.append(v)
                    elif k == "artifacts_produced":
                        set_clauses.append("artifacts_produced = ?")
                        params.append(json.dumps(v) if isinstance(v, list) else v)
                    elif k == "metadata_json":
                        # Merge into existing metadata
                        if isinstance(v, dict):
                            extra_meta.update(v)
                    else:
                        # Unknown keys go into metadata_json
                        extra_meta[k] = v

                set_clauses.append("metadata_json = ?")
                params.append(json.dumps(extra_meta))
                set_clauses.append("updated_at = ?")
                params.append(now)
                params.append(session_id)

                await conn.execute(
                    f"UPDATE sessions SET {', '.join(set_clauses)} WHERE session_id = ?",
                    params,
                )
            else:
                # Create if not exists (lenient upsert)
                await self.create_session(session_id, updates)
                return

            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def delete_session(self, session_id: str) -> None:
        """Delete a session by ID."""
        conn = await self._get_conn()
        try:
            await conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise
