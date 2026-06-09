"""Graph store — SQLite adjacency tables in state.db.

Stores knowledge graph nodes and edges for AIP's corpus intelligence.
Populated from bridge tags (approved DEFINER connections) and enriched
via Beast entity extraction on high-importance turns.

Layer: adapter. Importing orchestration is forbidden.

Async initialization pattern:
- ``__init__`` is lightweight (stores path only, no I/O).
- Call ``initialize()`` (async) to create tables before first use,
  or rely on lazy creation via ``_get_conn()``.
- Uses a persistent aiosqlite connection with error recovery.
- WAL mode enabled for concurrent read/write with Sexton/Beast actors.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.adapter.read_pool import ReadPoolMixin
from aip.adapter.store_health import StoreHealthMixin
from aip.foundation.protocols import GraphStore as GraphStoreProtocol

log = logging.getLogger(__name__)

# SQLITE_BUSY retry configuration
_BUSY_RETRY_MAX = 3
_BUSY_RETRY_BASE_DELAY = 0.05  # 50ms base, doubles each retry

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_GRAPH_NODES = """
    CREATE TABLE IF NOT EXISTS graph_nodes (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        canonical_name TEXT NOT NULL,
        domain TEXT,
        confidence REAL DEFAULT 1.0,
        source TEXT DEFAULT 'manual',
        aliases_json TEXT DEFAULT '[]',
        metadata_json TEXT DEFAULT '{}',
        created_at TEXT,
        updated_at TEXT
    )
"""

_DDL_GRAPH_EDGES = """
    CREATE TABLE IF NOT EXISTS graph_edges (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        relationship_type TEXT NOT NULL,
        bridge_tag TEXT,
        confidence REAL DEFAULT 1.0,
        evidence_turn_ids_json TEXT DEFAULT '[]',
        weight REAL DEFAULT 1.0,
        created_at TEXT
    )
"""

_DDL_GRAPH_EXTRACTION_LOG = """
    CREATE TABLE IF NOT EXISTS graph_extraction_log (
        turn_id TEXT PRIMARY KEY,
        extracted_at TEXT NOT NULL,
        entities_found INTEGER DEFAULT 0,
        relationships_found INTEGER DEFAULT 0
    )
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_graph_nodes_domain ON graph_nodes(domain)",
    "CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes(entity_type)",
]


def _is_busy_error(exc: Exception) -> bool:
    """Return True if the exception is an SQLITE_BUSY error."""
    msg = str(exc).lower()
    return "database is locked" in msg or "sqlite_busy" in msg


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class GraphNode:
    """A node in the knowledge graph."""

    id: str                          # snake_case canonical identifier
    entity_type: str                 # PERSON | PROJECT | CONCEPT | PLACE | ORGANIZATION | MANUSCRIPT | DOMAIN
    canonical_name: str              # human-readable display name
    domain: str | None = None
    confidence: float = 1.0
    source: str = "manual"           # manual | bridge | beast_extraction | sexton_extraction
    aliases: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class GraphEdge:
    """A directed edge in the knowledge graph."""

    id: str                          # f"{source_id}__{relationship_type}__{target_id}"
    source_id: str
    target_id: str
    relationship_type: str           # CONNECTS | WORKS_ON | FUNDED_BY | AUTHORED | LOCATED_IN | RELATES_TO
    bridge_tag: str | None = None    # original bridge tag string if from corpus
    confidence: float = 1.0
    evidence_turn_ids: list[str] = field(default_factory=list)
    weight: float = 1.0
    created_at: str | None = None


class GraphStore(GraphStoreProtocol, StoreHealthMixin, ReadPoolMixin):
    """SQLite-backed knowledge graph store with async initialization.

    All tables live in the main state.db alongside artifacts, events, etc.
    Uses a persistent aiosqlite connection per instance with error recovery.
    WAL mode enabled for concurrent reads from API endpoints while
    Sexton/Beast actors write graph extraction results.

    Node/edge counts expected: <5,000 nodes, <20,000 edges at full corpus scale.
    """

    def __init__(self, db_path: str, config: dict | None = None) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._tables_ready = False
        self._read_pool_config = config
        from aip.adapter.read_pool import resolve_pool_size
        self._init_read_pool(pool_size=resolve_pool_size("graph_store", config))

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
        """Create graph tables, indexes, and extraction log."""
        await conn.execute(_DDL_GRAPH_NODES)
        await conn.execute(_DDL_GRAPH_EDGES)
        await conn.execute(_DDL_GRAPH_EXTRACTION_LOG)
        for idx_ddl in _DDL_INDEXES:
            await conn.execute(idx_ddl)
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
        """Close the persistent connection and read pool."""
        await self._close_read_pool()
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

    # -------------------------------------------------------------------
    # Write operations (with SQLITE_BUSY retry)
    # -------------------------------------------------------------------

    async def _execute_with_retry(self, coro_fn):
        """Execute an async callable with bounded exponential backoff on SQLITE_BUSY.

        coro_fn must be an async callable (no args), e.g. ``lambda: conn.commit()``.
        Up to _BUSY_RETRY_MAX retries with delays of _BUSY_RETRY_BASE_DELAY * 2^attempt.
        """
        last_exc = None
        for attempt in range(_BUSY_RETRY_MAX + 1):
            try:
                return await coro_fn()
            except Exception as exc:
                if _is_busy_error(exc) and attempt < _BUSY_RETRY_MAX:
                    delay = _BUSY_RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("sqlite_busy_retry attempt=%d delay_ms=%d", attempt + 1, int(delay * 1000))
                    await asyncio.sleep(delay)
                    last_exc = exc
                    continue
                if _is_busy_error(exc):
                    log.error("sqlite_busy_exhausted retries=%d", _BUSY_RETRY_MAX)
                raise
        raise last_exc  # type: ignore[misc]

    async def upsert_node(self, node: GraphNode) -> None:
        """Insert or update a node. Preserves created_at on update."""
        conn = await self._get_conn()
        t0 = time.monotonic()
        try:
            now = datetime.now(timezone.utc).isoformat()
            cursor = await conn.execute(
                "SELECT created_at FROM graph_nodes WHERE id = ?", (node.id,)
            )
            existing = await cursor.fetchone()
            created_at = existing["created_at"] if existing else (node.created_at or now)

            await conn.execute(
                """
                INSERT OR REPLACE INTO graph_nodes
                (id, entity_type, canonical_name, domain, confidence, source,
                 aliases_json, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.id,
                    node.entity_type,
                    node.canonical_name,
                    node.domain,
                    float(node.confidence),
                    node.source,
                    json.dumps(node.aliases or []),
                    json.dumps(node.metadata or {}),
                    created_at,
                    now,
                ),
            )
            await self._execute_with_retry(conn.commit)
            self._health_track_operation(time.monotonic() - t0)
        except Exception:
            await self._reset_conn()
            raise

    async def upsert_edge(self, edge: GraphEdge) -> None:
        """Insert or update an edge."""
        conn = await self._get_conn()
        t0 = time.monotonic()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                """
                INSERT OR REPLACE INTO graph_edges
                (id, source_id, target_id, relationship_type, bridge_tag,
                 confidence, evidence_turn_ids_json, weight, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.id,
                    edge.source_id,
                    edge.target_id,
                    edge.relationship_type,
                    edge.bridge_tag,
                    float(edge.confidence),
                    json.dumps(edge.evidence_turn_ids or []),
                    float(edge.weight),
                    edge.created_at or now,
                ),
            )
            await self._execute_with_retry(conn.commit)
            self._health_track_operation(time.monotonic() - t0)
        except Exception:
            await self._reset_conn()
            raise

    async def delete_node(self, node_id: str) -> None:
        """Delete a node and all its edges."""
        conn = await self._get_conn()
        t0 = time.monotonic()
        try:
            await conn.execute("DELETE FROM graph_nodes WHERE id = ?", (node_id,))
            await conn.execute(
                "DELETE FROM graph_edges WHERE source_id = ? OR target_id = ?",
                (node_id, node_id),
            )
            await self._execute_with_retry(conn.commit)
            self._health_track_operation(time.monotonic() - t0)
        except Exception:
            await self._reset_conn()
            raise

    async def upsert_nodes_batch(self, nodes: list[GraphNode]) -> int:
        """Insert or update multiple nodes in a single transaction.

        Preserves created_at on update for each node. Returns the
        number of nodes upserted. Empty list is a no-op.
        """
        if not nodes:
            return 0
        conn = await self._get_conn()
        t0 = time.monotonic()
        try:
            now = datetime.now(timezone.utc).isoformat()
            for node in nodes:
                cursor = await conn.execute(
                    "SELECT created_at FROM graph_nodes WHERE id = ?", (node.id,)
                )
                existing = await cursor.fetchone()
                created_at = existing["created_at"] if existing else (node.created_at or now)

                await conn.execute(
                    """
                    INSERT OR REPLACE INTO graph_nodes
                    (id, entity_type, canonical_name, domain, confidence, source,
                     aliases_json, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node.id,
                        node.entity_type,
                        node.canonical_name,
                        node.domain,
                        float(node.confidence),
                        node.source,
                        json.dumps(node.aliases or []),
                        json.dumps(node.metadata or {}),
                        created_at,
                        now,
                    ),
                )
            await self._execute_with_retry(conn.commit)
            self._health_track_operation(time.monotonic() - t0)
            return len(nodes)
        except Exception:
            await self._reset_conn()
            raise

    async def upsert_edges_batch(self, edges: list[GraphEdge]) -> int:
        """Insert or update multiple edges in a single transaction.

        Returns the number of edges upserted. Empty list is a no-op.
        """
        if not edges:
            return 0
        conn = await self._get_conn()
        t0 = time.monotonic()
        try:
            now = datetime.now(timezone.utc).isoformat()
            for edge in edges:
                await conn.execute(
                    """
                    INSERT OR REPLACE INTO graph_edges
                    (id, source_id, target_id, relationship_type, bridge_tag,
                     confidence, evidence_turn_ids_json, weight, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge.id,
                        edge.source_id,
                        edge.target_id,
                        edge.relationship_type,
                        edge.bridge_tag,
                        float(edge.confidence),
                        json.dumps(edge.evidence_turn_ids or []),
                        float(edge.weight),
                        edge.created_at or now,
                    ),
                )
            await self._execute_with_retry(conn.commit)
            self._health_track_operation(time.monotonic() - t0)
            return len(edges)
        except Exception:
            await self._reset_conn()
            raise

    async def log_turn_extracted(self, turn_id: str, entities: int, relationships: int) -> None:
        """Record that a turn has been graph-extracted (dedup guard)."""
        conn = await self._get_conn()
        t0 = time.monotonic()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                """
                INSERT OR REPLACE INTO graph_extraction_log
                (turn_id, extracted_at, entities_found, relationships_found)
                VALUES (?, ?, ?, ?)
                """,
                (turn_id, now, entities, relationships),
            )
            await self._execute_with_retry(conn.commit)
            self._health_track_operation(time.monotonic() - t0)
        except Exception:
            await self._reset_conn()
            raise

    # -------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------

    async def get_node(self, node_id: str) -> GraphNode | None:
        """Return a single node by ID, or None."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM graph_nodes WHERE id = ?", (node_id,)
            )
            row = await cursor.fetchone()
            return self._row_to_node(row) if row else None
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def get_neighbors(self, node_id: str, min_confidence: float = 0.4) -> list[GraphNode]:
        """Return all nodes directly connected to node_id (in or out edges)."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT DISTINCT n.*
                FROM graph_nodes n
                JOIN graph_edges e ON (e.target_id = n.id OR e.source_id = n.id)
                WHERE (e.source_id = ? OR e.target_id = ?)
                  AND n.id != ?
                  AND e.confidence >= ?
                """,
                (node_id, node_id, node_id, min_confidence),
            )
            rows = await cursor.fetchall()
            return [self._row_to_node(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def get_edges_for_node(self, node_id: str) -> list[GraphEdge]:
        """Return all edges where node is source or target."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM graph_edges WHERE source_id = ? OR target_id = ?",
                (node_id, node_id),
            )
            rows = await cursor.fetchall()
            return [self._row_to_edge(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def search_nodes(
        self,
        query: str,
        entity_type: str | None = None,
        domain: str | None = None,
        limit: int = 20,
    ) -> list[GraphNode]:
        """Search nodes by canonical_name substring (case-insensitive)."""
        conn = await self._checkout_read_conn()
        try:
            sql = "SELECT * FROM graph_nodes WHERE canonical_name LIKE ?"
            params: list[Any] = [f"%{query}%"]
            if entity_type:
                sql += " AND entity_type = ?"
                params.append(entity_type)
            if domain:
                sql += " AND domain = ?"
                params.append(domain)
            sql += " ORDER BY confidence DESC LIMIT ?"
            params.append(limit)
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_node(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def node_count(self) -> int:
        """Return total number of nodes."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) FROM graph_nodes")
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def edge_count(self) -> int:
        """Return total number of edges."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) FROM graph_edges")
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def get_all_nodes(self, min_confidence: float = 0.0) -> list[GraphNode]:
        """Return all nodes above min_confidence, ordered by id."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM graph_nodes WHERE confidence >= ? ORDER BY id",
                (min_confidence,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_node(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def get_all_edges(self, min_confidence: float = 0.0) -> list[GraphEdge]:
        """Return all edges above min_confidence, ordered by id."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM graph_edges WHERE confidence >= ? ORDER BY id",
                (min_confidence,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_edge(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def is_turn_extracted(self, turn_id: str) -> bool:
        """Check whether a turn has already been graph-extracted."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                "SELECT 1 FROM graph_extraction_log WHERE turn_id = ?", (turn_id,)
            )
            row = await cursor.fetchone()
            return row is not None
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def get_unextracted_high_importance_turns(
        self, min_importance: float = 0.7, limit: int = 50
    ) -> list[dict]:
        """Return high-importance corpus turns not yet graph-extracted.

        Queries the corpus_turns table in the same state.db, so no
        separate db_path parameter is needed (all tables share one DB).
        """
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT ct.turn_id, ct.primary_domain, ct.importance,
                       ct.user_text, ct.assistant_text, ct.tags, ct.bridges
                FROM corpus_turns ct
                WHERE ct.importance >= ?
                  AND ct.tagging_version > 0
                  AND ct.turn_id NOT IN (
                      SELECT turn_id FROM graph_extraction_log
                  )
                ORDER BY ct.importance DESC
                LIMIT ?
                """,
                (min_importance, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    # -------------------------------------------------------------------
    # Health / diagnostics
    # -------------------------------------------------------------------

    async def health_check(self) -> dict:
        """Return basic health info (connected, node count, edge count)."""
        try:
            nodes = await self.node_count()
            edges = await self.edge_count()
            return {"connected": True, "nodes": nodes, "edges": edges}
        except Exception as exc:
            return {"connected": False, "error": str(exc)}

    # -------------------------------------------------------------------
    # Row converters
    # -------------------------------------------------------------------

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> GraphNode:
        return GraphNode(
            id=row["id"],
            entity_type=row["entity_type"],
            canonical_name=row["canonical_name"],
            domain=row["domain"],
            confidence=float(row["confidence"] or 1.0),
            source=row["source"] or "manual",
            aliases=json.loads(row["aliases_json"] or "[]"),
            metadata=json.loads(row["metadata_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> GraphEdge:
        return GraphEdge(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            relationship_type=row["relationship_type"],
            bridge_tag=row["bridge_tag"],
            confidence=float(row["confidence"] or 1.0),
            evidence_turn_ids=json.loads(row["evidence_turn_ids_json"] or "[]"),
            weight=float(row["weight"] or 1.0),
            created_at=row["created_at"],
        )
