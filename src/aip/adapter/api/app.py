"""FastAPI application factory + lifespan for AIP surfaces.

Per spec prose + interfaces (exact).
Wires adapters and actor layer into AipContainer.
All privileged writes go through AutonomyGate.

Startup Classification
----------------------
Components are classified as REQUIRED or OPTIONAL:

REQUIRED (startup fails if these cannot initialize):
  - Entity store: artifact metadata, collaborator data
  - Canonical store: canonical artifact storage
  - Event store: audit trail, trace events
  - Autonomy gate: authorization enforcement (core security)
  - Artifact store: artifact versioned storage

OPTIONAL (startup logs warning, service degrades gracefully):
  - Lexical store: FTS5 full-text search (degrades to no text search)
  - Vector store: semantic search (degrades to keyword-only)
  - Embedding provider: vector embedding (degrades to fake_embed)
  - Project store: project management (degrades to empty projects)
  - Budget store: token budget tracking (degrades to unlimited)
  - Vigil store: canonical health monitoring (degrades to no monitoring)
  - Model provider: LLM dispatch (degrades to stub responses)
  - Knowledge store: compiled knowledge with embeddings (degrades to no knowledge compilation)
  - Beast actor: background health + corpus maintenance (degrades to no background tasks)
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aip.adapter.api import collaborators, performance, plugins
from aip.adapter.api.dependencies import AipContainer
from aip.adapter.api.routes import admin, artifacts, chat, health, memory, projects, review, sessions
from aip.config import validate_config
from aip.foundation.schemas import BeastCadenceConfig, SurfaceConfig

logger = logging.getLogger(__name__)


class StartupError(Exception):
    """Raised when a required component fails to initialize on startup."""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all adapters and orchestration components on startup.

    Required components cause startup to fail fast with a clear error.
    Optional components log warnings and degrade gracefully.
    """
    config: dict = app.state.raw_config or {}
    container = AipContainer(config)

    # --- Wire adapter stores from config ---
    db_path = config.get("db_path", "db/state.db")

    # =====================================================================
    # REQUIRED COMPONENTS — startup fails if these cannot initialize
    # =====================================================================

    # Entity store — artifact metadata, collaborator data
    try:
        from aip.adapter.entity.sqlite_entity_store import SqliteEntityStore

        container.entity_store = SqliteEntityStore(db_path)
        await container.entity_store.initialize()
        logger.info("Entity store initialized (required)")
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Entity store could not initialize. "
            f"Reason: {exc}. The application cannot start without entity storage.",
        ) from exc

    # Canonical store — canonical artifact storage
    try:
        from aip.adapter.canonical.sqlite_canonical_store import SqliteCanonicalStore

        container.canonical_store = SqliteCanonicalStore(db_path)
        await container.canonical_store.initialize()
        logger.info("Canonical store initialized (required)")
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Canonical store could not initialize. "
            f"Reason: {exc}. The application cannot start without canonical storage.",
        ) from exc

    # Event store — audit trail, trace events
    try:
        from aip.adapter.event_store_queryable import QueryableEventStore

        container.event_store = QueryableEventStore(db_path)
        await container.event_store.initialize()
        logger.info("Event store initialized (required)")
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Event store could not initialize. "
            f"Reason: {exc}. The application cannot start without event storage.",
        ) from exc

    # Autonomy gate — authorization enforcement (core security)
    try:
        _ag_mod = importlib.import_module("aip.adapter.autonomy.autonomy_gate")
        _AutonomyGateImpl = _ag_mod.AutonomyGateImpl
        container.autonomy_gate = _AutonomyGateImpl(config={**config, "db_path": db_path})
        await container.autonomy_gate.initialize()
        logger.info("Autonomy gate initialized (required)")
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Autonomy gate could not initialize. "
            f"Reason: {exc}. The application cannot start without authorization enforcement.",
        ) from exc

    # Artifact store (versioned) — artifact versioned storage
    try:
        _as_mod = importlib.import_module("aip.adapter.artifact_store_versioned")
        _VersionedArtifactStore = _as_mod.VersionedArtifactStore
        container.artifact_store = _VersionedArtifactStore(db_path)
        await container.artifact_store.initialize()
        logger.info("Artifact store initialized (required)")
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Artifact store could not initialize. "
            f"Reason: {exc}. The application cannot start without artifact storage.",
        ) from exc

    # =====================================================================
    # OPTIONAL COMPONENTS — startup logs warning, service degrades gracefully
    # =====================================================================

    # Lexical store — FTS5 full-text search (degrades to no text search)
    try:
        _ls_mod = importlib.import_module("aip.adapter.lexical.sqlite_fts5_store")
        _SqliteFts5LexicalStore = _ls_mod.SqliteFts5LexicalStore
        container.lexical_store = _SqliteFts5LexicalStore(db_path)
        await container.lexical_store.initialize()
        logger.info("Lexical store initialized (optional)")
    except Exception as exc:
        logger.warning("Lexical store initialization failed (optional — text search degraded): %s", exc)

    # Embedding provider — vector embedding (degrades to fake_embed)
    # NOTE: Initialized before vector store so it can be passed to the factory
    # for SqliteVssVectorStore's store() compat method.
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
        logger.info("Embedding provider initialized (optional, provider=%s)", provider)
    except Exception as exc:
        logger.warning("Embedding provider initialization failed (optional — using fake_embed): %s", exc)

    # Vector store — semantic search (degrades to keyword-only)
    # Passes embedding_provider so SqliteVssVectorStore.store() can generate
    # real embeddings instead of inserting zero vectors.
    try:
        _vs_mod = importlib.import_module("aip.adapter.vector.factory")
        _create_vector_store = _vs_mod.create_vector_store
        container.vector_store = await _create_vector_store(config, embedding_provider=container.embedding_provider)
        logger.info("Vector store initialized (optional)")
    except Exception as exc:
        logger.warning("Vector store initialization failed (optional — semantic search degraded): %s", exc)

    # Project store — project management (degrades to empty projects)
    try:
        from aip.adapter.project.sqlite_project_store import SqliteProjectStore

        container.project_store = SqliteProjectStore(db_path)
        await container.project_store.initialize()
        logger.info("Project store initialized (optional)")
    except Exception as exc:
        logger.warning("Project store initialization failed (optional — project management degraded): %s", exc)

    # Budget store — token budget tracking (degrades to unlimited)
    try:
        _bs_mod = importlib.import_module("aip.adapter.budget_store_sqlite")
        _SqliteBudgetStore = _bs_mod.SqliteBudgetStore
        container.budget_store = _SqliteBudgetStore(db_path)
        await container.budget_store.initialize()
        logger.info("Budget store initialized (optional)")
    except Exception as exc:
        logger.warning("Budget store initialization failed (optional — budget tracking disabled): %s", exc)

    # Vigil store — canonical health monitoring (degrades to no monitoring)
    try:
        _vs2_mod = importlib.import_module("aip.adapter.vigil.sqlite_vigil_store")
        _SqliteVigilStore = _vs2_mod.SqliteVigilStore
        container.vigil_store = _SqliteVigilStore(db_path)
        await container.vigil_store.initialize()
        logger.info("Vigil store initialized (optional)")
    except Exception as exc:
        logger.warning("Vigil store initialization failed (optional — health monitoring degraded): %s", exc)

    # Model provider — LLM dispatch (degrades to stub responses)
    try:
        from aip.adapter.model_slot_resolver import ModelSlotResolver

        container.model_provider = ModelSlotResolver(config)
        logger.info("Model provider initialized (optional)")
    except Exception as exc:
        logger.warning(
            "Model provider initialization failed (optional — model calls will return errors): %s",
            exc,
        )

    # Knowledge store — compiled knowledge with embeddings
    # Requires vector_store + lexical_store + optional embedding_provider.
    # When embedding_provider is available, APPROVED compiled knowledge gets
    # real embeddings for semantic search. Without it, degrades to lexical-only.
    if container.vector_store is not None and container.lexical_store is not None:
        try:
            _ks_mod = importlib.import_module("aip.adapter.knowledge.sqlite_knowledge_store")
            _SqliteKnowledgeStore = _ks_mod.SqliteKnowledgeStore
            container.knowledge_store = _SqliteKnowledgeStore(
                db_path=db_path,
                vector_store=container.vector_store,
                lexical_store=container.lexical_store,
                embedding_provider=container.embedding_provider,
            )
            await container.knowledge_store.initialize()
            if container.embedding_provider is not None:
                logger.info(
                    "Knowledge store initialized with EmbeddingProvider "
                    "(optional — semantic search for compiled knowledge enabled)",
                )
            else:
                logger.warning(
                    "Knowledge store initialized without EmbeddingProvider "
                    "(optional — compiled knowledge search degrades to lexical-only). "
                    "Configure an embedding provider for full semantic search.",
                )
        except Exception as exc:
            logger.warning(
                "Knowledge store initialization failed (optional — knowledge compilation degraded): %s",
                exc,
            )
    else:
        logger.info("Knowledge store not wired: missing vector_store or lexical_store")

    # --- Wire orchestration components (lazy import to preserve layer discipline) ---
    # Beast actor — requires vector_store + embedding_provider at minimum
    if container.vector_store is not None and container.embedding_provider is not None:
        try:
            _beast_mod = importlib.import_module("aip.orchestration.actors.beast")
            _Beast = _beast_mod.Beast
            beast_config = BeastCadenceConfig(
                **{k: v for k, v in config.get("beast", {}).items() if k in BeastCadenceConfig.__dataclass_fields__},
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
            logger.info("Beast actor initialized (optional)")
        except Exception as exc:
            logger.warning("Beast actor initialization failed (optional — no background tasks): %s", exc)
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

    logger.info(
        "AIP startup complete. Required: 5 stores initialized. "
        "Optional: lexical=%s, vector=%s, embedding=%s, project=%s, budget=%s, "
        "vigil=%s, model=%s, knowledge=%s, beast=%s",
        container.lexical_store is not None,
        container.vector_store is not None,
        container.embedding_provider is not None,
        container.project_store is not None,
        container.budget_store is not None,
        container.vigil_store is not None,
        container.model_provider is not None,
        container.knowledge_store is not None,
        container.beast is not None,
    )

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
    if container.knowledge_store and hasattr(container.knowledge_store, "close"):
        await container.knowledge_store.close()
    if container.vector_store and hasattr(container.vector_store, "close"):
        await container.vector_store.close()
    if container.lexical_store and hasattr(container.lexical_store, "close"):
        await container.lexical_store.close()
    if container.canonical_store and hasattr(container.canonical_store, "close"):
        await container.canonical_store.close()
    if container.entity_store and hasattr(container.entity_store, "close"):
        await container.entity_store.close()
    if container.event_store and hasattr(container.event_store, "close"):
        await container.event_store.close()
    if container.project_store and hasattr(container.project_store, "close"):
        await container.project_store.close()
    if container.budget_store and hasattr(container.budget_store, "close"):
        await container.budget_store.close()
    if hasattr(container, "vigil_store") and container.vigil_store and hasattr(container.vigil_store, "close"):
        await container.vigil_store.close()
    if container.autonomy_gate and hasattr(container.autonomy_gate, "close"):
        await container.autonomy_gate.close()
    if container.artifact_store and hasattr(container.artifact_store, "close"):
        await container.artifact_store.close()
    # Close model provider httpx client (if wired)
    if container.model_provider and hasattr(container.model_provider, "close"):
        await container.model_provider.close()


def create_app(config: dict | None = None) -> "FastAPI":
    cfg = config or {}

    # Config validation runs BEFORE the app is created.
    # Unsafe production configs fail fast with a clear error message.
    validation = validate_config(cfg)
    if not validation.is_valid:
        # Log all errors for visibility
        for err in validation.errors:
            logger.critical("Config validation error: %s", err)
        # Raise the first error — it includes the setting path and remediation hint
        raise validation.errors[0]

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
    from aip.adapter.auth.middleware import AuthMiddleware
    from aip.adapter.middleware.rate_limiter import RateLimitMiddleware, TokenBucketRateLimiter
    from aip.foundation.schemas import AuthConfig, RateLimitConfig

    auth_cfg = AuthConfig(**{k: v for k, v in cfg.get("auth", {}).items() if k in AuthConfig.__dataclass_fields__})
    rl_cfg = RateLimitConfig(
        **{k: v for k, v in cfg.get("rate_limit", {}).items() if k in RateLimitConfig.__dataclass_fields__},
    )

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

    # Additional routers
    app.include_router(collaborators.router, prefix="/api/v1", tags=["collaborators"])
    app.include_router(plugins.router, prefix="/api/v1", tags=["plugins"])
    app.include_router(performance.router, prefix="/api/v1", tags=["performance"])

    # Web UI static (HTMX dashboard)
    import pathlib

    from fastapi.staticfiles import StaticFiles

    _static_dir = pathlib.Path(__file__).parent / "static"
    if _static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    @app.get("/")
    async def root():
        return {"status": "ok", "service": "aip-surfaces"}

    return app
