"""Embedding provider factory — creates EmbeddingProvider from config.

Extracted from aip.adapter.api.app so that orchestration modules
(ask_pipeline, embed_providers, ingestion/pipeline) can import this
without creating an adapter → orchestration circular dependency.

Resolution order:
  1. [models.embedding] slot (if provider is openai_compatible) — primary
     path when a model has been selected via the UI.  Reads base_url, model,
     api_key from the slot config with env var overrides.
  2. [embedding] section — legacy backward-compatible config.
  3. Fallback: MockOllamaEmbeddingClient (deterministic fake for CI).

Returns an EmbeddingProvider instance, or None on failure.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aip.foundation.protocols import EmbeddingProvider


def create_embedding_provider(config: dict) -> "EmbeddingProvider | None":
    """Create an EmbeddingProvider from config.

    This is the canonical factory for creating embedding providers.
    Used by:
      - aip.adapter.api.app lifespan (API container wiring)
      - aip.orchestration.embed_providers (CLI/workflow embedding)
      - aip.orchestration.ask_pipeline (standalone ask pipeline)
      - aip.orchestration.ingestion.pipeline (ingestion embedding)
    """
    from aip.adapter.model_slot_resolver import ModelSlotResolver
    from aip.logging import get_logger

    log = get_logger(__name__)

    # Check the [models.embedding] slot first — this is the path used when
    # an embedding model is selected via the UI.
    models_cfg = config.get("models", {})
    embed_slot = models_cfg.get("embedding", {})
    if isinstance(embed_slot, dict) and embed_slot.get("provider"):
        # Use the slot resolver to get resolved config (includes env var overrides)
        resolver = ModelSlotResolver(config)
        try:
            resolved = resolver._resolve_slot_config("embedding")
            provider = resolved.get("provider", "")
            base_url = resolved.get("base_url", "https://api.openai.com")
            model = resolved.get("model", "")
            api_key = resolved.get("api_key")
            dimensions = resolved.get("dimensions")

            if provider == "openai_compatible" and model:
                from aip.adapter.embedding.openai_embed import OpenAICompatibleEmbeddingClient

                client = OpenAICompatibleEmbeddingClient(
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                    dimensions=dimensions,
                )
                log.info(
                    "embedding_provider_from_slot",
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    has_api_key=bool(api_key),
                )
                return client

            if provider == "ollama" and model:
                from aip.adapter.embedding.ollama_embed import OllamaEmbeddingClient

                return OllamaEmbeddingClient(
                    base_url=base_url,
                    model=model,
                    dimensions=dimensions or 768,
                )
        except Exception as exc:
            log.warning("embedding_slot_resolution_failed", error=str(exc))

    # Fallback: legacy [embedding] section
    embed_cfg = config.get("embedding", {})
    provider = embed_cfg.get("provider", "fake")

    if provider == "ollama":
        from aip.adapter.embedding.ollama_embed import OllamaEmbeddingClient

        return OllamaEmbeddingClient(
            base_url=embed_cfg.get("base_url", "http://localhost:11434"),
            model=embed_cfg.get("model", "nomic-embed-text"),
        )

    if provider == "openai_compatible":
        from aip.adapter.embedding.openai_embed import OpenAICompatibleEmbeddingClient

        base_url = embed_cfg.get("base_url", "https://api.openai.com")
        model = embed_cfg.get("model")
        api_key = (
            embed_cfg.get("api_key") or os.environ.get("AIP_EMBEDDING_API_KEY") or os.environ.get("AIP_OPENAI_API_KEY")
        )
        dimensions = embed_cfg.get("dimensions")

        if model:
            return OpenAICompatibleEmbeddingClient(
                base_url=base_url,
                model=model,
                api_key=api_key,
                dimensions=dimensions,
            )

    # Default: mock/fake
    from aip.adapter.embedding.ollama_embed import MockOllamaEmbeddingClient

    return MockOllamaEmbeddingClient()
