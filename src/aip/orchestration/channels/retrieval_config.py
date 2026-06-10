"""Retrieval configuration helpers for the ask pipeline.

Extracted from ``ask_pipeline._search_sources_with_trace`` to reduce
its complexity and make retrieval configuration logic independently
testable.  These helpers own:

- Building ``OrchestratorConfig`` from channel flags, coverage gating,
  and channel weights.
- Loading and applying channel weights from the TOML config dict.
- Applying adaptive channel selection via ``ChannelSelector``.

All functions are pure (no side effects, no I/O) except
``apply_channel_selector`` which may import and instantiate a
``ChannelSelector``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aip.orchestration.retrieval_orchestrator import OrchestratorConfig

logger = logging.getLogger(__name__)


def build_orchestrator_config(
    *,
    enable_fts: bool = True,
    enable_vector: bool = True,
    enable_graph: bool = False,
    enable_wiki: bool = False,
    enable_procedural: bool = False,
    vector_enabled: bool = True,
    max_sources: int = 10,
) -> OrchestratorConfig:
    """Build an OrchestratorConfig from channel enable flags.

    Separates the concern of config construction from the search
    orchestration flow in ``_search_sources_with_trace``.  The
    ``vector_enabled`` parameter reflects coverage-gated vector
    enablement (computed upstream).

    Args:
        enable_fts: Whether FTS5 channel is enabled.
        enable_vector: Whether vector channel is requested (before coverage gating).
        enable_graph: Whether graph channel is enabled.
        enable_wiki: Whether wiki channel is enabled.
        enable_procedural: Whether procedural channel is enabled.
        vector_enabled: Whether vector channel is actually enabled after
            coverage gating (may differ from ``enable_vector``).
        max_sources: Maximum number of sources to return (used to set
            ``max_hits`` with 3x overfetch for RRF fusion).

    Returns:
        A configured OrchestratorConfig instance.
    """
    from aip.orchestration.retrieval_orchestrator import OrchestratorConfig

    return OrchestratorConfig(
        enable_fts=enable_fts,
        enable_vector=vector_enabled,
        enable_graph=enable_graph,
        enable_wiki=enable_wiki,
        enable_procedural=enable_procedural,
        max_hits=max_sources * 3,
    )


def apply_channel_weights(
    config: OrchestratorConfig,
    effective_config: dict,
    vector_enabled: bool,
) -> OrchestratorConfig:
    """Apply channel weights from TOML config to OrchestratorConfig.

    Channel weights are only applied when both semantic (vector) and
    lexical (FTS/corpus) channels are active — weights distort scores
    when only one retrieval type is present.

    Args:
        config: The OrchestratorConfig to update (mutated in place).
        effective_config: The TOML config dict (from ``aip.config.loader``).
        vector_enabled: Whether vector channel is active after coverage gating.

    Returns:
        The same OrchestratorConfig with channel_weights populated
        (or cleared) as appropriate.
    """
    if not effective_config:
        return config

    _cw = effective_config.get("retrieval", {}).get("channel_weights", {})
    if not _cw:
        return config

    has_semantic = vector_enabled
    has_lexical = config.enable_fts or config.enable_corpus

    if has_semantic and has_lexical:
        config.channel_weights = {
            k: float(v) for k, v in _cw.items() if isinstance(v, (int, float))
        }
    else:
        config.channel_weights = {}

    return config


def apply_channel_selector(
    query: str,
    config: OrchestratorConfig,
    auto_channel_selection: bool = True,
) -> OrchestratorConfig:
    """Apply adaptive channel selection based on query signals.

    Only enables channels (never disables).  Set
    ``auto_channel_selection=False`` for manual control.

    Args:
        query: The user's query string.
        config: The OrchestratorConfig to update.
        auto_channel_selection: Whether to auto-enable channels based
            on query signals.

    Returns:
        The same OrchestratorConfig, possibly with additional channels
        enabled by the selector.
    """
    if not auto_channel_selection:
        return config

    try:
        from aip.orchestration.channel_selector import ChannelSelector
        _channel_selector = ChannelSelector()
        config = _channel_selector.apply_to_config(query, config)
    except Exception as exc:
        logger.debug("Channel selector failed (non-fatal): %s", exc)

    return config


async def check_vector_coverage(
    corpus_turn_store: object | None,
    vector_available: bool,
) -> bool:
    """Check whether vector coverage is sufficient for hybrid retrieval.

    Returns ``True`` if vector search should remain enabled, ``False``
    if coverage is below the 10% minimum threshold.  Non-fatal: if the
    progress check fails, vector remains enabled (fail-open for
    availability).

    Args:
        corpus_turn_store: The CorpusTurnStore (may be None).
        vector_available: Whether vector store and embedding provider are present.

    Returns:
        Whether vector search should be enabled after coverage gating.
    """
    if not vector_available:
        return False

    if corpus_turn_store is None:
        return True  # no corpus to check — allow vector

    try:
        progress = await corpus_turn_store.get_embedding_progress()
        coverage = progress.get("percentage", 0.0) / 100.0
        min_coverage = 0.10  # 10% minimum for hybrid mode
        if coverage < min_coverage:
            logger.debug(
                "vector_disabled_low_coverage",
                coverage_percent=progress.get("percentage", 0.0),
                min_coverage_percent=min_coverage * 100,
            )
            return False
    except Exception as exc:
        logger.debug("embedding_progress_check_failed (non-fatal): %s", exc)

    return True
