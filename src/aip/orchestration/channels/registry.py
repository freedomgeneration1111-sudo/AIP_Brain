"""Channel registry — auto-discovers and registers all retriever channels.

The registry knows the built-in channels (fts, vector, corpus, graph, wiki,
procedural) and also supports externally-registered custom channels.

**Dogfood gate**: A new retrieval channel can be added by:

1. Creating a new module in ``aip.orchestration.channels/`` with a
   ``register(orchestrator, stores, config)`` function.
2. Adding the module name to ``BUILTIN_CHANNELS`` in this file.
3. No edits to ``ask_pipeline.py`` required.

Alternatively, custom channels can be registered directly via
``register_custom_channel()`` before the orchestrator is created.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from aip.orchestration.channels.types import ChannelFailure

logger = logging.getLogger(__name__)

# Type alias for the register function each channel module must export.
ChannelRegisterFn = Callable[[Any, Any, dict | None], list[ChannelFailure]]

# Built-in channel modules in import order.
# Each module must have a ``register(orchestrator, stores, config)`` function.
BUILTIN_CHANNELS: list[str] = [
    "aip.orchestration.channels.lexical_channel",
    "aip.orchestration.channels.vector_channel",
    "aip.orchestration.channels.corpus_channel",
    "aip.orchestration.channels.graph_channel",
    "aip.orchestration.channels.wiki_channel",
    "aip.orchestration.channels.procedural_channel",
]

# Custom channels registered at runtime (before the orchestrator is built).
_custom_channels: list[tuple[str, ChannelRegisterFn]] = []


def register_custom_channel(name: str, register_fn: ChannelRegisterFn) -> None:
    """Register a custom channel that will be wired on next orchestrator creation.

    This is the public API for adding channels without editing any pipeline
    file.  Call this before ``_search_sources_with_trace()`` runs.

    Args:
        name: Human-readable name for logging (e.g. ``"my_custom"``).
        register_fn: Callable with signature ``(orchestrator, stores, config) -> list[ChannelFailure]``.
    """
    _custom_channels.append((name, register_fn))
    logger.info("Custom channel '%s' registered for next orchestrator creation", name)


def clear_custom_channels() -> None:
    """Remove all custom channel registrations.

    Primarily useful for test isolation.
    """
    _custom_channels.clear()


def register_all_channels(
    orchestrator: Any,
    stores: Any,
    config: dict | None = None,
) -> list[ChannelFailure]:
    """Register all built-in and custom channels on an orchestrator.

    Returns a list of all ChannelFailure objects produced during
    registration.  This gives the pipeline visibility into which
    channels were skipped and why — without scraping logs.

    Args:
        orchestrator: RetrievalOrchestrator instance to register on.
        stores: AskStores container.
        config: Optional TOML config dict.

    Returns:
        List of ChannelFailure from all channel registration attempts.
    """
    all_failures: list[ChannelFailure] = []

    # Register built-in channels
    for module_name in BUILTIN_CHANNELS:
        try:
            import importlib

            mod = importlib.import_module(module_name)
            failures = mod.register(orchestrator, stores, config)
            if failures:
                all_failures.extend(failures)
        except Exception as exc:
            channel_name = module_name.rsplit(".", 1)[-1].replace("_channel", "")
            failure = ChannelFailure(
                channel=channel_name,
                error_type="initialization",
                message=f"Failed to import/register channel module {module_name}: {exc}",
                exception_type=type(exc).__qualname__,
            )
            all_failures.append(failure)
            logger.warning("Channel registration failed for %s: %s", module_name, exc)

    # Register custom channels
    for name, register_fn in _custom_channels:
        try:
            failures = register_fn(orchestrator, stores, config)
            if failures:
                all_failures.extend(failures)
        except Exception as exc:
            failure = ChannelFailure(
                channel=name,
                error_type="initialization",
                message=f"Custom channel '{name}' registration failed: {exc}",
                exception_type=type(exc).__qualname__,
            )
            all_failures.append(failure)
            logger.warning("Custom channel '%s' registration failed: %s", name, exc)

    if all_failures:
        logger.info(
            "Channel registration completed with %d failure(s): %s",
            len(all_failures),
            ", ".join(f.channel for f in all_failures),
        )

    return all_failures
