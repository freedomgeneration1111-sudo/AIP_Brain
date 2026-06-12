"""Persistent SQLite-backed ECS store.

Replaces the in-memory-only GuardrailedEcsStore with a version that
persists ECS state and transitions to SQLite, ensuring state survives
process restart while preserving all guardrail validation.

Architecture:
- Follows existing aiosqlite pattern used by other adapter stores.
- Stores current state in ecs_state table.
- Stores transition history in ecs_transitions table.
- Uses GuardrailedEcsStore validation logic for transition rules.
- Lightweight constructor with async initialize() for table creation.
- Persistent connection with error recovery via _reset_conn().
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aip.adapter.store_health import StoreHealthMixin
from aip.foundation.ecs_graph import InvalidTransitionError, validate_transition
from aip.foundation.protocols import EcsStore, EventStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_ECS_STATE = """
    CREATE TABLE IF NOT EXISTS ecs_state (
        artifact_id TEXT PRIMARY KEY,
        current_state TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
"""

_DDL_ECS_TRANSITIONS = """
    CREATE TABLE IF NOT EXISTS ecs_transitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        artifact_id TEXT NOT NULL,
        from_state TEXT,
        to_state TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        superseded_by TEXT,
        timestamp TEXT NOT NULL,
        metadata TEXT DEFAULT '{}'
    )
"""

_DDL_IDX_ECS_TRANSITIONS_ARTIFACT = """
    CREATE INDEX IF NOT EXISTS idx_ecs_transitions_artifact
    ON ecs_transitions(artifact_id, timestamp DESC)
"""


class PersistentEcsStore(EcsStore, StoreHealthMixin):
    """ECS store that validates every transition against the state graph
    AND persists state to SQLite.

    Replaces the in-memory-only approach with proper persistence.
    State is loaded from SQLite on first access after restart.
    Transition history is recorded for audit.

    Uses a persistent aiosqlite connection per instance with error recovery.
    Includes connection health metrics via StoreHealthMixin.
    """

    def __init__(
        self,
        db_path: str = "db/state.db",
        event_store: EventStore | None = None,
    ) -> None:
        self._db_path = db_path
        self._event_store = event_store
        self._conn: aiosqlite.Connection | None = None
        # In-memory cache (populated from DB on first read)
        self._state_cache: dict[str, str] = {}
        self._cache_loaded = False
        self._tables_ready = False

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return a persistent connection, creating one if needed.

        Lazily ensures tables on first connection so that callers
        who bypass ``initialize()`` still get a working schema.
        """
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            # Sprint 6.3: busy_timeout to handle concurrent write contention
            await self._conn.execute("PRAGMA busy_timeout=5000")
            self._health_track_connect()
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create ECS tables and indexes on the given connection."""
        await conn.execute(_DDL_ECS_STATE)
        await conn.execute(_DDL_ECS_TRANSITIONS)
        await conn.execute(_DDL_IDX_ECS_TRANSITIONS_ARTIFACT)
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
            self._cache_loaded = False
            self._health_track_reset()

    async def _load_state_from_db(self) -> None:
        """Load all state from DB into the in-memory cache."""
        if self._cache_loaded:
            return
        conn = await self._get_conn()
        cursor = await conn.execute("SELECT artifact_id, current_state FROM ecs_state")
        rows = await cursor.fetchall()
        self._state_cache = {row["artifact_id"]: row["current_state"] for row in rows}
        self._cache_loaded = True

    async def transition(
        self,
        artifact_id: str,
        from_state: str | None,
        to_state: str,
        actor: str,
        reason: str,
        superseded_by: str | None = None,
    ) -> None:
        """Transition artifact between ECS states with validation and persistence.

        Steps:
        1. Load current state from DB (if not cached)
        2. Assert from_state precondition if provided
        3. Validate transition against state graph
        4. Write transition to SQLite
        5. Update state cache
        6. Record event in EventStore for provenance
        """
        await self._load_state_from_db()
        current = self._state_cache.get(artifact_id)

        # from_state precondition check
        if from_state is not None and current != from_state:
            raise InvalidTransitionError(
                current or "NONE",
                to_state,
                f"Precondition failed: expected {from_state!r}, but artifact {artifact_id!r} is in {current!r}",
            )

        # Guardrail validation
        if current is not None:
            validate_transition(current, to_state)

        # Write transition to SQLite
        timestamp = datetime.now(timezone.utc).isoformat()
        conn = await self._get_conn()
        try:
            await conn.execute(
                """
                INSERT INTO ecs_transitions
                    (artifact_id, from_state, to_state, actor, reason, superseded_by, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, current, to_state, actor, reason, superseded_by, timestamp),
            )
            # Upsert current state
            await conn.execute(
                """
                INSERT INTO ecs_state (artifact_id, current_state, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    current_state = excluded.current_state,
                    updated_at = excluded.updated_at
                """,
                (artifact_id, to_state, timestamp),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

        # Update cache
        self._state_cache[artifact_id] = to_state

        # Record event for provenance
        if self._event_store is not None:
            try:
                await self._event_store.write_event(
                    event_type="ecs_transition",
                    actor=actor,
                    artifact_id=artifact_id,
                    from_state=current,
                    to_state=to_state,
                    reason=reason,
                )
            except Exception as exc:
                logger.warning("Failed to write ECS event to EventStore: %s", exc)

    async def current_state(self, artifact_id: str) -> str | None:
        """Return current ECS state for an artifact.

        Loads from DB on first access, then uses cache.
        """
        await self._load_state_from_db()
        return self._state_cache.get(artifact_id)

    async def get_transition_history(self, artifact_id: str, limit: int = 100) -> list[dict]:
        """Get transition history for an artifact."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT from_state, to_state, actor, reason, superseded_by, timestamp
                FROM ecs_transitions
                WHERE artifact_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (artifact_id, limit),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "from_state": row["from_state"],
                    "to_state": row["to_state"],
                    "actor": row["actor"],
                    "reason": row["reason"],
                    "superseded_by": row["superseded_by"],
                    "timestamp": row["timestamp"],
                }
                for row in rows
            ]
        except Exception:
            await self._reset_conn()
            raise

    async def list_by_state(self, state: str, limit: int = 500) -> list[str]:
        """List artifact IDs currently in a given ECS state.

        Returns list of artifact_id strings for artifacts whose current
        state matches the given state.
        """
        await self._load_state_from_db()
        return [artifact_id for artifact_id, current in self._state_cache.items() if current == state][:limit]
