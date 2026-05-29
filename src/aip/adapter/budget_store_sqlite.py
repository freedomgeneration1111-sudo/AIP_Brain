"""SQLite-backed BudgetStore implementation.

BudgetStore Protocol required.
state.db stores budgets.
Adapter may import foundation but not orchestration.
Per ANNEX (exact).
Phase 3: migrated from blocking sqlite3 to aiosqlite to avoid event loop blocking.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import aiosqlite

from aip.foundation.protocols import BudgetStore
from aip.foundation.schemas import BudgetScope


class SqliteBudgetStore(BudgetStore):
    """SQLite implementation of BudgetStore Protocol.

    Uses state.db for persistence. Budget ledger is append-only
    (consumption records are never deleted, only summed).
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
                CREATE TABLE IF NOT EXISTS budget_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    model_slot TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_budget_scope
                ON budget_ledger(scope, scope_id)
            """)
            conn.commit()
        finally:
            conn.close()

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def _ensure_table(self) -> None:
        conn = await aiosqlite.connect(self._db_path)
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    model_slot TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_budget_scope
                ON budget_ledger(scope, scope_id)
            """)
            await conn.commit()
        finally:
            await conn.close()

    async def initialize(self) -> None:
        await self._ensure_table()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

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
        finally:
            await conn.close()
            self._conn = None

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
                "INSERT INTO budget_ledger (scope, scope_id, tokens_used, cost_usd, model_slot) "
                "VALUES (?, ?, ?, ?, ?)",
                (scope, scope_id, tokens_used, cost_usd, model_slot),
            )
            await conn.commit()
        finally:
            await conn.close()
            self._conn = None

    async def check_limit(self, scope: BudgetScope, scope_id: str) -> bool:
        """Check whether budget has remaining capacity.

        Returns True if budget is not exhausted, False if at/past limit.
        """
        budget = await self.get_budget(scope, scope_id)
        return budget["remaining"] > 0
