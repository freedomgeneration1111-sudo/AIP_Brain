"""SQLite-backed BudgetStore implementation.

Per §6: BudgetStore Protocol required.
Per §5.10: state.db stores budgets.
Per §7.2: adapter may import foundation but not orchestration.
Per CHUNK-7.0b ANNEX (exact).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from aip.foundation.protocols import BudgetStore
from aip.foundation.schemas import BudgetScope


class SqliteBudgetStore(BudgetStore):
    """SQLite implementation of BudgetStore Protocol.

    Uses state.db for persistence. Budget ledger is append-only
    (consumption records are never deleted, only summed).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_table()

    def _ensure_table(self) -> None:
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

    async def get_budget(self, scope: BudgetScope, scope_id: str) -> dict:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(tokens_used), 0), COALESCE(SUM(cost_usd), 0.0) "
                "FROM budget_ledger WHERE scope = ? AND scope_id = ?",
                (scope, scope_id),
            ).fetchone()
            consumed_tokens = row[0] if row else 0
            consumed_cost = row[1] if row else 0.0
            return {
                "consumed_tokens": consumed_tokens,
                "consumed_cost": consumed_cost,
            }
        finally:
            conn.close()

    async def record_usage(
        self,
        scope: BudgetScope,
        scope_id: str,
        tokens_used: int,
        cost_usd: float,
        model_slot: str,
    ) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT INTO budget_ledger (scope, scope_id, tokens_used, cost_usd, model_slot) "
                "VALUES (?, ?, ?, ?, ?)",
                (scope, scope_id, tokens_used, cost_usd, model_slot),
            )
            conn.commit()
        finally:
            conn.close()

    async def check_limit(self, scope: BudgetScope, scope_id: str) -> bool:
        """Always returns True — limit checking is done by BudgetManager with config."""
        return True
