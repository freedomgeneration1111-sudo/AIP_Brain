"""FastAPI application factory + lifespan for AIP surfaces.

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
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from aip.adapter.api import collaborators, performance, plugins
from aip.adapter.api.dependencies import AipContainer
from aip.adapter.api.routes import admin, actors, artifacts, ask, chat, ecs, health, ingest, knowledge, memory, models, projects, review, sessions, sources
from aip.config import validate_config
from aip.foundation.schemas import BeastCadenceConfig, SurfaceConfig
from aip.logging import configure_logging, get_logger, new_correlation_id, set_correlation_id

log = get_logger(__name__)


# ------------------------------------------------------------------
# TOML Config Loader
# ------------------------------------------------------------------

def _load_dotenv() -> None:
    """Load .env file from the project root if python-dotenv is available.

    This ensures AIP_OPENAI_API_KEY and other env vars are available
    to ModelSlotResolver without manually exporting them. Safe to call
    multiple times — dotenv won't overwrite existing env vars.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # Search for .env in CWD and project root
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent.parent.parent / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            load_dotenv(env_path, override=False)
            log.info("dotenv_loaded", path=str(env_path))
            return


def _load_toml_config() -> dict:
    """Load the AIP config from the default TOML file.

    Looks for config/aip.config.toml relative to the project root.
    The search order is:
      1. AIP_CONFIG_PATH environment variable (explicit override)
      2. config/aip.config.toml relative to CWD
      3. config/aip.config.toml relative to this file's parent (src/aip/adapter/api/ → ../../../../config/)
    Returns an empty dict if no config file is found (not an error —
    the app can run with defaults, just no model slots).

    Also loads .env file if python-dotenv is available, so that
    AIP_OPENAI_API_KEY and other env vars are available to the
    ModelSlotResolver without manually exporting them.
    """
    # Load .env BEFORE reading any env vars (so AIP_OPENAI_API_KEY is available)
    _load_dotenv()
    config_path = os.environ.get("AIP_CONFIG_PATH", "")
    candidates = []
    if config_path:
        candidates.append(Path(config_path))
    else:
        candidates.append(Path.cwd() / "config" / "aip.config.toml")
        # Relative to this source file: src/aip/adapter/api/ → project root
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        candidates.append(project_root / "config" / "aip.config.toml")

    for path in candidates:
        if path.is_file():
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    log.warning("toml_config_unavailable", reason="neither tomllib nor tomli is installed")
                    return {}
            try:
                with open(path, "rb") as f:
                    cfg = tomllib.load(f)
                log.info("config_loaded", path=str(path), sections=list(cfg.keys()))
                return cfg
            except Exception as exc:
                log.warning("config_load_failed", path=str(path), error=str(exc))
                return {}

    log.info("config_not_found", searched=[str(p) for p in candidates])
    return {}


class StartupError(Exception):
    """Raised when a required component fails to initialize on startup."""


def _create_embedding_provider(config: dict) -> "EmbeddingProvider | None":
    """Create an EmbeddingProvider from config.

    Resolution order:
      1. [models.embedding] slot (if provider is openai_compatible) — this
         is the primary path when a model has been selected via the UI.
         Reads base_url, model, api_key from the slot config with env var
         overrides (AIP_EMBEDDING_BASE_URL, AIP_EMBEDDING_MODEL, etc.).
      2. [embedding] section — legacy backward-compatible config.
         Supports "ollama" and "fake" providers.
      3. Fallback: MockOllamaEmbeddingClient (deterministic fake for CI).

    Returns an EmbeddingProvider instance, or None on failure.
    """
    import os

    from aip.adapter.model_slot_resolver import ModelSlotResolver

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
        api_key = embed_cfg.get("api_key") or os.environ.get("AIP_EMBEDDING_API_KEY") or os.environ.get("AIP_OPENAI_API_KEY")
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


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Injects a correlation ID into every request context.

    If the client sends an X-Request-ID header, that value is used.
    Otherwise a new ID is generated. The ID is stored in a contextvar
    so that all structlog log calls within the request automatically
    include it, and it is echoed back as X-Request-ID in the response.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or new_correlation_id()
        set_correlation_id(request_id)

        log.debug(
            "request_started",
            method=request.method,
            path=request.url.path,
        )

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all adapters and orchestration components on startup.

    Required components cause startup to fail fast with a clear error.
    Optional components log warnings and degrade gracefully.
    """
    # Initialize structured logging before anything else
    configure_logging()

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
        log.info("component_initialized", component="entity_store", required=True)
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
        log.info("component_initialized", component="canonical_store", required=True)
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
        log.info("component_initialized", component="event_store", required=True)
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
        log.info("component_initialized", component="autonomy_gate", required=True)
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
        log.info("component_initialized", component="artifact_store", required=True)
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Artifact store could not initialize. "
            f"Reason: {exc}. The application cannot start without artifact storage.",
        ) from exc

    # =====================================================================
    # OPTIONAL COMPONENTS — startup logs warning, service degrades gracefully
    # =====================================================================

    # Lexical store — FTS5 full-text search (degrades to no text search)
    # Use the same lexical.db derivation as CLI ingestion (cli/_db_path + ingestion/pipeline)
    # so that backfill, augmented chat, etc. see chunks written by `aip ingest` / scripts/ingest_claude.py.
    try:
        _ls_mod = importlib.import_module("aip.adapter.lexical.sqlite_fts5_store")
        _SqliteFts5LexicalStore = _ls_mod.SqliteFts5LexicalStore
        lexical_db = os.path.join(os.path.dirname(db_path), "lexical.db")
        container.lexical_store = _SqliteFts5LexicalStore(lexical_db)
        await container.lexical_store.initialize()
        log.info("component_initialized", component="lexical_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="lexical_store", degradation="no_text_search", error=str(exc))

    # Embedding provider — vector embedding (degrades to fake_embed)
    # NOTE: Initialized before vector store so it can be passed to the factory
    # for SqliteVssVectorStore's store() compat method.
    #
    # The embedding provider is created from the [models.embedding] slot config
    # (resolved via ModelSlotResolver) when available, falling back to the
    # [embedding] section for backward compatibility. This ensures the embedding
    # provider uses the same model/API key selected via the UI on /models.
    try:
        prov = _create_embedding_provider(config)
        container.set_embedding_provider(prov)
        provider_type = config.get("embedding", {}).get("provider", "mock")
        embed_slot = config.get("models", {}).get("embedding", {})
        if isinstance(embed_slot, dict) and embed_slot.get("provider"):
            provider_type = embed_slot.get("provider")
        log.info("component_initialized", component="embedding_provider", required=False, provider=provider_type)
    except Exception as exc:
        log.warning("component_failed", component="embedding_provider", degradation="fake_embed", error=str(exc))

    # Vector store — semantic search (degrades to keyword-only)
    # Passes embedding_provider so SqliteVssVectorStore.store() can generate
    # real embeddings instead of inserting zero vectors.
    try:
        _vs_mod = importlib.import_module("aip.adapter.vector.factory")
        _create_vector_store = _vs_mod.create_vector_store
        container.vector_store = await _create_vector_store(config, embedding_provider=container.embedding_provider)
        log.info("component_initialized", component="vector_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="vector_store", degradation="keyword_only", error=str(exc))

    # Project store — project management (degrades to empty projects)
    try:
        from aip.adapter.project.sqlite_project_store import SqliteProjectStore

        container.project_store = SqliteProjectStore(db_path)
        await container.project_store.initialize()
        log.info("component_initialized", component="project_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="project_store", degradation="empty_projects", error=str(exc))

    # Budget store — token budget tracking (degrades to unlimited)
    try:
        _bs_mod = importlib.import_module("aip.adapter.budget_store_sqlite")
        _SqliteBudgetStore = _bs_mod.SqliteBudgetStore
        container.budget_store = _SqliteBudgetStore(db_path)
        await container.budget_store.initialize()
        log.info("component_initialized", component="budget_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="budget_store", degradation="unlimited", error=str(exc))

    # Vigil store — canonical health monitoring (degrades to no monitoring)
    try:
        _vs2_mod = importlib.import_module("aip.adapter.vigil.sqlite_vigil_store")
        _SqliteVigilStore = _vs2_mod.SqliteVigilStore
        container.vigil_store = _SqliteVigilStore(db_path)
        await container.vigil_store.initialize()
        log.info("component_initialized", component="vigil_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="vigil_store", degradation="no_monitoring", error=str(exc))

    # Model provider — LLM dispatch (degrades to stub responses)
    try:
        from aip.adapter.model_slot_resolver import ModelSlotResolver

        container.model_provider = ModelSlotResolver(config)
        log.info("component_initialized", component="model_provider", required=False)
    except Exception as exc:
        log.warning("component_failed", component="model_provider", degradation="stub_responses", error=str(exc))

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
            has_embed = container.embedding_provider is not None
            log.info(
                "component_initialized",
                component="knowledge_store",
                required=False,
                semantic_search=has_embed,
            )
        except Exception as exc:
            log.warning(
                "component_failed",
                component="knowledge_store",
                degradation="no_knowledge_compilation",
                error=str(exc),
            )
    else:
        log.info("component_skipped", component="knowledge_store", reason="missing_vector_or_lexical_store")

    # Definer profile — optional profile for augmented chat system prompt injection
    # (degrades gracefully to no injection if missing/empty/disabled)
    try:
        _dp_mod = importlib.import_module("aip.adapter.definer_profile")
        _DefinerProfile = _dp_mod.DefinerProfile
        definer_cfg = config.get("definer", {})
        profile_path = definer_cfg.get("profile_path", "examples/seed_corpus/definer_profile_v1.md")
        container.definer_profile = _DefinerProfile(profile_path)
        log.info("component_initialized", component="definer_profile", required=False)
    except Exception as exc:
        log.warning("component_failed", component="definer_profile", degradation="no_profile_injection", error=str(exc))

    # --- Wire BudgetManager ---
    if container.budget_store is not None:
        try:
            _bm_mod = importlib.import_module("aip.orchestration.budget")
            _BudgetManager = _bm_mod.BudgetManager
            _BudgetConfig = importlib.import_module("aip.foundation.schemas.budget").BudgetConfig
            budget_cfg = _BudgetConfig(
                **{k: v for k, v in config.get("budget", {}).items() if k in _BudgetConfig.__dataclass_fields__},
            )
            container.budget_manager = _BudgetManager(
                config=budget_cfg,
                budget_store=container.budget_store,
                event_store=container.event_store,
            )
            log.info("component_initialized", component="budget_manager", required=False, hard_stop=budget_cfg.budget_hard_stop)
        except Exception as exc:
            log.warning("component_failed", component="budget_manager", degradation="no_budget_enforcement", error=str(exc))

    # --- Wire TraceStoreAdapter ---
    # Adapts QueryableEventStore to TraceStore protocol so that orchestration
    # modules (Sexton, L4, perf) can use write_event(session_id, node_type, ...)
    # without the signature mismatch that would cause TypeError at runtime.
    if container.event_store is not None:
        try:
            from aip.adapter.trace_store_adapter import TraceStoreAdapter
            container.trace_store = TraceStoreAdapter(container.event_store)
            log.info("component_initialized", component="trace_store", adapter="TraceStoreAdapter")
        except Exception as exc:
            log.warning("component_failed", component="trace_store", degradation="trace_events_unavailable", error=str(exc))

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
            log.info("component_initialized", component="beast", required=False)
        except Exception as exc:
            log.warning("component_failed", component="beast", degradation="no_background_tasks", error=str(exc))
    else:
        log.info("component_skipped", component="beast", reason="missing_vector_or_embedding")

    # Vigil actor — requires vigil_store, canonical_store, entity_store, model_provider, trace_store
    if (
        container.vigil_store is not None
        and container.canonical_store is not None
        and container.entity_store is not None
        and container.model_provider is not None
    ):
        try:
            _vigil_mod = importlib.import_module("aip.orchestration.actors.vigil")
            _Vigil = _vigil_mod.Vigil
            _VigilConfig = importlib.import_module("aip.foundation.schemas.review").VigilConfig
            vigil_config = _VigilConfig(
                **{k: v for k, v in config.get("vigil", {}).items() if k in _VigilConfig.__dataclass_fields__},
            )
            # trace_store: use event_store if it supports write_event (TraceStore protocol)
            # The event_store and trace_store share the same interface in the current impl
            trace_store = container.event_store
            container.vigil = _Vigil(
                config=vigil_config,
                vigil_store=container.vigil_store,
                canonical_store=container.canonical_store,
                entity_store=container.entity_store,
                model_provider=container.model_provider,
                trace_store=container.trace_store or trace_store,
            )
            log.info("component_initialized", component="vigil", required=False)
        except Exception as exc:
            log.warning("component_failed", component="vigil", degradation="no_canonical_monitoring", error=str(exc))
    else:
        log.info("component_skipped", component="vigil", reason="missing_required_stores_or_model_provider")

    # Sexton actor — requires config, model_resolver, trace_store, event_store
    # Sexton is lightweight: only needs TraceStore + EventStore + optional ModelProvider
    if container.event_store is not None:
        try:
            _sexton_mod = importlib.import_module("aip.orchestration.sexton.sexton")
            _Sexton = _sexton_mod.Sexton
            _SextonConfig = importlib.import_module("aip.foundation.schemas.evaluation").SextonConfig
            sexton_config = _SextonConfig(
                **{k: v for k, v in config.get("sexton", {}).items() if k in _SextonConfig.__dataclass_fields__},
            )
            container.sexton = _Sexton(
                config=sexton_config,
                model_resolver=container.model_provider,
                trace_store=getattr(container, "trace_store", None) or container.event_store,
                event_store=container.event_store,
            )
            log.info("component_initialized", component="sexton", required=False)
        except Exception as exc:
            log.warning("component_failed", component="sexton", degradation="no_failure_classification", error=str(exc))
    else:
        log.info("component_skipped", component="sexton", reason="missing_event_store")

    # PerformanceProfiler — optional, only wired when profiling_enabled=True
    try:
        _perf_mod = importlib.import_module("aip.orchestration.perf")
        _PerformanceProfiler = _perf_mod.PerformanceProfiler
        _PerfConfig = importlib.import_module("aip.foundation.schemas.config").PerformanceConfig
        perf_cfg = _PerfConfig(
            **{k: v for k, v in config.get("performance", {}).items() if k in _PerfConfig.__dataclass_fields__},
        )
        if perf_cfg.profiling_enabled and container.event_store is not None:
            container.performance_profiler = _PerformanceProfiler(
                config=perf_cfg,
                trace_store=getattr(container, "trace_store", None) or container.event_store,
            )
            log.info("component_initialized", component="performance_profiler", profiling_enabled=True)
        else:
            log.info(
                "component_skipped",
                component="performance_profiler",
                profiling_enabled=perf_cfg.profiling_enabled,
            )
    except Exception as exc:
        log.warning(
            "component_failed",
            component="performance_profiler",
            degradation="performance_api_disabled",
            error=str(exc),
        )

    # ReviewQueueStore — optional, for MANUAL mode review queue persistence
    try:
        _rqs_mod = importlib.import_module("aip.adapter.review_queue_store")
        _ReviewQueueStore = _rqs_mod.ReviewQueueStore
        container.review_queue_store = _ReviewQueueStore(db_path=db_path)
        await container.review_queue_store.initialize()
        log.info("component_initialized", component="review_queue_store", required=False)
    except Exception as exc:
        log.warning(
            "component_failed",
            component="review_queue_store",
            degradation="manual_review_degraded",
            error=str(exc),
        )

    # ECS store — use PersistentEcsStore for state that survives restart
    try:
        _ecs_mod = importlib.import_module("aip.adapter.ecs_store_persistent")
        _PersistentEcsStore = _ecs_mod.PersistentEcsStore
        container.ecs_store = _PersistentEcsStore(
            db_path=db_path,
            event_store=container.event_store,
        )
        await container.ecs_store.initialize()
        log.info("component_initialized", component="ecs_store", persistent=True)
    except Exception as exc:
        log.warning("component_failed", component="ecs_store", degradation="in_memory_fallback", error=str(exc))

    # Session store — chat session persistence (degrades to in-memory)
    try:
        _ss_mod = importlib.import_module("aip.adapter.session.sqlite_session_store")
        _SqliteSessionStore = _ss_mod.SqliteSessionStore
        container.session_store = _SqliteSessionStore(db_path)
        await container.session_store.initialize()
        log.info("component_initialized", component="session_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="session_store", degradation="in_memory_sessions", error=str(exc))

    # SessionManager — orchestration session lifecycle
    if container.session_store is not None:
        try:
            _sm_mod = importlib.import_module("aip.orchestration.session")
            _SessionManager = _sm_mod.SessionManager
            container.session_manager = _SessionManager(config=config)
            log.info("component_initialized", component="session_manager", required=False)
        except Exception as exc:
            log.warning("component_failed", component="session_manager", degradation="no_trajectory_regulation", error=str(exc))

    app.state.container = container
    app.state.start_time = time.time()
    container._app_start_time = time.time()

    # --- Beast background scheduler ---
    beast_task: asyncio.Task | None = None
    if container.beast is not None:

        async def _beast_scheduler():
            """Background loop that runs beast.run_cycle() periodically.

            Each cycle gets its own correlation ID so that all log messages
            within a single cycle can be traced back to that cycle.
            """
            interval = container.beast._config.health_check_interval_seconds
            # Enforce a reasonable minimum to avoid busy-looping
            if interval < 60:
                interval = 300
                log.warning("beast_cadence_clamped", original_interval=interval, clamped_to=300)
            log.info("beast_scheduler_starting", interval_s=interval)
            cycle_num = 0
            while True:
                cycle_num += 1
                cycle_id = f"beast-{new_correlation_id()}"
                set_correlation_id(cycle_id)
                try:
                    log.info("beast_cycle_start", cycle=cycle_num)
                    summary = await container.beast.run_cycle()
                    log.info(
                        "beast_cycle_complete",
                        cycle=cycle_num,
                        health=summary.get("health_overall", "unknown"),
                        stale_vectors=summary.get("corpus", {}).get("stale_vectors_found", "?"),
                        reembedded=summary.get("corpus", {}).get("vectors_reembedded", 0),
                        elapsed_s=summary.get("cycle_elapsed_seconds", 0),
                    )
                except asyncio.CancelledError:
                    log.info("beast_scheduler_cancelled", cycle=cycle_num)
                    raise
                except Exception as exc:
                    log.error("beast_cycle_failed", cycle=cycle_num, error=str(exc), exc_info=True)
                finally:
                    set_correlation_id(None)
                await asyncio.sleep(interval)

        beast_task = asyncio.create_task(_beast_scheduler(), name="beast-scheduler")
        log.info("beast_scheduler_created")

    # --- Vigil background scheduler ---
    vigil_task: asyncio.Task | None = None
    if container.vigil is not None:

        async def _vigil_scheduler():
            """Background loop that runs vigil.run() periodically.

            Vigil monitors canonical health and detects stale items.
            Runs on a configurable interval (default: 3600s = 1 hour).
            """
            interval = container.vigil.config.canonical_health_check_interval_seconds
            if interval < 60:
                interval = 3600
                log.warning("vigil_cadence_clamped", clamped_to=3600)
            log.info("vigil_scheduler_starting", interval_s=interval)
            cycle_num = 0
            while True:
                cycle_num += 1
                cycle_id = f"vigil-{new_correlation_id()}"
                set_correlation_id(cycle_id)
                try:
                    log.info("vigil_cycle_start", cycle=cycle_num)
                    await container.vigil.run()
                    log.info("vigil_cycle_complete", cycle=cycle_num)
                except asyncio.CancelledError:
                    log.info("vigil_scheduler_cancelled", cycle=cycle_num)
                    raise
                except Exception as exc:
                    log.error("vigil_cycle_failed", cycle=cycle_num, error=str(exc), exc_info=True)
                finally:
                    set_correlation_id(None)
                await asyncio.sleep(interval)

        vigil_task = asyncio.create_task(_vigil_scheduler(), name="vigil-scheduler")
        log.info("vigil_scheduler_created")

    # --- Sexton background scheduler ---
    sexton_task: asyncio.Task | None = None
    if container.sexton is not None:

        async def _sexton_scheduler():
            """Background loop that runs sexton.run_classification_cycle() periodically.

            Sexton classifies unclassified failures from the trace store.
            Runs on a configurable interval (default: 300s = 5 minutes).
            """
            interval = container.sexton._config.classification_interval_seconds
            if interval < 30:
                interval = 300
                log.warning("sexton_cadence_clamped", clamped_to=300)
            log.info("sexton_scheduler_starting", interval_s=interval)
            cycle_num = 0
            while True:
                cycle_num += 1
                cycle_id = f"sexton-{new_correlation_id()}"
                set_correlation_id(cycle_id)
                try:
                    log.info("sexton_cycle_start", cycle=cycle_num)
                    await container.sexton.run_classification_cycle()
                    log.info("sexton_cycle_complete", cycle=cycle_num)
                except asyncio.CancelledError:
                    log.info("sexton_scheduler_cancelled", cycle=cycle_num)
                    raise
                except Exception as exc:
                    log.error("sexton_cycle_failed", cycle=cycle_num, error=str(exc), exc_info=True)
                finally:
                    set_correlation_id(None)
                await asyncio.sleep(interval)

        sexton_task = asyncio.create_task(_sexton_scheduler(), name="sexton-scheduler")
        log.info("sexton_scheduler_created")

    log.info(
        "startup_complete",
        required_initialized=5,
        lexical=container.lexical_store is not None,
        vector=container.vector_store is not None,
        embedding=container.embedding_provider is not None,
        project=container.project_store is not None,
        budget=container.budget_store is not None,
        vigil_store=container.vigil_store is not None,
        vigil_actor=container.vigil is not None,
        sexton_actor=container.sexton is not None,
        model=container.model_provider is not None,
        knowledge=container.knowledge_store is not None,
        beast=container.beast is not None,
        session_store=container.session_store is not None,
        session_manager=container.session_manager is not None,
    )

    yield

    # --- Shutdown ---
    for task_name, task in [
        ("beast", beast_task),
        ("vigil", vigil_task),
        ("sexton", sexton_task),
    ]:
        if task is not None:
            log.info(f"{task_name}_scheduler_cancelling")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # shutdown: close any open connections (the individual stores implement close())
    for store_name, store in [
        ("knowledge_store", container.knowledge_store),
        ("vector_store", container.vector_store),
        ("lexical_store", container.lexical_store),
        ("canonical_store", container.canonical_store),
        ("entity_store", container.entity_store),
        ("event_store", container.event_store),
        ("project_store", container.project_store),
        ("budget_store", container.budget_store),
        ("vigil_store", getattr(container, "vigil_store", None)),
        ("autonomy_gate", container.autonomy_gate),
        ("artifact_store", container.artifact_store),
        ("model_provider", container.model_provider),
        ("ecs_store", container.ecs_store),
        ("review_queue_store", container.review_queue_store),
        ("session_store", container.session_store),
    ]:
        if store and hasattr(store, "close"):
            try:
                await store.close()
            except Exception as exc:
                log.warning("store_close_failed", store=store_name, error=str(exc))

    log.info("shutdown_complete")


def create_app(config: dict | None = None) -> "FastAPI":
    # If no config dict was passed, load from the TOML file.
    # This is the normal path when uvicorn calls create_app() via --factory
    # with no arguments. Without this, the entire config/aip.config.toml
    # is ignored and all model slots are empty (ci_mode=True).
    if config is None:
        config = _load_toml_config()
    cfg = config or {}

    # Config validation runs BEFORE the app is created.
    # Unsafe production configs fail fast with a clear error message.
    validation = validate_config(cfg)
    if not validation.is_valid:
        # Initialize logging early so critical messages are structured
        configure_logging()
        for err in validation.errors:
            log.critical("config_validation_error", error=str(err))
        raise validation.errors[0]

    surface_cfg = SurfaceConfig(**{k: v for k, v in cfg.items() if k in SurfaceConfig.__dataclass_fields__})

    app = FastAPI(title="AIP 0.1 Surfaces", version="0.1", lifespan=lifespan)

    # Global exception handler — returns structured JSON for unhandled exceptions
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        log.error(
            "unhandled_exception",
            method=request.method,
            path=request.url.path,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "error_type": type(exc).__name__,
                "path": request.url.path,
            },
        )

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

    # Correlation ID middleware — runs early so all downstream logs have the ID
    app.add_middleware(CorrelationIdMiddleware)

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
    app.include_router(models.router, prefix="/api/v1", tags=["models"])
    app.include_router(actors.router, prefix="/api/v1", tags=["actors"])
    app.include_router(ingest.router, prefix="/api/v1", tags=["ingest"])
    app.include_router(ask.router, prefix="/api/v1", tags=["ask"])
    app.include_router(knowledge.router, prefix="/api/v1", tags=["knowledge"])
    app.include_router(ecs.router, prefix="/api/v1", tags=["ecs"])
    app.include_router(sources.router, prefix="/api/v1", tags=["sources"])

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
