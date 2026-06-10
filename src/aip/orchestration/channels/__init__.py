"""Retriever channel modules — one file per channel, auto-discovered.

Each channel module exposes a ``register`` function that wires its retriever
callable into a ``RetrievalOrchestrator`` instance.  Adding a new channel
means adding a new file here — *not* editing ask_pipeline.py.

Channel modules are auto-discovered by the registry
(:mod:`aip.orchestration.channels.registry`).
"""

from aip.orchestration.channels.registry import (
    BUILTIN_CHANNELS,
    register_all_channels,
    register_custom_channel,
    clear_custom_channels,
)
from aip.orchestration.channels.types import ChannelFailure, ChannelResult, safe_retriever

__all__ = [
    "BUILTIN_CHANNELS",
    "ChannelFailure",
    "ChannelResult",
    "clear_custom_channels",
    "register_all_channels",
    "register_custom_channel",
    "safe_retriever",
]
