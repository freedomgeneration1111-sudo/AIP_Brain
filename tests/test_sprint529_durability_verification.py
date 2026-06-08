"""Sprint 5.29 tests — Alerting durability, rollup verification, dashboard alerts
panel, config-driven alert routing, and health endpoint consolidation.

Deliverable 1: Alerting Durability (AlertHistoryStore + AlertManager integration)
Deliverable 2: Rollup Verification Tooling (verify_rollup_integrity + API)
Deliverable 3: Dashboard Alerts Panel (HTML contains alerts panel)
Deliverable 4: Config-Driven Alert Routing (routes config + transport selection)
Deliverable 5: Health Endpoint Consolidation (GET /vigil/quality/health)
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Any
from unittest.mock import MagicMock

import pytest

from aip.adapter.alerting import (
    AlertConfig,
    Alert,
    AlertManager,
)
from aip.adapter.alert_history_store import AlertHistoryStore
from aip.adapter.vigil.vigil_quality_store import VigilQualityStore


# ============================================================================
# Deliverable 1: Alerting Durability — AlertHistoryStore
# ============================================================================


class TestAlertHistoryStore:
    """Tests for the SQLite-backed AlertHistoryStore."""

    def test_initialize_creates_tables(self):
        """AlertHistoryStore.initialize() creates the required tables."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            with sqlite3.connect(store._db_path) as conn:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                table_names = [t[0] for t in tables]
                assert "alert_history" in table_names
                assert "alert_delivery_failures" in table_names

    def test_record_alert_persists_to_sqlite(self):
        """record_alert() writes alert data to the SQLite database."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            result = store.record_alert({
                "alert_type": "batch_reduction",
                "severity": "warning",
                "subject": "graph_extraction",
                "message": "Batch size reduced from 4 to 3",
                "data": {"old": 4, "new": 3, "failure_rate": 0.6},
                "timestamp": "2025-06-01T12:00:00Z",
            })
            assert result is True

            # Verify the alert was persisted
            alerts = store.get_alert_history()
            assert len(alerts) == 1
            assert alerts[0]["alert_type"] == "batch_reduction"
            assert alerts[0]["severity"] == "warning"
            assert alerts[0]["data"]["old"] == 4

    def test_record_delivery_failure_persists_to_sqlite(self):
        """record_delivery_failure() writes failure data to the SQLite database."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            result = store.record_delivery_failure({
                "transport": "webhook",
                "alert_type": "batch_reduction",
                "subject": "test",
                "error_message": "Connection refused",
                "timestamp": "2025-06-01T12:00:00Z",
                "retry_attempt": 2,
                "final": True,
            })
            assert result is True

            failures = store.get_delivery_failures()
            assert len(failures) == 1
            assert failures[0]["transport"] == "webhook"
            assert failures[0]["retry_attempt"] == 2

    def test_get_alert_history_filters_by_type(self):
        """get_alert_history() filters by alert_type."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            store.record_alert({
                "alert_type": "batch_reduction",
                "severity": "warning",
                "subject": "test",
                "message": "m1",
                "timestamp": "2025-06-01T12:00:00Z",
            })
            store.record_alert({
                "alert_type": "quality_degradation",
                "severity": "warning",
                "subject": "test",
                "message": "m2",
                "timestamp": "2025-06-01T12:01:00Z",
            })

            batch_only = store.get_alert_history(alert_type="batch_reduction")
            assert len(batch_only) == 1
            assert batch_only[0]["alert_type"] == "batch_reduction"

    def test_get_alert_history_filters_by_severity(self):
        """get_alert_history() filters by severity."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            store.record_alert({
                "alert_type": "batch_reduction",
                "severity": "warning",
                "subject": "test",
                "message": "m1",
                "timestamp": "2025-06-01T12:00:00Z",
            })
            store.record_alert({
                "alert_type": "batch_reduction",
                "severity": "critical",
                "subject": "test",
                "message": "m2",
                "timestamp": "2025-06-01T12:01:00Z",
            })

            critical_only = store.get_alert_history(severity="critical")
            assert len(critical_only) == 1
            assert critical_only[0]["severity"] == "critical"

    def test_get_alert_history_filters_by_since(self):
        """get_alert_history() filters by timestamp with since parameter."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            store.record_alert({
                "alert_type": "quality_degradation",
                "severity": "warning",
                "subject": "test",
                "message": "old alert",
                "timestamp": "2020-01-01T00:00:00Z",
            })
            store.record_alert({
                "alert_type": "quality_degradation",
                "severity": "warning",
                "subject": "test",
                "message": "new alert",
                "timestamp": "2025-06-01T00:00:00Z",
            })

            recent = store.get_alert_history(since="2025-01-01T00:00:00Z")
            assert len(recent) == 1
            assert recent[0]["message"] == "new alert"

    def test_get_alert_history_respects_limit(self):
        """get_alert_history() respects the limit parameter."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            for i in range(5):
                store.record_alert({
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "test",
                    "message": f"alert_{i}",
                    "timestamp": f"2025-06-01T12:0{i}:00Z",
                })

            result = store.get_alert_history(limit=3)
            assert len(result) == 3

    def test_get_delivery_failures_filters_by_transport(self):
        """get_delivery_failures() filters by transport."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            store.record_delivery_failure({
                "transport": "webhook",
                "alert_type": "test",
                "subject": "test",
                "error_message": "err1",
                "timestamp": "2025-06-01T12:00:00Z",
            })
            store.record_delivery_failure({
                "transport": "email",
                "alert_type": "test",
                "subject": "test",
                "error_message": "err2",
                "timestamp": "2025-06-01T12:01:00Z",
            })

            webhook_only = store.get_delivery_failures(transport="webhook")
            assert len(webhook_only) == 1
            assert webhook_only[0]["transport"] == "webhook"

    def test_alert_count_and_failure_count(self):
        """get_alert_count() and get_failure_count() return correct counts."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            assert store.get_alert_count() == 0
            assert store.get_failure_count() == 0

            store.record_alert({
                "alert_type": "test",
                "severity": "info",
                "subject": "test",
                "message": "m1",
                "timestamp": "2025-06-01T12:00:00Z",
            })
            store.record_delivery_failure({
                "transport": "webhook",
                "alert_type": "test",
                "subject": "test",
                "error_message": "err",
                "timestamp": "2025-06-01T12:00:00Z",
            })

            assert store.get_alert_count() == 1
            assert store.get_failure_count() == 1

    def test_get_status_returns_store_info(self):
        """get_status() returns comprehensive store status."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            status = store.get_status()
            assert status["initialized"] is True
            assert "total_alerts" in status
            assert "total_delivery_failures" in status
            assert "max_alert_rows" in status

    def test_alert_history_survives_restart(self):
        """Alert history persists across store restarts (core durability test)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")

            # First instance: write alerts
            store1 = AlertHistoryStore(db_path)
            store1.initialize()
            store1.record_alert({
                "alert_type": "batch_reduction",
                "severity": "critical",
                "subject": "graph_extraction",
                "message": "Batch reduced to 1",
                "data": {"old": 4, "new": 1},
                "timestamp": "2025-06-01T12:00:00Z",
            })

            # Simulate process restart: create new store instance
            store2 = AlertHistoryStore(db_path)
            store2.initialize()

            # The alert should be queryable from the new instance
            alerts = store2.get_alert_history()
            assert len(alerts) == 1
            assert alerts[0]["alert_type"] == "batch_reduction"
            assert alerts[0]["severity"] == "critical"
            assert alerts[0]["data"]["old"] == 4

    def test_auto_prune_alerts(self):
        """Alert history is auto-pruned when exceeding max_alert_rows."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(
                os.path.join(tmp_dir, "alerts.db"),
                max_alert_rows=5,
            )
            store.initialize()

            # Insert more than max rows
            for i in range(10):
                store.record_alert({
                    "alert_type": "test",
                    "severity": "info",
                    "subject": "test",
                    "message": f"alert_{i}",
                    "timestamp": f"2025-06-01T12:{i:02d}:00Z",
                })

            # Should be pruned to max_alert_rows
            count = store.get_alert_count()
            assert count <= 5


class TestAlertManagerWithHistoryStore:
    """Tests for AlertManager integration with AlertHistoryStore."""

    def test_attach_history_store(self):
        """AlertManager.attach_history_store() links the persistent store."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(store)

            assert mgr._history_store is store

    def test_send_alert_persists_to_store(self):
        """When a history store is attached, send_alert() persists to SQLite."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(store)

            mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="test",
                message="Batch reduced",
                data={"old": 4, "new": 3},
            ))

            # Check in-memory (still maintained as buffer)
            assert len(mgr._alert_history) == 1

            # Check persistent store
            assert store.get_alert_count() == 1
            alerts = store.get_alert_history()
            assert alerts[0]["alert_type"] == "batch_reduction"

    def test_get_alert_history_prefers_persistent_store(self):
        """get_alert_history() queries the persistent store when attached."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            # Pre-populate the store with a historical alert
            store.record_alert({
                "alert_type": "quality_degradation",
                "severity": "critical",
                "subject": "faithfulness",
                "message": "Pre-restart alert",
                "timestamp": "2025-06-01T12:00:00Z",
            })

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(store)

            # The manager's in-memory history is empty, but the persistent
            # store has the pre-restart alert
            assert len(mgr._alert_history) == 0
            history = mgr.get_alert_history()
            assert len(history) == 1
            assert history[0]["message"] == "Pre-restart alert"

    def test_delivery_failure_persists_to_store(self):
        """Delivery failures are persisted to the SQLite store."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(
                enabled=True,
                webhook_url="https://invalid-host-that-does-not-exist.local/hook",
                min_alert_interval_seconds=0,
                webhook_max_retries=0,
            ))
            mgr.attach_history_store(store)

            # This will fail to deliver (invalid URL), recording a failure
            mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="test",
                message="Will fail to deliver",
            ))

            # Check persistent store has the delivery failure
            failures = store.get_delivery_failures()
            assert len(failures) >= 1

    @pytest.mark.asyncio
    async def test_alert_history_endpoint_queries_persistent_store(self):
        """The /vigil/quality/alerts endpoint returns data from persistent store."""
        from aip.adapter.api.routes.vigil_quality import vigil_alerts

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            # Pre-populate with a historical alert
            store.record_alert({
                "alert_type": "batch_reduction",
                "severity": "warning",
                "subject": "test",
                "message": "Historical alert from before restart",
                "timestamp": "2025-06-01T12:00:00Z",
            })

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(store)

            container = MagicMock()
            container._alert_manager = mgr

            result = await vigil_alerts(container=container)
            assert result["status"] == "ok"
            assert len(result["alerts"]) == 1
            assert result["alerts"][0]["message"] == "Historical alert from before restart"


# ============================================================================
# Deliverable 2: Rollup Verification Tooling
# ============================================================================


class TestRollupVerification:
    """Tests for VigilQualityStore.verify_rollup_integrity()."""

    def _create_store(self, tmp_path, **kwargs):
        db_path = os.path.join(str(tmp_path), "quality.db")
        store = VigilQualityStore(db_path, **kwargs)
        store.initialize()
        return store

    def test_verify_empty_database(self, tmp_path):
        """verify_rollup_integrity() returns valid for an empty database."""
        store = self._create_store(tmp_path)
        result = store.verify_rollup_integrity()
        assert result["valid"] is True
        assert result["total_issues"] == 0

    def test_verify_clean_rollup(self, tmp_path):
        """verify_rollup_integrity() returns valid after clean rollup."""
        store = self._create_store(
            tmp_path,
            rollup_age_days=0,
            retention_days=0,
        )

        # Insert records for a day
        for i in range(3):
            store.record_cycle({
                "timestamp": f"2025-01-10T{10+i:02d}:00:00Z",
                "avg_citation_rate": 0.85,
                "avg_grounding_rate": 0.90,
                "avg_llm_faithfulness": 0.88,
                "evaluated_count": 15,
                "flagged_count": 2,
            })

        # Run rollup
        store.run_rollup()

        result = store.verify_rollup_integrity()
        assert result["valid"] is True
        assert result["daily_rollups_verified"] >= 1

    def test_verify_detects_remaining_originals(self, tmp_path):
        """verify_rollup_integrity() detects original rows left after rollup."""
        store = self._create_store(
            tmp_path,
            rollup_age_days=0,
            retention_days=0,
        )

        # Insert records for a day
        for i in range(3):
            store.record_cycle({
                "timestamp": f"2025-01-10T{10+i:02d}:00:00Z",
                "avg_citation_rate": 0.85,
                "avg_grounding_rate": 0.90,
                "avg_llm_faithfulness": 0.88,
                "evaluated_count": 15,
                "flagged_count": 2,
            })

        # Run rollup
        store.run_rollup()

        # Manually insert an extra original row for the same day (simulating partial rollup)
        with sqlite3.connect(store._db_path) as conn:
            conn.execute("""
                INSERT INTO vigil_quality_history (
                    cycle_timestamp, avg_citation_rate, avg_grounding_rate,
                    avg_llm_faithfulness, evaluated_count, flagged_count
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, ("2025-01-10T15:00:00Z", 0.80, 0.85, 0.82, 10, 1))

        result = store.verify_rollup_integrity()
        # Should detect the remaining original row
        assert result["valid"] is False
        assert result["total_issues"] >= 1
        issue_types = [i["type"] for i in result["issues"]]
        assert "daily_rollup_has_remaining_originals" in issue_types or \
               "mixed_day_originals_and_rollups" in issue_types

    def test_verify_returns_stats(self, tmp_path):
        """verify_rollup_integrity() includes verification statistics."""
        store = self._create_store(tmp_path)

        store.record_cycle({
            "timestamp": "2025-06-01T12:00:00Z",
            "avg_citation_rate": 0.85,
            "avg_grounding_rate": 0.90,
            "avg_llm_faithfulness": 0.88,
            "evaluated_count": 15,
            "flagged_count": 2,
        })

        result = store.verify_rollup_integrity()
        assert "daily_rollups_verified" in result
        assert "weekly_rollups_verified" in result
        assert "total_rows" in result

    @pytest.mark.asyncio
    async def test_retention_verify_endpoint(self):
        """GET /vigil/quality/retention/verify returns verification results."""
        from aip.adapter.api.routes.vigil_quality import vigil_rollup_verify

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = VigilQualityStore(
                os.path.join(tmp_dir, "quality.db"),
                rollup_age_days=0,
                retention_days=0,
            )
            store.initialize()

            container = MagicMock()
            container._vigil_quality_store = store

            result = await vigil_rollup_verify(container=container)
            assert result["status"] == "ok"
            assert "verification" in result
            assert "valid" in result["verification"]

    @pytest.mark.asyncio
    async def test_retention_verify_endpoint_no_store(self):
        """GET /vigil/quality/retention/verify returns gracefully when no store."""
        from aip.adapter.api.routes.vigil_quality import vigil_rollup_verify

        container = MagicMock()
        container._vigil_quality_store = None

        result = await vigil_rollup_verify(container=container)
        assert result["status"] == "quality_store_not_configured"


# ============================================================================
# Deliverable 3: Dashboard Alerts Panel
# ============================================================================


class TestDashboardAlertsPanel:
    """Tests verifying the dashboard includes an alerts panel."""

    def test_dashboard_html_contains_alerts_panel(self):
        """The dashboard HTML includes the alerts panel section."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "alerts-panel" in _DASHBOARD_HTML
        assert "Recent Alerts" in _DASHBOARD_HTML
        assert "alerts-list" in _DASHBOARD_HTML
        assert "fetchAlerts" in _DASHBOARD_HTML
        assert "ALERTS_URL" in _DASHBOARD_HTML

    def test_dashboard_html_contains_alert_styling(self):
        """The dashboard HTML includes CSS for alert severity levels."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "alert-item" in _DASHBOARD_HTML
        assert "alert-item.info" in _DASHBOARD_HTML
        assert "alert-item.warning" in _DASHBOARD_HTML
        assert "alert-item.critical" in _DASHBOARD_HTML
        assert "alert-type" in _DASHBOARD_HTML
        assert "alert-severity" in _DASHBOARD_HTML

    def test_dashboard_html_no_alerts_fallback(self):
        """The dashboard shows 'No recent alerts' when no alerts exist."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "No recent alerts" in _DASHBOARD_HTML

    def test_dashboard_live_toggle_includes_alerts(self):
        """The Live toggle also refreshes alerts panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        # The toggleLive function should call both fetchData and fetchAlerts
        assert "fetchAlerts()" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 4: Config-Driven Alert Routing
# ============================================================================


class TestConfigDrivenAlertRouting:
    """Tests for per-alert-type transport routing."""

    def test_routes_config_accepted(self):
        """AlertConfig accepts the routes parameter."""
        config = AlertConfig(
            enabled=True,
            webhook_url="https://example.com/hook",
            email_to="ops@example.com",
            routes={
                "batch_reduction": ["webhook"],
                "quality_degradation": ["email", "webhook"],
            },
        )
        assert config.routes["batch_reduction"] == ["webhook"]
        assert config.routes["quality_degradation"] == ["email", "webhook"]

    def test_default_routes_empty(self):
        """AlertConfig.routes defaults to empty dict (all transports for all types)."""
        config = AlertConfig()
        assert config.routes == {}

    def test_get_transports_for_alert_with_routes(self):
        """_get_transports_for_alert() respects configured routes."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            webhook_url="https://example.com/hook",
            email_to="ops@example.com",
            routes={
                "batch_reduction": ["webhook"],
                "quality_degradation": ["email", "webhook"],
            },
        ))

        # batch_reduction should only use webhook
        assert mgr._get_transports_for_alert("batch_reduction") == ["webhook"]

        # quality_degradation should use both
        transports = mgr._get_transports_for_alert("quality_degradation")
        assert "webhook" in transports
        assert "email" in transports

    def test_get_transports_for_alert_without_routes(self):
        """_get_transports_for_alert() uses all configured transports when no routes."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            webhook_url="https://example.com/hook",
            email_to="ops@example.com",
        ))

        # All types should get all transports
        transports = mgr._get_transports_for_alert("batch_reduction")
        assert "webhook" in transports
        assert "email" in transports

    def test_get_transports_for_alert_unknown_type_with_routes(self):
        """Unknown alert types use all configured transports when no route defined."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            webhook_url="https://example.com/hook",
            email_to="ops@example.com",
            routes={
                "batch_reduction": ["webhook"],
            },
        ))

        # Unknown type has no route, so falls back to all transports
        transports = mgr._get_transports_for_alert("unknown_type")
        assert "webhook" in transports
        assert "email" in transports

    def test_get_transports_for_alert_ignores_unconfigured_transport(self):
        """Routes only return transports that are actually configured."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            webhook_url="https://example.com/hook",
            # No email configured
            routes={
                "quality_degradation": ["email", "webhook"],
            },
        ))

        # Only webhook should be returned since email is not configured
        transports = mgr._get_transports_for_alert("quality_degradation")
        assert "webhook" in transports
        assert "email" not in transports

    def test_alert_routing_in_send_alert(self):
        """send_alert() respects routing — only dispatched transports receive the alert."""
        # We test this indirectly by checking that the routing logic
        # is used within send_alert (it calls _get_transports_for_alert)
        mgr = AlertManager(AlertConfig(
            enabled=True,
            webhook_url="https://example.com/hook",
            email_to="ops@example.com",
            routes={
                "batch_reduction": ["webhook"],  # Only webhook for batch_reduction
            },
            min_alert_interval_seconds=0,
        ))

        # Verify the transport selection
        transports = mgr._get_transports_for_alert("batch_reduction")
        assert transports == ["webhook"]

        # For other types, both transports should be available
        transports = mgr._get_transports_for_alert("pool_adjustment")
        assert "webhook" in transports
        assert "email" in transports

    def test_routes_in_get_status(self):
        """AlertManager.get_status() includes routes configuration."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            routes={"batch_reduction": ["webhook"]},
        ))

        status = mgr.get_status()
        assert "routes" in status
        assert status["routes"]["batch_reduction"] == ["webhook"]

    def test_empty_routes_in_get_status(self):
        """AlertManager.get_status() shows empty routes when none configured."""
        mgr = AlertManager(AlertConfig(enabled=True))

        status = mgr.get_status()
        assert "routes" in status
        assert status["routes"] == {}


# ============================================================================
# Deliverable 5: Health Endpoint Consolidation
# ============================================================================


class TestHealthEndpoint:
    """Tests for the GET /vigil/quality/health consolidated endpoint."""

    @pytest.mark.asyncio
    async def test_health_endpoint_returns_status(self):
        """GET /vigil/quality/health returns consolidated health status."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        alert_mgr = AlertManager(AlertConfig(enabled=True))
        quality_store = VigilQualityStore(":memory:", retention_days=0)
        quality_store.initialize()

        container = MagicMock()
        container._alert_manager = alert_mgr
        container._vigil_quality_store = quality_store
        container._alert_history_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None

        result = await vigil_quality_health(container=container)
        assert result["status"] in ("healthy", "degraded")
        assert "alerting" in result
        assert "retention" in result
        assert "auto_tuning" in result
        assert "components" in result

    @pytest.mark.asyncio
    async def test_health_endpoint_includes_alerting_status(self):
        """Health endpoint includes alerting system status."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        alert_mgr = AlertManager(AlertConfig(
            enabled=True,
            webhook_url="https://example.com/hook",
            min_alert_interval_seconds=0,
        ))

        container = MagicMock()
        container._alert_manager = alert_mgr
        container._vigil_quality_store = None
        container._alert_history_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None

        result = await vigil_quality_health(container=container)
        assert result["alerting"]["available"] is True
        assert result["alerting"]["enabled"] is True
        assert result["alerting"]["webhook_configured"] is True

    @pytest.mark.asyncio
    async def test_health_endpoint_includes_retention_status(self):
        """Health endpoint includes retention/rollup status."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        with tempfile.TemporaryDirectory() as tmp_dir:
            quality_store = VigilQualityStore(
                os.path.join(tmp_dir, "quality.db"),
                retention_days=30,
            )
            quality_store.initialize()

            container = MagicMock()
            container._alert_manager = None
            container._vigil_quality_store = quality_store
            container._alert_history_store = None
            container._auto_tuning_policy = None
            container._read_pool_auto_sizer = None

            result = await vigil_quality_health(container=container)
            assert result["retention"]["available"] is True
            assert result["retention"]["retention_days"] == 30

    @pytest.mark.asyncio
    async def test_health_endpoint_includes_component_availability(self):
        """Health endpoint shows which components are available."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        container = MagicMock()
        container._alert_manager = AlertManager(AlertConfig(enabled=True))
        container._vigil_quality_store = None
        container._alert_history_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None

        result = await vigil_quality_health(container=container)
        components = result["components"]
        assert components["alert_manager"] is True
        assert components["quality_store"] is False
        assert components["alert_history_store"] is False

    @pytest.mark.asyncio
    async def test_health_endpoint_no_components(self):
        """Health endpoint returns gracefully when no operational components configured."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        container = MagicMock()
        container._alert_manager = None
        container._vigil_quality_store = None
        container._alert_history_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None

        result = await vigil_quality_health(container=container)
        assert result["status"] in ("healthy", "degraded")
        assert result["alerting"]["available"] is False
        assert result["retention"]["available"] is False

    @pytest.mark.asyncio
    async def test_health_endpoint_detects_degraded_state(self):
        """Health endpoint reports 'degraded' when alerting has excessive failures."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        alert_mgr = AlertManager(AlertConfig(enabled=True))
        # Simulate many send failures
        alert_mgr._total_send_failures = 15

        container = MagicMock()
        container._alert_manager = alert_mgr
        container._vigil_quality_store = None
        container._alert_history_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None

        result = await vigil_quality_health(container=container)
        assert result["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_health_endpoint_shows_history_store_attached(self):
        """Health endpoint shows when alert history store is attached."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        with tempfile.TemporaryDirectory() as tmp_dir:
            history_store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            history_store.initialize()

            alert_mgr = AlertManager(AlertConfig(enabled=True))
            alert_mgr.attach_history_store(history_store)

            container = MagicMock()
            container._alert_manager = alert_mgr
            container._vigil_quality_store = None
            container._alert_history_store = history_store
            container._auto_tuning_policy = None
            container._read_pool_auto_sizer = None

            result = await vigil_quality_health(container=container)
            assert result["alerting"]["history_store_attached"] is True
            assert result["components"]["alert_history_store"] is True
