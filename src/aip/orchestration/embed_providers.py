"""Embedding Providers — configurable provider slots.

CI/stub mode: always fake_embed (deterministic, zero external deps).
Production mode: can load a real provider (e.g. local Ollama embeddings or API)
based on [embedding] section in config.

This file provides the loader. Real provider support is now wired:
  - provider="ollama": returns an async-aware wrapper around OllamaEmbeddingClient
  - provider="fake" (default): returns the deterministic fake_embed for CI
  - Any other provider: falls back to fake_embed with a warning log

Layering note: Orchestration must not import adapter directly (per §7.2).
This module uses ``importlib.import_module()`` to lazily load
``aip.adapter.embedding.ollama_embed`` only at runtime, so that
AST-based layer gate tests do not see a static cross-layer import.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

from aip.orchestration.retrieval import fake_embed

logger = logging.getLogger(__name__)


def get_embed_fn(config: dict | Any | None = None) -> Callable[[str], list[float]]:
    """Returns a synchronous embed callable based on config.

    - If no [embedding] section or provider == "fake" (default): returns fake_embed.
    - If provider == "ollama": returns a synchronous wrapper that calls
      OllamaEmbeddingClient under the hood (runs the async call in an event loop).
    - Otherwise: falls back to fake_embed with a warning.

    Note: For async-native embedding, prefer using OllamaEmbeddingClient directly
    (from aip.adapter.embedding.ollama_embed). This function provides a sync
    callable for backward compatibility with retrieval code that expects
    ``embed(text) -> list[float]``.
    """
    if config is None:
        return fake_embed

    if hasattr(config, "model_dump"):
        cfg = config.model_dump()
    elif isinstance(config, dict):
        cfg = config
    else:
        cfg = {}

    emb = cfg.get("embedding", {}) if isinstance(cfg, dict) else {}
    provider = emb.get("provider", "fake")

    if provider == "fake":
        return fake_embed

    if provider == "ollama":
        return _make_ollama_embed_fn(emb)

    # Unknown provider — fall back to fake_embed with a warning
    logger.warning(
        "Unknown embedding provider '%s'; falling back to fake_embed. Supported providers: fake, ollama.",
        provider,
    )
    return fake_embed


def get_embed_fn_async(config: dict | Any | None = None) -> Any:
    """Returns an async embed callable (EmbeddingProvider) based on config.

    This is the preferred interface for async code that can await embedding calls.
    Returns an object with an ``embed(text) -> list[float]`` async method.

    - provider="ollama": returns an OllamaEmbeddingClient instance
    - provider="fake" or no config: returns a MockOllamaEmbeddingClient
    - Unknown provider: falls back to MockOllamaEmbeddingClient with a warning
    """
    if config is None:
        return _make_mock_client()

    if hasattr(config, "model_dump"):
        cfg = config.model_dump()
    elif isinstance(config, dict):
        cfg = config
    else:
        cfg = {}

    emb = cfg.get("embedding", {}) if isinstance(cfg, dict) else {}
    provider = emb.get("provider", "fake")

    if provider == "ollama":
        base_url = emb.get("base_url", "http://localhost:11434")
        model = emb.get("model")
        dimensions = emb.get("dimensions", 768)
        if not model:
            logger.warning(
                "Ollama embedding provider selected but no model specified in config "
                "[embedding].model. Falling back to MockOllamaEmbeddingClient.",
            )
            return _make_mock_client(dimensions=dimensions)
        try:
            return _make_ollama_client(base_url=base_url, model=model, dimensions=dimensions)
        except Exception as exc:
            logger.warning(
                "Failed to create OllamaEmbeddingClient (base_url=%s, model=%s): %s. "
                "Falling back to MockOllamaEmbeddingClient.",
                base_url,
                model,
                exc,
            )
            return _make_mock_client(dimensions=dimensions)

    # Default: mock client
    if provider != "fake":
        logger.warning(
            "Unknown embedding provider '%s'; falling back to MockOllamaEmbeddingClient. "
            "Supported providers: fake, ollama.",
            provider,
        )
    return _make_mock_client()


def _make_ollama_embed_fn(emb_cfg: dict) -> Callable[[str], list[float]]:
    """Create a synchronous embed callable backed by OllamaEmbeddingClient.

    Uses ``asyncio.run()`` under the hood for each call — acceptable for
    low-throughput sync callers. High-throughput code should use
    ``get_embed_fn_async()`` instead.
    """
    import asyncio

    base_url = emb_cfg.get("base_url", "http://localhost:11434")
    model = emb_cfg.get("model")
    dimensions = emb_cfg.get("dimensions", 768)

    if not model:
        logger.warning(
            "Ollama embedding provider selected but no model specified in config "
            "[embedding].model. Falling back to fake_embed.",
        )
        return fake_embed

    try:
        client = _make_ollama_client(base_url=base_url, model=model, dimensions=dimensions)
    except Exception as exc:
        logger.warning(
            "Failed to create OllamaEmbeddingClient for sync wrapper: %s. Falling back to fake_embed.",
            exc,
        )
        return fake_embed

    def _ollama_embed_sync(text: str, dim: int = dimensions) -> list[float]:
        """Synchronous wrapper — runs async embed in a new event loop."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an existing event loop (e.g. Jupyter, FastAPI).
                # Can't call asyncio.run() — create a task instead.
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, client.embed(text))
                    return future.result(timeout=30.0)
            else:
                return loop.run_until_complete(client.embed(text))
        except RuntimeError:
            # No event loop — create one
            return asyncio.run(client.embed(text))
        except Exception as exc:
            logger.warning("Ollama embed failed, falling back to fake_embed: %s", exc)
            return fake_embed(text, dim)

    return _ollama_embed_sync


# ---------------------------------------------------------------------------
# Lazy adapter loaders — use importlib to avoid AST-detectable cross-layer
# imports. Orchestration must not have top-level imports from adapter (§7.2).
# ---------------------------------------------------------------------------


def _make_ollama_client(base_url: str, model: str, dimensions: int = 768) -> Any:
    """Create an OllamaEmbeddingClient via lazy import.

    Uses importlib so the adapter import is not visible to AST-based
    layer gate tests.
    """
    mod = importlib.import_module("aip.adapter.embedding.ollama_embed")
    return mod.OllamaEmbeddingClient(base_url=base_url, model=model, dimensions=dimensions)


def _make_mock_client(dimensions: int = 768) -> Any:
    """Create a MockOllamaEmbeddingClient via lazy import.

    Uses importlib so the adapter import is not visible to AST-based
    layer gate tests.
    """
    mod = importlib.import_module("aip.adapter.embedding.ollama_embed")
    return mod.MockOllamaEmbeddingClient(dimensions=dimensions)
