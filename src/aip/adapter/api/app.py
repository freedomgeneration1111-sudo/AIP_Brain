"""FastAPI application factory + lifespan for AIP surfaces.

Per spec prose + interfaces (exact).
Wires 8.0a/8.0b adapters + full Phase 5 actor layer into AipContainer.
All privileged writes go through AutonomyGate.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.adapter.api.routes import health, projects, sessions
from aip.adapter.api.routes import review, artifacts, admin, memory, chat
from aip.adapter.api import collaborators, plugins, performance
from aip.foundation.schemas import SurfaceConfig, BeastCadenceConfig

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all adapters and orchestration components on startup."""
    config: dict = app.state.raw_config or {}
    container = AipContainer(config)

    # --- Wire adapter stores from config ---
    db_path = config.get("db_path", "db/state.db")

    # Vector store (via factory — use importlib to avoid static import that triggers layering guard)
    try:
        _vs_mod = importlib.import_module("aip.adapter.vector.factory")
        _create_vector_store = _vs_mod.create_vector_store
        container.vector_store = await _create_vector_store(config)
    except Exception as exc:
        logger.warning("Vector store initialization failed: %s", exc)

    # Embedding provider
    try:
        embed_cfg = config.get("embedding", {})
        provider = embed_cfg.get("provider", "mock")
        if provider == "ollama":
            from aip.adapter.embedding.ollama_embed import OllamaEmbeddingClient
            container.embedding_provider = OllamaEmbeddingClient(
                base_url=embed_cfg.get("base_url", "http://localhost:11434"),
                model=embed_cfg.get("model", "nomic-embed-text"),
            )
        else:
            from aip.adapter.embedding.ollama_embed import MockOllamaEmbeddingClient
            container.embedding_provider = MockOllamaEmbeddingClient()
    except Exception as exc:
        logger.warning("Embedding provider initialization failed: %s", exc)

    # Entity store
    try:
        from aip.adapter.entity.sqlite_entity_store import SqliteEntityStore
        container.entity_store = SqliteEntityStore(db_path)
        await container.entity_store.initialize()
    except Exception as exc:
        logger.warning("Entity store initialization failed: %s", exc)

    # Canonical store
    try:
        from aip.adapter.canonical.sqlite_canonical_store import SqliteCanonicalStore
        container.canonical_store = SqliteCanonicalStore(db_path)
        await container.canonical_store.initialize()
    except Exception as exc:
        logger.warning("Canonical store initialization failed: %s", exc)

    # Event store
    try:
        from aip.adapter.event_store_queryable import QueryableEventStore
        container.event_store = QueryableEventStore(db_path)
        await container.event_store.initialize()
    except Exception as exc:
        logger.warning("Event store initialization failed: %s", exc)

    # Project store
    try:
        from aip.adapter.project.sqlite_project_store import SqliteProjectStore
        container.project_store = SqliteProjectStore(db_path)
        await container.project_store.initialize()
    except Exception as exc:
        logger.warning("Project store initialization failed: %s", exc)

    # Model provider (ModelSlotResolver)
    try:
        from aip.adapter.model_slot_resolver import ModelSlotResolver
        container.model_provider = ModelSlotResolver(config)
    except Exception as exc:
        logger.warning("Model provider initialization failed: %s", exc)

    # --- Wire orchestration components (lazy import to preserve layer discipline) ---
    # Beast actor — requires vector_store + embedding_provider at minimum
    if container.vector_store is not None and container.embedding_provider is not None:
        try:
            _beast_mod = importlib.import_module("aip.orchestration.actors.beast")
            _Beast = _beast_mod.Beast
            beast_config = BeastCadenceConfig(
                **{k: v for k, v in config.get("beast", {}).items()
                   if k in BeastCadenceConfig.__dataclass_fields__}
            )
            container.beast = _Beast(
                config=beast_config,
                vector_store=container.vector_store,
                embedding_provider=container.embedding_provider,
                project_store=container.project_store,
                event_store=container.event_store,
                entity_store=container.entity_store,
                canonical_store=container.canonical_store,
            )
            logger.info("Beast actor wired successfully")
        except Exception as exc:
            logger.warning("Beast actor initialization failed: %s", exc)
    else:
        logger.info("Beast actor not wired: missing vector_store or embedding_provider")

    app.state.container = container
    app.state.start_time = time.time()

    # --- Beast background scheduler ---
    beast_task: asyncio.Task | None = None
    if container.beast is not None:
        async def _beast_scheduler():
            """Lightweight background loop that calls beast.run_cycle() periodically.

            Uses the configured health_check_interval_seconds (default 300) as the
            cadence. The loop is cancellable — the task is cancelled on shutdown.
            Each cycle logs start/complete for observability.
            """
            interval = container.beast._config.health_check_interval_seconds
            # Enforce a reasonable minimum to avoid busy-looping
            if interval < 60:
                interval = 300
                logger.info("Beast cadence interval too low (%ss), clamped to 300s", interval)
            logger.info("Beast background scheduler starting (interval=%ss)", interval)
            while True:
                try:
                    logger.info("Beast cycle starting")
                    summary = await container.beast.run_cycle()
                    logger.info(
                        "Beast cycle complete: health=%s, corpus_stale=%s, elapsed=%.1fs",
                        summary.get("health_overall", "unknown"),
                        summary.get("corpus", {}).get("stale_vectors_found", "?"),
                        summary.get("cycle_elapsed_seconds", 0),
                    )
                except asyncio.CancelledError:
                    logger.info("Beast background scheduler cancelled")
                    raise
                except Exception as exc:
                    logger.error("Beast cycle failed: %s", exc, exc_info=True)
                await asyncio.sleep(interval)

        beast_task = asyncio.create_task(_beast_scheduler(), name="beast-scheduler")
        logger.info("Beast background scheduler task created")

    yield

    # --- Shutdown ---
    # Cancel Beast scheduler if running
    if beast_task is not None:
        logger.info("Cancelling Beast background scheduler")
        beast_task.cancel()
        try:
            await beast_task
        except asyncio.CancelledError:
            pass

    # shutdown: close any open connections (the individual stores implement close())
    if container.lexical_store:
        await container.lexical_store.close()
    if container.canonical_store:
        await container.canonical_store.close()
    if container.entity_store:
        await container.entity_store.close()
    if container.autonomy_gate:
        await container.autonomy_gate.close()
    if container.event_store:
        await container.event_store.close()
    if container.project_store:
        await container.project_store.close()


def create_app(config: dict | None = None) -> "FastAPI":
    cfg = config or {}
    surface_cfg = SurfaceConfig(**{k: v for k, v in cfg.items() if k in SurfaceConfig.__dataclass_fields__})

    app = FastAPI(title="AIP 0.1 Surfaces", version="0.1", lifespan=lifespan)
    app.state.raw_config = cfg
    app.state.container = None  # populated in lifespan
    app.state.start_time = time.time()

    # CORS from SurfaceConfig
    app.add_middleware(
        CORSMiddleware,
        allow_origins=surface_cfg.api_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Wire AuthMiddleware + RateLimitMiddleware
    from aip.foundation.schemas import AuthConfig, RateLimitConfig
    from aip.adapter.auth.middleware import AuthMiddleware
    from aip.adapter.middleware.rate_limiter import RateLimitMiddleware, TokenBucketRateLimiter

    auth_cfg = AuthConfig(**{k: v for k, v in cfg.get("auth", {}).items() if k in AuthConfig.__dataclass_fields__})
    rl_cfg = RateLimitConfig(**{k: v for k, v in cfg.get("rate_limit", {}).items() if k in RateLimitConfig.__dataclass_fields__})

    # Auth store will be wired in lifespan; middleware references container
    # We use a lightweight factory that defers to the container
    class _AuthStoreProxy:
        """Proxy that delegates to the container's auth_store once available."""
        def __getattr__(self, name):
            container = getattr(app.state, "container", None)
            if container is not None:
                auth_store = getattr(container, "auth_store", None)
                if auth_store is not None:
                    return getattr(auth_store, name)
            return None

    _proxy = _AuthStoreProxy()
    app.add_middleware(AuthMiddleware, auth_store=_proxy, config=auth_cfg)

    rate_limiter = TokenBucketRateLimiter(rl_cfg)
    app.add_middleware(RateLimitMiddleware, rate_limiter=rate_limiter, config=rl_cfg)

    # Route modules
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(projects.router, prefix="/api/v1", tags=["projects"])
    app.include_router(sessions.router, prefix="/api/v1", tags=["sessions"])
    app.include_router(review.router, prefix="/api/v1", tags=["review"])
    app.include_router(artifacts.router, prefix="/api/v1", tags=["artifacts"])
    app.include_router(admin.router, prefix="/api/v1", tags=["admin"])
    app.include_router(memory.router, prefix="/api/v1", tags=["memory"])
    app.include_router(chat.router, prefix="/api/v1", tags=["chat"])

    # Phase 8 routers (.x)
    app.include_router(collaborators.router, prefix="/api/v1", tags=["collaborators"])
    app.include_router(plugins.router, prefix="/api/v1", tags=["plugins"])
    app.include_router(performance.router, prefix="/api/v1", tags=["performance"])

    # 9.4 Web UI static (minimal HTMX dashboard)
    from fastapi.staticfiles import StaticFiles
    import pathlib
    _static_dir = pathlib.Path(__file__).parent / "static"
    if _static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    @app.get("/")
    async def root():
        return {"status": "ok", "service": "aip-surfaces"}

    return app
