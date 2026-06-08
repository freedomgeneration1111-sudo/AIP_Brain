"""Tests for StoreHealthMixin integration across adapter stores.

Verifies that stores with StoreHealthMixin expose connection_health()
with the expected fields, including the new operation metrics.
"""

from __future__ import annotations

import pytest

from aip.adapter.store_health import StoreHealthMixin


def _assert_health_fields(health: dict, store_name: str) -> None:
    """Assert that a connection_health dict has all required fields."""
    assert health["store_type"] == store_name
    assert "connected" in health
    assert "tables_ready" in health
    assert "connection_age_seconds" in health
    assert "resets" in health
    assert "seconds_since_last_reset" in health
    assert "seconds_since_last_op" in health
    assert "total_ops" in health
    assert "avg_op_latency_ms" in health
    assert "db_path" in health


# ---------------------------------------------------------------------------
# SqliteBudgetStore
# ---------------------------------------------------------------------------


class TestBudgetStoreHealth:
    @pytest.mark.asyncio
    async def test_connection_health(self, tmp_path):
        from aip.adapter.budget_store_sqlite import SqliteBudgetStore

        store = SqliteBudgetStore(str(tmp_path / "budget.db"))
        await store.initialize()
        try:
            # Trigger connection
            await store.get_budget("session", "test")
            health = store.connection_health()
            _assert_health_fields(health, "SqliteBudgetStore")
            assert health["connected"] is True
            assert health["tables_ready"] is True
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# SqliteProjectStore
# ---------------------------------------------------------------------------


class TestProjectStoreHealth:
    @pytest.mark.asyncio
    async def test_connection_health(self, tmp_path):
        from aip.adapter.project.sqlite_project_store import SqliteProjectStore

        store = SqliteProjectStore(str(tmp_path / "project.db"))
        await store.initialize()
        try:
            await store.list_projects()
            health = store.connection_health()
            _assert_health_fields(health, "SqliteProjectStore")
            assert health["connected"] is True
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# SqliteSessionStore (adapter session)
# ---------------------------------------------------------------------------


class TestSessionStoreHealth:
    @pytest.mark.asyncio
    async def test_connection_health(self, tmp_path):
        from aip.adapter.session.sqlite_session_store import SqliteSessionStore

        store = SqliteSessionStore(str(tmp_path / "session.db"))
        await store.initialize()
        try:
            await store.list_sessions()
            health = store.connection_health()
            _assert_health_fields(health, "SqliteSessionStore")
            assert health["connected"] is True
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# ReviewQueueStore
# ---------------------------------------------------------------------------


class TestReviewQueueStoreHealth:
    @pytest.mark.asyncio
    async def test_connection_health(self, tmp_path):
        from aip.adapter.review_queue_store import ReviewQueueStore

        store = ReviewQueueStore(str(tmp_path / "review.db"))
        await store.initialize()
        try:
            await store.list_pending()
            health = store.connection_health()
            _assert_health_fields(health, "ReviewQueueStore")
            assert health["connected"] is True
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# AutonomyGateImpl
# ---------------------------------------------------------------------------


class TestAutonomyGateHealth:
    @pytest.mark.asyncio
    async def test_connection_health(self, tmp_path):
        from aip.adapter.autonomy.autonomy_gate import AutonomyGateImpl

        gate = AutonomyGateImpl(config={"db_path": str(tmp_path / "autonomy.db")})
        await gate.initialize()
        try:
            await gate.audit_log(limit=1)
            health = gate.connection_health()
            _assert_health_fields(health, "AutonomyGateImpl")
            assert health["connected"] is True
        finally:
            await gate.close()


# ---------------------------------------------------------------------------
# Auth session store
# ---------------------------------------------------------------------------


class TestAuthSessionStoreHealth:
    @pytest.mark.asyncio
    async def test_connection_health(self, tmp_path):
        from aip.adapter.auth.session_store import SqliteSessionStore as AuthSessionStore
        from aip.foundation.schemas import AuthConfig

        config = AuthConfig(session_timeout_seconds=3600)
        store = AuthSessionStore(str(tmp_path / "auth.db"), config)
        await store.initialize()
        try:
            await store.list_api_keys()
            health = store.connection_health()
            _assert_health_fields(health, "SqliteSessionStore")
            assert health["connected"] is True
        finally:
            await store.close()
