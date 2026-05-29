"""Tests for config validation — production safety gates.

Validates that unsafe production configurations fail programmatically
and safe laptop/dev configurations pass.
"""

from __future__ import annotations

import os

import pytest

from aip.config import (
    ConfigValidationError,
    RuntimeMode,
    ValidationResult,
    get_runtime_mode,
    validate_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _laptop_config(**overrides) -> dict:
    """Create a minimal valid laptop config with optional overrides."""
    cfg = {
        "api": {"host": "127.0.0.1", "port": 8000},
        "auth": {"auth_enabled": False},
        "deployment": {
            "profile_name": "laptop",
            "auth_enabled": False,
            "vector_backend": "sqlite_vss",
            "model_provider": "ollama",
        },
        "embedding": {"provider": "fake"},
    }
    for key, value in overrides.items():
        if key in cfg and isinstance(cfg[key], dict) and isinstance(value, dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


def _production_config(**overrides) -> dict:
    """Create a minimal valid production config with optional overrides."""
    cfg = {
        "api": {"host": "0.0.0.0", "port": 8000},
        "auth": {"auth_enabled": True},
        "deployment": {
            "profile_name": "production",
            "auth_enabled": True,
            "vector_backend": "pgvector",
            "model_provider": "api",
        },
        "embedding": {"provider": "api"},
    }
    for key, value in overrides.items():
        if key in cfg and isinstance(cfg[key], dict) and isinstance(value, dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


class TestLaptopAllowed:
    """Safe laptop/dev configurations should pass validation."""

    def test_laptop_localhost_auth_disabled(self):
        config = _laptop_config()
        result = validate_config(config)
        assert result.is_valid, f"Expected valid, got errors: {result.errors}"

    def test_laptop_localhost_auth_enabled(self):
        config = _laptop_config(
            auth={"auth_enabled": True},
            deployment={
                "profile_name": "laptop",
                "auth_enabled": True,
                "vector_backend": "sqlite_vss",
                "model_provider": "ollama",
            },
        )
        result = validate_config(config)
        assert result.is_valid

    def test_laptop_fixture_provider(self):
        config = _laptop_config(embedding={"provider": "fake"})
        result = validate_config(config)
        assert result.is_valid

    def test_laptop_no_postgres_password(self):
        config = _laptop_config()
        result = validate_config(config)
        assert result.is_valid


class TestPublicBindNoAuth:
    """Public bind + auth disabled must fail unless explicit override."""

    def test_public_bind_auth_disabled_fails(self):
        config = _laptop_config(api={"host": "0.0.0.0"})
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PUBLIC_NO_AUTH" for e in result.errors)

    def test_public_bind_auth_disabled_with_override(self, monkeypatch):
        monkeypatch.setenv("AIP_UNSAFE_ALLOW_PUBLIC_NO_AUTH", "true")
        config = _laptop_config(api={"host": "0.0.0.0"})
        result = validate_config(config)
        assert result.is_valid

    def test_ipv6_public_bind_auth_disabled_fails(self):
        config = _laptop_config(api={"host": "::"})
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PUBLIC_NO_AUTH" for e in result.errors)

    def test_public_bind_auth_enabled_passes(self):
        config = _laptop_config(
            api={"host": "0.0.0.0"},
            auth={"auth_enabled": True},
            deployment={
                "profile_name": "laptop",
                "auth_enabled": True,
                "vector_backend": "sqlite_vss",
                "model_provider": "ollama",
            },
        )
        result = validate_config(config)
        assert result.is_valid


class TestProductionAuthRequired:
    """Production mode must have auth enabled."""

    def test_production_auth_disabled_fails(self):
        config = _production_config(
            auth={"auth_enabled": False},
            deployment={
                "profile_name": "production",
                "auth_enabled": False,
                "vector_backend": "pgvector",
                "model_provider": "api",
            },
        )
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PROD_AUTH_DISABLED" for e in result.errors)

    def test_production_deployment_auth_disabled_fails(self):
        config = _production_config(
            auth={"auth_enabled": False},
            deployment={
                "profile_name": "production",
                "auth_enabled": False,
                "vector_backend": "pgvector",
                "model_provider": "api",
            },
        )
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PROD_AUTH_DISABLED" for e in result.errors)

    def test_production_auth_enabled_passes(self):
        config = _production_config()
        os.environ["POSTGRES_PASSWORD"] = "a_strong_production_password_12345"
        try:
            result = validate_config(config)
            assert result.is_valid, f"Expected valid, got errors: {result.errors}"
        finally:
            del os.environ["POSTGRES_PASSWORD"]


class TestProductionValid:
    """Valid production configurations should pass all checks."""

    def test_production_valid_config(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_PASSWORD", "a_strong_production_password_abc123")
        config = _production_config()
        result = validate_config(config)
        assert result.is_valid

    def test_production_with_database_url(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://aip:my_secure_password@postgres:5432/aip")
        config = _production_config()
        result = validate_config(config)
        assert result.is_valid


class TestProductionMissingPassword:
    """Production must have a database password set."""

    def test_production_missing_password_fails(self, monkeypatch):
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        config = _production_config()
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PROD_MISSING_DB_PASSWORD" for e in result.errors)

    def test_production_empty_password_fails(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_PASSWORD", "")
        config = _production_config()
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code in ("PROD_MISSING_DB_PASSWORD", "PROD_WEAK_DB_PASSWORD") for e in result.errors)


class TestProductionWeakPassword:
    """Production must not use default or weak passwords."""

    @pytest.mark.parametrize(
        "weak_password",
        ["changeme", "password", "postgres", "secret", "default", "admin", "aip", "123456"],
    )
    def test_production_weak_password_fails(self, weak_password, monkeypatch):
        monkeypatch.setenv("POSTGRES_PASSWORD", weak_password)
        config = _production_config()
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PROD_WEAK_DB_PASSWORD" for e in result.errors)

    def test_production_strong_password_passes(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_PASSWORD", "a_very_secure_random_hex_password_abc123xyz789")
        config = _production_config()
        result = validate_config(config)
        assert result.is_valid


class TestProductionFixtureBlocked:
    """Production must not use fixture/CI providers."""

    @pytest.mark.parametrize("fixture_provider", ["fake", "mock", "ci", "fixture"])
    def test_production_fixture_embedding_fails(self, fixture_provider, monkeypatch):
        monkeypatch.setenv("POSTGRES_PASSWORD", "strong_password_12345")
        config = _production_config(embedding={"provider": fixture_provider})
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PROD_FIXTURE_PROVIDER" for e in result.errors)

    @pytest.mark.parametrize("fixture_provider", ["fake", "mock", "ci", "fixture"])
    def test_production_fixture_model_provider_fails(self, fixture_provider, monkeypatch):
        monkeypatch.setenv("POSTGRES_PASSWORD", "strong_password_12345")
        config = _production_config(
            deployment={
                "profile_name": "production",
                "auth_enabled": True,
                "vector_backend": "pgvector",
                "model_provider": fixture_provider,
            }
        )
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PROD_FIXTURE_MODEL_PROVIDER" for e in result.errors)

    def test_production_real_embedding_provider_passes(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_PASSWORD", "strong_password_12345")
        config = _production_config(embedding={"provider": "api"})
        result = validate_config(config)
        assert result.is_valid

    def test_production_ollama_provider_passes(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_PASSWORD", "strong_password_12345")
        config = _production_config(embedding={"provider": "ollama"})
        result = validate_config(config)
        assert result.is_valid


class TestAPIValidationIntegration:
    """Verify create_app() calls validate_config and fails on unsafe configs."""

    def test_create_app_rejects_unsafe_production_config(self, monkeypatch):
        from aip.adapter.api.app import create_app

        monkeypatch.setenv("AIP_PROFILE", "production")
        unsafe_config = {
            "api": {"host": "0.0.0.0", "port": 8000},
            "auth": {"auth_enabled": False},
            "deployment": {
                "profile_name": "production",
                "auth_enabled": False,
                "vector_backend": "pgvector",
                "model_provider": "api",
            },
            "embedding": {"provider": "api"},
        }
        with pytest.raises(ConfigValidationError) as exc_info:
            create_app(unsafe_config)
        assert "PROD_AUTH_DISABLED" in str(exc_info.value) or exc_info.value.code == "PROD_AUTH_DISABLED"

    def test_create_app_accepts_safe_laptop_config(self):
        from aip.adapter.api.app import create_app

        safe_config = _laptop_config()
        app = create_app(safe_config)
        assert app is not None
        assert app.title == "AIP 0.1 Surfaces"

    def test_create_app_accepts_safe_production_config(self, monkeypatch):
        from aip.adapter.api.app import create_app

        monkeypatch.setenv("POSTGRES_PASSWORD", "a_strong_password_for_production_abc")
        safe_config = _production_config()
        app = create_app(safe_config)
        assert app is not None

    def test_create_app_rejects_public_bind_no_auth(self):
        from aip.adapter.api.app import create_app

        unsafe_config = _laptop_config(api={"host": "0.0.0.0"})
        with pytest.raises(ConfigValidationError) as exc_info:
            create_app(unsafe_config)
        assert exc_info.value.code == "PUBLIC_NO_AUTH"


class TestCLIValidationIntegration:
    """Verify CLI validation command works."""

    def test_validate_config_function_exists(self):
        from aip.cli.config import validate_config_file

        assert callable(validate_config_file)

    def test_cli_validate_command_registered(self):
        from aip.cli.main import cli

        assert "validate" in cli.commands

    def test_cli_validate_laptop_config(self, tmp_path, monkeypatch):
        from aip.cli.config import validate_config_file

        config_content = (
            '[api]\nhost = "127.0.0.1"\nport = 8000\n\n'
            "[auth]\nauth_enabled = false\n\n"
            '[deployment]\nprofile_name = "laptop"\n'
            'auth_enabled = false\nvector_backend = "sqlite_vss"\n'
            'model_provider = "ollama"\n\n'
            '[embedding]\nprovider = "fake"\n'
        )
        config_path = tmp_path / "aip.config.toml"
        config_path.write_text(config_content)
        monkeypatch.delenv("AIP_PROFILE", raising=False)
        result = validate_config_file(config_path)
        assert result is True

    def test_cli_validate_rejects_unsafe_production_config(self, tmp_path, monkeypatch):
        from aip.cli.config import validate_config_file

        config_content = (
            '[api]\nhost = "0.0.0.0"\nport = 8000\n\n'
            "[auth]\nauth_enabled = false\n\n"
            '[deployment]\nprofile_name = "production"\n'
            'auth_enabled = false\nvector_backend = "pgvector"\n'
            'model_provider = "api"\n\n'
            '[embedding]\nprovider = "api"\n'
        )
        config_path = tmp_path / "aip.config.toml"
        config_path.write_text(config_content)
        monkeypatch.setenv("AIP_PROFILE", "production")
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
        result = validate_config_file(config_path)
        assert result is False


class TestDockerComposeSafety:
    """Verify Docker compose files have no default/changeme password fallbacks."""

    def test_production_compose_no_changeme(self):
        from pathlib import Path

        compose_path = Path(__file__).parent.parent / "deploy" / "docker-compose.production.yml"
        if compose_path.exists():
            content = compose_path.read_text()
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if ":-changeme" in stripped or ':-"changeme"' in stripped or ":-'changeme'" in stripped:
                    pytest.fail(
                        "Found 'changeme' default fallback in production Docker compose. "
                        "Use ${VAR:?error} syntax instead."
                    )

    def test_unified_compose_no_changeme(self):
        from pathlib import Path

        compose_path = Path(__file__).parent.parent / "deploy" / "docker-compose.yml"
        if compose_path.exists():
            content = compose_path.read_text()
            assert "changeme" not in content.lower(), (
                "Found 'changeme' in unified Docker compose — no default password fallbacks allowed"
            )

    def test_production_compose_requires_password(self):
        from pathlib import Path

        compose_path = Path(__file__).parent.parent / "deploy" / "docker-compose.production.yml"
        if compose_path.exists():
            content = compose_path.read_text()
            assert ":?" in content, (
                "Production Docker compose must use ${VAR:?error} syntax to require POSTGRES_PASSWORD"
            )

    def test_laptop_compose_no_changeme(self):
        from pathlib import Path

        compose_path = Path(__file__).parent.parent / "deploy" / "docker-compose.laptop.yml"
        if compose_path.exists():
            content = compose_path.read_text()
            assert "changeme" not in content.lower(), "Found 'changeme' in laptop Docker compose"


class TestRuntimeModeDetection:
    """Verify get_runtime_mode works correctly."""

    def test_config_profile_production(self):
        config = {"deployment": {"profile_name": "production"}}
        assert get_runtime_mode(config) == RuntimeMode.PRODUCTION

    def test_config_profile_laptop(self):
        config = {"deployment": {"profile_name": "laptop"}}
        assert get_runtime_mode(config) == RuntimeMode.LAPTOP

    def test_env_var_production(self, monkeypatch):
        monkeypatch.setenv("AIP_PROFILE", "production")
        config = {}
        assert get_runtime_mode(config) == RuntimeMode.PRODUCTION

    def test_env_var_laptop(self, monkeypatch):
        monkeypatch.setenv("AIP_PROFILE", "laptop")
        config = {}
        assert get_runtime_mode(config) == RuntimeMode.LAPTOP

    def test_config_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("AIP_PROFILE", "laptop")
        config = {"deployment": {"profile_name": "production"}}
        assert get_runtime_mode(config) == RuntimeMode.PRODUCTION

    def test_default_is_laptop(self, monkeypatch):
        monkeypatch.delenv("AIP_PROFILE", raising=False)
        config = {}
        assert get_runtime_mode(config) == RuntimeMode.LAPTOP

    def test_ci_env_does_not_affect_runtime_mode(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        monkeypatch.delenv("AIP_PROFILE", raising=False)
        config = {"deployment": {"profile_name": "laptop"}}
        assert get_runtime_mode(config) == RuntimeMode.LAPTOP

    def test_case_insensitive_profile(self):
        config = {"deployment": {"profile_name": "Production"}}
        assert get_runtime_mode(config) == RuntimeMode.PRODUCTION


class TestConfigValidationError:
    """Verify error structure and messages."""

    def test_error_has_all_fields(self):
        err = ConfigValidationError(
            message="test message",
            code="TEST_CODE",
            setting_path="test.setting",
            remediation="fix it",
        )
        assert err.message == "test message"
        assert err.code == "TEST_CODE"
        assert err.setting_path == "test.setting"
        assert err.remediation == "fix it"

    def test_error_str_includes_remediation(self):
        err = ConfigValidationError(
            message="Bad config", code="BAD", setting_path="foo.bar", remediation="Set foo.bar to a safe value"
        )
        err_str = str(err)
        assert "BAD" in err_str
        assert "foo.bar" in err_str
        assert "Set foo.bar to a safe value" in err_str

    def test_error_does_not_leak_secrets(self):
        config = _production_config(auth={"auth_enabled": False})
        result = validate_config(config)
        if result.errors:
            for err in result.errors:
                assert (
                    "POSTGRES_PASSWORD" not in err.message
                    or "missing" in err.message.lower()
                    or "weak" in err.message.lower()
                )
                assert "changeme" not in err.remediation.lower() or "do not use" in err.remediation.lower()


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_config_is_laptop(self):
        result = validate_config({})
        assert result.is_valid

    def test_production_with_only_env_profile(self, monkeypatch):
        monkeypatch.setenv("AIP_PROFILE", "production")
        config = {"api": {"host": "0.0.0.0"}, "auth": {"auth_enabled": False}, "embedding": {"provider": "api"}}
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PROD_AUTH_DISABLED" for e in result.errors)

    def test_production_localhost_auth_disabled_still_fails(self, monkeypatch):
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
        config = _production_config(
            api={"host": "127.0.0.1"},
            auth={"auth_enabled": False},
            deployment={
                "profile_name": "production",
                "auth_enabled": False,
                "vector_backend": "pgvector",
                "model_provider": "api",
            },
        )
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PROD_AUTH_DISABLED" for e in result.errors)

    def test_validation_result_aggregates_multiple_errors(self, monkeypatch):
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
        config = _production_config(
            auth={"auth_enabled": False},
            deployment={
                "profile_name": "production",
                "auth_enabled": False,
                "vector_backend": "pgvector",
                "model_provider": "api",
            },
            embedding={"provider": "fake"},
        )
        result = validate_config(config)
        assert not result.is_valid
        error_codes = {e.code for e in result.errors}
        assert len(result.errors) >= 2
        assert "PROD_AUTH_DISABLED" in error_codes
        assert "PROD_FIXTURE_PROVIDER" in error_codes

    def test_public_bind_weak_password_fails(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_PASSWORD", "changeme")
        config = _laptop_config(
            api={"host": "0.0.0.0"},
            auth={"auth_enabled": True},
            deployment={
                "profile_name": "laptop",
                "auth_enabled": True,
                "vector_backend": "sqlite_vss",
                "model_provider": "ollama",
            },
        )
        result = validate_config(config)
        assert not result.is_valid
        assert any(e.code == "PUBLIC_WEAK_SECRET" for e in result.errors)

    def test_raise_if_invalid_raises_first_error(self):
        result = ValidationResult()
        result.add_error("error1", "CODE1", "path1", "fix1")
        result.add_error("error2", "CODE2", "path2", "fix2")
        with pytest.raises(ConfigValidationError) as exc_info:
            result.raise_if_invalid()
        assert exc_info.value.code == "CODE1"
