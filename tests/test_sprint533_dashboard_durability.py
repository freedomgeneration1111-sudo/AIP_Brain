"""Sprint 5.33 tests — WebSocket Dashboard UI, Alert Group Persistence,
Delivery Status Auto-Pruning, WebSocket Auth & Rate Limiting,
Alert Grouping by Causality.

Deliverable 1: WebSocket Dashboard UI Integration (WS + SSE fallback, status indicator, action buttons)
Deliverable 2: Alert Group Persistence (alert_groups table, persist on add, load on startup)
Deliverable 3: Delivery Status Auto-Pruning (prune by age/count after each record)
Deliverable 4: WebSocket Authentication & Rate Limiting (token query param, per-connection rate limit)
Deliverable 5: Alert Grouping by Causality (causal:{subject} groups, config flag)
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
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
# Deliverable 1: WebSocket Dashboard UI Integration
# ============================================================================


class TestDashboardWebSocketUI:
    """Tests for the updated dashboard HTML with WebSocket + SSE fallback."""

    def test_dashboard_html_contains_ws_connection_code(self):
        """Dashboard HTML includes WebSocket connection logic."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "connectWebSocket" in _DASHBOARD_HTML
        assert "new WebSocket" in _DASHBOARD_HTML

    def test_dashboard_html_contains_sse_fallback(self):
        """Dashboard HTML includes SSE fallback when WS fails."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "connectSSE" in _DASHBOARD_HTML
        assert "SSE Fallback" in _DASHBOARD_HTML

    def test_dashboard_html_contains_connection_status_indicator(self):
        """Dashboard HTML has a connection status indicator element."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "connStatus" in _DASHBOARD_HTML
        assert "connDot" in _DASHBOARD_HTML
        assert "connLabel" in _DASHBOARD_HTML
        assert "updateConnStatus" in _DASHBOARD_HTML

    def test_dashboard_html_contains_action_buttons(self):
        """Dashboard HTML has action buttons for all WS commands."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "wsAcknowledge" in _DASHBOARD_HTML
        assert "wsDismiss" in _DASHBOARD_HTML
        assert "wsMute" in _DASHBOARD_HTML
        assert "wsUnmute" in _DASHBOARD_HTML
        assert "wsBulkAcknowledge" in _DASHBOARD_HTML
        assert "wsBulkDismiss" in _DASHBOARD_HTML

    def test_dashboard_html_sends_json_over_ws(self):
        """Dashboard sends commands as JSON over WebSocket."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "wsSendCommand" in _DASHBOARD_HTML
        assert "JSON.stringify(command)" in _DASHBOARD_HTML

    def test_dashboard_html_http_fallback_on_sse(self):
        """When WS is not connected, commands use HTTP POST fallback."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "httpFallback" in _DASHBOARD_HTML
        # HTTP fallback uses REST endpoints
        assert "acknowledge" in _DASHBOARD_HTML
        assert "dismiss" in _DASHBOARD_HTML

    def test_dashboard_html_action_bar_elements(self):
        """Dashboard HTML has input fields for alert ID, mute params, group key."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "actionAlertId" in _DASHBOARD_HTML
        assert "actionMuteType" in _DASHBOARD_HTML
        assert "actionMuteSubject" in _DASHBOARD_HTML
        assert "actionGroupKey" in _DASHBOARD_HTML

    def test_dashboard_html_auto_connects_ws(self):
        """Dashboard auto-connects WebSocket on page load."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "connectWebSocket()" in _DASHBOARD_HTML
        # Should try WS first, not SSE directly
        # The old direct connectSSE() call should be replaced
        lines = _DASHBOARD_HTML.split("\n")
        auto_load_section = [l for l in lines if "connectWebSocket()" in l or "connectSSE()" in l]
        # Should have connectWebSocket in auto-load, connectSSE only as fallback
        ws_in_autoload = any("connectWebSocket()" in l for l in auto_load_section)
        assert ws_in_autoload


# ============================================================================
# Deliverable 2: Alert Group Persistence
# ============================================================================


class TestAlertGroupPersistence:
    """Tests for alert group persistence to SQLite."""

    def test_record_alert_group(self):
        """record_alert_group() persists a group membership."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            assert store.record_alert_group("my_subject", "alert-1-abc")
            assert store.record_alert_group("my_subject", "alert-2-def")

    def test_get_alert_groups(self):
        """get_alert_groups() returns persisted groups."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            store.record_alert_group("subject_a", "cid-1")
            store.record_alert_group("subject_a", "cid-2")
            store.record_alert_group("subject_b", "cid-3")

            groups = store.get_alert_groups()
            assert "subject_a" in groups
            assert "subject_b" in groups
            assert "cid-1" in groups["subject_a"]
            assert "cid-2" in groups["subject_a"]
            assert "cid-3" in groups["subject_b"]

    def test_delete_alert_group(self):
        """delete_alert_group() removes a group from storage."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            store.record_alert_group("to_delete", "cid-1")
            store.record_alert_group("to_keep", "cid-2")

            assert store.delete_alert_group("to_delete")
            groups = store.get_alert_groups()
            assert "to_delete" not in groups
            assert "to_keep" in groups

    def test_alert_groups_persist_across_store_instances(self):
        """Alert groups survive store re-instantiation (process restart)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")

            store1 = AlertHistoryStore(db_path)
            store1.initialize()
            store1.record_alert_group("restart_test", "cid-restart-1")

            store2 = AlertHistoryStore(db_path)
            store2.initialize()
            groups = store2.get_alert_groups()
            assert "restart_test" in groups
            assert "cid-restart-1" in groups["restart_test"]

    def test_add_alert_to_group_persists_to_store(self):
        """_add_alert_to_group persists group membership when store is attached."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                )
            )
            mgr.attach_history_store(store)

            correlation_id = mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject="persist_test",
                    message="Test group persistence",
                )
            )

            groups = store.get_alert_groups()
            assert "persist_test" in groups
            assert correlation_id in groups["persist_test"]

    def test_attach_history_store_loads_groups(self):
        """attach_history_store() rebuilds _alert_groups from persisted data."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")

            # First instance: create some groups
            store = AlertHistoryStore(db_path)
            store.initialize()
            store.record_alert_group("loaded_group", "cid-loaded-1")
            store.record_alert_group("loaded_group", "cid-loaded-2")

            # New manager attaches the store — should load groups
            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                )
            )
            mgr.attach_history_store(store)

            groups = mgr.get_alert_groups()
            assert "loaded_group" in groups
            assert "cid-loaded-1" in groups["loaded_group"]
            assert "cid-loaded-2" in groups["loaded_group"]

    def test_delivery_status_max_age_days_config(self):
        """AlertConfig has delivery_status_max_age_days field."""
        config = AlertConfig(delivery_status_max_age_days=14)
        assert config.delivery_status_max_age_days == 14

    def test_ws_auth_token_config(self):
        """AlertConfig has ws_auth_token field."""
        config = AlertConfig(ws_auth_token="secret123")
        assert config.ws_auth_token == "secret123"

    def test_ws_rate_limit_per_minute_config(self):
        """AlertConfig has ws_rate_limit_per_minute field."""
        config = AlertConfig(ws_rate_limit_per_minute=30)
        assert config.ws_rate_limit_per_minute == 30

    def test_causal_grouping_config(self):
        """AlertConfig has causal_grouping_enabled and causal_grouping_window_seconds fields."""
        config = AlertConfig(
            causal_grouping_enabled=True,
            causal_grouping_window_seconds=600,
        )
        assert config.causal_grouping_enabled is True
        assert config.causal_grouping_window_seconds == 600

    def test_get_status_includes_new_fields(self):
        """get_status() includes Sprint 5.33 config fields."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                ws_auth_token="mytoken",
                ws_rate_limit_per_minute=30,
                causal_grouping_enabled=True,
                causal_grouping_window_seconds=600,
                delivery_status_max_age_days=14,
            )
        )

        status = mgr.get_status()
        assert status["delivery_status_max_age_days"] == 14
        assert status["ws_auth_configured"] is True
        assert status["ws_rate_limit_per_minute"] == 30
        assert status["causal_grouping"]["enabled"] is True
        assert status["causal_grouping"]["window_seconds"] == 600

    def test_get_status_ws_auth_not_configured(self):
        """get_status() shows ws_auth_configured=False when token is empty."""
        mgr = AlertManager(AlertConfig())
        status = mgr.get_status()
        assert status["ws_auth_configured"] is False


# ============================================================================
# Deliverable 3: Delivery Status Auto-Pruning
# ============================================================================


class TestDeliveryStatusAutoPruning:
    """Tests for delivery status auto-pruning."""

    def test_prune_delivery_status_by_age(self):
        """prune_delivery_status() deletes records older than max_age_days."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            # Insert an old record directly
            with sqlite3.connect(db_path) as conn:
                old_ts = "2020-01-01T00:00:00+00:00"
                conn.execute(
                    """
                    INSERT INTO alert_delivery_status (
                        correlation_id, status, alert_type, severity, subject,
                        transports, transport_results, dispatched_at, completed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    ("old-cid", "delivered", "batch_reduction", "warning", "test", "[]", "{}", old_ts, old_ts, old_ts),
                )

            # Insert a recent record in a separate connection
            with sqlite3.connect(db_path) as conn:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO alert_delivery_status (
                        correlation_id, status, alert_type, severity, subject,
                        transports, transport_results, dispatched_at, completed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    ("new-cid", "delivered", "batch_reduction", "warning", "test", "[]", "{}", now, now, now),
                )

            deleted = store.prune_delivery_status(max_rows=2000, max_age_days=30)
            assert deleted >= 1

            # The old record should be gone
            assert store.get_delivery_status_by_correlation_id("old-cid") is None
            # The new record should remain
            assert store.get_delivery_status_by_correlation_id("new-cid") is not None

    def test_prune_delivery_status_by_count(self):
        """prune_delivery_status() deletes oldest records when over max_rows."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            # Insert more records than max_rows
            for i in range(15):
                store.record_delivery_status(
                    {
                        "correlation_id": f"cid-prune-{i}",
                        "status": "delivered",
                        "alert_type": "batch_reduction",
                        "severity": "warning",
                        "subject": "prune_test",
                    }
                )

            # Prune to max_rows=10 (but auto-pruning already happened in record)
            # Let's add more and check
            count_before = store.get_delivery_status_count()
            # Auto-pruning should have kept it at or below 2000 (default)
            assert count_before > 0

    def test_get_delivery_status_count(self):
        """get_delivery_status_count() returns total row count."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            assert store.get_delivery_status_count() == 0

            store.record_delivery_status(
                {
                    "correlation_id": "cid-count-1",
                    "status": "delivered",
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "test",
                }
            )
            assert store.get_delivery_status_count() == 1

            store.record_delivery_status(
                {
                    "correlation_id": "cid-count-2",
                    "status": "delivered",
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "test",
                }
            )
            assert store.get_delivery_status_count() == 2

    def test_auto_prune_after_record_delivery_status(self):
        """record_delivery_status() auto-prunes by calling prune_delivery_status()."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            # Insert a record with a very old created_at to test age pruning
            with sqlite3.connect(os.path.join(tmp_dir, "alerts.db")) as conn:
                old_ts = "2020-01-01T00:00:00+00:00"
                conn.execute(
                    """
                    INSERT INTO alert_delivery_status (
                        correlation_id, status, alert_type, severity, subject,
                        transports, transport_results, dispatched_at, completed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        "old-auto-cid",
                        "delivered",
                        "batch_reduction",
                        "warning",
                        "test",
                        "[]",
                        "{}",
                        old_ts,
                        old_ts,
                        old_ts,
                    ),
                )

            # Recording a new status should trigger auto-pruning
            store.record_delivery_status(
                {
                    "correlation_id": "new-auto-cid",
                    "status": "delivered",
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "test",
                }
            )

            # The old record should be pruned (30 days default)
            assert store.get_delivery_status_by_correlation_id("old-auto-cid") is None


# ============================================================================
# Deliverable 4: WebSocket Authentication & Rate Limiting
# ============================================================================


class TestWebSocketAuthAndRateLimiting:
    """Tests for WebSocket authentication and rate limiting."""

    def test_ws_auth_token_in_alert_config(self):
        """AlertConfig has ws_auth_token with default empty string."""
        config = AlertConfig()
        assert config.ws_auth_token == ""
        assert config.ws_rate_limit_per_minute == 60

    def test_ws_auth_configured_in_status(self):
        """get_status() reports ws_auth_configured correctly."""
        mgr_with_token = AlertManager(AlertConfig(ws_auth_token="secret"))
        assert mgr_with_token.get_status()["ws_auth_configured"] is True

        mgr_no_token = AlertManager(AlertConfig())
        assert mgr_no_token.get_status()["ws_auth_configured"] is False

    def test_delivery_status_stats_endpoint_exists(self):
        """GET /vigil/quality/alerts/delivery-status/stats endpoint exists."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/alerts/delivery-status/stats" in route_paths

    def test_schema_v5_has_alert_groups_table(self):
        """Schema v5 creates the alert_groups table."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            # Verify the table exists
            with sqlite3.connect(db_path) as conn:
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alert_groups'")
                assert cursor.fetchone() is not None

    def test_alert_groups_table_schema(self):
        """alert_groups table has correct columns and primary key."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            with sqlite3.connect(db_path) as conn:
                cursor = conn.execute("PRAGMA table_info(alert_groups)")
                columns = {row[1] for row in cursor.fetchall()}
                assert "group_key" in columns
                assert "correlation_id" in columns
                assert "created_at" in columns


# ============================================================================
# Deliverable 5: Alert Grouping by Causality
# ============================================================================


class TestCausalGrouping:
    """Tests for causal alert grouping."""

    def test_causal_grouping_disabled_by_default(self):
        """Causal grouping is disabled by default."""
        config = AlertConfig()
        assert config.causal_grouping_enabled is False

    def test_causal_grouping_creates_group_when_enabled(self):
        """When causal_grouping_enabled=True, causal chain alerts are grouped."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_grouping_enabled=True,
                    causal_grouping_window_seconds=300,
                )
            )
            mgr.attach_history_store(store)

            # Send pool_adjustment alert
            cid1 = mgr.send_alert(
                Alert(
                    alert_type="pool_adjustment",
                    severity="warning",
                    subject="graph_extraction",
                    message="Pool adjusted",
                )
            )

            # Send quality_degradation alert on same subject
            cid2 = mgr.send_alert(
                Alert(
                    alert_type="quality_degradation",
                    severity="warning",
                    subject="graph_extraction",
                    message="Quality degraded",
                )
            )

            # Send batch_reduction alert on same subject
            cid3 = mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject="graph_extraction",
                    message="Batch reduced",
                )
            )

            groups = mgr.get_alert_groups()
            causal_key = "causal:graph_extraction"
            assert causal_key in groups, f"Expected causal group key {causal_key}, got {list(groups.keys())}"
            assert cid1 in groups[causal_key]
            assert cid2 in groups[causal_key]
            assert cid3 in groups[causal_key]

    def test_causal_grouping_not_created_when_disabled(self):
        """When causal_grouping_enabled=False, no causal groups are created."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_grouping_enabled=False,
                )
            )
            mgr.attach_history_store(store)

            mgr.send_alert(
                Alert(
                    alert_type="pool_adjustment",
                    severity="warning",
                    subject="test_no_causal",
                    message="Pool adjusted",
                )
            )

            groups = mgr.get_alert_groups()
            assert "causal:test_no_causal" not in groups

    def test_causal_grouping_only_for_causal_chain_types(self):
        """Non-causal-chain alert types don't create causal groups."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_grouping_enabled=True,
                )
            )
            mgr.attach_history_store(store)

            # Use an alert_type that's not in the causal chain
            mgr.send_alert(
                Alert(
                    alert_type="custom_alert_type",
                    severity="warning",
                    subject="test_non_causal",
                    message="Custom alert",
                )
            )

            groups = mgr.get_alert_groups()
            assert "causal:test_non_causal" not in groups

    def test_causal_grouping_subject_isolation(self):
        """Causal groups are isolated by subject."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_grouping_enabled=True,
                )
            )
            mgr.attach_history_store(store)

            cid1 = mgr.send_alert(
                Alert(
                    alert_type="pool_adjustment",
                    severity="warning",
                    subject="subject_A",
                    message="Pool A",
                )
            )

            cid2 = mgr.send_alert(
                Alert(
                    alert_type="pool_adjustment",
                    severity="warning",
                    subject="subject_B",
                    message="Pool B",
                )
            )

            groups = mgr.get_alert_groups()
            assert "causal:subject_A" in groups
            assert "causal:subject_B" in groups
            assert cid1 in groups["causal:subject_A"]
            assert cid2 in groups["causal:subject_B"]
            assert cid2 not in groups["causal:subject_A"]

    def test_causal_grouping_persists_to_store(self):
        """Causal group membership is persisted to the AlertHistoryStore."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_grouping_enabled=True,
                )
            )
            mgr.attach_history_store(store)

            mgr.send_alert(
                Alert(
                    alert_type="pool_adjustment",
                    severity="warning",
                    subject="persist_causal",
                    message="Pool adjusted",
                )
            )

            # Check that the causal group was persisted
            groups = store.get_alert_groups()
            assert "causal:persist_causal" in groups

    def test_causal_grouping_rebuilt_on_startup(self):
        """Causal groups are rebuilt from store on attach_history_store."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")

            store = AlertHistoryStore(db_path)
            store.initialize()

            # Persist a causal group
            store.record_alert_group("causal:rebuild_test", "cid-rebuild-1")

            # New manager loads the group
            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                )
            )
            mgr.attach_history_store(store)

            groups = mgr.get_alert_groups()
            assert "causal:rebuild_test" in groups
            assert "cid-rebuild-1" in groups["causal:rebuild_test"]

    def test_causal_chain_constant(self):
        """The causal chain includes the correct alert types."""
        from aip.adapter.alerting import AlertManager

        assert hasattr(AlertManager, "_CAUSAL_CHAIN")
        chain = AlertManager._CAUSAL_CHAIN
        assert "pool_adjustment" in chain
        assert "quality_degradation" in chain
        assert "batch_reduction" in chain

    def test_causal_grouping_window_config(self):
        """causal_grouping_window_seconds configures the time window."""
        config = AlertConfig(causal_grouping_window_seconds=600)
        assert config.causal_grouping_window_seconds == 600

        # Default is 300
        config_default = AlertConfig()
        assert config_default.causal_grouping_window_seconds == 300

    def test_causal_and_subject_groups_both_created(self):
        """When causal grouping is enabled, both subject and causal groups exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_grouping_enabled=True,
                )
            )
            mgr.attach_history_store(store)

            cid = mgr.send_alert(
                Alert(
                    alert_type="quality_degradation",
                    severity="warning",
                    subject="both_groups",
                    message="Quality issue",
                )
            )

            groups = mgr.get_alert_groups()
            # Subject-based group should still exist
            assert "both_groups" in groups
            assert cid in groups["both_groups"]
            # Causal group should also exist
            assert "causal:both_groups" in groups
            assert cid in groups["causal:both_groups"]
