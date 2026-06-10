"""Retrieval pipeline protocols.

Defines the public interface for the retrieval/ask pipeline so that
adapter-layer routes can call into orchestration without importing
orchestration modules directly — the container mediates access.

These protocols describe what the container provides; the actual
implementations live in aip.orchestration.ask_pipeline.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AskStoresProtocol(Protocol):
    """Protocol for the AskStores parameter bundle.

    Routes construct this from container-wired stores and pass it
    to the ask pipeline function. The actual dataclass lives in
    aip.orchestration.ask_pipeline — this protocol describes the
    shape so the adapter layer can type-check without importing it.
    """

    artifact_store: Any
    lexical_store: Any
    vector_store: Any
    event_store: Any
    project_store: Any
    ecs_store: Any
    model_provider: Any
    embedding_provider: Any
    corpus_turn_store: Any
    graph_store: Any


@runtime_checkable
class AskPipelineFn(Protocol):
    """Protocol for the ask() pipeline function."""

    async def __call__(
        self,
        question: str,
        project_name: str,
        stores: Any,
        source: str = "all",
        max_sources: int = 10,
        save_artifact: bool = False,
        model_slot: str = "synthesis",
        system_prompt_modifier: str = "",
    ) -> Any: ...


@runtime_checkable
class SearchSourcesFn(Protocol):
    """Protocol for the _search_sources_with_trace function."""

    async def __call__(
        self,
        query: str,
        stores: Any,
        source_filter: str = "all",
        max_sources: int = 10,
    ) -> tuple: ...


@runtime_checkable
class SanitizeFtsQueryFn(Protocol):
    """Protocol for the _sanitize_fts_query function."""

    def __call__(self, query: str) -> str: ...


@runtime_checkable
class IngestConversationFn(Protocol):
    """Protocol for the ingest_conversation function."""

    async def __call__(self, **kwargs: Any) -> Any: ...


@runtime_checkable
class IngestFileFn(Protocol):
    """Protocol for the ingest_file function."""

    async def __call__(self, **kwargs: Any) -> list: ...
