"""System health check — verifies all AIP components are operational.

Reports vector backend status and embedding provider status honestly.
Uses VectorBackendStatus enum for explicit status reporting.
"""

from __future__ import annotations

import time
from typing import Any

from aip.foundation.schemas.vector import VectorBackendStatus
from aip.logging import get_logger

logger = get_logger(__name__)


async def system_health_check(config: Any) -> dict:
    """Check health of all AIP components.

    Returns dict with status of: vector_store, embedding, model_slots,
    uptime_seconds, overall_healthy.
    Used by `aip status` CLI command.
    """
    from aip.adapter.vector.factory import create_vector_store

    # Determine vector_backend from config
    vector_backend = "sqlite_vss"
    if hasattr(config, "get"):
        vector_backend = config.get("vector_backend", "sqlite_vss")
    elif hasattr(config, "vector_backend"):
        vector_backend = config.vector_backend

    # Check vector store
    vector_status = {"status": "unknown", "backend": vector_backend, "degraded": False}
    try:
        store = await create_vector_store(config)
        health = await store.health_check()
        # Use the explicit backend_status from health_check
        backend_status_str = health.get("backend_status", "unknown")
        try:
            backend_status = VectorBackendStatus(backend_status_str)
        except ValueError:
            backend_status = VectorBackendStatus.FAILED
        vector_status = {
            "status": "healthy" if backend_status == VectorBackendStatus.AVAILABLE else "degraded",
            "backend": health.get("backend_name", vector_backend),
            "backend_status": backend_status.value,
            "degraded": backend_status.is_degraded,
            "vss_available": health.get("vss_available", False),
            "degradation": health.get("degradation", {}),
            "human_message": backend_status.human_message(),
            **health,
        }
        # Clean up
        if hasattr(store, "close"):
            await store.close()
    except Exception as e:
        vector_status = {
            "status": "unhealthy",
            "backend": vector_backend,
            "backend_status": VectorBackendStatus.FAILED.value,
            "degraded": True,
            "error": str(e),
            "human_message": VectorBackendStatus.FAILED.human_message(),
        }

    # Check embedding — detect actual provider from config
    embedding_status = {"status": "unknown", "backend": "unknown", "degraded": True}
    try:
        from aip.adapter.embedding.factory import create_embedding_provider

        provider = create_embedding_provider(config)
        if provider is None:
            embedding_status = {
                "status": "not_configured",
                "backend": "none",
                "degraded": True,
            }
        else:
            class_name = type(provider).__name__
            if "Mock" in class_name or "Fake" in class_name:
                provider_type = "mock"
                embedding_status = {
                    "status": "not_configured",
                    "backend": provider_type,
                    "degraded": True,
                }
            else:
                _PROVIDER_TYPE_MAP = {
                    "OllamaEmbeddingClient": "ollama",
                    "OpenAICompatibleEmbeddingClient": "openai_compatible",
                }
                provider_type = _PROVIDER_TYPE_MAP.get(class_name, class_name.replace("EmbeddingClient", "").lower())
                model_name = getattr(provider, "model", None) or getattr(provider, "_model", None) or "unknown"
                embedding_status = {
                    "status": "healthy",
                    "backend": provider_type,
                    "model": model_name,
                    "degraded": False,
                }
            # Clean up real providers that hold httpx clients
            if hasattr(provider, "close"):
                try:
                    await provider.close()
                except Exception:
                    pass
    except Exception as e:
        embedding_status = {
            "status": "failed",
            "backend": "unknown",
            "degraded": True,
            "error": str(e),
        }

    # Model slots from resolver
    model_slots = {}
    try:
        from aip.adapter.model_slot_resolver import ModelSlotResolver

        resolver = ModelSlotResolver(config)
        for slot_name in resolver.list_slots():
            try:
                model_slots[slot_name] = resolver.resolve(slot_name)
            except Exception as exc:
                logger.debug("Model slot resolve failed for %s: %s", slot_name, exc)
    except Exception as exc:
        logger.warning("health_model_resolver_unavailable", error=str(exc))
        model_slots = {"error": "resolver not available"}

    # Compute uptime from app start time (if available)
    uptime_seconds = 0
    try:
        # For standalone CLI checks, just report 0
        if hasattr(config, "_app_start_time"):
            uptime_seconds = int(time.time() - config._app_start_time)
    except Exception as exc:
        logger.debug("Uptime calculation failed: %s", exc)

    overall_healthy = (
        vector_status.get("status") in ("healthy", "degraded")
        and embedding_status.get("status") == "healthy"
        and not embedding_status.get("degraded", False)
    )

    return {
        "vector_store": vector_status,
        "embedding": embedding_status,
        "model_slots": model_slots,
        "uptime_seconds": uptime_seconds,
        "overall_healthy": overall_healthy,
    }
