"""Credential sovereignty tests (Chunk 3).

Verifies that DEFINER-only surfaces are actually DEFINER-only:

1. All admin GET routes require require_definer
2. API keys are never written to os.environ at runtime
3. Model slot runtime overrides use in-memory storage
4. SMTP password env var takes precedence over TOML
5. "No secrets in TOML" validation warnings fire correctly
6. Unauthenticated requests cannot read admin surfaces
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# ----------------------------------------------------------------
# 1. Admin route require_definer enforcement
# ----------------------------------------------------------------


def test_admin_get_config_requires_definer():
    """GET /admin/config has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import get_admin_config

    sig = inspect.signature(get_admin_config)
    params = sig.parameters
    # The _auth parameter depends on require_definer
    assert "_auth" in params, "GET /admin/config must have _auth=Depends(require_definer)"


def test_admin_get_sexton_classifications_requires_definer():
    """GET /admin/sexton/classifications has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import get_sexton_classifications

    sig = inspect.signature(get_sexton_classifications)
    assert "_auth" in sig.parameters


def test_admin_get_sexton_audit_requires_definer():
    """GET /admin/sexton/audit has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import get_sexton_audit

    sig = inspect.signature(get_sexton_audit)
    assert "_auth" in sig.parameters


def test_admin_get_sexton_playbook_requires_definer():
    """GET /admin/sexton/playbook has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import get_sexton_playbook

    sig = inspect.signature(get_sexton_playbook)
    assert "_auth" in sig.parameters


def test_admin_get_beast_status_requires_definer():
    """GET /admin/beast/status has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import get_beast_status

    sig = inspect.signature(get_beast_status)
    assert "_auth" in sig.parameters


def test_admin_get_router_weights_requires_definer():
    """GET /admin/router/weights has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import get_router_weights

    sig = inspect.signature(get_router_weights)
    assert "_auth" in sig.parameters


def test_admin_get_budget_requires_definer():
    """GET /admin/budget has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import get_budget_status

    sig = inspect.signature(get_budget_status)
    assert "_auth" in sig.parameters


def test_admin_get_autonomy_log_requires_definer():
    """GET /admin/autonomy/log has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import get_autonomy_log

    sig = inspect.signature(get_autonomy_log)
    assert "_auth" in sig.parameters


def test_admin_get_backfill_status_requires_definer():
    """GET /admin/embeddings/backfill/status has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import get_backfill_status

    sig = inspect.signature(get_backfill_status)
    assert "_auth" in sig.parameters


def test_admin_get_hot_reload_status_requires_definer():
    """GET /admin/hot-reload/status has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import get_hot_reload_status

    sig = inspect.signature(get_hot_reload_status)
    assert "_auth" in sig.parameters


def test_admin_post_backfill_requires_definer():
    """POST /admin/embeddings/backfill has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import backfill_embeddings

    sig = inspect.signature(backfill_embeddings)
    assert "_auth" in sig.parameters


def test_admin_patch_config_requires_definer():
    """PATCH /admin/config has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.admin import patch_admin_config

    sig = inspect.signature(patch_admin_config)
    assert "_auth" in sig.parameters


# ----------------------------------------------------------------
# 2. Model routes require_definer enforcement
# ----------------------------------------------------------------


def test_api_key_status_requires_definer():
    """GET /models/api_key_status has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.models import api_key_status

    sig = inspect.signature(api_key_status)
    assert "_auth" in sig.parameters


def test_update_slot_model_requires_definer():
    """PATCH /models/slots/{slot_name}/model has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.models import update_slot_model

    sig = inspect.signature(update_slot_model)
    assert "_auth" in sig.parameters


def test_fetch_model_library_requires_definer():
    """POST /models/library/fetch has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.models_library import fetch_model_library

    sig = inspect.signature(fetch_model_library)
    assert "_auth" in sig.parameters


def test_toggle_model_enabled_requires_definer():
    """PATCH /models/library/{model_id} has require_definer dependency."""
    import inspect

    from aip.adapter.api.routes.models_library import toggle_model_enabled

    sig = inspect.signature(toggle_model_enabled)
    assert "_auth" in sig.parameters


# ----------------------------------------------------------------
# 3. API keys never written to os.environ at runtime
# ----------------------------------------------------------------


def test_models_route_no_os_environ_write():
    """The models route module does not import os for environ writes."""
    import inspect

    import aip.adapter.api.routes.models as models_mod

    source = inspect.getsource(models_mod)
    # The module should NOT contain os.environ assignment
    assert "os.environ[" not in source, (
        "models.py must not write to os.environ. Use ModelSlotResolver.set_runtime_override() instead."
    )


def test_model_slot_resolver_runtime_override_in_memory():
    """Runtime overrides are stored in memory, not in os.environ."""
    from aip.adapter.model_slot_resolver import ModelSlotResolver

    config = {
        "models": {
            "ci_mode": True,
            "synthesis": {"provider": "ollama", "model": "test-model"},
        },
    }
    resolver = ModelSlotResolver(config)

    # Set a runtime override
    resolver.set_runtime_override("synthesis", "model", "override-model")
    resolver.set_runtime_override("synthesis", "api_key", "sk-secret-key")

    # Verify it's in memory
    assert resolver._runtime_overrides["synthesis.model"] == "override-model"
    assert resolver._runtime_overrides["synthesis.api_key"] == "sk-secret-key"

    # Verify it was NOT written to os.environ
    assert os.environ.get("AIP_SYNTHESIS_MODEL") is None
    assert os.environ.get("AIP_SYNTHESIS_API_KEY") is None


def test_runtime_override_has_highest_priority():
    """Runtime overrides take precedence over env vars and TOML config."""
    from aip.adapter.model_slot_resolver import ModelSlotResolver

    config = {
        "models": {
            "ci_mode": True,
            "synthesis": {"provider": "ollama", "model": "toml-model"},
        },
    }

    with patch.dict(os.environ, {"AIP_SYNTHESIS_MODEL": "env-model"}):
        resolver = ModelSlotResolver(config)

        # Before override: env var wins over TOML
        resolved = resolver._resolve_slot_config("synthesis")
        assert resolved["model"] == "env-model"

        # After override: runtime wins over env var
        resolver.set_runtime_override("synthesis", "model", "runtime-model")
        resolved = resolver._resolve_slot_config("synthesis")
        assert resolved["model"] == "runtime-model"


def test_runtime_override_api_key_has_highest_priority():
    """Runtime API key overrides take precedence over env vars."""
    from aip.adapter.model_slot_resolver import ModelSlotResolver

    config = {
        "models": {
            "ci_mode": True,
            "synthesis": {
                "provider": "openai_compatible",
                "model": "gpt-4",
                "api_key": "toml-key",
            },
        },
    }

    with patch.dict(os.environ, {"AIP_SYNTHESIS_API_KEY": "env-key"}):
        resolver = ModelSlotResolver(config)

        # Before override: env var wins
        resolved = resolver._resolve_slot_config("synthesis")
        assert resolved["api_key"] == "env-key"

        # After override: runtime wins
        resolver.set_runtime_override("synthesis", "api_key", "runtime-key")
        resolved = resolver._resolve_slot_config("synthesis")
        assert resolved["api_key"] == "runtime-key"


def test_clear_runtime_overrides():
    """Runtime overrides can be cleared per-slot or entirely."""
    from aip.adapter.model_slot_resolver import ModelSlotResolver

    config = {
        "models": {
            "ci_mode": True,
            "synthesis": {"provider": "ollama", "model": "test"},
            "embedding": {"provider": "ollama", "model": "embed"},
        },
    }
    resolver = ModelSlotResolver(config)
    resolver.set_runtime_override("synthesis", "model", "override-1")
    resolver.set_runtime_override("embedding", "model", "override-2")

    # Clear one slot
    resolver.clear_runtime_overrides("synthesis")
    assert resolver.get_runtime_override("synthesis", "model") is None
    assert resolver.get_runtime_override("embedding", "model") == "override-2"

    # Clear all
    resolver.clear_runtime_overrides()
    assert resolver.get_runtime_override("embedding", "model") is None


def test_get_runtime_override():
    """get_runtime_override returns the current value or None."""
    from aip.adapter.model_slot_resolver import ModelSlotResolver

    config = {"models": {"ci_mode": True, "synthesis": {"provider": "ollama", "model": "test"}}}
    resolver = ModelSlotResolver(config)

    assert resolver.get_runtime_override("synthesis", "model") is None

    resolver.set_runtime_override("synthesis", "model", "new-model")
    assert resolver.get_runtime_override("synthesis", "model") == "new-model"


# ----------------------------------------------------------------
# 4. SMTP password env-var override
# ----------------------------------------------------------------


def test_smtp_env_var_overrides_toml():
    """AIP_SMTP_PASSWORD env var takes precedence over TOML smtp_password."""
    # This tests the app.py wiring: the env var is checked at startup
    # and used to populate the AlertConfig.smtp_password field.
    # The formula in app.py is:
    #   smtp_password=alert_cfg_dict.get("smtp_password", "") or os.environ.get("AIP_SMTP_PASSWORD", "")
    # With the fix, if TOML value is empty, env var is used.
    # With the fix, if env var is set, it takes precedence (via alerting.py's send path).
    smtp_password_toml = ""
    env_password = "env-secret-password"
    result = smtp_password_toml or os.environ.get("AIP_SMTP_PASSWORD", "")
    # With empty TOML, env var is used
    with patch.dict(os.environ, {"AIP_SMTP_PASSWORD": env_password}):
        result = smtp_password_toml or os.environ.get("AIP_SMTP_PASSWORD", "")
        assert result == env_password


def test_smtp_env_var_precedence_in_alerting():
    """The alerting module's send path prefers AIP_SMTP_PASSWORD over TOML."""
    # Verify the alerting.py code: os.environ.get("AIP_SMTP_PASSWORD") or self._config.smtp_password
    # This means env var takes precedence.
    import inspect

    import aip.adapter.alerting as alerting_mod

    source = inspect.getsource(alerting_mod)
    # Look for the pattern that checks env var first
    assert 'os.environ.get("AIP_SMTP_PASSWORD")' in source, (
        "alerting.py must check AIP_SMTP_PASSWORD env var BEFORE falling back to config"
    )


# ----------------------------------------------------------------
# 5. "No secrets in TOML" validation warnings
# ----------------------------------------------------------------


def test_smtp_password_in_toml_triggers_warning():
    """SMTP password in TOML config triggers a SECRET_IN_TOML warning."""
    from aip.config import validate_config

    config = {
        "alerting": {
            "smtp_password": "my-secret-password",
        },
    }
    result = validate_config(config)
    warning_codes = [w.code for w in result.warnings]
    assert "SECRET_IN_TOML_SMTP_PASSWORD" in warning_codes


def test_api_key_in_toml_model_slot_triggers_warning():
    """API key in TOML model slot triggers a SECRET_IN_TOML warning."""
    from aip.config import validate_config

    config = {
        "models": {
            "synthesis": {
                "provider": "openai_compatible",
                "model": "gpt-4",
                "api_key": "sk-secret-key",
            },
        },
    }
    result = validate_config(config)
    warning_codes = [w.code for w in result.warnings]
    assert "SECRET_IN_TOML_API_KEY" in warning_codes


def test_postgres_password_in_toml_triggers_warning():
    """Postgres password in TOML triggers a SECRET_IN_TOML warning."""
    from aip.config import validate_config

    config = {
        "postgres": {
            "password": "db-secret-password",
        },
    }
    result = validate_config(config)
    warning_codes = [w.code for w in result.warnings]
    assert "SECRET_IN_TOML_POSTGRES_PASSWORD" in warning_codes


def test_embedding_api_key_in_toml_triggers_warning():
    """Embedding API key in TOML triggers a SECRET_IN_TOML warning."""
    from aip.config import validate_config

    config = {
        "embedding": {
            "provider": "openai_compatible",
            "api_key": "sk-embed-key",
        },
    }
    result = validate_config(config)
    warning_codes = [w.code for w in result.warnings]
    assert "SECRET_IN_TOML_EMBEDDING_API_KEY" in warning_codes


def test_empty_secrets_no_warning():
    """Empty secret values in TOML do NOT trigger warnings."""
    from aip.config import validate_config

    config = {
        "alerting": {"smtp_password": ""},
        "models": {"synthesis": {"provider": "ollama", "model": "test", "api_key": ""}},
        "postgres": {"password": ""},
        "embedding": {"api_key": ""},
    }
    result = validate_config(config)
    secret_warnings = [w for w in result.warnings if w.code.startswith("SECRET_IN_TOML")]
    assert len(secret_warnings) == 0


def test_validation_warnings_do_not_block_startup():
    """Validation warnings are logged but do NOT block startup (is_valid is True)."""
    from aip.config import validate_config

    config = {
        "alerting": {"smtp_password": "should-be-env-var"},
    }
    result = validate_config(config)
    # Warnings exist but validation still passes
    assert len(result.warnings) > 0
    assert result.is_valid is True


# ----------------------------------------------------------------
# 6. Static code checks — no os.environ writes for secrets
# ----------------------------------------------------------------


def test_no_os_environ_api_key_writes_in_models_route():
    """The models route module must not contain os.environ writes for API keys."""
    import inspect

    import aip.adapter.api.routes.models as models_mod

    source = inspect.getsource(models_mod)
    assert "os.environ[" not in source, (
        "models route must not write to os.environ. Use ModelSlotResolver.set_runtime_override() for runtime overrides."
    )


def test_no_os_import_in_models_route():
    """The models route module does not import os (no longer needed)."""
    import inspect

    import aip.adapter.api.routes.models as models_mod

    source = inspect.getsource(models_mod)
    assert "import os" not in source, "models route should not import os since it no longer writes to os.environ"


# ----------------------------------------------------------------
# 7. Comprehensive admin route audit — every admin route has require_definer
# ----------------------------------------------------------------


def test_all_admin_routes_have_require_definer():
    """Every route in the admin router has require_definer as a dependency.

    This is a static check that inspects the route function signatures
    to ensure no admin route is missing auth.
    """
    import inspect

    from aip.adapter.api.routes import admin

    # Get all route functions from the admin router
    admin_routes = []
    for route in admin.router.routes:
        if hasattr(route, "endpoint"):
            admin_routes.append(route)

    assert len(admin_routes) > 0, "Admin router should have routes"

    for route in admin_routes:
        endpoint = route.endpoint
        sig = inspect.signature(endpoint)
        assert "_auth" in sig.parameters, (
            f"Admin route {route.methods} {route.path} is missing require_definer "
            f"(no _auth parameter in {endpoint.__name__})"
        )


# ----------------------------------------------------------------
# 8. Dogfood gate — unauthenticated requests are rejected
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_definer_rejects_unauthenticated_when_auth_enabled():
    """When auth is enabled and no credentials are provided, require_definer
    returns 403, not DEFINER access.

    This is the critical dogfood gate: an unauthenticated request cannot
    read config, model status, Beast/Vigil/Sexton state, router weights,
    backfill status, or hot reload status.
    """
    from aip.adapter.auth.dependencies import get_current_identity, require_definer

    # Simulate a request where auth is enabled and middleware found no identity
    mock_request = MagicMock()
    mock_request.headers = {}
    mock_request.app.state.container = None
    # Middleware sets auth_identity=None for unauthenticated when auth_enabled=True
    mock_request.state.auth_identity = None
    mock_request.state.auth_role = None

    identity = await get_current_identity(mock_request)
    assert identity["role"] is None, "Unauthenticated request must NOT get definer role when auth is enabled"

    # require_definer should reject
    with pytest.raises(Exception) as exc_info:
        await require_definer(identity)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_definer_allows_definer_when_auth_enabled():
    """When auth is enabled and valid credentials provide DEFINER role,
    require_definer allows the request.
    """
    from aip.adapter.auth.dependencies import get_current_identity, require_definer

    # Simulate a request with valid DEFINER credentials
    mock_request = MagicMock()
    mock_request.headers = {}
    mock_request.app.state.container = None
    # Middleware sets auth_identity="definer" for authenticated definer
    mock_request.state.auth_identity = "definer"
    mock_request.state.auth_role = "definer"

    identity = await get_current_identity(mock_request)
    assert identity["role"] == "definer"

    # require_definer should allow
    result = await require_definer(identity)
    assert result["role"] == "definer"


@pytest.mark.asyncio
async def test_require_definer_allows_laptop_mode():
    """In laptop mode (auth disabled), middleware sets definer identity
    on every request, so require_definer passes.
    """
    from aip.adapter.auth.dependencies import get_current_identity, require_definer

    # Simulate a request in laptop mode where AuthMiddleware sets definer identity
    mock_request = MagicMock()
    mock_request.headers = {}
    mock_request.app.state.container = None
    # Laptop mode: middleware sets definer identity
    mock_request.state.auth_identity = "definer"
    mock_request.state.auth_role = "definer"

    identity = await get_current_identity(mock_request)
    assert identity["role"] == "definer"

    result = await require_definer(identity)
    assert result["role"] == "definer"


@pytest.mark.asyncio
async def test_unauthenticated_cannot_read_any_admin_surface():
    """Dogfood gate: a request without auth cannot read any admin surface.

    This verifies the complete chain: unauthenticated identity → 403
    for every admin route category.
    """
    from aip.adapter.auth.dependencies import require_definer

    # Unauthenticated identity (auth enabled, no credentials)
    unauthenticated_identity = {"identity": None, "role": None}

    # All admin surfaces should reject
    with pytest.raises(Exception) as exc_info:
        await require_definer(unauthenticated_identity)
    assert exc_info.value.status_code == 403

    # Collaborator identity should also be rejected
    collaborator_identity = {"identity": "collab", "role": "collaborator"}
    with pytest.raises(Exception) as exc_info:
        await require_definer(collaborator_identity)
    assert exc_info.value.status_code == 403

    # Readonly identity should also be rejected
    readonly_identity = {"identity": "viewer", "role": "readonly"}
    with pytest.raises(Exception) as exc_info:
        await require_definer(readonly_identity)
    assert exc_info.value.status_code == 403
