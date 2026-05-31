"""Review queue store — persistent SQLite-backed review queue for MANUAL mode.

When MANUAL mode requires approval, a review item is persisted.
DEFINER can list pending items, approve/reject with notes.
Approval resumes the relevant workflow/canonical/artifact path.

No always-approve production behavior.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)


class ReviewQueueStore:
    """Persistent review queue for MANUAL mode definer gate.

    Stores pending review items that require explicit DEFINER approval.
    No auto-approve path exists — every approval must come from an
    authenticated DEFINER with a recorded decision.
    """

    def __init__(self, db_path: str = "db/state.db") -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._ensure_table_sync()

    def _ensure_table_sync(self) -> None:
        """Create tables synchronously for backward compatibility."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id TEXT NOT NULL,
                    artifact_version INTEGER DEFAULT 1,
                    ecs_state TEXT DEFAULT 'GENERATED',
                    domain TEXT DEFAULT '',
                    project_id TEXT DEFAULT '',
                    review_type TEXT DEFAULT 'definer',
                    evaluation_scores TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    decision TEXT,
                    decision_notes TEXT,
                    reason TEXT DEFAULT '',
                    context_json TEXT DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_queue_status
                ON review_queue(status, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_queue_artifact
                ON review_queue(artifact_id, status)
                """
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Could not create review_queue tables synchronously: %s", exc)

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    async def initialize(self) -> None:
        """Initialize the async connection and ensure tables exist."""
        conn = await self._get_conn()
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id TEXT NOT NULL,
                artifact_version INTEGER DEFAULT 1,
                ecs_state TEXT DEFAULT 'GENERATED',
                domain TEXT DEFAULT '',
                project_id TEXT DEFAULT '',
                review_type TEXT DEFAULT 'definer',
                evaluation_scores TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by TEXT,
                decision TEXT,
                decision_notes TEXT,
                reason TEXT DEFAULT '',
                context_json TEXT DEFAULT '{}'
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_review_queue_status
            ON review_queue(status, created_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_review_queue_artifact
            ON review_queue(artifact_id, status)
            """
        )
        await conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def enqueue(
        self,
        artifact_id: str,
        artifact_version: int = 1,
        ecs_state: str = "GENERATED",
        domain: str = "",
        project_id: str = "",
        review_type: str = "definer",
        evaluation_scores: list[dict] | None = None,
        reason: str = "",
        context: dict | None = None,
    ) -> int:
        """Add an item to the review queue. Returns the queue item ID."""
        conn = await self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        cursor = await conn.execute(
            """
            INSERT INTO review_queue
                (artifact_id, artifact_version, ecs_state, domain, project_id,
                 review_type, evaluation_scores, status, created_at, reason, context_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                artifact_id,
                artifact_version,
                ecs_state,
                domain,
                project_id,
                review_type,
                json.dumps(evaluation_scores or []),
                now,
                reason,
                json.dumps(context or {}),
            ),
        )
        await conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def list_pending(self, limit: int = 100) -> list[dict]:
        """List all pending review items."""
        conn = await self._get_conn()
        cursor = await conn.execute(
            """
            SELECT id, artifact_id, artifact_version, ecs_state, domain, project_id,
                   review_type, evaluation_scores, status, created_at, reason, context_json
            FROM review_queue
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def decide(
        self,
        item_id: int,
        decision: str,
        decided_by: str,
        notes: str = "",
    ) -> dict:
        """Record a decision on a review queue item.

        Args:
            item_id: The queue item ID.
            decision: Either 'approved' or 'rejected'.
            decided_by: The identity of the actor making the decision.
            notes: Optional notes explaining the decision.

        Returns:
            Dict with status and details.

        Raises:
            ValueError: If decision is not 'approved' or 'rejected'.
            PermissionError: If decided_by is not 'definer'.
        """
        if decision not in ("approved", "rejected"):
            raise ValueError(f"Invalid decision: {decision!r}. Must be 'approved' or 'rejected'.")

        if decided_by != "definer":
            raise PermissionError(f"Only DEFINER can approve/reject review items. Actor: {decided_by!r}")

        conn = await self._get_conn()
        now = datetime.now(timezone.utc).isoformat()

        # Check item exists and is pending
        cursor = await conn.execute(
            "SELECT id, status, artifact_id FROM review_queue WHERE id = ?",
            (item_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"Review item {item_id} not found"}}

        if row["status"] != "pending":
            return {
                "ok": False,
                "error": {
                    "code": "ALREADY_DECIDED",
                    "message": f"Review item {item_id} already has status: {row['status']}",
                },
            }

        await conn.execute(
            """
            UPDATE review_queue
            SET status = ?, decision = ?, decided_by = ?, decided_at = ?, decision_notes = ?
            WHERE id = ?
            """,
            (decision, decision, decided_by, now, notes, item_id),
        )
        await conn.commit()

        return {
            "ok": True,
            "item_id": item_id,
            "artifact_id": row["artifact_id"],
            "decision": decision,
            "decided_by": decided_by,
            "decided_at": now,
        }

    async def get_item(self, item_id: int) -> dict | None:
        """Get a specific review queue item by ID."""
        conn = await self._get_conn()
        cursor = await conn.execute(
            """
            SELECT id, artifact_id, artifact_version, ecs_state, domain, project_id,
                   review_type, evaluation_scores, status, created_at, decided_at,
                   decided_by, decision, decision_notes, reason, context_json
            FROM review_queue WHERE id = ?
            """,
            (item_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def _row_to_dict(self, row: aiosqlite.Row) -> dict:
        """Convert a database row to a dict."""
        result = dict(row)
        # Parse JSON fields
        for key in ("evaluation_scores", "context_json"):
            val = result.get(key)
            if isinstance(val, str):
                try:
                    result[key] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        return result
