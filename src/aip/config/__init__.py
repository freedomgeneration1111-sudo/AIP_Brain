"""AIP configuration validation and runtime mode detection.

This module provides hard validation so that production mode cannot start
with unsafe defaults, weak secrets, fixture behavior, or public
unauthenticated API exposure.

Key types:
    RuntimeMode — LAPTOP or PRODUCTION
    ConfigValidationError — structured error with code, setting path, remediation
    validate_config() — run all safety checks before app/CLI startup
    get_runtime_mode() — deterministic profile detection from config + env
    DogfoodMode — MINIMAL, FULL, or DIAGNOSTIC dogfood readiness level
    get_dogfood_mode() — resolve dogfood mode from config + env
    DogfoodReadinessCheck — structured readiness report for dogfood validation
    validate_dogfood_readiness() — check all components/actors for dogfood readiness
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default / known-weak database passwords that must not appear in production
_WEAK_POSTGRES_PASSWORDS: frozenset[str] = frozenset(
    {
        "changeme",
        "password",
        "postgres",
        "postgres_password",
        "secret",
        "default",
        "admin",
        "aip",
        "123456",
        "",
    }
)

# Bind hosts that expose the API on all network interfaces
_PUBLIC_BIND_HOSTS: frozenset[str] = frozenset(
    {
        "0.0.0.0",
        "::",
        ":::",
        "[::]",
    }
)

# Embedding/model providers that indicate fixture / CI mode
_FIXTURE_PROVIDERS: frozenset[str] = frozenset(
    {
        "fake",
        "mock",
        "ci",
        "fixture",
    }
)


# ---------------------------------------------------------------------------
# Runtime mode
# ---------------------------------------------------------------------------


class RuntimeMode(Enum):
    """Detected deployment runtime mode."""

    LAPTOP = "laptop"
    PRODUCTION = "production"


def get_runtime_mode(config: dict[str, Any]) -> RuntimeMode:
    """Determine the runtime mode from config and environment.

    Resolution order (first wins):
      1. ``config["deployment"]["profile_name"]`` (explicit config setting)
      2. ``AIP_PROFILE`` environment variable (Docker / systemd)
      3. Default to LAPTOP

    Production safety does NOT depend on ambient CI variables.
    """
    # 1. Explicit config
    profile = config.get("deployment", {}).get("profile_name", "")
    if isinstance(profile, str) and profile.strip().lower() == "production":
        return RuntimeMode.PRODUCTION

    # 2. Environment variable (Docker sets AIP_PROFILE)
    env_profile = os.environ.get("AIP_PROFILE", "").strip().lower()
    if env_profile == "production":
        return RuntimeMode.PRODUCTION

    # 3. Default
    return RuntimeMode.LAPTOP


# ---------------------------------------------------------------------------
# Dogfood mode
# ---------------------------------------------------------------------------


class DogfoodMode(Enum):
    """Sprint 8 dogfood readiness level.

    Controls how aggressively the system validates that all components
    and actors are initialized before accepting real workloads.

    MINIMAL — only core stores must be present; degraded operation is fine.
    FULL — all stores, actors, and embedding/retrieval channels must be up.
    DIAGNOSTIC — same checks as FULL but logs detailed diagnostics without
                 blocking startup; useful for pre-flight inspection.
    """

    MINIMAL = "minimal"
    FULL = "full"
    DIAGNOSTIC = "diagnostic"


def get_dogfood_mode(config: dict[str, Any]) -> DogfoodMode:
    """Determine the dogfood mode from config and environment.

    Resolution order (first wins):
      1. ``config["alpha"]["dogfood_mode"]`` (explicit config setting)
      2. ``AIP_DOGFOOD_MODE`` environment variable
      3. Default to MINIMAL

    The value is matched case-insensitively against the enum members.
    Invalid values trigger a warning and fall back to MINIMAL.
    """
    logger = logging.getLogger("aip.config")

    raw: str | None = None
    source: str = "default"

    # 1. Explicit config
    alpha_section = config.get("alpha", {})
    if isinstance(alpha_section, dict):
        cfg_val = alpha_section.get("dogfood_mode", "")
        if isinstance(cfg_val, str) and cfg_val.strip():
            raw = cfg_val.strip()
            source = 'config["alpha"]["dogfood_mode"]'

    # 2. Environment variable
    if raw is None:
        env_val = os.environ.get("AIP_DOGFOOD_MODE", "").strip()
        if env_val:
            raw = env_val
            source = "AIP_DOGFOOD_MODE"

    # 3. Default
    if raw is None:
        return DogfoodMode.MINIMAL

    # Validate (case-insensitive)
    normalized = raw.lower()
    for mode in DogfoodMode:
        if mode.value == normalized:
            return mode

    # Invalid value — warn and fall back
    valid_values = ", ".join(m.value for m in DogfoodMode)
    logger.warning(
        "Invalid dogfood_mode '%s' from %s. Must be one of: %s. "
        "Falling back to MINIMAL.",
        raw,
        source,
        valid_values,
    )
    return DogfoodMode.MINIMAL


# ---------------------------------------------------------------------------
# Dogfood readiness check
# ---------------------------------------------------------------------------


@dataclass
class DogfoodReadinessCheck:
    """Structured readiness report for Sprint 8 dogfood validation.

    Attributes:
        mode: The resolved DogfoodMode.
        required_components: Mapping of component name → initialized (bool).
        required_actors: Mapping of actor name → active (bool).
        embedding_provider_active: Whether the embedding provider is running.
        embedding_provider_type: Human-readable provider type (e.g. 'openai_compatible', 'mock').
        retrieval_channels: Mapping of channel name → available (bool).
        db_paths_valid: Whether all registered DB paths exist and are writable.
        db_path_details: Per-store DB path existence details.
        is_ready: Computed — True only if mode is FULL and all required items
                  are True.
        degraded_components: Computed — names of components/actors/channels
                             where the bool is False.
        summary: Computed — human-readable readiness report.
    """

    mode: DogfoodMode
    required_components: dict[str, bool] = field(default_factory=dict)
    required_actors: dict[str, bool] = field(default_factory=dict)
    embedding_provider_active: bool = False
    embedding_provider_type: str = "unknown"
    embedding_backfill_state: str = "not_configured"
    retrieval_channels: dict[str, bool] = field(default_factory=dict)
    db_paths_valid: bool = True
    db_path_details: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        """True only if mode is FULL and every required item is True."""
        if self.mode != DogfoodMode.FULL:
            return False
        return (
            all(self.required_components.values())
            and all(self.required_actors.values())
            and self.embedding_provider_active
            and all(self.retrieval_channels.values())
            and self.db_paths_valid
        )

    @property
    def degraded_components(self) -> list[str]:
        """Names of components, actors, or channels where the bool is False."""
        degraded: list[str] = []
        for name, ok in self.required_components.items():
            if not ok:
                degraded.append(name)
        for name, ok in self.required_actors.items():
            if not ok:
                degraded.append(name)
        if not self.embedding_provider_active:
            degraded.append("embedding_provider")
        for name, ok in self.retrieval_channels.items():
            if not ok:
                degraded.append(name)
        if not self.db_paths_valid:
            degraded.append("db_paths")
        return degraded

    @property
    def summary(self) -> str:
        """Human-readable readiness report."""
        lines: list[str] = []
        lines.append(f"DogfoodMode: {self.mode.value}")
        lines.append(f"Ready: {self.is_ready}")

        comp_ok = sum(1 for v in self.required_components.values() if v)
        comp_total = len(self.required_components)
        lines.append(f"Components: {comp_ok}/{comp_total} initialized")

        actor_ok = sum(1 for v in self.required_actors.values() if v)
        actor_total = len(self.required_actors)
        lines.append(f"Actors: {actor_ok}/{actor_total} active")

        lines.append(f"Embedding provider: {'active' if self.embedding_provider_active else 'INACTIVE'} ({self.embedding_provider_type})")
        lines.append(f"Embedding backfill: {self.embedding_backfill_state}")

        ch_ok = sum(1 for v in self.retrieval_channels.values() if v)
        ch_total = len(self.retrieval_channels)
        lines.append(f"Retrieval channels: {ch_ok}/{ch_total} available")

        if self.degraded_components:
            lines.append(f"Degraded: {', '.join(self.degraded_components)}")
        else:
            lines.append("Degraded: none")

        lines.append(f"DB paths valid: {self.db_paths_valid}")

        return "\n".join(lines)


def _detect_embedding_provider_type(config: dict[str, Any], container: Any) -> str:
    """Detect the embedding provider type for readiness reporting.

    Checks the actual provider instance first, then falls back to config.
    Returns a human-readable provider type string.
    """
    # Try to get the type from the actual provider instance
    provider = getattr(container, "embedding_provider", None)
    if provider is not None:
        # Check common attribute names for the provider type
        for attr in ("provider_type", "_provider_type", "provider"):
            val = getattr(provider, attr, None)
            if val and isinstance(val, str):
                return val
        # Check class name as fallback
        cls_name = provider.__class__.__name__
        if "Mock" in cls_name or "Fake" in cls_name:
            return "mock"
        if "OpenAI" in cls_name:
            return "openai_compatible"
        return cls_name

    # Fall back to config
    embed_slot = config.get("models", {}).get("embedding", {})
    if isinstance(embed_slot, dict) and embed_slot.get("provider"):
        return embed_slot["provider"]
    return config.get("embedding", {}).get("provider", "unknown")


def _validate_db_paths(container: Any) -> tuple[bool, dict[str, dict[str, Any]]]:
    """Validate that all registered DB paths exist and are writable.

    Returns (all_valid, details_dict) where details_dict maps
    store_name → {db_path, exists, size_mb, valid}.
    """
    from pathlib import Path

    details: dict[str, dict[str, Any]] = {}
    all_valid = True

    registry = getattr(container, "_store_registry", {})
    for store_name, db_path in registry.items():
        p = Path(db_path)
        exists = p.exists()
        size_mb = round(p.stat().st_size / (1024 * 1024), 2) if exists else 0
        # A DB file is valid if it exists (SQLite creates it on init)
        valid = exists
        if not valid:
            all_valid = False
        details[store_name] = {
            "db_path": db_path,
            "exists": exists,
            "size_mb": size_mb,
            "valid": valid,
        }

    return all_valid, details


def validate_dogfood_readiness(
    config: dict[str, Any],
    container: Any,
) -> DogfoodReadinessCheck:
    """Check all components and actors for dogfood readiness.

    Inspects the *container*-like object for the presence and
    initialization of required stores, providers, and actors.

    For FULL mode, missing components/actors emit **warnings** (not
    errors) because degraded operation is acceptable — the system
    should still start, just with reduced capability. However, the
    readiness check clearly marks is_ready=False so that operators
    can see the system is in degraded mode.

    Full dogfood mode is not a slogan. It is a boot-validated
    operating state.

    Args:
        config: The full configuration dict.
        container: A container-like object whose attributes are checked
                   for component/actor availability.

    Returns:
        A :class:`DogfoodReadinessCheck` with the full readiness report.
    """
    logger = logging.getLogger("aip.config")
    mode = get_dogfood_mode(config)

    # --- Component checks ---
    component_names = [
        "lexical_store",
        "vector_store",
        "embedding_provider",
        "ecs_store",
        "artifact_store",
        "project_store",
        "graph_store",
        "corpus_turn_store",
        "event_store",
        "model_provider",
        "budget_store",
        "session_store",
        "review_queue_store",
        "knowledge_store",
    ]

    required_components: dict[str, bool] = {}
    for name in component_names:
        available = hasattr(container, name) and getattr(container, name) is not None
        required_components[name] = available

    # --- Actor checks ---
    actor_names = [
        "beast",
        "vigil",
        "sexton_actor",
    ]

    required_actors: dict[str, bool] = {}
    for name in actor_names:
        active = hasattr(container, name) and getattr(container, name) is not None
        required_actors[name] = active

    # --- Embedding provider check ---
    embedding_provider_active = required_components.get("embedding_provider", False)
    embedding_provider_type = _detect_embedding_provider_type(config, container)

    # Chunk 4: Detect embedding backfill state from Sexton actor
    embedding_backfill_state = "not_configured"
    sexton_actor = getattr(container, "sexton_actor", None)
    if sexton_actor is not None and hasattr(sexton_actor, "_embedding_backfill_state"):
        try:
            embedding_backfill_state = sexton_actor._embedding_backfill_state
        except Exception:
            pass

    # --- Retrieval channels ---
    # Six built-in channels: lexical (fts), vector, corpus, graph, wiki, procedural.
    # Each channel is available when its backing store is initialized.
    retrieval_channels: dict[str, bool] = {}
    retrieval_channels["lexical"] = required_components.get("lexical_store", False)
    retrieval_channels["vector"] = required_components.get("vector_store", False)
    retrieval_channels["corpus"] = required_components.get("corpus_turn_store", False)
    retrieval_channels["graph"] = required_components.get("graph_store", False)
    # Wiki channel requires graph_store (wiki articles stored as graph nodes)
    retrieval_channels["wiki"] = required_components.get("graph_store", False)
    # Procedural channel requires ace_playbook
    retrieval_channels["procedural"] = hasattr(container, "ace_playbook") and getattr(container, "ace_playbook", None) is not None

    # --- DB path validation ---
    db_paths_valid, db_path_details = _validate_db_paths(container)

    check = DogfoodReadinessCheck(
        mode=mode,
        required_components=required_components,
        required_actors=required_actors,
        embedding_provider_active=embedding_provider_active,
        embedding_provider_type=embedding_provider_type,
        embedding_backfill_state=embedding_backfill_state,
        retrieval_channels=retrieval_channels,
        db_paths_valid=db_paths_valid,
        db_path_details=db_path_details,
    )

    # --- Emit warnings for FULL mode ---
    if mode == DogfoodMode.FULL:
        for name, ok in required_components.items():
            if not ok:
                logger.warning(
                    "Dogfood FULL mode: component '%s' is not initialized. "
                    "System will run in degraded mode.",
                    name,
                )
        for name, ok in required_actors.items():
            if not ok:
                logger.warning(
                    "Dogfood FULL mode: actor '%s' is not active. "
                    "System will run in degraded mode.",
                    name,
                )
        if not embedding_provider_active:
            logger.warning(
                "Dogfood FULL mode: embedding_provider is not active. "
                "System will run in degraded mode.",
            )
        elif embedding_provider_type in ("mock", "fake", "ci", "fixture"):
            logger.warning(
                "Dogfood FULL mode: embedding_provider is using a "
                "fixture/mock provider ('%s'). Real embeddings will not be generated.",
                embedding_provider_type,
            )
        # Chunk 4: Warn on degraded backfill states
        if embedding_backfill_state in ("not_configured", "degraded", "failed"):
            logger.warning(
                "Dogfood FULL mode: embedding backfill state is '%s'. "
                "Vector retrieval will be limited or unavailable.",
                embedding_backfill_state,
            )
        for name, ok in retrieval_channels.items():
            if not ok:
                logger.warning(
                    "Dogfood FULL mode: retrieval channel '%s' is not available. "
                    "System will run in degraded mode.",
                    name,
                )
        if not db_paths_valid:
            for store_name, details in db_path_details.items():
                if not details.get("valid", True):
                    logger.warning(
                        "Dogfood FULL mode: DB path for '%s' does not exist: %s",
                        store_name,
                        details.get("db_path", "unknown"),
                    )

        # The gate: FULL dogfood mode is not a slogan.
        if not check.is_ready:
            logger.warning(
                "Dogfood FULL mode is NOT ready. "
                "Degraded components: %s. "
                "Full dogfood mode is a boot-validated operating state, "
                "not a slogan. Fix missing components or switch to "
                "dogfood_mode='minimal'.",
                ", ".join(check.degraded_components),
            )

    # --- DIAGNOSTIC mode: log the full summary ---
    if mode == DogfoodMode.DIAGNOSTIC:
        logger.info("Dogfood DIAGNOSTIC readiness report:\n%s", check.summary)

    return check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_public_bind(host: str) -> bool:
    """Return True if *host* binds on all interfaces (public)."""
    return host.strip().lower() in _PUBLIC_BIND_HOSTS or host.strip() == ""


def _is_localhost(host: str) -> bool:
    """Return True if *host* is a localhost / loopback address."""
    h = host.strip().lower()
    return h in ("127.0.0.1", "localhost", "::1", "[::1]")


def _is_fixture_provider(provider: str) -> bool:
    """Return True if *provider* indicates CI / fixture / fake mode."""
    return provider.strip().lower() in _FIXTURE_PROVIDERS


def _get_auth_enabled(config: dict[str, Any]) -> bool:
    """Determine effective auth_enabled from config.

    Checks both ``auth.auth_enabled`` and ``deployment.auth_enabled``,
    returning True if either is explicitly True.
    """
    auth_section = config.get("auth", {})
    deployment_section = config.get("deployment", {})
    # Respect explicit True in either location
    if isinstance(auth_section, dict) and auth_section.get("auth_enabled") is True:
        return True
    if isinstance(deployment_section, dict) and deployment_section.get("auth_enabled") is True:
        return True
    return False


def _get_postgres_password(config: dict[str, Any]) -> str | None:
    """Extract Postgres password from DATABASE_URL or config.

    Returns None if no password can be found (not an error for laptop mode).
    """
    # 1. DATABASE_URL environment variable
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url and "postgresql" in db_url:
        # Parse postgresql://user:password@host:port/db
        try:
            after_scheme = db_url.split("://", 1)[1]
            if ":" in after_scheme.split("@", 1)[0]:
                password_part = after_scheme.split(":", 1)[1].split("@", 1)[0]
                if password_part:
                    return password_part
        except (IndexError, ValueError):
            pass

    # 2. POSTGRES_PASSWORD environment variable
    pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
    if pg_pass:
        return pg_pass

    # 3. Config dict
    return config.get("postgres", {}).get("password") or None


def _is_unsafe_override_set() -> bool:
    """Check if the explicit unsafe-override env var is set.

    ``AIP_UNSAFE_ALLOW_PUBLIC_NO_AUTH=true`` must be set explicitly and
    intentionally.  This override does NOT bypass production auth
    requirements — it only allows public-bind + auth-disabled for
    local/dev scenarios where the operator accepts the risk.
    """
    return os.environ.get("AIP_UNSAFE_ALLOW_PUBLIC_NO_AUTH", "").strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Validation error
# ---------------------------------------------------------------------------


class ConfigValidationError(Exception):
    """Raised when configuration fails production-safety validation.

    Attributes:
        message: Human-readable description of the problem.
        code: Machine-readable error code (e.g. ``"PROD_AUTH_DISABLED"``).
        setting_path: Dot-separated path to the offending setting
            (e.g. ``"auth.auth_enabled"``).
        remediation: One-line hint telling the user how to fix it.
    """

    def __init__(
        self,
        message: str,
        code: str,
        setting_path: str,
        remediation: str,
    ) -> None:
        self.message = message
        self.code = code
        self.setting_path = setting_path
        self.remediation = remediation
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.message} [code={self.code}, setting={self.setting_path}] Fix: {self.remediation}"


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------


class ValidationResult:
    """Aggregated validation result with all errors and warnings found."""

    def __init__(self) -> None:
        self.errors: list[ConfigValidationError] = []
        self.warnings: list[ConfigValidationError] = []

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(
        self,
        message: str,
        code: str,
        setting_path: str,
        remediation: str,
    ) -> None:
        self.errors.append(
            ConfigValidationError(
                message=message,
                code=code,
                setting_path=setting_path,
                remediation=remediation,
            )
        )

    def add_warning(
        self,
        message: str,
        code: str,
        setting_path: str,
        remediation: str,
    ) -> None:
        """Add a non-fatal warning (logged but does not block startup)."""
        self.warnings.append(
            ConfigValidationError(
                message=message,
                code=code,
                setting_path=setting_path,
                remediation=remediation,
            )
        )

    def raise_if_invalid(self) -> None:
        """Raise the first error if any validation failed.

        All errors are logged; the first is raised for the traceback.
        Warnings are logged but never block startup.
        """
        import logging

        logger = logging.getLogger("aip.config")

        # Log warnings first
        for warn in self.warnings:
            logger.warning("Config validation warning: %s", warn)

        if self.errors:
            # Log all errors for visibility
            for err in self.errors:
                logger.error("Config validation error: %s", err)
            raise self.errors[0]


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------


def validate_config(config: dict[str, Any]) -> ValidationResult:
    """Validate configuration against production-safety rules.

    Runs after config is loaded and before API/CLI/MCP/background runtime
    starts.  Validation is profile-aware: laptop mode has relaxed rules,
    production mode has hard requirements.

    Returns a :class:`ValidationResult` that callers can inspect or
    ``.raise_if_invalid()`` to fail fast.
    """
    result = ValidationResult()
    mode = get_runtime_mode(config)
    auth_enabled = _get_auth_enabled(config)
    host = config.get("api", {}).get("host", "127.0.0.1")
    if not isinstance(host, str):
        host = str(host)
    public_bind = _is_public_bind(host)

    # ==================================================================
    # PRODUCTION-MODE CHECKS (hard failures)
    # ==================================================================
    if mode == RuntimeMode.PRODUCTION:
        # 1. Production + auth_enabled=false => hard failure
        if not auth_enabled:
            result.add_error(
                message="Unsafe production config: auth.enabled=false is not allowed "
                "when deployment.profile=production.",
                code="PROD_AUTH_DISABLED",
                setting_path="auth.auth_enabled",
                remediation="Set auth.auth_enabled=true and deployment.auth_enabled=true in your production config.",
            )

        # 2. Production + fixture evaluation/model mode => hard failure
        embed_provider = config.get("embedding", {}).get("provider", "")
        if isinstance(embed_provider, str) and _is_fixture_provider(embed_provider):
            result.add_error(
                message=f"Unsafe production config: embedding.provider='{embed_provider}' "
                f"is a fixture/CI provider not allowed in production.",
                code="PROD_FIXTURE_PROVIDER",
                setting_path="embedding.provider",
                remediation="Set embedding.provider to a real provider "
                "(e.g. 'api', 'ollama') in your production config.",
            )

        model_provider = config.get("deployment", {}).get("model_provider", "")
        if isinstance(model_provider, str) and _is_fixture_provider(model_provider):
            result.add_error(
                message=f"Unsafe production config: deployment.model_provider='{model_provider}' "
                f"is a fixture/CI provider not allowed in production.",
                code="PROD_FIXTURE_MODEL_PROVIDER",
                setting_path="deployment.model_provider",
                remediation="Set deployment.model_provider to a real provider "
                "(e.g. 'api', 'ollama') in your production config.",
            )

        # 3. Production + default/missing/weak Postgres password => hard failure
        pg_password = _get_postgres_password(config)
        if pg_password is None:
            result.add_error(
                message="Unsafe production config: POSTGRES_PASSWORD is missing. "
                "Production deployments must provide an explicit database password.",
                code="PROD_MISSING_DB_PASSWORD",
                setting_path="POSTGRES_PASSWORD",
                remediation="Set POSTGRES_PASSWORD environment variable or provide it in "
                "DATABASE_URL. Example: export POSTGRES_PASSWORD=$(openssl rand -hex 32)",
            )
        elif pg_password.lower() in _WEAK_POSTGRES_PASSWORDS:
            result.add_error(
                message="Unsafe production config: POSTGRES_PASSWORD uses a default or "
                "weak value that is not allowed in production.",
                code="PROD_WEAK_DB_PASSWORD",
                setting_path="POSTGRES_PASSWORD",
                remediation="Set POSTGRES_PASSWORD to a strong, unique value. "
                "Example: export POSTGRES_PASSWORD=$(openssl rand -hex 32)",
            )

        # 4. Production + public bind + auth disabled => hard failure
        #    (redundant with check 1 but gives a more specific message)
        if public_bind and not auth_enabled:
            result.add_error(
                message="Unsafe production config: host=0.0.0.0 with auth_enabled=false "
                "is blocked. Public API must require authentication.",
                code="PROD_PUBLIC_NO_AUTH",
                setting_path="api.host",
                remediation="Set auth.auth_enabled=true when binding to public interfaces in production.",
            )

    # ==================================================================
    # CROSS-PROFILE CHECKS (apply to both laptop and production)
    # ==================================================================

    # 5. Public bind host + auth_enabled=false => hard failure unless
    #    explicit unsafe override is set (laptop/dev only escape hatch)
    if public_bind and not auth_enabled and mode != RuntimeMode.PRODUCTION:
        if not _is_unsafe_override_set():
            result.add_error(
                message=f"Unsafe public bind: host={host} with auth_enabled=false is blocked. "
                f"Unauthenticated API on public interfaces is a security risk.",
                code="PUBLIC_NO_AUTH",
                setting_path="api.host",
                remediation="Either bind to localhost (host=127.0.0.1) or enable auth "
                "(auth.auth_enabled=true). To explicitly accept the risk for "
                "local development, set AIP_UNSAFE_ALLOW_PUBLIC_NO_AUTH=true.",
            )

    # 6. Public bind host + default/weak secrets => hard failure
    if public_bind:
        pg_password = _get_postgres_password(config)
        if pg_password is not None and pg_password.lower() in _WEAK_POSTGRES_PASSWORDS:
            result.add_error(
                message=f"Unsafe public bind: host={host} with a weak database password "
                f"is blocked. Public-facing services must use strong secrets.",
                code="PUBLIC_WEAK_SECRET",
                setting_path="POSTGRES_PASSWORD",
                remediation="Set POSTGRES_PASSWORD to a strong value. "
                "Example: export POSTGRES_PASSWORD=$(openssl rand -hex 32)",
            )

    # ==================================================================
    # SECRETS-IN-TOML CHECKS (warnings for both profiles)
    # Credential sovereignty: secrets should come from env vars or a
    # restricted secrets file, not from the TOML config which may be
    # checked into version control or displayed via GET /admin/config.
    # ==================================================================

    # 7. SMTP password in TOML — should use AIP_SMTP_PASSWORD env var
    smtp_password = config.get("alerting", {}).get("smtp_password", "")
    if isinstance(smtp_password, str) and smtp_password.strip():
        result.add_warning(
            message="SMTP password found in TOML config [alerting].smtp_password. "
            "Secrets in TOML risk exposure via config dumps and version control. "
            "Prefer the AIP_SMTP_PASSWORD environment variable.",
            code="SECRET_IN_TOML_SMTP_PASSWORD",
            setting_path="alerting.smtp_password",
            remediation="Remove smtp_password from TOML and set AIP_SMTP_PASSWORD env var instead.",
        )

    # 8. API keys in TOML model slots — should use AIP_<SLOT>_API_KEY env vars
    models_cfg = config.get("models", {})
    if isinstance(models_cfg, dict):
        for slot_name, slot_val in models_cfg.items():
            if not isinstance(slot_val, dict):
                continue
            api_key = slot_val.get("api_key", "")
            if isinstance(api_key, str) and api_key.strip():
                result.add_warning(
                    message=f"API key found in TOML config [models.{slot_name}].api_key. "
                    "Secrets in TOML risk exposure via config dumps and version control. "
                    f"Prefer the AIP_{slot_name.upper()}_API_KEY environment variable.",
                    code="SECRET_IN_TOML_API_KEY",
                    setting_path=f"models.{slot_name}.api_key",
                    remediation=f"Remove api_key from TOML [models.{slot_name}] and set "
                    f"AIP_{slot_name.upper()}_API_KEY env var instead.",
                )

    # 9. Postgres password in TOML — should use POSTGRES_PASSWORD env var
    pg_password_toml = config.get("postgres", {}).get("password", "")
    if isinstance(pg_password_toml, str) and pg_password_toml.strip():
        result.add_warning(
            message="Postgres password found in TOML config [postgres].password. "
            "Secrets in TOML risk exposure via config dumps and version control. "
            "Prefer the POSTGRES_PASSWORD or DATABASE_URL environment variable.",
            code="SECRET_IN_TOML_POSTGRES_PASSWORD",
            setting_path="postgres.password",
            remediation="Remove password from TOML [postgres] and set POSTGRES_PASSWORD "
            "or DATABASE_URL env var instead.",
        )

    # 10. Embedding API key in TOML [embedding].api_key — legacy path
    embed_api_key = config.get("embedding", {}).get("api_key", "")
    if isinstance(embed_api_key, str) and embed_api_key.strip():
        result.add_warning(
            message="API key found in TOML config [embedding].api_key. "
            "Secrets in TOML risk exposure via config dumps and version control. "
            "Prefer the AIP_EMBEDDING_API_KEY or AIP_OPENAI_API_KEY environment variable.",
            code="SECRET_IN_TOML_EMBEDDING_API_KEY",
            setting_path="embedding.api_key",
            remediation="Remove api_key from TOML [embedding] and set AIP_EMBEDDING_API_KEY "
            "or AIP_OPENAI_API_KEY env var instead.",
        )

    return result


__all__ = [
    "ConfigValidationError",
    "DogfoodMode",
    "DogfoodReadinessCheck",
    "RuntimeMode",
    "ValidationResult",
    "get_dogfood_mode",
    "get_runtime_mode",
    "validate_config",
    "validate_dogfood_readiness",
]
