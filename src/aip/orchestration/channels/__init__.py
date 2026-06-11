"""Retriever channel modules — one file per channel, auto-discovered.

Each channel module exposes a ``register`` function that wires its retriever
callable into a ``RetrievalOrchestrator`` instance.  Adding a new channel
means adding a new file here — *not* editing ask_pipeline.py.

Channel modules are auto-discovered by the registry
(:mod:`aip.orchestration.channels.registry`).

Configuration helpers for the ask pipeline are in
:mod:`aip.orchestration.channels.retrieval_config` and trace/degradation
utilities are in :mod:`aip.orchestration.channels.retrieval_trace_utils`.
"""

from aip.orchestration.channels.registry import (
    BUILTIN_CHANNELS,
    register_all_channels,
    register_custom_channel,
    clear_custom_channels,
)
from aip.orchestration.channels.retrieval_config import (
    build_orchestrator_config,
    apply_channel_weights,
    apply_channel_selector,
    check_vector_coverage,
)
from aip.orchestration.channels.retrieval_trace_utils import (
    build_degradation_dict,
    build_retrieval_warnings,
    enrich_vector_trace_detail,
)
from aip.orchestration.channels.types import ChannelFailure, ChannelResult, safe_retriever

__all__ = [
    "BUILTIN_CHANNELS",
    "ChannelFailure",
    "ChannelResult",
    "build_degradation_dict",
    "build_orchestrator_config",
    "build_retrieval_warnings",
    "check_vector_coverage",
    "clear_custom_channels",
    "apply_channel_selector",
    "apply_channel_weights",
    "enrich_vector_trace_detail",
    "register_all_channels",
    "register_custom_channel",
    "safe_retriever",
]
