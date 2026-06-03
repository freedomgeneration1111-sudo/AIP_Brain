"""Vector store factory — creates the appropriate VectorStore implementation.

Configuration flag switches between "pgvector" and "sqlite_vss".
Adapter may import foundation but not orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.protocols import VectorStore
from aip.foundation.schemas import PgvectorConfig

logger = logging.getLogger(__name__)


async def create_vector_store(config: Any, embedding_provider: Any = None) -> VectorStore:
    """Create the appropriate VectorStore based on config.

    Reads [vector_backend] provider from config and returns:
    - PgvectorStore for "pgvector"
    - SqliteVssVectorStore for "sqlite_vss"
    - Falls back to sqlite_vss if pgvector is unavailable

    When ``embedding_provider`` is provided, it is passed to the
    SqliteVssVectorStore constructor so that the ``store()`` compat
    method can generate real embeddings instead of inserting zero vectors.

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
            logger.warning(f"pgvector unavailable ({e}), falling back to sqlite_vss")
            # Graceful degradation
            return await _create_sqlite_vss(vector_cfg, embedding_provider)

    return await _create_sqlite_vss(vector_cfg, embedding_provider)


async def _create_sqlite_vss(vector_cfg: dict, embedding_provider: Any = None) -> VectorStore:
    """Create SqliteVssVectorStore as default or fallback.

    If sqlite_vss is not available, returns an InMemoryVectorStore
    as a last-resort fallback (graceful degradation).

    When ``embedding_provider`` is provided, it is passed to the
    SqliteVssVectorStore constructor so that the ``store()`` compat
    method can generate real embeddings.
    """
    try:
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = vector_cfg.get("db_path", "db/vectors.db")
        dimensions = vector_cfg.get("dimensions", 768)
        store = SqliteVssVectorStore(
            db_path=db_path,
            dimensions=dimensions,
            embedding_provider=embedding_provider,
        )
        if store._vss_available:
            if embedding_provider is not None:
                logger.info("VectorStore: sqlite_vss backend initialized with EmbeddingProvider")
            else:
                logger.info(
                    "VectorStore: sqlite_vss backend initialized (no EmbeddingProvider — "
                    "store() will use metadata-only fallback)",
                )
            return store
        else:
            logger.warning(
                "sqlite_vss extension not available, using persistent sqlite metadata + "
                "brute-force search (embeddings stored in embedding_json column for persistence)"
            )
            return store  # still return for persistent storage of vectors/metadata
    except Exception as e:
        logger.warning(f"sqlite_vss store creation failed ({e}), falling back to in-memory store")

    # Last-resort in-memory fallback
    from aip.adapter.vector._in_memory import InMemoryVectorStore

    return InMemoryVectorStore()
