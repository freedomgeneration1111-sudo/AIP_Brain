"""Knowledge store Protocol definition.

Abstraction for the Deferred Compiled Knowledge Layer.
Compiled knowledge tracks provenance to source canonicals.
Per Appendix D: compiled knowledge ≠ canonical artifact.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class KnowledgeStore(Protocol):
    """Abstraction for the Deferred Compiled Knowledge Layer.

    Compiled knowledge must track provenance to source canonicals.
    Per Appendix D: compiled knowledge ≠ canonical artifact.
    Per Process Rule 12: CompilationState is distinct from ECS states.
    """

    async def store_compiled(
        self,
        knowledge_id: str,
        content: str,
        source_canonical_ids: list[str],
        domain: str,
        metadata: dict,
    ) -> None:
        """Store a compiled knowledge artifact with provenance.

        metadata includes: compilation_model_slot, evaluation_scores,
        compilation_timestamp, confidence.
        """
        ...

    async def get_compiled(self, knowledge_id: str) -> dict | None:
        """Get a compiled knowledge artifact by ID.

        Returns dict with: knowledge_id, content, source_canonical_ids,
        domain, state, metadata, created_at, updated_at.
        """
        ...

    async def list_compiled(
        self, domain: str | None = None, state: "CompilationState" | None = None
    ) -> list[dict]:
        """List compiled knowledge, optionally filtered."""
        ...

    async def update_state(self, knowledge_id: str, new_state: "CompilationState") -> None:
        """Transition the compilation state."""
        ...

    async def get_provenance(self, knowledge_id: str) -> list[dict]:
        """Return the list of source canonicals used to compile this knowledge."""
        ...

    async def search_compiled(
        self, query: str, domain: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Search compiled knowledge by query and domain."""
        ...


__all__ = [
    "KnowledgeStore",
]
