"""aip_search MCP tool (CHUNK-8.5) — read, uses LexicalStore + VectorStore Protocols."""

from __future__ import annotations

from typing import Any


async def aip_search(container: Any, query: str, domain: str | None = None) -> list[dict]:
    # Must go through container.lexical_store.search + container.vector_store.retrieve
    # (not direct DB)
    if not container.lexical_store:
        return []
    # In full: merge with vector results
    return await container.lexical_store.search(query, domain=domain)
