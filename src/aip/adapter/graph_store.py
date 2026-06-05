"""Graph store — SQLite adjacency tables in state.db.

Stores knowledge graph nodes and edges for AIP's corpus intelligence.
Phase 1: populated from bridge tags (approved DEFINER connections).
Phase 2: enriched via Beast entity extraction on high-importance turns.

Layer: adapter. Synchronous (no aiosqlite) — graph is small (<50k nodes)
and read-heavy. Importing orchestration is forbidden.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class GraphNode:
    """A node in the knowledge graph."""

    id: str                          # snake_case canonical identifier
    entity_type: str                 # PERSON | PROJECT | CONCEPT | PLACE | ORGANIZATION | MANUSCRIPT | DOMAIN
    canonical_name: str              # human-readable display name
    domain: str | None = None
    confidence: float = 1.0
    source: str = "manual"           # manual | bridge | beast_extraction
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


class GraphStore:
    """SQLite-backed knowledge graph store.

    All tables live in the main state.db alongside artifacts, events, etc.
    Synchronous API — no aiosqlite overhead for this small-scale graph.

    Node/edge counts expected: <5,000 nodes, <20,000 edges at full corpus scale.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
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
            """)
            conn.execute("""
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
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_extraction_log (
                    turn_id TEXT PRIMARY KEY,
                    extracted_at TEXT NOT NULL,
                    entities_found INTEGER DEFAULT 0,
                    relationships_found INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_nodes_domain ON graph_nodes(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes(entity_type)")
            conn.commit()
        finally:
            conn.close()

    def upsert_node(self, node: GraphNode) -> None:
        """Insert or update a node. Preserves created_at on update."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        try:
            existing = conn.execute(
                "SELECT created_at FROM graph_nodes WHERE id = ?", (node.id,)
            ).fetchone()
            created_at = existing[0] if existing else (node.created_at or now)

            conn.execute(
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
            conn.commit()
        finally:
            conn.close()

    def upsert_edge(self, edge: GraphEdge) -> None:
        """Insert or update an edge."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
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
            conn.commit()
        finally:
            conn.close()

    def get_node(self, node_id: str) -> GraphNode | None:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM graph_nodes WHERE id = ?", (node_id,)).fetchone()
            return self._row_to_node(row) if row else None
        finally:
            conn.close()

    def get_neighbors(self, node_id: str, min_confidence: float = 0.4) -> list[GraphNode]:
        """Return all nodes directly connected to node_id (in or out edges)."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT n.*
                FROM graph_nodes n
                JOIN graph_edges e ON (e.target_id = n.id OR e.source_id = n.id)
                WHERE (e.source_id = ? OR e.target_id = ?)
                  AND n.id != ?
                  AND e.confidence >= ?
                """,
                (node_id, node_id, node_id, min_confidence),
            ).fetchall()
            return [self._row_to_node(r) for r in rows]
        finally:
            conn.close()

    def get_edges_for_node(self, node_id: str) -> list[GraphEdge]:
        """Return all edges where node is source or target."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM graph_edges WHERE source_id = ? OR target_id = ?",
                (node_id, node_id),
            ).fetchall()
            return [self._row_to_edge(r) for r in rows]
        finally:
            conn.close()

    def search_nodes(
        self,
        query: str,
        entity_type: str | None = None,
        domain: str | None = None,
        limit: int = 20,
    ) -> list[GraphNode]:
        """Search nodes by canonical_name substring (case-insensitive)."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
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
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_node(r) for r in rows]
        finally:
            conn.close()

    def node_count(self) -> int:
        conn = sqlite3.connect(self._db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        finally:
            conn.close()

    def edge_count(self) -> int:
        conn = sqlite3.connect(self._db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        finally:
            conn.close()

    def get_all_nodes(self, min_confidence: float = 0.0) -> list[GraphNode]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM graph_nodes WHERE confidence >= ? ORDER BY id",
                (min_confidence,),
            ).fetchall()
            return [self._row_to_node(r) for r in rows]
        finally:
            conn.close()

    def get_all_edges(self, min_confidence: float = 0.0) -> list[GraphEdge]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM graph_edges WHERE confidence >= ? ORDER BY id",
                (min_confidence,),
            ).fetchall()
            return [self._row_to_edge(r) for r in rows]
        finally:
            conn.close()

    def delete_node(self, node_id: str) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("DELETE FROM graph_nodes WHERE id = ?", (node_id,))
            conn.execute("DELETE FROM graph_edges WHERE source_id = ? OR target_id = ?", (node_id, node_id))
            conn.commit()
        finally:
            conn.close()

    def is_turn_extracted(self, turn_id: str) -> bool:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM graph_extraction_log WHERE turn_id = ?", (turn_id,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def log_turn_extracted(self, turn_id: str, entities: int, relationships: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO graph_extraction_log
                (turn_id, extracted_at, entities_found, relationships_found)
                VALUES (?, ?, ?, ?)
                """,
                (turn_id, now, entities, relationships),
            )
            conn.commit()
        finally:
            conn.close()

    def get_unextracted_high_importance_turns(
        self, db_path: str, min_importance: float = 0.7, limit: int = 50
    ) -> list[dict]:
        """Return high-importance corpus turns not yet graph-extracted."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
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
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def health_check(self) -> dict:
        try:
            nodes = self.node_count()
            edges = self.edge_count()
            return {"connected": True, "nodes": nodes, "edges": edges}
        except Exception as exc:
            return {"connected": False, "error": str(exc)}

    def _row_to_node(self, row: sqlite3.Row) -> GraphNode:
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

    def _row_to_edge(self, row: sqlite3.Row) -> GraphEdge:
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
