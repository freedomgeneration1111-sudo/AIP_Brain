"""aip_search MCP tool — read, uses LexicalStore + VectorStore Protocols.

MCP routes through Protocols, not around them.
No direct store access — all access through container Protocols.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def aip_search(container: Any, query: str, domain: str | None = None) -> list[dict]:
    """Search across lexical and vector stores via Protocols.

    Returns a list of result dicts, each with id, content, score, source.
    Returns empty list if no backends have results (which is a valid real result).
    Logs errors instead of silently swallowing them.
    """
    results: list[dict] = []

    # Lexical search
    if container.lexical_store:
        try:
            lexical_results = await container.lexical_store.search(query, domain=domain)
            results.extend(
                [{"id": r.id, "content": r.content, "score": r.score, "source": "lexical"} for r in lexical_results],
            )
        except Exception as exc:
            logger.warning("MCP lexical search failed: %s", exc)

    # Vector search — use embedding provider from container, or adapter-local stub
    if container.vector_store:
        try:
            embed_fn = getattr(container, "embedding_provider", None)
            if embed_fn is not None and hasattr(embed_fn, "embed"):
                query_vector = await embed_fn.embed(query)
            else:
                from aip.adapter.embedding.ollama_embed import fake_embed_via_provider

                query_vector = fake_embed_via_provider(query)
            vector_results = await container.vector_store.retrieve(query_vector, domain=domain, top_k=10)
            results.extend(
                [{"id": r.id, "content": r.content, "score": r.score, "source": "vector"} for r in vector_results],
            )
        except Exception as exc:
            logger.warning("MCP vector search failed: %s", exc)

    return results
