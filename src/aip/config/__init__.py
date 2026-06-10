"""AIP configuration validation and runtime mode detection.

This module provides hard validation so that production mode cannot start
with unsafe defaults, weak secrets, fixture behavior, or public
unauthenticated API exposure.

Key types:
    RuntimeMode — LAPTOP or PRODUCTION
    ConfigValidationError — structured error with code, setting path, remediation
    validate_config() — run all safety checks before app/CLI startup
    get_runtime_mode() — deterministic profile detection from config + env
"""

from __future__ import annotations

import os
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
    "RuntimeMode",
    "ValidationResult",
    "get_runtime_mode",
    "validate_config",
]
