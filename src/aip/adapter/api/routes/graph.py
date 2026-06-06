"""Knowledge graph API routes.

GET /api/v1/graph/data — returns nodes and edges for Cytoscape.js visualization.
GET /api/v1/graph/neighbors/{domain} — returns direct domain neighbors.
GET /api/v1/graph/stats — returns node/edge counts by type.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_graph_store(container: AipContainer):
    """Create a GraphStore using the container's db_path.

    Falls back to get_default_db_path() if container config is unavailable.
    """
    from aip.adapter.graph_store import GraphStore
    db_path = container.config.get("db_path", "")
    if not db_path:
        try:
            from aip.cli._db_path import get_default_db_path
            db_path = get_default_db_path()
        except Exception:
            db_path = "db/state.db"
    return GraphStore(db_path)


@router.get("/graph/data")
async def graph_data(
    min_confidence: float = Query(default=0.4, ge=0.0, le=1.0),
    domain: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    container: AipContainer = Depends(get_container),
):
    """Return nodes and edges for Cytoscape.js visualization.

    Filters: min_confidence (default 0.4), domain, entity_type.
    """
    try:
        store = _get_graph_store(container)
    except Exception as exc:
        return {"error": str(exc), "nodes": [], "edges": []}

    nodes = store.get_all_nodes(min_confidence=min_confidence)
    edges = store.get_all_edges(min_confidence=min_confidence)

    if domain:
        domain_node_ids = {n.id for n in nodes if n.domain == domain or n.id == domain}
        nodes = [n for n in nodes if n.id in domain_node_ids]
        edges = [e for e in edges if e.source_id in domain_node_ids or e.target_id in domain_node_ids]

    if entity_type:
        nodes = [n for n in nodes if n.entity_type == entity_type]
        visible_ids = {n.id for n in nodes}
        edges = [e for e in edges if e.source_id in visible_ids and e.target_id in visible_ids]

    return {
        "nodes": [
            {
                "id": n.id,
                "label": n.canonical_name,
                "entity_type": n.entity_type,
                "domain": n.domain or "",
                "confidence": n.confidence,
                "source": n.source,
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": e.id,
                "source": e.source_id,
                "target": e.target_id,
                "relationship_type": e.relationship_type,
                "bridge_tag": e.bridge_tag or "",
                "confidence": e.confidence,
                "weight": e.weight,
            }
            for e in edges
        ],
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


@router.get("/graph/neighbors/{node_id}")
async def graph_neighbors(
    node_id: str,
    min_confidence: float = Query(default=0.4, ge=0.0, le=1.0),
    container: AipContainer = Depends(get_container),
):
    """Return direct neighbors of a node."""
    try:
        store = _get_graph_store(container)
        neighbors = store.get_neighbors(node_id, min_confidence=min_confidence)
        return {
            "node_id": node_id,
            "nodes": [
                {"id": n.id, "canonical_name": n.canonical_name, "entity_type": n.entity_type}
                for n in neighbors
            ],
            "edges": [],
        }
    except Exception as exc:
        return {"error": str(exc), "node_id": node_id, "nodes": [], "edges": []}


@router.get("/graph/stats")
async def graph_stats(container: AipContainer = Depends(get_container)):
    """Return graph statistics."""
    try:
        store = _get_graph_store(container)
        nodes = store.get_all_nodes()
        edges = store.get_all_edges()

        by_type: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for n in nodes:
            by_type[n.entity_type] = by_type.get(n.entity_type, 0) + 1
            by_source[n.source] = by_source.get(n.source, 0) + 1

        edge_by_rel: dict[str, int] = {}
        for e in edges:
            edge_by_rel[e.relationship_type] = edge_by_rel.get(e.relationship_type, 0) + 1

        return {
            "nodes": len(nodes),
            "edges": len(edges),
            "nodes_by_type": by_type,
            "nodes_by_source": by_source,
            "edges_by_relationship": edge_by_rel,
        }
    except Exception as exc:
        logger.warning("graph_stats failed: %s", exc)
        return {"error": str(exc), "nodes": 0, "edges": 0}
