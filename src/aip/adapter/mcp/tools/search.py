"""aip_search MCP tool (CHUNK-8.5) — read, uses LexicalStore + VectorStore Protocols."""

from __future__ import annotations

from typing import Any


async def aip_search(container: Any, query: str, domain: str | None = None) -> list[dict]:
    """Search across lexical and vector stores (CHUNK-8.5)."""
    results = []
    # Lexical search
    if container.lexical_store:
        try:
            lexical_results = await container.lexical_store.search(query, domain=domain)
            results.extend([
                {"id": r.id, "content": r.content, "score": r.score, "source": "lexical"}
                for r in lexical_results
            ])
        except Exception:
            pass
    # Vector search
    if container.vector_store:
        try:
            from aip.orchestration.retrieval import fake_embed
            query_vector = fake_embed(query)
            vector_results = await container.vector_store.retrieve(query_vector, domain=domain)
            results.extend([
                {"id": r.id, "content": r.content, "score": r.score, "source": "vector"}
                for r in vector_results
            ])
        except Exception:
            pass
    return results
