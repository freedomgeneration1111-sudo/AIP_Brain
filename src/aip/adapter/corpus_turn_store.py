"""CorpusTurnStore — SQLite + FTS5 store for turn-level corpus.

Persistent storage for CorpusTurn objects using the shared state.db.
FTS5 virtual table with triggers for automatic search-index sync.

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

from aip.foundation.schemas.corpus_turn import CorpusTurn

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_CORPUS_TURNS = """
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
        embedded INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_turns_conversation ON corpus_turns(conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_turns_source_model ON corpus_turns(source_model)",
    "CREATE INDEX IF NOT EXISTS idx_turns_primary_domain ON corpus_turns(primary_domain)",
    "CREATE INDEX IF NOT EXISTS idx_turns_tagging_version ON corpus_turns(tagging_version)",
    "CREATE INDEX IF NOT EXISTS idx_turns_importance ON corpus_turns(importance DESC)",
]

_DDL_MIGRATIONS = [
    "ALTER TABLE corpus_turns ADD COLUMN embedded INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE corpus_turns ADD COLUMN metadata_json TEXT DEFAULT '{}'",
]

_DDL_FTS = """
    CREATE VIRTUAL TABLE IF NOT EXISTS corpus_turns_fts USING fts5(
        turn_id UNINDEXED,
        conversation_name,
        searchable_text,
        primary_domain UNINDEXED,
        content='corpus_turns',
        content_rowid='rowid'
    )
"""

_DDL_TRIGGER_INSERT = """
    CREATE TRIGGER IF NOT EXISTS corpus_turns_ai
    AFTER INSERT ON corpus_turns BEGIN
        INSERT INTO corpus_turns_fts(
            rowid, turn_id, conversation_name, searchable_text, primary_domain
        ) VALUES (new.rowid, new.turn_id, new.conversation_name,
                  new.searchable_text, new.primary_domain);
    END
"""

_DDL_TRIGGER_DELETE = """
    CREATE TRIGGER IF NOT EXISTS corpus_turns_ad
    AFTER DELETE ON corpus_turns BEGIN
        INSERT INTO corpus_turns_fts(
            corpus_turns_fts, rowid, turn_id, conversation_name,
            searchable_text, primary_domain
        ) VALUES ('delete', old.rowid, old.turn_id, old.conversation_name,
                  old.searchable_text, old.primary_domain);
    END
"""

_DDL_TRIGGER_UPDATE = """
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
"""


class CorpusTurnStore:
    """SQLite-backed store for CorpusTurn objects with FTS5 search.

    All turns live in the main state.db alongside artifacts/events.
    FTS5 virtual table + triggers keep search index in sync automatically.
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
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create tables, indexes, FTS virtual table, and triggers."""
        await conn.execute(_DDL_CORPUS_TURNS)

        for idx_ddl in _DDL_INDEXES:
            await conn.execute(idx_ddl)

        for mig_ddl in _DDL_MIGRATIONS:
            try:
                await conn.execute(mig_ddl)
            except sqlite3.OperationalError:
                pass  # column already exists

        await conn.execute(_DDL_FTS)
        await conn.execute(_DDL_TRIGGER_INSERT)
        await conn.execute(_DDL_TRIGGER_DELETE)
        await conn.execute(_DDL_TRIGGER_UPDATE)
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

    async def write_turn(self, turn: CorpusTurn) -> None:
        """Insert or replace a turn. Sets timestamps. Serializes lists as JSON."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"

            cursor = await conn.execute("SELECT created_at FROM corpus_turns WHERE turn_id = ?", (turn.turn_id,))
            row = await cursor.fetchone()
            created_at = row["created_at"] if row and row["created_at"] else now

            await conn.execute(
                """
                INSERT OR REPLACE INTO corpus_turns (
                    turn_id, conversation_id, conversation_name, turn_index,
                    source_model, source_account, export_date,
                    user_text, assistant_text, turn_timestamp, thinking_text,
                    domains, primary_domain, tags, importance, bridges,
                    beast_confidence, tagging_version,
                    searchable_text, word_count,
                    embedded, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    int(getattr(turn, "embedded", 0) or 0),
                    getattr(turn, "metadata_json", "{}") or "{}",
                    created_at,
                    now,
                ),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def get_turn(self, turn_id: str) -> CorpusTurn | None:
        """Return CorpusTurn or None (not KeyError — turns are optional)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT * FROM corpus_turns WHERE turn_id = ?", (turn_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_turn(row)
        except Exception:
            await self._reset_conn()
            raise

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
        except Exception:
            await self._reset_conn()
            raise

    async def mark_embedded(self, turn_id: str) -> None:
        """Mark a turn as embedded (embedded=1). Called by Sexton after vector upsert."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            await conn.execute(
                "UPDATE corpus_turns SET embedded = 1, updated_at = ? WHERE turn_id = ?",
                (now, turn_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

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
        except Exception:
            await self._reset_conn()
            raise

    async def get_untagged_turns(self, limit: int = 50) -> list[CorpusTurn]:
        """Turns with tagging_version == 0 (never tagged by Beast)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM corpus_turns WHERE tagging_version = 0 ORDER BY created_at ASC LIMIT ?",
                (int(limit),),
            )
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def get_unembedded_turns(self, limit: int = 50) -> list[CorpusTurn]:
        """Turns with embedded == 0 (not yet in vector store)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM corpus_turns WHERE embedded = 0 ORDER BY importance DESC, created_at ASC LIMIT ?",
                (int(limit),),
            )
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def count_unembedded(self) -> int:
        """Count of turns not yet embedded."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE embedded = 0")
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            await self._reset_conn()
            raise

    async def get_turns_for_retagging(self, max_tagging_version: int, limit: int = 20) -> list[CorpusTurn]:
        """Already-tagged turns eligible for re-evaluation (tagging_version <= max)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT * FROM corpus_turns
                WHERE tagging_version > 0 AND tagging_version <= ?
                ORDER BY importance ASC LIMIT ?
                """,
                (int(max_tagging_version), int(limit)),
            )
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def count_by_domain(self) -> dict[str, int]:
        """Count of turns per primary_domain (descending by count)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT primary_domain, COUNT(*) as c
                FROM corpus_turns
                WHERE primary_domain IS NOT NULL AND primary_domain != ""
                GROUP BY primary_domain
                ORDER BY c DESC
                """
            )
            rows = await cursor.fetchall()
            return {row["primary_domain"]: int(row["c"]) for row in rows}
        except Exception:
            await self._reset_conn()
            raise

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
        except Exception:
            await self._reset_conn()
            raise

    async def total_turns(self) -> int:
        """Total number of turns in the corpus."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns")
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            await self._reset_conn()
            raise

    async def top_turns_by_importance(self, limit: int = 10) -> list[dict]:
        """Top corpus turns ranked by importance score."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT turn_id, user_text, primary_domain, source_model, importance
                FROM corpus_turns
                WHERE primary_domain IS NOT NULL AND primary_domain != ""
                ORDER BY importance DESC NULLS LAST
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "turn_id": row["turn_id"],
                    "user_text": row["user_text"] or "",
                    "primary_domain": row["primary_domain"],
                    "source_model": row["source_model"] or "",
                    "importance": float(row["importance"] or 0),
                }
                for row in rows
            ]
        except Exception:
            await self._reset_conn()
            raise

    async def get_augmented_turns_since(self, since: float | None = None, limit: int = 100) -> list[CorpusTurn]:
        """Return augmented chat turns created since a given timestamp."""
        conn = await self._get_conn()
        try:
            sql = """
                SELECT * FROM corpus_turns
                WHERE source_model = 'aip_chat'
                  AND metadata_json LIKE '%"augmented": true%'
            """
            params: list[Any] = []
            if since is not None:
                since_iso = datetime.fromtimestamp(since, tz=timezone.utc).isoformat()
                sql += " AND created_at > ?"
                params.append(since_iso)
            sql += " ORDER BY created_at ASC LIMIT ?"
            params.append(int(limit))
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def update_metadata_json(self, turn_id: str, metadata_json: str) -> None:
        """Update only the metadata_json column for a turn."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            await conn.execute(
                "UPDATE corpus_turns SET metadata_json = ?, updated_at = ? WHERE turn_id = ?",
                (metadata_json, now, turn_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def has_bridge_tagged_turns(self) -> bool:
        """Check if any turns with bridge tags exist in the corpus."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM corpus_turns "
                "WHERE bridges IS NOT NULL AND bridges != '[]' AND tagging_version > 0 LIMIT 1"
            )
            row = await cursor.fetchone()
            return bool(row and row[0] > 0)
        except Exception:
            return False

    async def count_domain_words_since(self, domain_id: str, since: str | None) -> int:
        """Count word_count for turns in a domain updated since a timestamp."""
        conn = await self._get_conn()
        try:
            if since:
                cursor = await conn.execute(
                    "SELECT COALESCE(SUM(word_count), 0) FROM corpus_turns "
                    "WHERE primary_domain = ? AND tagging_version > 0 AND updated_at >= ?",
                    (domain_id, since),
                )
            else:
                cursor = await conn.execute(
                    "SELECT COALESCE(SUM(word_count), 0) FROM corpus_turns "
                    "WHERE primary_domain = ? AND tagging_version > 0",
                    (domain_id,),
                )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            await self._reset_conn()
            raise

    async def get_domain_stats(self, domain_id: str, sample_limit: int = 20, tag_limit: int = 200) -> dict:
        """Gather domain statistics and sample turns for wiki generation.

        Returns a dict with keys: total_turns, avg_importance, top_tags,
        bridge_connectors, sample_turns, max_tagging_version.
        """
        data: dict = {
            "total_turns": 0,
            "avg_importance": 0.0,
            "top_tags": [],
            "bridge_connectors": [],
            "sample_turns": [],
            "max_tagging_version": 0,
        }
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT COUNT(*) as c, COALESCE(AVG(importance), 0) as ai, "
                "COALESCE(MAX(tagging_version), 0) as mv "
                "FROM corpus_turns WHERE primary_domain = ? AND tagging_version > 0",
                (domain_id,),
            )
            row = await cursor.fetchone()
            if row:
                data["total_turns"] = int(row["c"])
                data["avg_importance"] = round(float(row["ai"]), 4)
                data["max_tagging_version"] = int(row["mv"])

            cursor = await conn.execute(
                "SELECT tags FROM corpus_turns WHERE primary_domain = ? AND tagging_version > 0 LIMIT ?",
                (domain_id, tag_limit),
            )
            tag_rows = await cursor.fetchall()
            tag_counts: dict[str, int] = {}
            for row in tag_rows:
                try:
                    for t in json.loads(row["tags"] or "[]"):
                        tag_counts[t] = tag_counts.get(t, 0) + 1
                except Exception:
                    pass
            data["top_tags"] = [t for t, _ in sorted(tag_counts.items(), key=lambda kv: -kv[1])[:10]]

            cursor = await conn.execute(
                "SELECT bridges FROM corpus_turns WHERE primary_domain = ? "
                "AND bridges != '[]' AND tagging_version > 0 LIMIT 100",
                (domain_id,),
            )
            bridge_rows = await cursor.fetchall()
            bridge_counts: dict[str, int] = {}
            for row in bridge_rows:
                try:
                    for b in json.loads(row["bridges"] or "[]"):
                        bridge_counts[b] = bridge_counts.get(b, 0) + 1
                except Exception:
                    pass
            data["bridge_connectors"] = sorted(bridge_counts.keys())

            cursor = await conn.execute(
                "SELECT turn_id, importance, tags, bridges, user_text, assistant_text "
                "FROM corpus_turns WHERE primary_domain = ? AND tagging_version > 0 "
                "ORDER BY importance DESC LIMIT ?",
                (domain_id, sample_limit),
            )
            turn_rows = await cursor.fetchall()
            sample_turns = []
            for row in turn_rows:
                try:
                    sample_turns.append({
                        "turn_id": row["turn_id"],
                        "importance": float(row["importance"]),
                        "tags": json.loads(row["tags"] or "[]"),
                        "bridges": json.loads(row["bridges"] or "[]"),
                        "user_text": (row["user_text"] or "")[:300],
                        "assistant_text": (row["assistant_text"] or "")[:500],
                    })
                except Exception:
                    pass
            data["sample_turns"] = sample_turns
        except Exception:
            await self._reset_conn()
            raise
        return data

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
            embedded=int(row["embedded"] or 0) if "embedded" in row.keys() else 0,
            metadata_json=row["metadata_json"] if "metadata_json" in row.keys() else "{}",
            searchable_text=row["searchable_text"] or "",
            word_count=int(row["word_count"] or 0),
        )
