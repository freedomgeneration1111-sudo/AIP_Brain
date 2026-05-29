"""VectorStoreConnectionManager — production lifecycle and hardening for VectorStore.

Per : manages the VectorStore instance for the application lifetime.
Wraps the factory  with retry + graceful degradation.
Connection pooling is handled inside the concrete stores.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aip.foundation.protocols import VectorStore

logger = logging.getLogger(__name__)


class VectorStoreConnectionManager:
    """Long-lived manager for the active VectorStore.

    - Lazily initializes the store on first get_store() via the factory.
    - Implements exponential backoff retry (3 attempts: 1s, 2s, 4s) for transient
      pgvector connection failures before falling back to sqlite_vss.
    - Provides shutdown() to gracefully close the underlying pool.
    - health_check_all() reports the current store status.

    The returned VectorStore is the active backend (orchestration code never
    knows which concrete implementation is in use).
    """

    def __init__(self, config: Any) -> None:
        self._config = config
        self._store: VectorStore | None = None
        self._lock = asyncio.Lock()

    async def get_store(self) -> VectorStore:
        """Return the current active VectorStore (lazy + retry + fallback)."""
        if self._store is not None:
            return self._store

        async with self._lock:
            if self._store is not None:
                return self._store

            # Retry logic with exp backoff (only for connection failures)
            delays = [1.0, 2.0, 4.0]
            last_exc: Exception | None = None

            for attempt, delay in enumerate(delays, 1):
                try:
                    from aip.adapter.vector.factory import create_vector_store

                    store = await create_vector_store(self._config)
                    self._store = store
                    logger.info("VectorStoreConnectionManager: store initialized")
                    return self._store
                except Exception as e:
                    last_exc = e
                    logger.warning(
                        f"VectorStoreConnectionManager: attempt {attempt} failed ({e}), retrying in {delay}s",
                    )
                    await asyncio.sleep(delay)

            # Final fallback after 3 attempts
            logger.warning("VectorStoreConnectionManager: all retries exhausted, falling back to sqlite_vss")
            try:
                from aip.adapter.vector.factory import create_vector_store

                # Force sqlite_vss fallback by mutating a copy of config if needed
                # (the factory already does graceful degradation on pgvector failure)
                fallback_config = dict(self._config) if isinstance(self._config, dict) else self._config
                if isinstance(fallback_config, dict):
                    vb = fallback_config.setdefault("vector_backend", {})
                    vb["provider"] = "sqlite_vss"

                store = await create_vector_store(fallback_config)
                self._store = store

                # Record degradation event (best-effort; adapter layer)
                logger.warning(
                    "VectorStoreConnectionManager: degradation to sqlite_vss active "
                    "(intervention_type=backend_fallback)",
                )
                return self._store
            except Exception as e:
                logger.error(f"VectorStoreConnectionManager: fallback also failed: {e}")
                raise

    async def health_check_all(self) -> dict:
        """Return health status of the managed store."""
        try:
            store = await self.get_store()
            if hasattr(store, "health_check"):
                health = await store.health_check()
                return {
                    "status": "healthy" if health.get("connected") else "degraded",
                    "backend": health.get("backend_name", "unknown"),
                    **health,
                }
            return {"status": "unknown", "backend": "unknown"}
        except Exception as e:
            return {
                "status": "unhealthy",
                "backend": "none",
                "error": str(e),
            }

    async def shutdown(self) -> None:
        """Gracefully close the underlying connection pool."""
        if self._store is not None and hasattr(self._store, "close"):
            try:
                await self._store.close()
                logger.info("VectorStoreConnectionManager: store shutdown complete")
            except Exception as e:
                logger.warning(f"VectorStoreConnectionManager: shutdown error (ignored): {e}")
            finally:
                self._store = None
