"""Retriever Protocol — the contract for all retrieval backends.

Every retriever (FTS, Vector, Graph, Wiki, Procedural) must implement
this protocol. The RetrievalOrchestrator dispatches the same
RetrievalQuery to all enabled retrievers and fuses their outputs
via RRF.

Layer: foundation only. No adapter/orchestration imports.

Phase 5.1 deliverable: the protocol substrate that lets future
retrievers (GraphRetriever, WikiRetriever, etc.) be dropped in
with zero changes to the orchestrator.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from aip.foundation.schemas.retrieval_trace import (
    RetrievalBudget,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
)


@runtime_checkable
class Retriever(Protocol):
    """Unified retrieval protocol — all retrievers implement this interface.

    Contract:
    - receive the same RetrievalQuery as every other retriever
    - respect the RetrievalBudget (do not return 500 hits when budget
      allows 25)
    - write timing and debug data into the RetrievalTrace
    - never raise — on failure, return [] and set degraded/error in trace
    - return list[RetrievalHit] with retrieval_channel set correctly

    This is the key abstraction that prevents retrieval from becoming
    a tangle of inconsistent code paths. Fusion does not care whether
    a hit came from FTS5, vector, graph, or wiki — it only sees
    RetrievalHit.
    """

    @property
    def name(self) -> str:
        """Human-readable retriever name for trace/debug (e.g. 'FTSRetriever')."""
        ...

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        budget: RetrievalBudget,
        trace: RetrievalTrace,
    ) -> list[RetrievalHit]:
        """Execute retrieval and return hits.

        Args:
            query: The normalized query (same for all retrievers).
            budget: Token/count constraints — retriever should not exceed
                    budget.max_sources candidates.
            trace: Mutable trace object — retriever must append its
                   RetrieverTrace to trace.retriever_traces.

        Returns:
            List of RetrievalHit, ordered by descending relevance
            within this retriever. May be empty (not an error).
        """
        ...


__all__ = ["Retriever"]
