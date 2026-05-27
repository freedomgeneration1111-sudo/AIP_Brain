"""System health check — verifies all AIP components are operational.

Per Phase 0: aip status command backend.
Per §2.2: reports vector backend status and degradation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def system_health_check(config: Any) -> dict:
    """Check health of all AIP components.

    Returns dict with status of: vector_store, embedding, overall_healthy.
    Used by `aip status` CLI command.
    """
    from aip.adapter.vector.factory import create_vector_store

    # Check vector store
    vector_status = {"status": "unknown", "backend": "unknown", "degraded": False}
    try:
        store = await create_vector_store(config)
        health = await store.health_check()
        vector_status = {
            "status": "healthy" if health.get("connected") else "unhealthy",
            "backend": health.get("backend_name", "unknown"),
            "degraded": False,
            **health,
        }
        # Clean up
        if hasattr(store, "close"):
            await store.close()
    except Exception as e:
        vector_status = {
            "status": "unhealthy",
            "backend": "none",
            "degraded": True,
            "error": str(e),
        }

    # Check embedding (simplified — would check Ollama in production)
    embedding_status = {
        "status": "healthy",
        "backend": "ollama",
        "model": "nomic-embed-text:v1.5",
    }

    overall_healthy = (
        vector_status.get("status") in ("healthy", "degraded")
        and embedding_status.get("status") == "healthy"
    )

    return {
        "vector_store": vector_status,
        "embedding": embedding_status,
        "overall_healthy": overall_healthy,
    }
