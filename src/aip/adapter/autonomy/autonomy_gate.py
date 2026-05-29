"""AutonomyGateImpl — SQLite-backed enforcement of DEFINER sovereignty.

Per prose + ANNEX (exact).
No UI, workflow, Beast, MCP, or queued task may bypass the DEFINER gates.
Adapter only (composes Foundation Protocols/schemas; no orchestration imports).
Phase 3: migrated from blocking sqlite3 to aiosqlite to avoid event loop blocking.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.foundation.protocols import AutonomyGate
from aip.foundation.schemas import AutonomyEscalation, AutonomyLevel, coerce_autonomy_level


class AutonomyGateImpl(AutonomyGate):
    """SQLite-backed AutonomyGate.

    Enforces the hierarchy: none < read < write < admin.
    - check(): non-blocking, returns Escalation record.
    - escalate(): blocking for admin when escalation_requires_definer=True (returns granted=False).
    - Auto-grants read/write.
    - Writes full audit trail to autonomy_escalations in the provided state db.

    Uses aiosqlite for async-compatible database access.
    """

    def __init__(self, config: dict | None = None, escalation_store: Any | None = None) -> None:
        self._config = config or {}
        # escalation_store param accepted for interface compatibility (ignored; we manage table directly like other adapters)
        self._db_path = self._config.get("db_path", "db/state.db")  # fallback; tests override via config
        self._conn: aiosqlite.Connection | None = None
        self._ensure_table_sync()

    def _ensure_table_sync(self) -> None:
        """Synchronous table creation during init (runs once at startup)."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS autonomy_escalations (
                    escalation_id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    current_level TEXT NOT NULL,
                    requested_level TEXT NOT NULL,
                    granted INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    model_gen_assumption TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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

    async def _ensure_table(self) -> None:
        conn = await aiosqlite.connect(self._db_path)
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS autonomy_escalations (
                    escalation_id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    current_level TEXT NOT NULL,
                    requested_level TEXT NOT NULL,
                    granted INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    model_gen_assumption TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
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

    def _level_rank(self, level: AutonomyLevel | str) -> int:
        order = {"none": 0, "read": 1, "write": 2, "admin": 3}
        return order.get(str(level), 0)

    async def check(
        self,
        action_type: str,
        resource_id: str,
        requested_level: AutonomyLevel,
        requested_by: str,
    ) -> AutonomyEscalation:
        current = self._config.get("default_level", "read")
        granted = self._level_rank(requested_level) <= self._level_rank(current)

        esc = AutonomyEscalation(
            escalation_id=str(uuid.uuid4()),
            action_type=action_type,
            requested_by=requested_by,
            resource_id=resource_id,
            current_level=coerce_autonomy_level(current),
            requested_level=requested_level,
            granted=bool(granted),
            reason="auto-granted" if granted else "escalation required",
            model_gen_assumption=self._config.get("model_gen_assumption"),
            created_at=datetime.now(timezone.utc).isoformat() + "Z",
        )
        await self._record_escalation(esc)
        return esc

    async def escalate(
        self,
        action_type: str,
        resource_id: str,
        requested_level: AutonomyLevel,
        requested_by: str,
    ) -> AutonomyEscalation:
        current = self._config.get("default_level", "read")
        requires_definer = self._config.get("escalation_requires_definer", True)

        if self._level_rank(requested_level) <= self._level_rank(current):
            granted = True
            reason = "auto-granted (sufficient level)"
        elif requested_level == "admin" and requires_definer and requested_by != "definer":
            granted = False
            reason = "DEFINER approval required for admin escalation"
        else:
            granted = True
            reason = "escalation granted"

        esc = AutonomyEscalation(
            escalation_id=str(uuid.uuid4()),
            action_type=action_type,
            requested_by=requested_by,
            resource_id=resource_id,
            current_level=coerce_autonomy_level(current),
            requested_level=requested_level,
            granted=bool(granted),
            reason=reason,
            model_gen_assumption=self._config.get("model_gen_assumption"),
            created_at=datetime.now(timezone.utc).isoformat() + "Z",
        )
        await self._record_escalation(esc)
        return esc

    async def audit_log(self, limit: int = 100) -> list[AutonomyEscalation]:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM autonomy_escalations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            results: list[AutonomyEscalation] = []
            for r in rows:
                results.append(
                    AutonomyEscalation(
                        escalation_id=r["escalation_id"],
                        action_type=r["action_type"],
                        requested_by=r["requested_by"],
                        resource_id=r["resource_id"],
                        current_level=coerce_autonomy_level(r["current_level"]),
                        requested_level=coerce_autonomy_level(r["requested_level"]),
                        granted=bool(r["granted"]),
                        reason=r["reason"],
                        model_gen_assumption=r["model_gen_assumption"],
                        created_at=r["created_at"],
                    )
                )
            return results
        finally:
            await conn.close()
            self._conn = None

    async def _record_escalation(self, esc: AutonomyEscalation) -> None:
        conn = await self._get_conn()
        try:
            await conn.execute(
                """
                INSERT OR REPLACE INTO autonomy_escalations
                (escalation_id, action_type, requested_by, resource_id, current_level,
                 requested_level, granted, reason, model_gen_assumption, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    esc.escalation_id,
                    esc.action_type,
                    esc.requested_by,
                    esc.resource_id,
                    esc.current_level,
                    esc.requested_level,
                    1 if esc.granted else 0,
                    esc.reason,
                    esc.model_gen_assumption,
                    esc.created_at,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
            self._conn = None
