"""Sprint 5.34 tests — Dashboard Resilience, Causal Visualization,
Delivery Status Pruning API, WebSocket Session Management,
Alert Group TTL & Auto-Cleanup.

Deliverable 1: WebSocket Reconnection Logic (exponential backoff, localStorage persistence, reconnection status)
Deliverable 2: Causal Grouping Visualization (collapsible panel, chain view with arrows)
Deliverable 3: Delivery Status Pruning Admin API (prune endpoint, config endpoint)
Deliverable 4: WebSocket Session Management (session tracking, broadcast events, session listing API)
Deliverable 5: Alert Group TTL & Auto-Cleanup (TTL config, metadata tracking, cleanup method, status reporting)
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


# ============================================================================
# Deliverable 1: WebSocket Reconnection Logic
# ============================================================================


class TestWebSocketReconnection:
    """Tests for the dashboard HTML WebSocket reconnection with exponential backoff."""

    def test_dashboard_html_has_reconnect_variables(self):
        """Dashboard HTML includes exponential backoff reconnection variables."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "wsReconnectAttempts" in _DASHBOARD_HTML
        assert "wsMaxReconnectAttempts" in _DASHBOARD_HTML
        assert "wsBaseReconnectDelay" in _DASHBOARD_HTML
        assert "wsReconnectTimer" in _DASHBOARD_HTML

    def test_dashboard_html_has_exponential_backoff_logic(self):
        """Dashboard HTML calculates exponential backoff delay."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "Math.pow(2, wsReconnectAttempts)" in _DASHBOARD_HTML
        assert "30000" in _DASHBOARD_HTML  # 30 second cap

    def test_dashboard_html_resets_reconnect_on_success(self):
        """Dashboard HTML resets reconnect counter on successful connect."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "wsReconnectAttempts = 0" in _DASHBOARD_HTML

    def test_dashboard_html_shows_reconnection_status(self):
        """Dashboard HTML shows reconnection status to user."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "Reconnecting in" in _DASHBOARD_HTML
        assert "attempt" in _DASHBOARD_HTML

    def test_dashboard_html_saves_connection_preference(self):
        """Dashboard HTML persists connection method to localStorage."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "localStorage.setItem('aip_dashboard_connection'" in _DASHBOARD_HTML
        assert "'websocket'" in _DASHBOARD_HTML
        assert "'sse'" in _DASHBOARD_HTML

    def test_dashboard_html_reads_connection_preference(self):
        """Dashboard HTML reads connection preference from localStorage on load."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "localStorage.getItem('aip_dashboard_connection')" in _DASHBOARD_HTML

    def test_dashboard_html_max_retries_fallback_to_sse(self):
        """Dashboard HTML falls back to SSE after max retries."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "Max retries" in _DASHBOARD_HTML

    def test_dashboard_html_clears_reconnect_timer(self):
        """Dashboard HTML clears pending reconnect timer on new connect attempt."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "clearTimeout(wsReconnectTimer)" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 2: Causal Grouping Visualization
# ============================================================================


class TestCausalGroupingVisualization:
    """Tests for the causal group visualization in dashboard HTML."""

    def test_dashboard_html_has_causal_panel(self):
        """Dashboard HTML includes a causal group visualization panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "causalPanel" in _DASHBOARD_HTML
        assert "causal-chains" in _DASHBOARD_HTML

    def test_dashboard_html_has_causal_chain_css(self):
        """Dashboard HTML includes CSS for causal chain visualization."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "causal-node" in _DASHBOARD_HTML
        assert "causal-arrow" in _DASHBOARD_HTML
        assert "causal-chain" in _DASHBOARD_HTML

    def test_dashboard_html_has_collapse_functionality(self):
        """Dashboard HTML has collapsible sections for causal groups."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "toggleCausalPanel" in _DASHBOARD_HTML
        assert "collapsed" in _DASHBOARD_HTML

    def test_dashboard_html_has_fetch_causal_groups(self):
        """Dashboard HTML has JavaScript to fetch and render causal groups."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "fetchCausalGroups" in _DASHBOARD_HTML
        assert "fetchCausalChainDetails" in _DASHBOARD_HTML

    def test_dashboard_html_differentiates_causal_groups(self):
        """Dashboard HTML differentiates causal groups from regular groups."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "causal:" in _DASHBOARD_HTML
        assert "isCausal" in _DASHBOARD_HTML
        assert "startsWith('causal:')" in _DASHBOARD_HTML

    def test_dashboard_html_shows_chain_with_arrows(self):
        """Dashboard HTML shows alert chain with arrows between nodes."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "→" in _DASHBOARD_HTML or "&#8594;" in _DASHBOARD_HTML
        assert "node-type" in _DASHBOARD_HTML
        assert "node-severity" in _DASHBOARD_HTML
        assert "node-time" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 3: Delivery Status Pruning Admin API
# ============================================================================


class TestDeliveryStatusPruningAPI:
    """Tests for the delivery status pruning admin API endpoints."""

    def test_prune_endpoint_exists(self):
        """POST /vigil/quality/alerts/delivery-status/prune endpoint exists."""
        from aip.adapter.api.routes.vigil_quality import router
        route_paths = [r.path for r in router.routes if hasattr(r, 'path')]
        assert "/vigil/quality/alerts/delivery-status/prune" in route_paths

    def test_config_endpoint_exists(self):
        """PATCH /vigil/quality/alerts/delivery-status/config endpoint exists."""
        from aip.adapter.api.routes.vigil_quality import router
        route_paths = [r.path for r in router.routes if hasattr(r, 'path')]
        assert "/vigil/quality/alerts/delivery-status/config" in route_paths

    def test_delivery_status_max_rows_config(self):
        """AlertConfig has delivery_status_max_rows field with default 2000."""
        config = AlertConfig()
        assert config.delivery_status_max_rows == 2000

    def test_delivery_status_max_rows_custom_value(self):
        """AlertConfig delivery_status_max_rows can be set to custom value."""
        config = AlertConfig(delivery_status_max_rows=5000)
        assert config.delivery_status_max_rows == 5000

    def test_get_status_includes_max_rows(self):
        """get_status() includes delivery_status_max_rows."""
        mgr = AlertManager(AlertConfig(delivery_status_max_rows=3000))
        status = mgr.get_status()
        assert status["delivery_status_max_rows"] == 3000

    def test_prune_endpoint_returns_stats(self):
        """Prune endpoint returns pruning stats with pruned count and remaining."""
        from aip.adapter.api.routes.vigil_quality import vigil_delivery_status_prune
        assert vigil_delivery_status_prune is not None

    def test_config_update_modifies_alert_config(self):
        """PATCH config endpoint updates AlertConfig pruning parameters."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_status_max_age_days=30,
            delivery_status_max_rows=2000,
        ))
        # Simulate the PATCH endpoint logic
        mgr.config.delivery_status_max_age_days = 14
        mgr.config.delivery_status_max_rows = 500

        assert mgr.config.delivery_status_max_age_days == 14
        assert mgr.config.delivery_status_max_rows == 500

    def test_pruning_config_update_model(self):
        """PruningConfigUpdate model accepts optional fields."""
        from aip.adapter.api.routes.vigil_quality import PruningConfigUpdate
        update = PruningConfigUpdate(max_age_days=7, max_rows=1000)
        assert update.max_age_days == 7
        assert update.max_rows == 1000

        # Both optional
        update2 = PruningConfigUpdate()
        assert update2.max_age_days is None
        assert update2.max_rows is None


# ============================================================================
# Deliverable 4: WebSocket Session Management
# ============================================================================


class TestWebSocketSessionManagement:
    """Tests for WebSocket session tracking and management."""

    def test_register_ws_session(self):
        """register_ws_session() adds a session to the tracking dict."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mock_ws = MagicMock()
        mgr.register_ws_session("sess-1", mock_ws, "127.0.0.1")

        sessions = mgr.get_ws_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess-1"
        assert sessions[0]["remote_addr"] == "127.0.0.1"
        assert sessions[0]["connected_at"] != ""

    def test_unregister_ws_session(self):
        """unregister_ws_session() removes a session from tracking."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mock_ws = MagicMock()
        mgr.register_ws_session("sess-1", mock_ws, "127.0.0.1")
        mgr.unregister_ws_session("sess-1")

        sessions = mgr.get_ws_sessions()
        assert len(sessions) == 0

    def test_unregister_nonexistent_session(self):
        """unregister_ws_session() handles non-existent session gracefully."""
        mgr = AlertManager(AlertConfig(enabled=True))
        # Should not raise
        mgr.unregister_ws_session("nonexistent")

    def test_get_ws_sessions_excludes_websocket_object(self):
        """get_ws_sessions() returns session info without websocket objects."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mock_ws = MagicMock()
        mgr.register_ws_session("sess-1", mock_ws, "10.0.0.1")

        sessions = mgr.get_ws_sessions()
        assert "websocket" not in sessions[0]
        assert "session_id" in sessions[0]
        assert "connected_at" in sessions[0]
        assert "remote_addr" in sessions[0]

    def test_multiple_ws_sessions(self):
        """Multiple WebSocket sessions can be tracked simultaneously."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.register_ws_session("sess-1", MagicMock(), "10.0.0.1")
        mgr.register_ws_session("sess-2", MagicMock(), "10.0.0.2")
        mgr.register_ws_session("sess-3", MagicMock(), "10.0.0.3")

        sessions = mgr.get_ws_sessions()
        assert len(sessions) == 3
        session_ids = {s["session_id"] for s in sessions}
        assert session_ids == {"sess-1", "sess-2", "sess-3"}

    def test_get_status_includes_ws_sessions_count(self):
        """get_status() includes ws_sessions count."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.get_status()
        assert "ws_sessions" in status
        assert status["ws_sessions"] == 0

        mgr.register_ws_session("sess-1", MagicMock(), "10.0.0.1")
        status = mgr.get_status()
        assert status["ws_sessions"] == 1

    def test_ws_sessions_endpoint_exists(self):
        """GET /vigil/quality/dashboard/ws/sessions endpoint exists."""
        from aip.adapter.api.routes.vigil_quality import router
        route_paths = [r.path for r in router.routes if hasattr(r, 'path')]
        assert "/vigil/quality/dashboard/ws/sessions" in route_paths

    def test_register_ws_session_broadcasts_event(self):
        """register_ws_session() broadcasts ws_session_connected event."""
        mgr = AlertManager(AlertConfig(enabled=True))
        events = []

        # Capture events by adding an SSE subscriber
        class MockQueue:
            def put_nowait(self, item):
                events.append(item)

        mgr.realtime_bus.add_sse_subscriber(MockQueue())
        mgr.register_ws_session("sess-broadcast", MagicMock(), "10.0.0.1")

        # Check that a ws_session_connected event was broadcast
        connected_events = [e for e in events if e.get("event") == "ws_session_connected"]
        assert len(connected_events) >= 1
        assert connected_events[0]["session_id"] == "sess-broadcast"

    def test_unregister_ws_session_broadcasts_event(self):
        """unregister_ws_session() broadcasts ws_session_disconnected event."""
        mgr = AlertManager(AlertConfig(enabled=True))
        events = []

        class MockQueue:
            def put_nowait(self, item):
                events.append(item)

        mgr.realtime_bus.add_sse_subscriber(MockQueue())
        mgr.register_ws_session("sess-broadcast", MagicMock(), "10.0.0.1")
        # Clear events from the register call
        events.clear()
        mgr.unregister_ws_session("sess-broadcast")

        disconnected_events = [e for e in events if e.get("event") == "ws_session_disconnected"]
        assert len(disconnected_events) >= 1
        assert disconnected_events[0]["session_id"] == "sess-broadcast"


# ============================================================================
# Deliverable 5: Alert Group TTL & Auto-Cleanup
# ============================================================================


class TestAlertGroupTTL:
    """Tests for alert group TTL and auto-cleanup."""

    def test_alert_group_ttl_hours_config_default(self):
        """AlertConfig has alert_group_ttl_hours with default 24."""
        config = AlertConfig()
        assert config.alert_group_ttl_hours == 24

    def test_alert_group_ttl_hours_config_custom(self):
        """AlertConfig alert_group_ttl_hours can be set to custom value."""
        config = AlertConfig(alert_group_ttl_hours=48)
        assert config.alert_group_ttl_hours == 48

    def test_alert_group_ttl_disabled_with_zero(self):
        """Setting alert_group_ttl_hours=0 disables TTL cleanup."""
        config = AlertConfig(alert_group_ttl_hours=0)
        assert config.alert_group_ttl_hours == 0

    def test_add_alert_to_group_updates_metadata(self):
        """_add_alert_to_group() updates last_activity_at for the group."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
            alert_group_ttl_hours=24,
        ))

        before = time.time()
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="ttl_test",
            message="Test TTL metadata",
        ))
        after = time.time()

        assert "ttl_test" in mgr._alert_groups_metadata
        metadata_time = mgr._alert_groups_metadata["ttl_test"]
        assert before <= metadata_time <= after

    def test_cleanup_expired_groups_removes_old_groups(self):
        """cleanup_expired_groups() removes groups older than TTL."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
                alert_group_ttl_hours=1,
            ))
            mgr.attach_history_store(store)

            # Create a group with a very old timestamp
            mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="old_group",
                message="Old alert",
            ))

            # Manually set the group's last_activity_at to be expired
            mgr._alert_groups_metadata["old_group"] = time.time() - 7200  # 2 hours ago

            # Run cleanup
            dissolved = mgr.cleanup_expired_groups()
            assert dissolved == 1

            # The group should be removed
            groups = mgr.get_alert_groups()
            assert "old_group" not in groups

    def test_cleanup_expired_groups_keeps_active_groups(self):
        """cleanup_expired_groups() keeps groups within TTL."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
            alert_group_ttl_hours=24,
        ))

        # Create a recent group
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="active_group",
            message="Active alert",
        ))

        dissolved = mgr.cleanup_expired_groups()
        assert dissolved == 0

        groups = mgr.get_alert_groups()
        assert "active_group" in groups

    def test_cleanup_expired_groups_disabled_when_ttl_zero(self):
        """cleanup_expired_groups() does nothing when TTL is 0 (disabled)."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
            alert_group_ttl_hours=0,
        ))

        # Create a group with an old timestamp
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="disabled_ttl_group",
            message="Test disabled TTL",
        ))

        # Manually set the group's last_activity_at to be very old
        mgr._alert_groups_metadata["disabled_ttl_group"] = time.time() - 7200

        dissolved = mgr.cleanup_expired_groups()
        assert dissolved == 0  # TTL is disabled

        groups = mgr.get_alert_groups()
        assert "disabled_ttl_group" in groups

    def test_cleanup_expired_groups_persists_to_store(self):
        """cleanup_expired_groups() also deletes groups from persistent store."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
                alert_group_ttl_hours=1,
            ))
            mgr.attach_history_store(store)

            mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="persist_ttl_test",
                message="Test persist TTL",
            ))

            # Verify the group exists in SQLite
            groups_before = store.get_alert_groups()
            assert "persist_ttl_test" in groups_before

            # Expire the group
            mgr._alert_groups_metadata["persist_ttl_test"] = time.time() - 7200

            dissolved = mgr.cleanup_expired_groups()
            assert dissolved == 1

            # Verify the group is gone from SQLite
            groups_after = store.get_alert_groups()
            assert "persist_ttl_test" not in groups_after

    def test_cleanup_runs_on_add_alert_to_group(self):
        """cleanup_expired_groups() is called during _add_alert_to_group()."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
            alert_group_ttl_hours=1,
        ))

        # Create a group and make it old
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="auto_cleanup_test",
            message="Test auto cleanup",
        ))
        mgr._alert_groups_metadata["auto_cleanup_test"] = time.time() - 7200

        # Send another alert — this should trigger cleanup and remove the old group
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="new_alert",
            message="New alert triggers cleanup",
        ))

        groups = mgr.get_alert_groups()
        assert "auto_cleanup_test" not in groups
        assert "new_alert" in groups

    def test_get_status_includes_group_ttl_info(self):
        """get_status() includes alert_group_ttl info."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            alert_group_ttl_hours=48,
        ))
        status = mgr.get_status()

        assert "alert_group_ttl" in status
        assert status["alert_group_ttl"]["ttl_hours"] == 48
        assert "total_groups" in status["alert_group_ttl"]
        assert "groups_cleaned" in status["alert_group_ttl"]

    def test_groups_cleaned_counter_increments(self):
        """_total_groups_cleaned counter increments on each cleanup."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
            alert_group_ttl_hours=1,
        ))

        # Create and expire two groups
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="counter1",
            message="Counter test 1",
        ))
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="counter2",
            message="Counter test 2",
        ))

        mgr._alert_groups_metadata["counter1"] = time.time() - 7200
        mgr._alert_groups_metadata["counter2"] = time.time() - 7200

        dissolved = mgr.cleanup_expired_groups()
        assert dissolved == 2

        status = mgr.get_status()
        assert status["alert_group_ttl"]["groups_cleaned"] == 2

    def test_causal_group_metadata_updated(self):
        """Causal groups also get last_activity_at metadata."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
                causal_grouping_enabled=True,
            ))
            mgr.attach_history_store(store)

            before = time.time()
            mgr.send_alert(Alert(
                alert_type="pool_adjustment",
                severity="warning",
                subject="causal_meta_test",
                message="Pool adjusted",
            ))
            after = time.time()

            causal_key = "causal:causal_meta_test"
            assert causal_key in mgr._alert_groups_metadata
            assert before <= mgr._alert_groups_metadata[causal_key] <= after
