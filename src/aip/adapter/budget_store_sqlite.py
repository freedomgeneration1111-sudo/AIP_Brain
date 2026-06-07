"""SQLite-backed BudgetStore implementation.

BudgetStore Protocol required.
state.db stores budgets.
Adapter may import foundation but not orchestration.
Uses aiosqlite for async-safe database access.

Constructor is lightweight (stores path only). Call ``initialize()``
(async) to create tables before first use, or rely on lazy creation
via ``_get_conn()``.
"""

from __future__ import annotations

import sqlite3

import aiosqlite

from aip.adapter.store_health import StoreHealthMixin
from aip.foundation.protocols import BudgetStore
from aip.foundation.schemas import BudgetScope

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_BUDGET_LEDGER = """
    CREATE TABLE IF NOT EXISTS budget_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        tokens_used INTEGER NOT NULL,
        cost_usd REAL NOT NULL DEFAULT 0.0,
        model_slot TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""

_DDL_IDX_BUDGET_SCOPE = """
    CREATE INDEX IF NOT EXISTS idx_budget_scope
    ON budget_ledger(scope, scope_id)
"""


class SqliteBudgetStore(BudgetStore, StoreHealthMixin):
    """SQLite implementation of BudgetStore Protocol.

    Uses state.db for persistence. Budget ledger is append-only
    (consumption records are never deleted, only summed).

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
            self._health_track_connect()
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create budget_ledger table and index on the given connection."""
        await conn.execute(_DDL_BUDGET_LEDGER)
        await conn.execute(_DDL_IDX_BUDGET_SCOPE)
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

    async def get_budget(self, scope: BudgetScope, scope_id: str) -> dict:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT COALESCE(SUM(tokens_used), 0), COALESCE(SUM(cost_usd), 0.0) "
                "FROM budget_ledger WHERE scope = ? AND scope_id = ?",
                (scope, scope_id),
            )
            row = await cursor.fetchone()
            consumed_tokens = row[0] if row else 0
            consumed_cost = row[1] if row else 0.0

            # Resolve the limit from BudgetConfig defaults per scope
            limits = {
                "session": 500000,
                "project": 5000000,
                "daily": 10000000,
            }
            limit = limits.get(scope, 500000)
            remaining = max(0, limit - consumed_tokens)
            warning_threshold = 0.80

            return {
                "consumed": consumed_tokens,
                "consumed_tokens": consumed_tokens,  # backward compat
                "consumed_cost": consumed_cost,
                "remaining": remaining,
                "limit": limit,
                "warning_threshold": warning_threshold,
            }
        except Exception:
            await self._reset_conn()
            raise

    async def record_usage(
        self,
        scope: BudgetScope,
        scope_id: str,
        tokens_used: int,
        cost_usd: float,
        model_slot: str,
    ) -> None:
        conn = await self._get_conn()
        try:
            await conn.execute(
                "INSERT INTO budget_ledger (scope, scope_id, tokens_used, cost_usd, model_slot) VALUES (?, ?, ?, ?, ?)",
                (scope, scope_id, tokens_used, cost_usd, model_slot),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def check_limit(self, scope: BudgetScope, scope_id: str) -> bool:
        """Check whether budget has remaining capacity.

        Returns True if budget is not exhausted, False if at/past limit.
        """
        budget = await self.get_budget(scope, scope_id)
        return budget["remaining"] > 0
