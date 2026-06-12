"""CodexStore — SQLite-backed store for the CODEX internal map.

Persists source registry, topic map, contradictions, and staleness data
in the shared state.db. Provides query methods for the librarian maintenance
cycle and the `aip codex` CLI commands.

Tables:
    codex_sources: Registered source documents
    codex_topics: Topic nodes in the knowledge map
    codex_contradictions: Detected contradictions between sources
    codex_duplicate_candidates: Potential duplicate source pairs

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

from aip.adapter.store_health import StoreHealthMixin
from aip.foundation.schemas.codex import (
    CodexConfig,
    CodexContradiction,
    CodexDashboard,
    CodexSource,
    CodexTopic,
)

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_CODEX_SOURCES = """
    CREATE TABLE IF NOT EXISTS codex_sources (
        source_id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        source_type TEXT NOT NULL DEFAULT 'document',
        source_path TEXT NOT NULL DEFAULT '',
        domain TEXT NOT NULL DEFAULT '',
        topics_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'active',
        content_hash TEXT NOT NULL DEFAULT '',
        word_count INTEGER NOT NULL DEFAULT 0,
        turn_count INTEGER NOT NULL DEFAULT 0,
        first_ingested_at TEXT NOT NULL DEFAULT '',
        last_updated_at TEXT NOT NULL DEFAULT '',
        last_reviewed_at TEXT NOT NULL DEFAULT '',
        superseded_by TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL DEFAULT ''
    )
"""

_DDL_CODEX_TOPICS = """
    CREATE TABLE IF NOT EXISTS codex_topics (
        topic_id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        domain TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        source_ids_json TEXT NOT NULL DEFAULT '[]',
        related_topics_json TEXT NOT NULL DEFAULT '[]',
        contradiction_count INTEGER NOT NULL DEFAULT 0,
        staleness_score REAL NOT NULL DEFAULT 0.0,
        last_activity_at TEXT NOT NULL DEFAULT '',
        is_wiki_page INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL DEFAULT ''
    )
"""

_DDL_CODEX_CONTRADICTIONS = """
    CREATE TABLE IF NOT EXISTS codex_contradictions (
        contradiction_id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL DEFAULT '',
        claim_a TEXT NOT NULL DEFAULT '',
        source_a_id TEXT NOT NULL DEFAULT '',
        source_a_title TEXT NOT NULL DEFAULT '',
        claim_b TEXT NOT NULL DEFAULT '',
        source_b_id TEXT NOT NULL DEFAULT '',
        source_b_title TEXT NOT NULL DEFAULT '',
        severity TEXT NOT NULL DEFAULT 'major',
        status TEXT NOT NULL DEFAULT 'open',
        context TEXT NOT NULL DEFAULT '',
        resolution_notes TEXT NOT NULL DEFAULT '',
        resolved_by TEXT NOT NULL DEFAULT '',
        resolved_at TEXT NOT NULL DEFAULT '',
        detected_at TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )
"""

_DDL_CODEX_DUPLICATE_CANDIDATES = """
    CREATE TABLE IF NOT EXISTS codex_duplicate_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_a_id TEXT NOT NULL,
        source_b_id TEXT NOT NULL DEFAULT '',
        similarity_score REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'open',
        resolved_by TEXT NOT NULL DEFAULT '',
        resolution TEXT NOT NULL DEFAULT '',
        detected_at TEXT NOT NULL DEFAULT '',
        UNIQUE(source_a_id, source_b_id)
    )
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_codex_sources_domain ON codex_sources(domain)",
    "CREATE INDEX IF NOT EXISTS idx_codex_sources_status ON codex_sources(status)",
    "CREATE INDEX IF NOT EXISTS idx_codex_sources_content_hash ON codex_sources(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_codex_sources_source_type ON codex_sources(source_type)",
    "CREATE INDEX IF NOT EXISTS idx_codex_topics_domain ON codex_topics(domain)",
    "CREATE INDEX IF NOT EXISTS idx_codex_topics_staleness ON codex_topics(staleness_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_codex_topics_is_wiki ON codex_topics(is_wiki_page)",
    "CREATE INDEX IF NOT EXISTS idx_codex_contradictions_topic ON codex_contradictions(topic_id)",
    "CREATE INDEX IF NOT EXISTS idx_codex_contradictions_status ON codex_contradictions(status)",
    "CREATE INDEX IF NOT EXISTS idx_codex_contradictions_severity ON codex_contradictions(severity)",
    "CREATE INDEX IF NOT EXISTS idx_codex_duplicates_status ON codex_duplicate_candidates(status)",
]


class CodexStore(StoreHealthMixin):
    """SQLite-backed store for the CODEX internal librarian map.

    All tables live in the main state.db alongside artifacts, events,
    corpus_turns, etc. Uses a persistent aiosqlite connection per instance
    with error recovery. WAL mode enabled for concurrent access.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._tables_ready = False

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return a persistent connection, creating one if needed."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=5000")
            self._health_track_connect()
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create all CODEX tables and indexes."""
        await conn.execute(_DDL_CODEX_SOURCES)
        await conn.execute(_DDL_CODEX_TOPICS)
        await conn.execute(_DDL_CODEX_CONTRADICTIONS)
        await conn.execute(_DDL_CODEX_DUPLICATE_CANDIDATES)
        for idx_ddl in _DDL_INDEXES:
            await conn.execute(idx_ddl)
        await conn.commit()

    async def initialize(self) -> None:
        """Idempotent table creation."""
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
        """Reset the persistent connection on errors."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._health_track_reset()

    # ------------------------------------------------------------------
    # Source operations
    # ------------------------------------------------------------------

    async def upsert_source(self, source: CodexSource) -> None:
        """Insert or update a source entry."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                """
                INSERT OR REPLACE INTO codex_sources
                (source_id, title, source_type, source_path, domain, topics_json,
                 status, content_hash, word_count, turn_count, first_ingested_at,
                 last_updated_at, last_reviewed_at, superseded_by, metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.title,
                    source.source_type,
                    source.source_path,
                    source.domain,
                    json.dumps(source.topics),
                    source.status,
                    source.content_hash,
                    source.word_count,
                    source.turn_count,
                    source.first_ingested_at or now,
                    source.last_updated_at or now,
                    source.last_reviewed_at,
                    source.superseded_by,
                    json.dumps(source.metadata),
                    now,
                ),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def get_source(self, source_id: str) -> CodexSource | None:
        """Get a source by ID."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT * FROM codex_sources WHERE source_id = ?", (source_id,))
            row = await cursor.fetchone()
            return self._row_to_source(row) if row else None
        except Exception:
            await self._reset_conn()
            raise

    async def list_sources(
        self,
        domain: str | None = None,
        status: str | None = None,
        source_type: str | None = None,
        limit: int = 100,
    ) -> list[CodexSource]:
        """List sources with optional filters."""
        conn = await self._get_conn()
        try:
            sql = "SELECT * FROM codex_sources WHERE 1=1"
            params: list[Any] = []
            if domain:
                sql += " AND domain = ?"
                params.append(domain)
            if status:
                sql += " AND status = ?"
                params.append(status)
            if source_type:
                sql += " AND source_type = ?"
                params.append(source_type)
            sql += " ORDER BY last_updated_at DESC LIMIT ?"
            params.append(limit)
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_source(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def find_source_by_hash(self, content_hash: str) -> CodexSource | None:
        """Find a source by content hash (for dedup)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM codex_sources WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            )
            row = await cursor.fetchone()
            return self._row_to_source(row) if row else None
        except Exception:
            await self._reset_conn()
            raise

    async def find_source_by_path(self, source_path: str) -> list[CodexSource]:
        """Find sources by file path."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM codex_sources WHERE source_path = ? ORDER BY last_updated_at DESC",
                (source_path,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_source(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def count_sources_by_status(self) -> dict[str, int]:
        """Count sources grouped by status."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT status, COUNT(*) as c FROM codex_sources GROUP BY status")
            rows = await cursor.fetchall()
            return {row["status"]: int(row["c"]) for row in rows}
        except Exception:
            await self._reset_conn()
            raise

    async def count_sources_by_domain(self) -> dict[str, int]:
        """Count sources grouped by domain."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT domain, COUNT(*) as c FROM codex_sources WHERE domain != '' GROUP BY domain ORDER BY c DESC"
            )
            rows = await cursor.fetchall()
            return {row["domain"]: int(row["c"]) for row in rows}
        except Exception:
            await self._reset_conn()
            raise

    async def get_stale_sources(self, threshold_days: int = 90, limit: int = 50) -> list[CodexSource]:
        """Get sources that haven't been updated within the threshold."""
        conn = await self._get_conn()
        try:
            from datetime import timedelta

            cutoff = (datetime.now(timezone.utc) - timedelta(days=threshold_days)).isoformat()
            cursor = await conn.execute(
                "SELECT * FROM codex_sources WHERE status = 'active' AND last_updated_at < ? "
                "ORDER BY last_updated_at ASC LIMIT ?",
                (cutoff, limit),
            )
            rows = await cursor.fetchall()
            return [self._row_to_source(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def mark_source_stale(self, source_id: str) -> None:
        """Mark a source as stale."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                "UPDATE codex_sources SET status = 'stale', updated_at = ? WHERE source_id = ?",
                (now, source_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def mark_source_superseded(self, source_id: str, superseded_by: str) -> None:
        """Mark a source as superseded by another."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                "UPDATE codex_sources SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE source_id = ?",
                (superseded_by, now, source_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def update_source_status(self, source_id: str, status: str) -> None:
        """Update a source's status."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                "UPDATE codex_sources SET status = ?, updated_at = ? WHERE source_id = ?",
                (status, now, source_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def get_unclassified_sources(self, limit: int = 50) -> list[CodexSource]:
        """Get sources with no domain classification."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM codex_sources WHERE (domain = '' OR domain = 'unclassified') "
                "AND status = 'active' LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_source(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    # ------------------------------------------------------------------
    # Topic operations
    # ------------------------------------------------------------------

    async def upsert_topic(self, topic: CodexTopic) -> None:
        """Insert or update a topic entry."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                """
                INSERT OR REPLACE INTO codex_topics
                (topic_id, title, domain, description, source_ids_json, related_topics_json,
                 contradiction_count, staleness_score, last_activity_at, is_wiki_page,
                 metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic.topic_id,
                    topic.title,
                    topic.domain,
                    topic.description,
                    json.dumps(topic.source_ids),
                    json.dumps(topic.related_topics),
                    topic.contradiction_count,
                    topic.staleness_score,
                    topic.last_activity_at,
                    1 if topic.is_wiki_page else 0,
                    json.dumps(topic.metadata),
                    now,
                ),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def get_topic(self, topic_id: str) -> CodexTopic | None:
        """Get a topic by ID."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT * FROM codex_topics WHERE topic_id = ?", (topic_id,))
            row = await cursor.fetchone()
            return self._row_to_topic(row) if row else None
        except Exception:
            await self._reset_conn()
            raise

    async def list_topics(
        self,
        domain: str | None = None,
        limit: int = 100,
    ) -> list[CodexTopic]:
        """List topics with optional domain filter."""
        conn = await self._get_conn()
        try:
            sql = "SELECT * FROM codex_topics WHERE 1=1"
            params: list[Any] = []
            if domain:
                sql += " AND domain = ?"
                params.append(domain)
            sql += " ORDER BY last_activity_at DESC LIMIT ?"
            params.append(limit)
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_topic(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def search_topics(self, query: str, limit: int = 20) -> list[CodexTopic]:
        """Search topics by title or description substring."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM codex_topics WHERE title LIKE ? OR description LIKE ? "
                "ORDER BY staleness_score ASC LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            )
            rows = await cursor.fetchall()
            return [self._row_to_topic(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def count_topics_by_domain(self) -> dict[str, int]:
        """Count topics grouped by domain."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT domain, COUNT(*) as c FROM codex_topics WHERE domain != '' GROUP BY domain ORDER BY c DESC"
            )
            rows = await cursor.fetchall()
            return {row["domain"]: int(row["c"]) for row in rows}
        except Exception:
            await self._reset_conn()
            raise

    async def get_topics_with_contradictions(self, limit: int = 50) -> list[CodexTopic]:
        """Get topics that have open contradictions."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM codex_topics WHERE contradiction_count > 0 ORDER BY contradiction_count DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_topic(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def get_stale_topics(self, staleness_threshold: float = 0.5, limit: int = 50) -> list[CodexTopic]:
        """Get topics with staleness above threshold."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM codex_topics WHERE staleness_score >= ? ORDER BY staleness_score DESC LIMIT ?",
                (staleness_threshold, limit),
            )
            rows = await cursor.fetchall()
            return [self._row_to_topic(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def add_source_to_topic(self, topic_id: str, source_id: str) -> None:
        """Add a source reference to a topic."""
        topic = await self.get_topic(topic_id)
        if topic is None:
            return
        if source_id not in topic.source_ids:
            topic.source_ids.append(source_id)
            await self.upsert_topic(topic)

    async def add_related_topic(self, topic_id: str, related_id: str) -> None:
        """Add a related topic link."""
        topic = await self.get_topic(topic_id)
        if topic is None:
            return
        if related_id not in topic.related_topics:
            topic.related_topics.append(related_id)
            await self.upsert_topic(topic)
        # Also add the reverse link
        related = await self.get_topic(related_id)
        if related is not None and topic_id not in related.related_topics:
            related.related_topics.append(topic_id)
            await self.upsert_topic(related)

    async def compute_staleness_scores(self, config: CodexConfig) -> int:
        """Recompute staleness scores for all topics based on source freshness.

        Staleness is computed as: max(0, 1.0 - (days_since_last_activity / stale_threshold_days))
        A topic with no recent activity has staleness near 1.0.
        Returns the number of topics updated.
        """
        from datetime import timedelta

        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc)
            stale_cutoff = (now - timedelta(days=config.stale_threshold_days)).isoformat()
            very_stale_cutoff = (now - timedelta(days=config.very_stale_threshold_days)).isoformat()

            # Get all topics with their source last_updated_at
            cursor = await conn.execute("SELECT topic_id FROM codex_topics")
            topic_rows = await cursor.fetchall()

            updated = 0
            for row in topic_rows:
                tid = row["topic_id"]
                topic = await self.get_topic(tid)
                if topic is None:
                    continue

                # Find the most recent source update
                most_recent = ""
                for sid in topic.source_ids:
                    src = await self.get_source(sid)
                    if src and src.last_updated_at > most_recent:
                        most_recent = src.last_updated_at

                if not most_recent:
                    topic.staleness_score = 1.0  # No sources = maximally stale
                elif most_recent < very_stale_cutoff:
                    topic.staleness_score = 1.0
                elif most_recent < stale_cutoff:
                    # Linear interpolation between threshold and very-stale
                    try:
                        last_dt = datetime.fromisoformat(most_recent.replace("Z", "+00:00"))
                        stale_dt = datetime.fromisoformat(stale_cutoff.replace("Z", "+00:00"))
                        very_stale_dt = datetime.fromisoformat(very_stale_cutoff.replace("Z", "+00:00"))
                        if very_stale_dt.timestamp() != stale_dt.timestamp():
                            ratio = (stale_dt.timestamp() - last_dt.timestamp()) / (
                                very_stale_dt.timestamp() - stale_dt.timestamp()
                            )
                            topic.staleness_score = min(1.0, max(0.0, 0.5 + ratio * 0.5))
                        else:
                            topic.staleness_score = 0.5
                    except Exception:
                        topic.staleness_score = 0.5
                else:
                    topic.staleness_score = 0.0

                topic.last_activity_at = most_recent
                await self.upsert_topic(topic)
                updated += 1

            return updated
        except Exception:
            await self._reset_conn()
            raise

    # ------------------------------------------------------------------
    # Contradiction operations
    # ------------------------------------------------------------------

    async def upsert_contradiction(self, contradiction: CodexContradiction) -> None:
        """Insert or update a contradiction."""
        conn = await self._get_conn()
        try:
            await conn.execute(
                """
                INSERT OR REPLACE INTO codex_contradictions
                (contradiction_id, topic_id, claim_a, source_a_id, source_a_title,
                 claim_b, source_b_id, source_b_title, severity, status, context,
                 resolution_notes, resolved_by, resolved_at, detected_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contradiction.contradiction_id,
                    contradiction.topic_id,
                    contradiction.claim_a,
                    contradiction.source_a_id,
                    contradiction.source_a_title,
                    contradiction.claim_b,
                    contradiction.source_b_id,
                    contradiction.source_b_title,
                    contradiction.severity,
                    contradiction.status,
                    contradiction.context,
                    contradiction.resolution_notes,
                    contradiction.resolved_by,
                    contradiction.resolved_at,
                    contradiction.detected_at,
                    json.dumps(contradiction.metadata),
                ),
            )
            await conn.commit()

            # Update the topic's contradiction_count
            if contradiction.topic_id:
                await self._update_topic_contradiction_count(contradiction.topic_id)
        except Exception:
            await self._reset_conn()
            raise

    async def get_contradiction(self, contradiction_id: str) -> CodexContradiction | None:
        """Get a contradiction by ID."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM codex_contradictions WHERE contradiction_id = ?",
                (contradiction_id,),
            )
            row = await cursor.fetchone()
            return self._row_to_contradiction(row) if row else None
        except Exception:
            await self._reset_conn()
            raise

    async def list_contradictions(
        self,
        topic_id: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 100,
    ) -> list[CodexContradiction]:
        """List contradictions with optional filters."""
        conn = await self._get_conn()
        try:
            sql = "SELECT * FROM codex_contradictions WHERE 1=1"
            params: list[Any] = []
            if topic_id:
                sql += " AND topic_id = ?"
                params.append(topic_id)
            if status:
                sql += " AND status = ?"
                params.append(status)
            if severity:
                sql += " AND severity = ?"
                params.append(severity)
            sql += " ORDER BY detected_at DESC LIMIT ?"
            params.append(limit)
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_contradiction(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def resolve_contradiction(
        self,
        contradiction_id: str,
        status: str,
        resolved_by: str = "definer",
        resolution_notes: str = "",
    ) -> None:
        """Resolve a contradiction with a DEFINER decision."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                "UPDATE codex_contradictions SET status = ?, resolved_by = ?, "
                "resolution_notes = ?, resolved_at = ? WHERE contradiction_id = ?",
                (status, resolved_by, resolution_notes, now, contradiction_id),
            )
            await conn.commit()

            # Update topic contradiction count
            c = await self.get_contradiction(contradiction_id)
            if c and c.topic_id:
                await self._update_topic_contradiction_count(c.topic_id)
        except Exception:
            await self._reset_conn()
            raise

    async def count_contradictions_by_status(self) -> dict[str, int]:
        """Count contradictions grouped by status."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT status, COUNT(*) as c FROM codex_contradictions GROUP BY status")
            rows = await cursor.fetchall()
            return {row["status"]: int(row["c"]) for row in rows}
        except Exception:
            await self._reset_conn()
            raise

    async def count_contradictions_by_severity(self) -> dict[str, int]:
        """Count contradictions grouped by severity."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT severity, COUNT(*) as c FROM codex_contradictions WHERE status = 'open' GROUP BY severity"
            )
            rows = await cursor.fetchall()
            return {row["severity"]: int(row["c"]) for row in rows}
        except Exception:
            await self._reset_conn()
            raise

    async def _update_topic_contradiction_count(self, topic_id: str) -> None:
        """Update a topic's contradiction_count from actual open contradictions."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT COUNT(*) as c FROM codex_contradictions WHERE topic_id = ? AND status = 'open'",
                (topic_id,),
            )
            row = await cursor.fetchone()
            count = int(row["c"]) if row else 0
            await conn.execute(
                "UPDATE codex_topics SET contradiction_count = ? WHERE topic_id = ?",
                (count, topic_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    # ------------------------------------------------------------------
    # Duplicate candidate operations
    # ------------------------------------------------------------------

    async def add_duplicate_candidate(self, source_a_id: str, source_b_id: str, similarity_score: float) -> None:
        """Record a potential duplicate pair."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                """
                INSERT OR IGNORE INTO codex_duplicate_candidates
                (source_a_id, source_b_id, similarity_score, status, detected_at)
                VALUES (?, ?, ?, 'open', ?)
                """,
                (source_a_id, source_b_id, similarity_score, now),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def list_duplicate_candidates(self, status: str = "open", limit: int = 50) -> list[dict]:
        """List duplicate candidate pairs."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM codex_duplicate_candidates WHERE status = ? ORDER BY similarity_score DESC LIMIT ?",
                (status, limit),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "source_a_id": row["source_a_id"],
                    "source_b_id": row["source_b_id"],
                    "similarity_score": float(row["similarity_score"]),
                    "status": row["status"],
                    "resolved_by": row["resolved_by"],
                    "resolution": row["resolution"],
                    "detected_at": row["detected_at"],
                }
                for row in rows
            ]
        except Exception:
            await self._reset_conn()
            raise

    async def resolve_duplicate(self, candidate_id: int, resolved_by: str, resolution: str) -> None:
        """Resolve a duplicate candidate."""
        conn = await self._get_conn()
        try:
            await conn.execute(
                "UPDATE codex_duplicate_candidates SET status = 'resolved', "
                "resolved_by = ?, resolution = ? WHERE id = ?",
                (resolved_by, resolution, candidate_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    async def get_dashboard(self, config: CodexConfig | None = None) -> CodexDashboard:
        """Build the full CODEX dashboard summary."""
        config = config or CodexConfig()
        dash = CodexDashboard()

        try:
            # Source counts
            source_counts = await self.count_sources_by_status()
            dash.total_sources = sum(source_counts.values())
            dash.active_sources = source_counts.get("active", 0)
            dash.stale_sources = source_counts.get("stale", 0)
            dash.superseded_sources = source_counts.get("superseded", 0)
            dash.quarantined_sources = source_counts.get("quarantined", 0)

            # Topic counts
            all_topics = await self.list_topics(limit=10000)
            dash.total_topics = len(all_topics)
            dash.topics_with_contradictions = sum(1 for t in all_topics if t.contradiction_count > 0)
            dash.topics_with_wiki = sum(1 for t in all_topics if t.is_wiki_page)

            # Contradiction counts
            sev_counts = await self.count_contradictions_by_severity()
            dash.critical_contradictions = sev_counts.get("critical", 0)
            dash.major_contradictions = sev_counts.get("major", 0)
            dash.minor_contradictions = sev_counts.get("minor", 0)
            dash.open_contradictions = sum(sev_counts.values())

            # Unclassified
            unclassified = await self.get_unclassified_sources(limit=1000)
            dash.unclassified_sources = len(unclassified)

            # Topic graph (domain -> count)
            dash.topic_graph = await self.count_topics_by_domain()

            # Recently changed topics
            recent = await self.list_topics(limit=10)
            dash.recently_changed = [
                {
                    "topic_id": t.topic_id,
                    "title": t.title or t.topic_id,
                    "domain": t.domain,
                    "last_activity_at": t.last_activity_at,
                    "contradiction_count": t.contradiction_count,
                }
                for t in recent
                if t.last_activity_at
            ]

            # Stale documents
            stale_sources = await self.get_stale_sources(threshold_days=config.stale_threshold_days, limit=10)
            dash.stale_documents = [
                {
                    "source_id": s.source_id,
                    "title": s.title or s.source_path,
                    "domain": s.domain,
                    "last_updated_at": s.last_updated_at,
                    "status": s.status,
                }
                for s in stale_sources
            ]

            # Open contradictions
            open_contradictions = await self.list_contradictions(status="open", limit=10)
            dash.open_contradiction_list = [
                {
                    "contradiction_id": c.contradiction_id,
                    "topic_id": c.topic_id,
                    "claim_a": c.claim_a[:100],
                    "source_a_title": c.source_a_title,
                    "claim_b": c.claim_b[:100],
                    "source_b_title": c.source_b_title,
                    "severity": c.severity,
                }
                for c in open_contradictions
            ]

        except Exception:
            pass  # Best-effort dashboard

        return dash

    # ------------------------------------------------------------------
    # "What do I know about X?" summary
    # ------------------------------------------------------------------

    async def get_topic_summary(self, topic_id: str) -> dict:
        """Build a "What do I know about X?" summary for a topic.

        Returns a dict with topic metadata, sources, contradictions,
        related topics, and a computed staleness assessment.
        """
        topic = await self.get_topic(topic_id)
        if topic is None:
            return {"error": "topic_not_found", "topic_id": topic_id}

        # Enrich with source details
        sources = []
        for sid in topic.source_ids:
            src = await self.get_source(sid)
            if src:
                sources.append(
                    {
                        "source_id": src.source_id,
                        "title": src.title or src.source_path,
                        "source_type": src.source_type,
                        "status": src.status,
                        "last_updated_at": src.last_updated_at,
                    }
                )

        # Get contradictions
        contradictions = await self.list_contradictions(topic_id=topic_id, status="open", limit=10)

        # Get related topic details
        related = []
        for rid in topic.related_topics:
            rt = await self.get_topic(rid)
            if rt:
                related.append(
                    {
                        "topic_id": rt.topic_id,
                        "title": rt.title or rt.topic_id,
                        "domain": rt.domain,
                    }
                )

        staleness_label = "fresh"
        if topic.staleness_score >= 0.8:
            staleness_label = "very_stale"
        elif topic.staleness_score >= 0.5:
            staleness_label = "stale"
        elif topic.staleness_score >= 0.2:
            staleness_label = "aging"

        return {
            "topic_id": topic.topic_id,
            "title": topic.title or topic.topic_id,
            "domain": topic.domain,
            "description": topic.description,
            "staleness_score": round(topic.staleness_score, 3),
            "staleness_label": staleness_label,
            "source_count": len(sources),
            "sources": sources,
            "open_contradictions": len(contradictions),
            "contradictions": [
                {
                    "contradiction_id": c.contradiction_id,
                    "severity": c.severity,
                    "claim_a": c.claim_a[:200],
                    "claim_b": c.claim_b[:200],
                }
                for c in contradictions
            ],
            "related_topics": related,
            "has_wiki_page": topic.is_wiki_page,
            "last_activity_at": topic.last_activity_at,
        }

    # ------------------------------------------------------------------
    # Row converters
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_source(row: sqlite3.Row) -> CodexSource:
        return CodexSource(
            source_id=row["source_id"],
            title=row["title"],
            source_type=row["source_type"],
            source_path=row["source_path"],
            domain=row["domain"],
            topics=json.loads(row["topics_json"] or "[]"),
            status=row["status"],
            content_hash=row["content_hash"],
            word_count=int(row["word_count"] or 0),
            turn_count=int(row["turn_count"] or 0),
            first_ingested_at=row["first_ingested_at"],
            last_updated_at=row["last_updated_at"],
            last_reviewed_at=row["last_reviewed_at"],
            superseded_by=row["superseded_by"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    @staticmethod
    def _row_to_topic(row: sqlite3.Row) -> CodexTopic:
        return CodexTopic(
            topic_id=row["topic_id"],
            title=row["title"],
            domain=row["domain"],
            description=row["description"],
            source_ids=json.loads(row["source_ids_json"] or "[]"),
            related_topics=json.loads(row["related_topics_json"] or "[]"),
            contradiction_count=int(row["contradiction_count"] or 0),
            staleness_score=float(row["staleness_score"] or 0.0),
            last_activity_at=row["last_activity_at"],
            is_wiki_page=bool(row["is_wiki_page"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    @staticmethod
    def _row_to_contradiction(row: sqlite3.Row) -> CodexContradiction:
        return CodexContradiction(
            contradiction_id=row["contradiction_id"],
            topic_id=row["topic_id"],
            claim_a=row["claim_a"],
            source_a_id=row["source_a_id"],
            source_a_title=row["source_a_title"],
            claim_b=row["claim_b"],
            source_b_id=row["source_b_id"],
            source_b_title=row["source_b_title"],
            severity=row["severity"],
            status=row["status"],
            context=row["context"],
            resolution_notes=row["resolution_notes"],
            resolved_by=row["resolved_by"],
            resolved_at=row["resolved_at"],
            detected_at=row["detected_at"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )
