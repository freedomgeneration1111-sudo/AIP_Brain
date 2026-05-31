"""Sources API route — browse indexed sources and their chunks.

Provides an overview of all ingested content: conversations, artifacts,
and compiled knowledge that have been indexed into LexicalStore and
VectorStore. Unlike /memory/search which searches *within* sources,
this endpoint *browses* the source inventory.

Phase 4: Knowledge Exploration Features.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/sources")
async def list_sources(
    domain: str | None = None,
    source_type: str | None = None,
    container: AipContainer = Depends(get_container),
):
    """List indexed sources with metadata.

    Returns a summary of all indexed content organized by source type:
      - conversation: Ingested conversation chunks
      - artifact: Generated and saved artifacts
      - compiled_knowledge: Approved compiled knowledge

    Each source entry includes: source_id, type, domain, and metadata.
    """
    sources: list[dict[str, Any]] = []

    # Gather from entity store (artifact metadata)
    if container.entity_store is not None:
        try:
            entities = await container.entity_store.list_entities(
                entity_type=source_type,
            )
            for entity in entities:
                etype = entity.get("entity_type", entity.get("type", "artifact"))
                if source_type and etype != source_type:
                    continue
                entity_domain = entity.get("domain", "")
                if domain and entity_domain != domain:
                    continue
                sources.append({
                    "source_id": entity.get("entity_id", entity.get("id", "")),
                    "source_type": etype,
                    "domain": entity_domain,
                    "title": entity.get("name", entity.get("title", "")),
                    "metadata": entity,
                })
        except Exception as exc:
            logger.warning("Failed to list entities for sources: %s", exc)

    # Gather from knowledge store (compiled knowledge)
    if container.knowledge_store is not None and (source_type is None or source_type == "compiled_knowledge"):
        try:
            knowledge_items = await container.knowledge_store.list_compiled(domain=domain)
            for item in knowledge_items:
                sources.append({
                    "source_id": item.get("knowledge_id", ""),
                    "source_type": "compiled_knowledge",
                    "domain": item.get("domain", ""),
                    "title": item.get("knowledge_id", ""),
                    "state": item.get("state", ""),
                    "metadata": {
                        "source_canonical_ids": item.get("source_canonical_ids", []),
                        "created_at": item.get("created_at", ""),
                        "updated_at": item.get("updated_at", ""),
                    },
                })
        except Exception as exc:
            logger.warning("Failed to list knowledge for sources: %s", exc)

    # Add vector store stats
    vector_stats: dict[str, Any] = {}
    if container.vector_store is not None:
        try:
            vector_count = await container.vector_store.count(domain=domain)
            vector_stats = {
                "total_vectors": vector_count,
                "domain": domain,
            }
        except Exception as exc:
            logger.warning("Failed to get vector store stats: %s", exc)

    # Add lexical store stats (approximate via search)
    lexical_stats: dict[str, Any] = {}
    if container.lexical_store is not None:
        try:
            # Try to get a count via a broad search
            # LexicalStore doesn't have a count method, so we estimate
            lexical_stats = {"available": True, "domain": domain}
        except Exception:
            lexical_stats = {"available": False}

    return {
        "sources": sources,
        "total": len(sources),
        "vector_stats": vector_stats,
        "lexical_stats": lexical_stats,
    }


@router.get("/sources/stats")
async def get_sources_stats(container: AipContainer = Depends(get_container)):
    """Get aggregate statistics about indexed content.

    Returns counts for vectors, entities, knowledge items, and
    storage health information. Useful for the Sources panel
    overview and for monitoring ingestion progress.
    """
    stats: dict[str, Any] = {
        "vector_store": {"available": False, "total_vectors": 0},
        "entity_store": {"available": False, "total_entities": 0},
        "knowledge_store": {"available": False, "total_items": 0},
        "lexical_store": {"available": False},
    }

    # Vector store stats
    if container.vector_store is not None:
        try:
            total = await container.vector_store.count()
            health = await container.vector_store.health_check()
            stats["vector_store"] = {
                "available": True,
                "total_vectors": total,
                "health": health,
            }
        except Exception as exc:
            logger.warning("Vector store stats failed: %s", exc)
            stats["vector_store"] = {"available": True, "error": str(exc)}

    # Entity store stats
    if container.entity_store is not None:
        try:
            entities = await container.entity_store.list_entities()
            stats["entity_store"] = {
                "available": True,
                "total_entities": len(entities),
            }
        except Exception as exc:
            logger.warning("Entity store stats failed: %s", exc)

    # Knowledge store stats
    if container.knowledge_store is not None:
        try:
            items = await container.knowledge_store.list_compiled()
            stats["knowledge_store"] = {
                "available": True,
                "total_items": len(items),
            }
        except Exception as exc:
            logger.warning("Knowledge store stats failed: %s", exc)

    # Lexical store availability
    stats["lexical_store"] = {"available": container.lexical_store is not None}

    return stats
