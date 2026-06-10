"""ACE Playbook — Async-safe SQLite-backed procedural intervention rules.

Procedural intervention rules, loaded at session start, curated by Sexton.
Derive and update from Sexton FailureClassification output.
Deprecation uses supersession, not deletion.
Uses AcePlaybookEntry (7.0a) which carries model_gen_assumption.

Chunk 4: Converted from per-call sqlite3.connect() to async persistent
connection pattern matching the rest of the adapter layer. __init__ is
lightweight; schema creation happens in async initialize().
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.foundation.schemas import AcePlaybookEntry, FailureClassification


_DDL_ACE_PLAYBOOK = """
    CREATE TABLE IF NOT EXISTS ace_playbook (
        entry_id TEXT PRIMARY KEY,
        domain TEXT,
        failure_type TEXT,
        intervention TEXT,
        condition TEXT,
        model_gen_assumption TEXT,
        source_trace_ids TEXT,
        confidence REAL,
        created_at TEXT,
        deprecated_at TEXT,
        deprecated_reason TEXT
    )
"""


class AcePlaybook:
    """Async-safe SQLite-backed ACE Playbook (orchestration layer).

    All storage via its own SQLite (ace_playbook.db per config). Never
    bypasses the Protocol injection contract for other stores.

    Chunk 4 changes:
    - __init__ is lightweight (stores path + config only, no DB access)
    - initialize() creates schema via aiosqlite (async-safe)
    - All methods use persistent aiosqlite connection instead of
      per-call sqlite3.connect()
    - Connection is reused across calls; closed in close()
    """

    def __init__(self, db_path: str, config: dict[str, Any] | None = None) -> None:
        self._db_path = db_path
        self._config = config or {}
        self._conn: aiosqlite.Connection | None = None
        self._tables_ready = False

    async def initialize(self) -> None:
        """Idempotent schema creation (called by lifespan / DI container).

        Uses a short-lived connection to create tables, then discards it.
        Subsequent operations use the persistent connection from _get_conn().
        """
        if self._tables_ready:
            return
        conn = await aiosqlite.connect(self._db_path)
        try:
            await conn.execute(_DDL_ACE_PLAYBOOK)
            await conn.commit()
            self._tables_ready = True
        finally:
            await conn.close()

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return a persistent connection, creating one if needed.

        Lazily ensures tables on first connection so that callers
        who bypass initialize() still get a working schema.
        """
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=5000")
            if not self._tables_ready:
                await self._conn.execute(_DDL_ACE_PLAYBOOK)
                await self._conn.commit()
                self._tables_ready = True
        return self._conn

    async def _reset_conn(self) -> None:
        """Reset the persistent connection (called on errors)."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def close(self) -> None:
        """Close the persistent connection."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def load_playbook(self, domain: str | None = None) -> list[AcePlaybookEntry]:
        conn = await self._get_conn()
        try:
            query = "SELECT * FROM ace_playbook WHERE deprecated_at IS NULL"
            params: tuple = ()
            if domain:
                query += " AND domain = ?"
                params = (domain,)
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_entry(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def add_entry(self, entry: AcePlaybookEntry) -> str:
        conn = await self._get_conn()
        try:
            await conn.execute(
                """INSERT OR REPLACE INTO ace_playbook
                (entry_id, domain, failure_type, intervention, condition, model_gen_assumption,
                 source_trace_ids, confidence, created_at, deprecated_at, deprecated_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.entry_id,
                    entry.domain,
                    entry.failure_type,
                    entry.intervention,
                    entry.condition,
                    entry.model_gen_assumption,
                    json.dumps(entry.source_trace_ids),
                    entry.confidence,
                    entry.created_at,
                    entry.deprecated_at,
                    entry.deprecated_reason,
                ),
            )
            await conn.commit()
            return entry.entry_id
        except Exception:
            await self._reset_conn()
            raise

    async def deprecate_entry(self, entry_id: str, reason: str) -> None:
        conn = await self._get_conn()
        try:
            await conn.execute(
                "UPDATE ace_playbook SET deprecated_at = ?, deprecated_reason = ? WHERE entry_id = ?",
                (datetime.now(timezone.utc).isoformat(), reason, entry_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def derive_from_classification(
        self,
        classification: FailureClassification,
        trace_event: dict[str, Any],
    ) -> AcePlaybookEntry | None:
        """Bridge from Sexton output to persistent playbook entry."""
        ft = classification.failure_type
        domain = trace_event.get("domain", "general")
        intervention = self._intervention_for(ft)
        condition = f"failure_type == '{ft}' and domain == '{domain}'"

        entry = AcePlaybookEntry(
            entry_id=f"ace_{ft}_{domain}_{int(datetime.now().timestamp())}",
            domain=domain,
            failure_type=ft,
            intervention=intervention,
            condition=condition,
            model_gen_assumption=classification.model_gen_assumption
            or "Derived from Sexton classification of observed failure pattern.",
            source_trace_ids=[str(classification.trace_event_id)],
            confidence=classification.confidence,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        min_conf = float(self._config.get("min_confidence", 0.70))
        auto = bool(self._config.get("auto_derive", True))

        if auto and classification.confidence >= min_conf:
            await self.add_entry(entry)
            return entry
        return entry  # return for DEFINER review if not auto-promoted

    async def get_active_entries(self, domain: str, failure_type: str | None = None) -> list[AcePlaybookEntry]:
        conn = await self._get_conn()
        try:
            query = "SELECT * FROM ace_playbook WHERE deprecated_at IS NULL AND domain = ?"
            params: list = [domain]
            if failure_type:
                query += " AND failure_type = ?"
                params.append(failure_type)
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_entry(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    def _row_to_entry(self, row: tuple | sqlite3.Row) -> AcePlaybookEntry:
        return AcePlaybookEntry(
            entry_id=row[0],
            domain=row[1],
            failure_type=row[2],
            intervention=row[3],
            condition=row[4],
            model_gen_assumption=row[5],
            source_trace_ids=json.loads(row[6]) if row[6] else [],
            confidence=row[7] or 0.0,
            created_at=row[8] or "",
            deprecated_at=row[9],
            deprecated_reason=row[10],
        )

    def _intervention_for(self, failure_type: str) -> str:
        # Minimal mapping per failure type (expandable)
        mapping = {
            "A": "Inject domain contract / strengthen retrieval before synthesis",
            "B": "Add or strengthen procedural ACE playbook entry",
            "C": "Apply structural validation / output repair step",
            "D": "Trigger L4 context reset or trajectory intervention",
            "E": "Require explicit verification step before commit",
            "F": "Reduce context window or inject anxiety-mitigation framing",
        }
        return mapping.get(failure_type, "Log for DEFINER review and manual intervention")
