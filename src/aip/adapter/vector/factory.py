"""Vector store factory — creates the appropriate VectorStore implementation.

Configuration flag switches between "pgvector" and "sqlite_vss".
The ``runtime_mode`` key in vector_cfg controls brute-force fallback
behavior (development / production / strict).
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.protocols import VectorStore
from aip.foundation.schemas import PgvectorConfig

logger = logging.getLogger(__name__)


def _resolve_runtime_mode(vector_cfg: dict) -> Any:
    """Resolve RuntimeMode from config, defaulting to DEVELOPMENT."""
    from aip.adapter.vector.sqlite_vss_store import RuntimeMode

    mode_str = vector_cfg.get("runtime_mode", "development").lower()
    try:
        return RuntimeMode(mode_str)
    except ValueError:
        logger.warning("Unknown runtime_mode '%s', defaulting to DEVELOPMENT", mode_str)
        return RuntimeMode.DEVELOPMENT


async def create_vector_store(config: Any, embedding_provider: Any = None) -> VectorStore:
    """Create the appropriate VectorStore based on config.

    Reads [vector_backend] provider from config and returns:
    - PgvectorStore for "pgvector"
    - SqliteVssVectorStore for "sqlite_vss"
    - Falls back to sqlite_vss if pgvector is unavailable

    The returned object implements the VectorStore Protocol.
    Orchestration code never knows which backend is active.
    """
    cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
    vector_cfg = cfg.get("vector_backend", {})
    provider = vector_cfg.get("provider", "sqlite_vss")

    if provider == "pgvector":
        try:
            from aip.adapter.vector.pgvector_store import PgvectorStore

            pgconfig = PgvectorConfig(
                connection_string=vector_cfg.get("connection_string", ""),
                pool_min_size=vector_cfg.get("pgvector", {}).get("pool_min_size", 2),
                pool_max_size=vector_cfg.get("pgvector", {}).get("pool_max_size", 10),
                pool_timeout_seconds=vector_cfg.get("pgvector", {}).get("pool_timeout_seconds", 30.0),
                statement_timeout_ms=vector_cfg.get("pgvector", {}).get("statement_timeout_ms", 5000),
                hnsw_m=vector_cfg.get("pgvector", {}).get("hnsw_m", 16),
                hnsw_ef_construction=vector_cfg.get("pgvector", {}).get("hnsw_ef_construction", 64),
                hnsw_ef_search=vector_cfg.get("pgvector", {}).get("hnsw_ef_search", 40),
            )
            store = PgvectorStore(pgconfig)
            await store.initialize()
            logger.info("VectorStore: pgvector backend initialized")
            return store
        except Exception as e:
            logger.warning("pgvector unavailable (%s), falling back to sqlite_vss", e)
            return await _create_sqlite_vss(vector_cfg, embedding_provider)

    return await _create_sqlite_vss(vector_cfg, embedding_provider)


async def _create_sqlite_vss(vector_cfg: dict, embedding_provider: Any = None) -> VectorStore:
    """Create SqliteVssVectorStore as default or fallback.

    Calls ``initialize()`` on the store before returning it so that
    VSS detection and table creation are complete.  Falls back to
    InMemoryVectorStore as a last resort.
    """
    try:
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = vector_cfg.get("db_path", "db/vectors.db")
        dimensions = vector_cfg.get("dimensions", 768)
        runtime_mode = _resolve_runtime_mode(vector_cfg)

        store = SqliteVssVectorStore(
            db_path=db_path,
            dimensions=dimensions,
            embedding_provider=embedding_provider,
            runtime_mode=runtime_mode,
        )
        await store.initialize()

        if store._vss_available:
            if embedding_provider is not None:
                logger.info("VectorStore: sqlite_vss backend initialized with EmbeddingProvider")
            else:
                logger.info(
                    "VectorStore: sqlite_vss backend initialized (no EmbeddingProvider — "
                    "store() will use metadata-only fallback)",
                )
        else:
            logger.warning(
                "sqlite_vss extension not available, using persistent sqlite metadata + "
                "brute-force search (mode=%s)", runtime_mode.value,
            )
        return store
    except Exception as e:
        logger.warning("sqlite_vss store creation failed (%s), falling back to in-memory store", e)

    # Last-resort in-memory fallback
    from aip.adapter.vector._in_memory import InMemoryVectorStore

    return InMemoryVectorStore()
