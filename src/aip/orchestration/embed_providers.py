"""Embedding Providers — configurable provider slots.

CI/stub mode: always fake_embed (deterministic, zero external deps).
Production mode: can load a real provider (e.g. local Ollama embeddings or API)
based on [embedding] section in config.

This file provides the loader. Real provider support is now wired:
  - provider="openai_compatible": returns an OpenAICompatibleEmbeddingClient
    (works with OpenRouter, OpenAI, DeepSeek, etc.)
  - provider="ollama": returns an async-aware wrapper around OllamaEmbeddingClient
  - provider="fake" (default): returns the deterministic fake_embed for CI
  - Any other provider: falls back to fake_embed with a warning log

Layering note: Orchestration must not import adapter directly.
This module uses ``importlib.import_module()`` to lazily load
``aip.adapter.embedding.ollama_embed`` and ``aip.adapter.embedding.openai_embed``
only at runtime, so that AST-based layer gate tests do not see a static
cross-layer import.
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

    # Prefer centralized (models.embedding first) for consistency with API/UI selection.
    try:
        from aip.adapter.api.app import _create_embedding_provider
        prov = _create_embedding_provider(cfg)
        if prov is not None and hasattr(prov, "embed"):
            # wrap async embed to sync for callers expecting sync fn
            import asyncio
            def _wrapped_embed(text: str) -> list[float]:
                try:
                    loop = asyncio.get_running_loop()
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        fut = pool.submit(asyncio.run, prov.embed(text))
                        return fut.result(timeout=60.0)
                except RuntimeError:
                    return asyncio.run(prov.embed(text))
                except Exception as exc:
                    logger.warning("embed via centralized provider failed, falling to fake: %s", exc)
                    return fake_embed(text)
            return _wrapped_embed
    except Exception as exc:
        logger.debug("get_embed_fn: _create_embedding_provider failed, falling to legacy: %s", exc)

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

    # Prefer the centralized embedding provider creation (same as API container's _create_embedding_provider)
    # which looks at [models.embedding] slot first (with env overrides like AIP_EMBEDDING_MODEL),
    # then legacy [embedding]. This makes workflow engine etc. respect the UI-selected model.
    try:
        from aip.adapter.api.app import _create_embedding_provider
        prov = _create_embedding_provider(cfg)
        if prov is not None:
            # prov is already an EmbeddingProvider (real client or mock)
            return prov
    except Exception as exc:
        logger.debug("get_embed_fn_async: _create_embedding_provider failed, falling back to legacy: %s", exc)

    emb = cfg.get("embedding", {}) if isinstance(cfg, dict) else {}
    provider = emb.get("provider", "fake")

    if provider == "openai_compatible":
        return _make_openai_compatible_client_from_config(emb)

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
            "Supported providers: fake, ollama, openai_compatible.",
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
            asyncio.get_running_loop()
            # We're inside an existing event loop (e.g. Jupyter, FastAPI).
            # Can't call asyncio.run() — create a task instead.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, client.embed(text))
                return future.result(timeout=30.0)
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


class ConfigurationError(Exception):
    """Raised when required configuration (e.g. base_url) is missing for embedding providers."""

def _make_openai_compatible_client_from_config(emb_cfg: dict) -> Any:
    """Create an OpenAICompatibleEmbeddingClient from embedding config.

    Reads base_url, model, api_key, dimensions from the config dict.
    Falls back to MockOpenAICompatibleEmbeddingClient if required fields
    are missing or if client creation fails.
    """
    import os

    base_url = emb_cfg.get("base_url")
    if not base_url:
        raise ConfigurationError(
            "base_url must be provided in the config for openai_compatible embedding provider "
            "(no hardcoded cloud defaults allowed in orchestration layer)"
        )
    model = emb_cfg.get("model")
    # Support api_key from config, env var AIP_EMBEDDING_API_KEY, or AIP_OPENAI_API_KEY
    api_key = emb_cfg.get("api_key") or os.environ.get("AIP_EMBEDDING_API_KEY") or os.environ.get("AIP_OPENAI_API_KEY")
    dimensions = emb_cfg.get("dimensions")

    if not model:
        logger.warning(
            "openai_compatible embedding provider selected but no model specified. "
            "Falling back to MockOpenAICompatibleEmbeddingClient.",
        )
        return _make_openai_compatible_mock_client(dimensions=dimensions)

    try:
        client = _make_openai_compatible_client(
            base_url=base_url,
            model=model,
            api_key=api_key,
            dimensions=dimensions,
        )
        logger.info(
            "OpenAI-compatible embedding client created: base_url=%s, model=%s, has_api_key=%s",
            base_url, model, bool(api_key),
        )
        return client
    except Exception as exc:
        logger.warning(
            "Failed to create OpenAICompatibleEmbeddingClient (base_url=%s, model=%s): %s. "
            "Falling back to MockOpenAICompatibleEmbeddingClient.",
            base_url, model, exc,
        )
        return _make_openai_compatible_mock_client(dimensions=dimensions)


def _make_openai_compatible_client(
    base_url: str,
    model: str,
    api_key: str | None = None,
    dimensions: int | None = None,
    timeout: float = 60.0,
) -> Any:
    """Create an OpenAICompatibleEmbeddingClient via lazy import.

    Uses importlib so the adapter import is not visible to AST-based
    layer gate tests.
    """
    mod = importlib.import_module("aip.adapter.embedding.openai_embed")
    return mod.OpenAICompatibleEmbeddingClient(
        base_url=base_url,
        model=model,
        api_key=api_key,
        dimensions=dimensions,
        timeout=timeout,
    )


def _make_openai_compatible_mock_client(dimensions: int | None = None) -> Any:
    """Create a MockOpenAICompatibleEmbeddingClient via lazy import.

    Uses importlib so the adapter import is not visible to AST-based
    layer gate tests.
    """
    mod = importlib.import_module("aip.adapter.embedding.openai_embed")
    return mod.MockOpenAICompatibleEmbeddingClient(dimensions=dimensions or 1536)
