"""CorpusTurnStore — dedicated SQLite + FTS5 store for turn-level corpus.

Implements persistent storage for CorpusTurn objects (the atomic unit
of the AIP knowledge corpus). Uses the same db/state.db as artifacts,
events, ECS, etc. — no separate database file.

Follows EXACT patterns from VersionedArtifactStore:
  - __init__(db_path)
  - sync _ensure_table_sync() on init (for startup)
  - async _get_conn() with row_factory=sqlite3.Row
  - async initialize() / close()
  - try/finally + await conn.close(); self._conn = None
  - aiosqlite throughout async paths
  - JSON for complex fields (lists)
  - No silent failures — raise on errors where appropriate

FTS5 is in the SAME file (corpus_turns_fts virtual table) with
triggers for auto-sync (as specified; unlike manual sync in current lexical).

Layer: adapter. Imports only foundation (CorpusTurn) + stdlib + aiosqlite.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.foundation.schemas.corpus_turn import CorpusTurn, make_turn_id


class CorpusTurnStore:
    """SQLite-backed store for CorpusTurn objects with FTS5 search.

    All turns live in the main state.db alongside artifacts/events.
    FTS5 virtual table + triggers keep search index in sync automatically.

    Beast uses this for:
      - writing new turns (ingest path)
      - searching by text/domain/source/importance
      - finding untagged or re-taggable turns
      - updating beast metadata (domains, tags, importance, etc.)
      - health counts

    DO NOT use this for vector storage or knowledge graph (separate layers).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._ensure_tables_sync()

    def _ensure_tables_sync(self) -> None:
        """Synchronous table + FTS + trigger creation (runs on __init__)."""
        conn = sqlite3.connect(self._db_path)
        try:
            # Main table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS corpus_turns (
                    turn_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    conversation_name TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    source_model TEXT NOT NULL,
                    source_account TEXT NOT NULL,
                    export_date TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    turn_timestamp TEXT NOT NULL,
                    thinking_text TEXT NOT NULL DEFAULT '',
                    domains TEXT NOT NULL DEFAULT '[]',
                    primary_domain TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '[]',
                    importance REAL NOT NULL DEFAULT 0.0,
                    bridges TEXT NOT NULL DEFAULT '[]',
                    beast_confidence REAL NOT NULL DEFAULT 0.0,
                    tagging_version INTEGER NOT NULL DEFAULT 0,
                    searchable_text TEXT NOT NULL,
                    word_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Indexes for common queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_conversation
                ON corpus_turns(conversation_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_source_model
                ON corpus_turns(source_model)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_primary_domain
                ON corpus_turns(primary_domain)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_tagging_version
                ON corpus_turns(tagging_version)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_importance
                ON corpus_turns(importance DESC)
            """)

            # FTS5 virtual table (content='corpus_turns' for external content)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS corpus_turns_fts USING fts5(
                    turn_id UNINDEXED,
                    conversation_name,
                    searchable_text,
                    primary_domain UNINDEXED,
                    content='corpus_turns',
                    content_rowid='rowid'
                )
            """)

            # Triggers to keep FTS in sync (exact spec)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS corpus_turns_ai
                AFTER INSERT ON corpus_turns BEGIN
                    INSERT INTO corpus_turns_fts(
                        rowid, turn_id, conversation_name, searchable_text, primary_domain
                    ) VALUES (new.rowid, new.turn_id, new.conversation_name,
                              new.searchable_text, new.primary_domain);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS corpus_turns_ad
                AFTER DELETE ON corpus_turns BEGIN
                    INSERT INTO corpus_turns_fts(
                        corpus_turns_fts, rowid, turn_id, conversation_name,
                        searchable_text, primary_domain
                    ) VALUES ('delete', old.rowid, old.turn_id, old.conversation_name,
                              old.searchable_text, old.primary_domain);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS corpus_turns_au
                AFTER UPDATE ON corpus_turns BEGIN
                    INSERT INTO corpus_turns_fts(
                        corpus_turns_fts, rowid, turn_id, conversation_name,
                        searchable_text, primary_domain
                    ) VALUES ('delete', old.rowid, old.turn_id, old.conversation_name,
                              old.searchable_text, old.primary_domain);
                    INSERT INTO corpus_turns_fts(
                        rowid, turn_id, conversation_name, searchable_text, primary_domain
                    ) VALUES (new.rowid, new.turn_id, new.conversation_name,
                              new.searchable_text, new.primary_domain);
                END
            """)

            conn.commit()
        finally:
            conn.close()

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def _ensure_tables(self) -> None:
        """Async table creation (idempotent, called by initialize)."""
        conn = await aiosqlite.connect(self._db_path)
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS corpus_turns (
                    turn_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    conversation_name TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    source_model TEXT NOT NULL,
                    source_account TEXT NOT NULL,
                    export_date TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    turn_timestamp TEXT NOT NULL,
                    thinking_text TEXT NOT NULL DEFAULT '',
                    domains TEXT NOT NULL DEFAULT '[]',
                    primary_domain TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '[]',
                    importance REAL NOT NULL DEFAULT 0.0,
                    bridges TEXT NOT NULL DEFAULT '[]',
                    beast_confidence REAL NOT NULL DEFAULT 0.0,
                    tagging_version INTEGER NOT NULL DEFAULT 0,
                    searchable_text TEXT NOT NULL,
                    word_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_conversation
                ON corpus_turns(conversation_id)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_source_model
                ON corpus_turns(source_model)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_primary_domain
                ON corpus_turns(primary_domain)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_tagging_version
                ON corpus_turns(tagging_version)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_importance
                ON corpus_turns(importance DESC)
            """)

            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS corpus_turns_fts USING fts5(
                    turn_id UNINDEXED,
                    conversation_name,
                    searchable_text,
                    primary_domain UNINDEXED,
                    content='corpus_turns',
                    content_rowid='rowid'
                )
            """)

            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS corpus_turns_ai
                AFTER INSERT ON corpus_turns BEGIN
                    INSERT INTO corpus_turns_fts(
                        rowid, turn_id, conversation_name, searchable_text, primary_domain
                    ) VALUES (new.rowid, new.turn_id, new.conversation_name,
                              new.searchable_text, new.primary_domain);
                END
            """)
            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS corpus_turns_ad
                AFTER DELETE ON corpus_turns BEGIN
                    INSERT INTO corpus_turns_fts(
                        corpus_turns_fts, rowid, turn_id, conversation_name,
                        searchable_text, primary_domain
                    ) VALUES ('delete', old.rowid, old.turn_id, old.conversation_name,
                              old.searchable_text, old.primary_domain);
                END
            """)
            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS corpus_turns_au
                AFTER UPDATE ON corpus_turns BEGIN
                    INSERT INTO corpus_turns_fts(
                        corpus_turns_fts, rowid, turn_id, conversation_name,
                        searchable_text, primary_domain
                    ) VALUES ('delete', old.rowid, old.turn_id, old.conversation_name,
                              old.searchable_text, old.primary_domain);
                    INSERT INTO corpus_turns_fts(
                        rowid, turn_id, conversation_name, searchable_text, primary_domain
                    ) VALUES (new.rowid, new.turn_id, new.conversation_name,
                              new.searchable_text, new.primary_domain);
                END
            """)

            await conn.commit()
        finally:
            await conn.close()

    async def initialize(self) -> None:
        """Async initialization — ensures tables + FTS + triggers exist."""
        await self._ensure_tables()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def write_turn(self, turn: CorpusTurn) -> None:
        """Insert or replace a turn. Sets timestamps. Serializes lists as JSON."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"

            # For new turns we set created_at; on replace we preserve it
            # but always bump updated_at. Simple approach: always set both,
            # caller can control via the object if it wants.
            created_at = getattr(turn, "_created_at", None) or now
            # We don't store _created_at on the dataclass; use SELECT or just
            # always INSERT OR REPLACE with now for created on first write.
            # To preserve original created_at on re-writes, we do a check.
            cursor = await conn.execute(
                "SELECT created_at FROM corpus_turns WHERE turn_id = ?", (turn.turn_id,)
            )
            row = await cursor.fetchone()
            if row and row["created_at"]:
                created_at = row["created_at"]
            else:
                created_at = now

            await conn.execute(
                """
                INSERT OR REPLACE INTO corpus_turns (
                    turn_id, conversation_id, conversation_name, turn_index,
                    source_model, source_account, export_date,
                    user_text, assistant_text, turn_timestamp, thinking_text,
                    domains, primary_domain, tags, importance, bridges,
                    beast_confidence, tagging_version,
                    searchable_text, word_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn.turn_id,
                    turn.conversation_id,
                    turn.conversation_name,
                    turn.turn_index,
                    turn.source_model,
                    turn.source_account,
                    turn.export_date,
                    turn.user_text,
                    turn.assistant_text,
                    turn.turn_timestamp,
                    turn.thinking_text or "",
                    json.dumps(turn.domains or []),
                    turn.primary_domain or "",
                    json.dumps(turn.tags or []),
                    float(turn.importance or 0.0),
                    json.dumps(turn.bridges or []),
                    float(turn.beast_confidence or 0.0),
                    int(turn.tagging_version or 0),
                    turn.searchable_text or "",
                    int(turn.word_count or 0),
                    created_at,
                    now,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
            self._conn = None

    async def get_turn(self, turn_id: str) -> CorpusTurn | None:
        """Return CorpusTurn or None (not KeyError — turns are optional)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM corpus_turns WHERE turn_id = ?", (turn_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_turn(row)
        finally:
            await conn.close()
            self._conn = None

    async def update_beast_tags(
        self,
        turn_id: str,
        domains: list[str],
        primary_domain: str,
        tags: list[str],
        importance: float,
        bridges: list[str],
        beast_confidence: float,
    ) -> None:
        """Beast-only update path for tagging metadata. Increments tagging_version."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            await conn.execute(
                """
                UPDATE corpus_turns
                SET domains = ?,
                    primary_domain = ?,
                    tags = ?,
                    importance = ?,
                    bridges = ?,
                    beast_confidence = ?,
                    tagging_version = tagging_version + 1,
                    updated_at = ?
                WHERE turn_id = ?
                """,
                (
                    json.dumps(domains or []),
                    primary_domain or "",
                    json.dumps(tags or []),
                    float(importance or 0.0),
                    json.dumps(bridges or []),
                    float(beast_confidence or 0.0),
                    now,
                    turn_id,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
            self._conn = None

    async def search(
        self,
        query: str,
        primary_domain: str | None = None,
        source_model: str | None = None,
        min_importance: float = 0.0,
        limit: int = 10,
    ) -> list[CorpusTurn]:
        """FTS5 search with optional filters. Returns [] on no results."""
        conn = await self._get_conn()
        try:
            # Basic FTS5 match on the virtual table
            sql = """
                SELECT t.*
                FROM corpus_turns_fts f
                JOIN corpus_turns t ON f.rowid = t.rowid
                WHERE corpus_turns_fts MATCH ?
            """
            params: list[Any] = [query]

            if primary_domain:
                sql += " AND t.primary_domain = ?"
                params.append(primary_domain)
            if source_model:
                sql += " AND t.source_model = ?"
                params.append(source_model)
            if min_importance > 0:
                sql += " AND t.importance >= ?"
                params.append(float(min_importance))

            sql += " ORDER BY rank LIMIT ?"
            params.append(int(limit))

            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        finally:
            await conn.close()
            self._conn = None

    async def get_untagged_turns(self, limit: int = 50) -> list[CorpusTurn]:
        """Turns with tagging_version == 0 (never tagged by Beast)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT * FROM corpus_turns
                WHERE tagging_version = 0
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (int(limit),),
            )
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        finally:
            await conn.close()
            self._conn = None

    async def get_turns_for_retagging(
        self, max_tagging_version: int, limit: int = 20
    ) -> list[CorpusTurn]:
        """Already-tagged turns eligible for re-evaluation (tagging_version <= max)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT * FROM corpus_turns
                WHERE tagging_version > 0
                  AND tagging_version <= ?
                ORDER BY importance ASC
                LIMIT ?
                """,
                (int(max_tagging_version), int(limit)),
            )
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        finally:
            await conn.close()
            self._conn = None

    async def count_by_domain(self) -> dict[str, int]:
        """Count of turns per primary_domain (descending by count)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT primary_domain, COUNT(*) as c
                FROM corpus_turns
                GROUP BY primary_domain
                ORDER BY c DESC
                """
            )
            rows = await cursor.fetchall()
            return {row["primary_domain"] or "": int(row["c"]) for row in rows}
        finally:
            await conn.close()
            self._conn = None

    async def count_by_source(self) -> dict[str, int]:
        """Count of turns per source_model (descending by count)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT source_model, COUNT(*) as c
                FROM corpus_turns
                GROUP BY source_model
                ORDER BY c DESC
                """
            )
            rows = await cursor.fetchall()
            return {row["source_model"] or "": int(row["c"]) for row in rows}
        finally:
            await conn.close()
            self._conn = None

    async def total_turns(self) -> int:
        """Total number of turns in the corpus."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns")
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0
        finally:
            await conn.close()
            self._conn = None

    def _row_to_turn(self, row: sqlite3.Row) -> CorpusTurn:
        """Rehydrate a CorpusTurn from a DB row (JSON fields -> lists)."""
        return CorpusTurn(
            turn_id=row["turn_id"],
            conversation_id=row["conversation_id"],
            conversation_name=row["conversation_name"],
            turn_index=int(row["turn_index"]),
            source_model=row["source_model"],
            source_account=row["source_account"],
            export_date=row["export_date"],
            user_text=row["user_text"],
            assistant_text=row["assistant_text"],
            turn_timestamp=row["turn_timestamp"],
            thinking_text=row["thinking_text"] or "",
            domains=json.loads(row["domains"]) if row["domains"] else [],
            primary_domain=row["primary_domain"] or "",
            tags=json.loads(row["tags"]) if row["tags"] else [],
            importance=float(row["importance"] or 0.0),
            bridges=json.loads(row["bridges"]) if row["bridges"] else [],
            beast_confidence=float(row["beast_confidence"] or 0.0),
            tagging_version=int(row["tagging_version"] or 0),
            searchable_text=row["searchable_text"] or "",
            word_count=int(row["word_count"] or 0),
        )
