"""Retrieval trace assembly, degradation, and warning utilities.

Extracted from ask_pipeline.py to reduce its complexity and make
trace/degradation handling easier to maintain and test independently.

This module owns:
- Building the ``retrieval_degradation`` dict for AskResult
- Building the ``retrieval_warnings`` list for AskResult
- Enriching a RetrievalTrace with vector-store-specific detail fields
  (backend type, VSS availability, embedding provider status, vector count)

All functions are pure (no side effects, no I/O) except
``enrich_vector_trace_detail`` which may call async store methods for
vector count and reads store attributes for backend status.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aip.foundation.schemas.retrieval import (
    ChannelHealthState,
    RetrievalTrace,
)
from aip.foundation.schemas.vector import VectorBackendStatus, VectorDegradationInfo
from aip.orchestration.channels.types import ChannelFailure

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def build_degradation_dict(
    retrieval_trace: RetrievalTrace | None,
    registration_failures: list[ChannelFailure] | None = None,
) -> dict:
    """Build the retrieval_degradation dict for AskResult from a RetrievalTrace.

    Ensures every AskResult carries an honest account of what retrieval
    backends were available, degraded, or absent.  Also includes any
    channel registration failures from the most recent orchestrator
    creation, so operators can see which channels were skipped.

    Args:
        retrieval_trace: The trace from the retrieval round, or None if
            retrieval did not execute.
        registration_failures: Channel registration failures from the
            most recent orchestrator creation, for trace visibility.

    Returns:
        Dict suitable for AskResult.retrieval_degradation.
    """
    if retrieval_trace is None:
        result: dict = {
            "backend_status": VectorBackendStatus.DISABLED.value,
            "reason": "No retrieval trace available",
            "human_message": VectorBackendStatus.DISABLED.human_message(),
        }
    else:
        vdi = retrieval_trace.vector_degradation
        result = vdi.to_dict()
        summary = retrieval_trace.degradation_summary()
        if summary:
            result["degradation_summary"] = summary

        # Include unified trace diagnostic info
        result["channel_health"] = retrieval_trace.channel_health
        result["channel_health_reasons"] = retrieval_trace.channel_health_reasons
        result["channel_details"] = {ch: d.to_dict() for ch, d in retrieval_trace.channel_details.items()}
        result["active_channels"] = retrieval_trace.get_active_channels()
        result["failed_channels"] = retrieval_trace.get_failed_channels()
        result["degraded_channels"] = retrieval_trace.get_degraded_channels()
        result["unavailable_channels"] = retrieval_trace.get_unavailable_channels()
        result["not_configured_channels"] = retrieval_trace.get_not_configured_channels()
        result["empty_channels"] = retrieval_trace.get_empty_channels()
        result["channels_attempted"] = retrieval_trace.channels_attempted
        result["channels_used"] = retrieval_trace.channels_used
        result["lexical_only"] = retrieval_trace.lexical_only
        result["vector_contributed"] = retrieval_trace.vector_contributed
        result["query_expansion"] = retrieval_trace.query_expansion
        result["entities_extracted"] = retrieval_trace.entities_extracted
        result["documents_retrieved_count"] = len(retrieval_trace.documents_retrieved_ids)
        result["top_scores"] = retrieval_trace.top_scores[:5]
        result["final_context_token_count"] = retrieval_trace.final_context_token_count
        result["verdict"] = retrieval_trace.verdict
        result["channel_contributions"] = retrieval_trace.channel_contributions

    # Include channel registration failures for visibility
    if registration_failures:
        result["channel_registration_failures"] = [f.to_dict() for f in registration_failures]

    return result


def build_retrieval_warnings(retrieval_trace: RetrievalTrace | None) -> list[str]:
    """Build visible retrieval warnings for AskResult.

    Every answer can explain where its context came from and whether
    retrieval was degraded.  These warnings are surfaced to the user
    so they know when an answer may be unreliable due to retrieval
    issues.

    Example output:
        ["Vector channel unavailable",
         "Graph channel returned 0 results",
         "Lexical channel supplied primary evidence"]

    Args:
        retrieval_trace: The trace from the retrieval round, or None.

    Returns:
        List of human-readable warning strings.
    """
    if retrieval_trace is None:
        return ["No retrieval trace available — retrieval may not have executed"]

    warnings: list[str] = []

    # 1. Channel health warnings
    for channel, health in retrieval_trace.channel_health.items():
        if health == "failed":
            warnings.append(f"{channel.capitalize()} channel unavailable")
        elif health == "degraded":
            warnings.append(f"{channel.capitalize()} channel degraded")
        elif health == "unavailable":
            warnings.append(f"{channel.capitalize()} channel unavailable")
        elif health == "not_configured":
            warnings.append(f"{channel.capitalize()} channel not configured")

    # 2. Empty result warnings
    if retrieval_trace.hits_after_quality_gate == 0:
        warnings.append("No documents passed the quality gate")
    elif retrieval_trace.verdict == "NEEDS_MORE_CONTEXT":
        warnings.append("Retrieval quality gate returned insufficient context")

    # 3. Primary evidence identification
    if retrieval_trace.channel_contributions and (warnings or retrieval_trace.get_degraded_channels()):
        best_channel = max(
            retrieval_trace.channel_contributions.keys(),
            key=lambda ch: retrieval_trace.channel_contributions[ch],
        )
        if best_channel:
            warnings.append(f"{best_channel.capitalize()} channel supplied primary evidence")

    # 4. Vector-specific warnings
    vdi = retrieval_trace.vector_degradation
    if vdi.backend_status.value in ("disabled", "failed"):
        if not any("Vector" in w for w in warnings):
            warnings.append("Vector channel unavailable")

    # 5. Add any pre-computed degradation warnings
    for w in retrieval_trace.degradation_warnings:
        if w not in warnings:
            warnings.append(w)

    return warnings


async def enrich_vector_trace_detail(
    trace: RetrievalTrace,
    vector_store: object | None,
    embedding_provider: object | None,
) -> None:
    """Populate vector-specific detail fields on a RetrievalTrace.

    This extracts the inline vector-trace-enrichment logic that was
    previously scattered through ``_search_sources_with_trace`` into
    a single focused helper.  Mutates the trace in place.

    Populates:
    - ``trace.vector_degradation`` from the vector store
    - ``trace.channel_details["vector"]`` with backend type, VSS
      availability, and embedding provider status
    - ``trace.channel_details["vector"].vector_count`` (async)

    Args:
        trace: The RetrievalTrace to enrich.
        vector_store: The vector store (may be None).
        embedding_provider: The embedding provider (may be None).
    """
    # Populate vector_degradation from the vector store
    if vector_store is not None:
        if hasattr(vector_store, "get_degradation_info"):
            try:
                trace.vector_degradation = vector_store.get_degradation_info()
            except Exception as exc:
                logger.debug("Failed to get vector degradation info: %s", exc)
    else:
        trace.vector_degradation = VectorDegradationInfo(
            backend_status=VectorBackendStatus.DISABLED,
            backend_name="none",
            reason="No vector store configured",
        )

    # Populate vector-specific fields in channel_details
    if vector_store is not None and "vector" in trace.channel_details:
        vec_detail = trace.channel_details["vector"]
        vec_detail.embedding_provider_configured = embedding_provider is not None
        vec_detail.vss_available = getattr(vector_store, "_vss_available", None)
        if hasattr(vector_store, "get_backend_status"):
            _vbs = vector_store.get_backend_status()
            if _vbs == VectorBackendStatus.AVAILABLE:
                vec_detail.backend_type = "sqlite_vss"
            elif _vbs == VectorBackendStatus.DEGRADED_BRUTEFORCE:
                vec_detail.backend_type = "brute_force"
            elif vec_detail.backend_type == "":
                vec_detail.backend_type = _vbs.value
        # vector_count is populated asynchronously below
        vec_detail.vector_count = None
        # Update the channel_health to reflect not_configured if needed
        if vec_detail.state == ChannelHealthState.DISABLED and embedding_provider is None:
            vec_detail.state = ChannelHealthState.NOT_CONFIGURED
            vec_detail.degradation_reason = "No embedding provider configured"
            trace.channel_health["vector"] = ChannelHealthState.NOT_CONFIGURED.value
            trace.channel_health_reasons["vector"] = "No embedding provider configured"

    # Populate final context info on the trace
    # (final_context_token_count and final_context_source_ids are set
    # by the caller after SmartContextPacker runs, so we skip them here)

    # Populate vector_count asynchronously
    if vector_store is not None and "vector" in trace.channel_details:
        try:
            vec_count = await vector_store.count()
            trace.channel_details["vector"].vector_count = vec_count
        except Exception as vec_exc:
            logger.debug("Failed to get vector count: %s", vec_exc)
