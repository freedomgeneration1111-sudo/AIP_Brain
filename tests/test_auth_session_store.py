"""Unit tests for SqliteSessionStore — AuthStore implementation.

Covers session lifecycle, API key lifecycle, user management,
session expiration, definer sovereignty, and connection health.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aip.adapter.auth.session_store import SqliteSessionStore
from aip.foundation.schemas import AuthConfig


@pytest.fixture
async def auth_store(tmp_path):
    """Create a fresh SqliteSessionStore with a temporary database."""
    db_path = str(tmp_path / "test_auth.db")
    config = AuthConfig(session_timeout_seconds=3600)
    store = SqliteSessionStore(db_path, config)
    await store.initialize()
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestSessionCreation:
    @pytest.mark.asyncio
    async def test_create_session_returns_token(self, auth_store):
        token = await auth_store.create_session("alice", "readonly")
        assert isinstance(token, str)
        assert len(token) > 0

    @pytest.mark.asyncio
    async def test_create_session_token_is_urlsafe(self, auth_store):
        token = await auth_store.create_session("alice", "readonly")
        # token_urlsafe produces base64url-encoded bytes — only [A-Za-z0-9_-]
        assert all(c.isalnum() or c in "-_=" for c in token)

    @pytest.mark.asyncio
    async def test_create_multiple_sessions_different_tokens(self, auth_store):
        token_a = await auth_store.create_session("alice", "readonly")
        token_b = await auth_store.create_session("bob", "collaborator")
        assert token_a != token_b


class TestSessionValidation:
    @pytest.mark.asyncio
    async def test_validate_session_returns_identity(self, auth_store):
        token = await auth_store.create_session("alice", "readonly")
        result = await auth_store.validate_session(token)
        assert result is not None
        assert result["identity"] == "alice"
        assert result["role"] == "readonly"

    @pytest.mark.asyncio
    async def test_validate_session_collaborator_role(self, auth_store):
        token = await auth_store.create_session("bob", "collaborator")
        result = await auth_store.validate_session(token)
        assert result is not None
        assert result["role"] == "collaborator"

    @pytest.mark.asyncio
    async def test_validate_session_invalid_token(self, auth_store):
        result = await auth_store.validate_session("nonexistent_token")
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_session_expired(self, auth_store):
        token = await auth_store.create_session("alice", "readonly")

        # Manually expire the session by updating expires_at to the past
        conn = await auth_store._get_conn()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat() + "Z"
        await conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE session_token = ?",
            (past, token),
        )
        await conn.commit()

        result = await auth_store.validate_session(token)
        assert result is None


class TestSessionRevocation:
    @pytest.mark.asyncio
    async def test_revoke_session_makes_token_invalid(self, auth_store):
        token = await auth_store.create_session("alice", "readonly")
        # Validate it works before revocation
        assert await auth_store.validate_session(token) is not None

        await auth_store.revoke_session(token)

        result = await auth_store.validate_session(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_session_idempotent(self, auth_store):
        token = await auth_store.create_session("alice", "readonly")
        await auth_store.revoke_session(token)
        # Revoking again should not raise
        await auth_store.revoke_session(token)

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_session(self, auth_store):
        # Should not raise
        await auth_store.revoke_session("nonexistent_token")


# ---------------------------------------------------------------------------
# API key lifecycle
# ---------------------------------------------------------------------------


class TestApiKeyCreation:
    @pytest.mark.asyncio
    async def test_create_api_key_returns_raw_key(self, auth_store):
        raw_key = await auth_store.create_api_key("alice", "readonly", "test-key")
        assert isinstance(raw_key, str)
        assert len(raw_key) > 0

    @pytest.mark.asyncio
    async def test_create_api_key_stored_in_list(self, auth_store):
        await auth_store.create_api_key("alice", "readonly", "test-key")
        keys = await auth_store.list_api_keys()
        assert len(keys) == 1
        assert keys[0]["key_name"] == "test-key"
        assert keys[0]["identity"] == "alice"
        assert keys[0]["role"] == "readonly"

    @pytest.mark.asyncio
    async def test_create_multiple_api_keys(self, auth_store):
        await auth_store.create_api_key("alice", "readonly", "key-1")
        await auth_store.create_api_key("bob", "collaborator", "key-2")
        keys = await auth_store.list_api_keys()
        assert len(keys) == 2


class TestApiKeyValidation:
    @pytest.mark.asyncio
    async def test_validate_api_key_returns_identity(self, auth_store):
        raw_key = await auth_store.create_api_key("alice", "readonly", "test-key")
        result = await auth_store.validate_api_key(raw_key)
        assert result is not None
        assert result["identity"] == "alice"
        assert result["role"] == "readonly"

    @pytest.mark.asyncio
    async def test_validate_api_key_invalid_key(self, auth_store):
        result = await auth_store.validate_api_key("not_a_real_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_api_key_revoked(self, auth_store):
        raw_key = await auth_store.create_api_key("alice", "readonly", "test-key")
        await auth_store.revoke_api_key("test-key")
        result = await auth_store.validate_api_key(raw_key)
        assert result is None


class TestApiKeyRevocation:
    @pytest.mark.asyncio
    async def test_revoke_api_key_makes_key_invalid(self, auth_store):
        raw_key = await auth_store.create_api_key("alice", "readonly", "test-key")
        # Validate it works before revocation
        assert await auth_store.validate_api_key(raw_key) is not None

        await auth_store.revoke_api_key("test-key")

        result = await auth_store.validate_api_key(raw_key)
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_api_key_reflected_in_list(self, auth_store):
        await auth_store.create_api_key("alice", "readonly", "test-key")
        await auth_store.revoke_api_key("test-key")
        keys = await auth_store.list_api_keys()
        assert len(keys) == 1
        assert keys[0]["revoked"] == 1

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_api_key(self, auth_store):
        # Should not raise
        await auth_store.revoke_api_key("nonexistent_key")


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


class TestUserCreation:
    @pytest.mark.asyncio
    async def test_create_user_collaborator(self, auth_store):
        result = await auth_store.create_user("bob", "collaborator")
        assert result is True

    @pytest.mark.asyncio
    async def test_create_user_readonly(self, auth_store):
        result = await auth_store.create_user("carol", "readonly")
        assert result is True

    @pytest.mark.asyncio
    async def test_create_user_cannot_create_definer(self, auth_store):
        result = await auth_store.create_user("eve", "definer")
        assert result is False

    @pytest.mark.asyncio
    async def test_create_user_duplicate_identity(self, auth_store):
        await auth_store.create_user("bob", "collaborator")
        # INSERT OR IGNORE silently skips; SELECT still finds the existing user
        result = await auth_store.create_user("bob", "readonly")
        # The method returns True because a non-revoked user with that identity exists
        assert result is True
        # Original role should be preserved (INSERT OR IGNORE didn't update it)
        users = await auth_store.list_users()
        bob = next(u for u in users if u["identity"] == "bob")
        assert bob["role"] == "collaborator"

    @pytest.mark.asyncio
    async def test_create_user_with_password_hash(self, auth_store):
        result = await auth_store.create_user("bob", "collaborator", password_hash="hashed_pw")
        assert result is True
        users = await auth_store.list_users()
        assert len(users) == 1


class TestUserRoleUpdate:
    @pytest.mark.asyncio
    async def test_update_user_role(self, auth_store):
        await auth_store.create_user("bob", "collaborator")
        result = await auth_store.update_user_role("bob", "readonly")
        assert result is True

        users = await auth_store.list_users()
        bob = next(u for u in users if u["identity"] == "bob")
        assert bob["role"] == "readonly"

    @pytest.mark.asyncio
    async def test_update_user_role_cannot_change_definer(self, auth_store):
        # Insert a definer user directly (bypass create_user guard)
        conn = await auth_store._get_conn()
        now = datetime.now(timezone.utc).isoformat() + "Z"
        await conn.execute(
            "INSERT INTO users (identity, role, created_at) VALUES (?, ?, ?)",
            ("definer", "definer", now),
        )
        await conn.commit()

        result = await auth_store.update_user_role("definer", "collaborator")
        assert result is False

    @pytest.mark.asyncio
    async def test_update_user_role_nonexistent_user(self, auth_store):
        result = await auth_store.update_user_role("nobody", "collaborator")
        assert result is False


class TestUserRevocation:
    @pytest.mark.asyncio
    async def test_revoke_user(self, auth_store):
        await auth_store.create_user("bob", "collaborator")
        result = await auth_store.revoke_user("bob")
        assert result is True

        users = await auth_store.list_users()
        bob = next(u for u in users if u["identity"] == "bob")
        assert bob["revoked"] == 1

    @pytest.mark.asyncio
    async def test_revoke_user_cannot_revoke_definer(self, auth_store):
        # Insert a definer user directly
        conn = await auth_store._get_conn()
        now = datetime.now(timezone.utc).isoformat() + "Z"
        await conn.execute(
            "INSERT INTO users (identity, role, created_at) VALUES (?, ?, ?)",
            ("definer", "definer", now),
        )
        await conn.commit()

        result = await auth_store.revoke_user("definer")
        assert result is False

    @pytest.mark.asyncio
    async def test_revoke_user_revokes_sessions(self, auth_store):
        await auth_store.create_user("bob", "collaborator")
        token = await auth_store.create_session("bob", "collaborator")
        # Session should be valid
        assert await auth_store.validate_session(token) is not None

        await auth_store.revoke_user("bob")

        # Session should now be invalid
        result = await auth_store.validate_session(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_user_revokes_api_keys(self, auth_store):
        await auth_store.create_user("bob", "collaborator")
        raw_key = await auth_store.create_api_key("bob", "collaborator", "bob-key")
        # Key should be valid
        assert await auth_store.validate_api_key(raw_key) is not None

        await auth_store.revoke_user("bob")

        # Key should now be invalid
        result = await auth_store.validate_api_key(raw_key)
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_user(self, auth_store):
        result = await auth_store.revoke_user("nobody")
        assert result is False


# ---------------------------------------------------------------------------
# Definer identity
# ---------------------------------------------------------------------------


class TestDefinerIdentity:
    @pytest.mark.asyncio
    async def test_get_definer_identity_none_when_not_configured(self, auth_store):
        result = await auth_store.get_definer_identity()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_definer_identity_returns_definer(self, auth_store):
        # Insert a definer user directly
        conn = await auth_store._get_conn()
        now = datetime.now(timezone.utc).isoformat() + "Z"
        await conn.execute(
            "INSERT INTO users (identity, role, created_at) VALUES (?, ?, ?)",
            ("definer", "definer", now),
        )
        await conn.commit()

        result = await auth_store.get_definer_identity()
        assert result is not None
        assert result["identity"] == "definer"
        assert result["role"] == "definer"

    @pytest.mark.asyncio
    async def test_get_definer_identity_ignores_revoked(self, auth_store):
        # Insert a revoked definer
        conn = await auth_store._get_conn()
        now = datetime.now(timezone.utc).isoformat() + "Z"
        await conn.execute(
            "INSERT INTO users (identity, role, created_at, revoked) VALUES (?, ?, ?, 1)",
            ("definer", "definer", now),
        )
        await conn.commit()

        result = await auth_store.get_definer_identity()
        assert result is None


# ---------------------------------------------------------------------------
# List operations
# ---------------------------------------------------------------------------


class TestListOperations:
    @pytest.mark.asyncio
    async def test_list_api_keys_empty(self, auth_store):
        keys = await auth_store.list_api_keys()
        assert keys == []

    @pytest.mark.asyncio
    async def test_list_users_empty(self, auth_store):
        users = await auth_store.list_users()
        assert users == []

    @pytest.mark.asyncio
    async def test_list_api_keys_includes_all_fields(self, auth_store):
        await auth_store.create_api_key("alice", "readonly", "my-key")
        keys = await auth_store.list_api_keys()
        assert len(keys) == 1
        key = keys[0]
        assert "key_name" in key
        assert "identity" in key
        assert "role" in key
        assert "created_at" in key
        assert "last_used_at" in key
        assert "revoked" in key

    @pytest.mark.asyncio
    async def test_list_users_includes_all_fields(self, auth_store):
        await auth_store.create_user("bob", "collaborator")
        users = await auth_store.list_users()
        assert len(users) == 1
        user = users[0]
        assert "identity" in user
        assert "role" in user
        assert "created_at" in user
        assert "last_active_at" in user
        assert "revoked" in user


# ---------------------------------------------------------------------------
# Connection health (StoreHealthMixin)
# ---------------------------------------------------------------------------


class TestConnectionHealth:
    @pytest.mark.asyncio
    async def test_connection_health_before_any_op(self, auth_store):
        # initialize() calls _ensure_tables via a separate connection,
        # not the persistent one, so health may show not connected yet
        health = auth_store.connection_health()
        assert health["store_type"] == "SqliteSessionStore"
        assert "connected" in health
        assert "tables_ready" in health
        assert "db_path" in health

    @pytest.mark.asyncio
    async def test_connection_health_after_op(self, auth_store):
        # Trigger persistent connection via an operation
        await auth_store.create_session("alice", "readonly")

        health = auth_store.connection_health()
        assert health["store_type"] == "SqliteSessionStore"
        assert health["connected"] is True
        assert health["tables_ready"] is True
        assert health["connection_age_seconds"] >= 0
        assert health["resets"] == 0
        assert "seconds_since_last_reset" in health
        assert "seconds_since_last_op" in health
        assert "total_ops" in health
        assert "avg_op_latency_ms" in health
        assert health["db_path"].endswith("test_auth.db")

    @pytest.mark.asyncio
    async def test_connection_health_includes_required_fields(self, auth_store):
        await auth_store.list_users()
        health = auth_store.connection_health()
        required_fields = [
            "store_type", "connected", "tables_ready",
            "connection_age_seconds", "resets", "seconds_since_last_reset",
            "seconds_since_last_op", "total_ops", "avg_op_latency_ms", "db_path",
        ]
        for field in required_fields:
            assert field in health, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnectionLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, tmp_path):
        db_path = str(tmp_path / "lifecycle.db")
        config = AuthConfig(session_timeout_seconds=300)
        store = SqliteSessionStore(db_path, config)
        await store.initialize()
        await store.initialize()  # second call should be no-op
        await store.close()

    @pytest.mark.asyncio
    async def test_close_and_reopen(self, tmp_path):
        db_path = str(tmp_path / "lifecycle.db")
        config = AuthConfig(session_timeout_seconds=300)
        store = SqliteSessionStore(db_path, config)
        await store.initialize()

        token = await store.create_session("alice", "readonly")
        await store.close()

        # Reopen and verify data persisted
        store2 = SqliteSessionStore(db_path, config)
        await store2.initialize()
        result = await store2.validate_session(token)
        assert result is not None
        assert result["identity"] == "alice"
        await store2.close()


# ---------------------------------------------------------------------------
# Session timeout configuration
# ---------------------------------------------------------------------------


class TestSessionTimeout:
    @pytest.mark.asyncio
    async def test_session_respects_timeout_config(self, tmp_path):
        db_path = str(tmp_path / "timeout.db")
        # Very short timeout: 1 second
        config = AuthConfig(session_timeout_seconds=1)
        store = SqliteSessionStore(db_path, config)
        await store.initialize()

        token = await store.create_session("alice", "readonly")

        # Manually set expires_at to 2 seconds in the past
        conn = await store._get_conn()
        past = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat() + "Z"
        await conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE session_token = ?",
            (past, token),
        )
        await conn.commit()

        result = await store.validate_session(token)
        assert result is None
        await store.close()
