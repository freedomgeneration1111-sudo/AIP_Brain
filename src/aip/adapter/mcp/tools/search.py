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
    # Vector search — use embed_fn from container (or local adapter stub) per §7.2 boundary
    if container.vector_store:
        try:
            # Use the embedding provider from the container, or fall back to
            # the adapter-local stub (no orchestration import).
            embed_fn = getattr(container, "embedding_provider", None)
            if embed_fn is not None and hasattr(embed_fn, "embed"):
                query_vector = await embed_fn.embed(query)
            else:
                from aip.adapter.embedding.ollama_embed import fake_embed_via_provider
                query_vector = fake_embed_via_provider(query)
            vector_results = await container.vector_store.retrieve(query_vector, domain=domain)
            results.extend([
                {"id": r.id, "content": r.content, "score": r.score, "source": "vector"}
                for r in vector_results
            ])
        except Exception:
            pass
    return results
