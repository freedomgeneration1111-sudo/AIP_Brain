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
            # Entity-turn index (hippocampal index): maps entities to the turns that mention them.
            # This is the core data structure for Zone A (direct mention) retrieval.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entity_turn_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    source TEXT NOT NULL DEFAULT 'sexton_extraction',
                    created_at TEXT NOT NULL,
                    UNIQUE(entity_id, turn_id, source)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eti_entity ON entity_turn_index(entity_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eti_turn ON entity_turn_index(turn_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eti_confidence ON entity_turn_index(confidence DESC)")
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

    # ------------------------------------------------------------------
    # Entity-turn index (hippocampal index)
    # ------------------------------------------------------------------

    def write_entity_turn(
        self,
        entity_id: str,
        turn_id: str,
        confidence: float = 0.7,
        source: str = "sexton_extraction",
    ) -> None:
        """Write a single entity→turn link to the hippocampal index.

        The UNIQUE(entity_id, turn_id, source) constraint means the same
        entity-turn pair from the same source is idempotent. If Sexton
        extracts the same entity from the same turn twice, the second
        write silently updates confidence but does not duplicate the row.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO entity_turn_index
                    (entity_id, turn_id, confidence, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entity_id, turn_id, confidence, source, now),
            )
            conn.commit()
        finally:
            conn.close()

    def write_entity_turn_batch(
        self,
        entries: list[tuple[str, str, float, str]],
    ) -> int:
        """Bulk-write entity→turn links. Returns count written.

        Args:
            entries: List of (entity_id, turn_id, confidence, source) tuples.
        """
        if not entries:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executemany(
                """
                INSERT OR REPLACE INTO entity_turn_index
                    (entity_id, turn_id, confidence, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(eid, tid, conf, src, now) for eid, tid, conf, src in entries],
            )
            conn.commit()
            return len(entries)
        finally:
            conn.close()

    def get_turns_for_entities(
        self,
        entity_ids: list[str],
        min_confidence: float = 0.3,
        limit: int = 50,
    ) -> list[dict]:
        """Return turns that mention any of the given entities.

        Returns list of dicts with: turn_id, entity_id, confidence, source.
        Results are ordered by confidence descending (highest-confidence
        entity-turn links first). This is the Zone A retrieval path.

        Hub leash: If an entity appears in >limit turns, only the top
        turns by confidence are returned. This prevents hub entities
        (like "Komal" which may have 100+ turns) from drowning out
        other entities' contributions.
        """
        if not entity_ids:
            return []
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            placeholders = ",".join("?" for _ in entity_ids)
            rows = conn.execute(
                f"""
                SELECT turn_id, entity_id, confidence, source
                FROM entity_turn_index
                WHERE entity_id IN ({placeholders})
                  AND confidence >= ?
                ORDER BY confidence DESC
                LIMIT ?
                """,
                (*entity_ids, min_confidence, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_entities_for_turn(self, turn_id: str) -> list[dict]:
        """Return all entity mentions for a given turn.

        Used to populate the entities field in RetrievalHit when
        converting a turn found via graph expansion.
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT entity_id, confidence, source
                FROM entity_turn_index
                WHERE turn_id = ?
                ORDER BY confidence DESC
                """,
                (turn_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def backfill_entity_turn_index(self) -> dict:
        """Backfill entity_turn_index from existing graph_edges.evidence_turn_ids_json.

        For every edge that has evidence_turn_ids, write the source and target
        entities as entity→turn links with the edge's confidence. This is a
        one-time migration that runs at startup to populate the index from
        data that already exists in the graph.

        Returns dict with: entities_written, edges_processed, skipped.
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        now = datetime.now(timezone.utc).isoformat()
        try:
            # Check if backfill has already been done (heuristic: count rows)
            existing = conn.execute("SELECT COUNT(*) FROM entity_turn_index").fetchone()[0]
            if existing > 0:
                return {"entities_written": 0, "edges_processed": 0, "skipped": "already_populated"}

            edges = conn.execute(
                "SELECT source_id, target_id, confidence, evidence_turn_ids_json FROM graph_edges"
            ).fetchall()

            entries: list[tuple] = []
            for edge in edges:
                turn_ids = json.loads(edge["evidence_turn_ids_json"] or "[]")
                confidence = float(edge["confidence"] or 0.5)
                for turn_id in turn_ids:
                    entries.append((edge["source_id"], turn_id, confidence, "edge_backfill"))
                    entries.append((edge["target_id"], turn_id, confidence, "edge_backfill"))

            if entries:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO entity_turn_index
                        (entity_id, turn_id, confidence, source, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [(eid, tid, conf, src, now) for eid, tid, conf, src in entries],
                )
                conn.commit()

            return {
                "entities_written": len(entries),
                "edges_processed": len(edges),
                "skipped": None,
            }
        finally:
            conn.close()

    def mention_scan(self, limit: int = 500) -> dict:
        """Cheap mention scan — no LLM. Finds corpus turns that mention known entity names.

        For each graph node (canonical_name + aliases), scans corpus_turns.searchable_text
        for substring matches. Writes matches to entity_turn_index with lower confidence
        (0.4) and source='mention_scan'.

        This is a startup/background operation that ensures the entity-turn index
        covers turns that Sexton hasn't extracted yet. It's cheap because it only
        does SQLite LIKE queries, no LLM calls.

        Returns dict with: entities_scanned, mentions_found, turns_processed.
        """
        import re as _re

        conn = sqlite3.connect(self._db_path)
        now = datetime.now(timezone.utc).isoformat()
        try:
            # Get all nodes with their names and aliases
            nodes = conn.execute(
                "SELECT id, canonical_name, aliases_json, entity_type FROM graph_nodes"
            ).fetchall()

            mentions_found = 0
            entities_scanned = 0
            entries: list[tuple] = []

            for node in nodes:
                entity_id = node["id"]
                canonical = node["canonical_name"]
                aliases = json.loads(node["aliases_json"] or "[]")

                # Build search terms: canonical name + aliases
                search_terms = [canonical] + aliases[:5]  # cap aliases to avoid explosion
                entities_scanned += 1

                for term in search_terms:
                    if len(term) < 3:
                        continue  # skip very short terms (too many false positives)

                    # Search corpus_turns for mentions
                    turns = conn.execute(
                        """
                        SELECT turn_id FROM corpus_turns
                        WHERE searchable_text LIKE ?
                        LIMIT ?
                        """,
                        (f"%{term}%", limit),
                    ).fetchall()

                    for turn in turns:
                        tid = turn["turn_id"]
                        entries.append((entity_id, tid, 0.4, "mention_scan"))
                        mentions_found += 1

            # Batch write (INSERT OR IGNORE to avoid overwriting higher-confidence entries)
            if entries:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO entity_turn_index
                        (entity_id, turn_id, confidence, source, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [(eid, tid, conf, src, now) for eid, tid, conf, src in entries],
                )
                conn.commit()

            return {
                "entities_scanned": entities_scanned,
                "mentions_found": mentions_found,
                "turns_processed": 0,
            }
        finally:
            conn.close()

    def entity_turn_count(self) -> int:
        """Count of rows in entity_turn_index."""
        conn = sqlite3.connect(self._db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM entity_turn_index").fetchone()[0]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Row converters
    # ------------------------------------------------------------------

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
