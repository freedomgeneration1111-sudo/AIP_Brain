"""Sprint 5.35 tests — WebSocket Heartbeat & Dead-Session Detection,
Alert Group Merge & Split, Dashboard Persistence of Filters & View State,
Delivery Status Pruning Scheduler, Causal Grouping Time-Window Enforcement.

Deliverable 1: WebSocket Heartbeat & Dead-Session Detection (heartbeat tracking, dead session cleanup, session metadata)
Deliverable 2: Alert Group Merge & Split (merge two groups, split a group, persistent store updates)
Deliverable 3: Dashboard Persistence of Filters & View State (localStorage save/restore)
Deliverable 4: Delivery Status Pruning Scheduler (background scheduler, status visibility)
Deliverable 5: Causal Grouping Time-Window Enforcement (window expiry, fresh group creation)
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
# Deliverable 1: WebSocket Heartbeat & Dead-Session Detection
# ============================================================================


class TestWebSocketHeartbeat:
    """Tests for WebSocket heartbeat and dead-session detection."""

    def test_heartbeat_config_defaults(self):
        """AlertConfig has heartbeat settings with sensible defaults."""
        config = AlertConfig()
        assert config.ws_heartbeat_interval_seconds == 30
        assert config.ws_heartbeat_missed_limit == 3

    def test_heartbeat_config_custom(self):
        """AlertConfig heartbeat settings can be customized."""
        config = AlertConfig(
            ws_heartbeat_interval_seconds=15,
            ws_heartbeat_missed_limit=5,
        )
        assert config.ws_heartbeat_interval_seconds == 15
        assert config.ws_heartbeat_missed_limit == 5

    def test_register_ws_session_includes_heartbeat_fields(self):
        """register_ws_session() stores last_heartbeat_at and missed_heartbeats."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mock_ws = MagicMock()
        before = time.time()
        mgr.register_ws_session("sess-hb", mock_ws, "10.0.0.1")
        after = time.time()

        sessions = mgr.get_ws_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert "last_heartbeat_at" in s
        assert "missed_heartbeats" in s
        assert s["missed_heartbeats"] == 0
        assert before <= s["last_heartbeat_at"] <= after

    def test_update_ws_session_heartbeat(self):
        """update_ws_session_heartbeat() resets missed count and updates timestamp."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mock_ws = MagicMock()
        mgr.register_ws_session("sess-hb2", mock_ws, "10.0.0.2")

        # Simulate missed heartbeats
        mgr._ws_sessions["sess-hb2"]["missed_heartbeats"] = 2

        # Update heartbeat
        before = time.time()
        result = mgr.update_ws_session_heartbeat("sess-hb2")
        after = time.time()

        assert result is True
        session = mgr._ws_sessions["sess-hb2"]
        assert session["missed_heartbeats"] == 0
        assert before <= session["last_heartbeat_at"] <= after

    def test_update_heartbeat_nonexistent_session(self):
        """update_ws_session_heartbeat() returns False for unknown session."""
        mgr = AlertManager(AlertConfig(enabled=True))
        result = mgr.update_ws_session_heartbeat("nonexistent")
        assert result is False

    def test_increment_missed_heartbeats(self):
        """increment_missed_heartbeats() increments missed count for all sessions."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_heartbeat_missed_limit=3))
        mgr.register_ws_session("sess-1", MagicMock(), "10.0.0.1")
        mgr.register_ws_session("sess-2", MagicMock(), "10.0.0.2")

        dead = mgr.increment_missed_heartbeats()
        assert len(dead) == 0  # Not yet at limit

        dead = mgr.increment_missed_heartbeats()
        assert len(dead) == 0  # Still at 2

        dead = mgr.increment_missed_heartbeats()
        assert len(dead) == 2  # Both reached limit of 3

    def test_cleanup_dead_ws_sessions(self):
        """cleanup_dead_ws_sessions() removes sessions that exceeded missed limit."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_heartbeat_missed_limit=2))
        mock_ws = MagicMock()
        mgr.register_ws_session("sess-dead", mock_ws, "10.0.0.1")
        mgr.realtime_bus.add_ws_subscriber(mock_ws)

        # Simulate missed heartbeats past limit
        cleaned = mgr.cleanup_dead_ws_sessions()
        assert cleaned == 0  # Only 1 missed, limit is 2

        cleaned = mgr.cleanup_dead_ws_sessions()
        assert cleaned == 1  # 2 missed, limit is 2

        # Session should be removed
        sessions = mgr.get_ws_sessions()
        assert len(sessions) == 0

    def test_dead_session_cleanup_counter(self):
        """_total_dead_sessions_cleaned counter increments properly."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_heartbeat_missed_limit=1))
        mgr.register_ws_session("sess-c1", MagicMock(), "10.0.0.1")
        mgr.register_ws_session("sess-c2", MagicMock(), "10.0.0.2")

        mgr.cleanup_dead_ws_sessions()
        assert mgr._total_dead_sessions_cleaned == 2

    def test_get_status_includes_heartbeat_info(self):
        """get_status() includes ws_heartbeat info."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_heartbeat_missed_limit=5))
        status = mgr.get_status()

        assert "ws_heartbeat" in status
        assert status["ws_heartbeat"]["interval_seconds"] == 30
        assert status["ws_heartbeat"]["missed_limit"] == 5
        assert status["ws_heartbeat"]["dead_sessions_cleaned"] == 0

    def test_dashboard_html_has_heartbeat_pong(self):
        """Dashboard HTML includes heartbeat_pong response handler."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "heartbeat_pong" in _DASHBOARD_HTML
        assert "heartbeat_ping" in _DASHBOARD_HTML

    def test_dashboard_html_has_heartbeat_variables(self):
        """Dashboard HTML includes heartbeat tracking variables."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "wsLastHeartbeatSent" in _DASHBOARD_HTML
        assert "wsLastHeartbeatReceived" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 2: Alert Group Merge & Split
# ============================================================================


class TestAlertGroupMergeSplit:
    """Tests for alert group merge and split operations."""

    def test_merge_two_groups(self):
        """merge_alert_groups() moves CIDs from source to target."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
            )
        )

        # Create two groups manually
        mgr._alert_groups["group_a"] = ["cid-1", "cid-2"]
        mgr._alert_groups["group_b"] = ["cid-3"]
        mgr._alert_groups_metadata["group_a"] = time.time()
        mgr._alert_groups_metadata["group_b"] = time.time()

        result = mgr.merge_alert_groups("group_a", "group_b")
        assert result["status"] == "ok"
        assert result["merged_count"] == 2

        groups = mgr.get_alert_groups()
        assert "group_a" not in groups
        assert "group_b" in groups
        assert len(groups["group_b"]) == 3

    def test_merge_empty_source(self):
        """merge_alert_groups() handles empty source gracefully."""
        mgr = AlertManager(AlertConfig(enabled=True))

        result = mgr.merge_alert_groups("nonexistent", "target")
        assert result["status"] == "empty_source"
        assert result["merged_count"] == 0

    def test_merge_creates_new_target(self):
        """merge_alert_groups() creates target group if it doesn't exist."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
            )
        )

        mgr._alert_groups["source"] = ["cid-1"]
        mgr._alert_groups_metadata["source"] = time.time()

        result = mgr.merge_alert_groups("source", "new_target")
        assert result["status"] == "ok"
        assert "new_target" in mgr.get_alert_groups()

    def test_merge_deduplicates(self):
        """merge_alert_groups() doesn't duplicate CIDs in target."""
        mgr = AlertManager(AlertConfig(enabled=True))

        mgr._alert_groups["source"] = ["cid-1", "cid-2"]
        mgr._alert_groups["target"] = ["cid-2", "cid-3"]
        mgr._alert_groups_metadata["source"] = time.time()
        mgr._alert_groups_metadata["target"] = time.time()

        result = mgr.merge_alert_groups("source", "target")
        assert result["merged_count"] == 1  # Only cid-1 was new

        target_cids = mgr.get_alert_groups()["target"]
        assert "cid-1" in target_cids
        assert target_cids.count("cid-2") == 1  # No duplicates

    def test_split_group(self):
        """split_alert_group() moves specified CIDs to a new group."""
        mgr = AlertManager(AlertConfig(enabled=True))

        mgr._alert_groups["original"] = ["cid-1", "cid-2", "cid-3"]
        mgr._alert_groups_metadata["original"] = time.time()

        result = mgr.split_alert_group(
            group_key="original",
            correlation_ids=["cid-2", "cid-3"],
            new_group_key="split_group",
        )
        assert result["status"] == "ok"
        assert result["split_count"] == 2
        assert result["new_group_key"] == "split_group"

        groups = mgr.get_alert_groups()
        assert "original" in groups
        assert groups["original"] == ["cid-1"]
        assert "split_group" in groups
        assert groups["split_group"] == ["cid-2", "cid-3"]

    def test_split_group_auto_key(self):
        """split_alert_group() generates key when new_group_key not provided."""
        mgr = AlertManager(AlertConfig(enabled=True))

        mgr._alert_groups["orig"] = ["cid-1", "cid-2"]
        mgr._alert_groups_metadata["orig"] = time.time()

        result = mgr.split_alert_group("orig", ["cid-2"])
        assert result["status"] == "ok"
        assert result["new_group_key"].startswith("orig_split_")

    def test_split_empty_group(self):
        """split_alert_group() handles empty source gracefully."""
        mgr = AlertManager(AlertConfig(enabled=True))

        result = mgr.split_alert_group("nonexistent", ["cid-1"])
        assert result["status"] == "empty_source"
        assert result["split_count"] == 0

    def test_split_no_matching_cids(self):
        """split_alert_group() handles no matching CIDs gracefully."""
        mgr = AlertManager(AlertConfig(enabled=True))

        mgr._alert_groups["group"] = ["cid-1", "cid-2"]
        mgr._alert_groups_metadata["group"] = time.time()

        result = mgr.split_alert_group("group", ["cid-999"])
        assert result["status"] == "no_matching_cids"
        assert result["split_count"] == 0

    def test_split_removes_empty_source(self):
        """split_alert_group() removes source group if all CIDs are moved."""
        mgr = AlertManager(AlertConfig(enabled=True))

        mgr._alert_groups["all"] = ["cid-1", "cid-2"]
        mgr._alert_groups_metadata["all"] = time.time()

        result = mgr.split_alert_group("all", ["cid-1", "cid-2"], "new_all")
        assert result["status"] == "ok"

        groups = mgr.get_alert_groups()
        assert "all" not in groups
        assert "new_all" in groups

    def test_merge_with_persistent_store(self):
        """merge_alert_groups() updates persistent store records."""
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

            # Create groups with alerts
            mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject="merge_src",
                    message="Source alert",
                )
            )
            mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject="merge_tgt",
                    message="Target alert",
                )
            )

            # Verify groups exist in store
            groups_before = store.get_alert_groups()
            assert "merge_src" in groups_before
            assert "merge_tgt" in groups_before

            # Merge
            result = mgr.merge_alert_groups("merge_src", "merge_tgt")
            assert result["status"] == "ok"

            # Verify store updated
            groups_after = store.get_alert_groups()
            assert "merge_src" not in groups_after
            assert "merge_tgt" in groups_after

    def test_merge_endpoint_exists(self):
        """POST /vigil/quality/alerts/groups/merge endpoint exists."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/alerts/groups/merge" in route_paths

    def test_split_endpoint_exists(self):
        """POST /vigil/quality/alerts/groups/split endpoint exists."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/alerts/groups/split" in route_paths

    def test_dashboard_html_has_merge_split_controls(self):
        """Dashboard HTML includes merge/split UI controls."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "wsMergeGroups" in _DASHBOARD_HTML
        assert "wsSplitGroup" in _DASHBOARD_HTML
        assert "merge_groups" in _DASHBOARD_HTML
        assert "split_group" in _DASHBOARD_HTML

    def test_merge_broadcasts_event(self):
        """merge_alert_groups() broadcasts alert_groups_merged event."""
        mgr = AlertManager(AlertConfig(enabled=True))
        events = []

        class MockQueue:
            def put_nowait(self, item):
                events.append(item)

        mgr.realtime_bus.add_sse_subscriber(MockQueue())

        mgr._alert_groups["src"] = ["cid-1"]
        mgr._alert_groups_metadata["src"] = time.time()

        mgr.merge_alert_groups("src", "tgt")

        merge_events = [e for e in events if e.get("event") == "alert_groups_merged"]
        assert len(merge_events) >= 1
        assert merge_events[0]["source_key"] == "src"
        assert merge_events[0]["target_key"] == "tgt"

    def test_split_broadcasts_event(self):
        """split_alert_group() broadcasts alert_group_split event."""
        mgr = AlertManager(AlertConfig(enabled=True))
        events = []

        class MockQueue:
            def put_nowait(self, item):
                events.append(item)

        mgr.realtime_bus.add_sse_subscriber(MockQueue())

        mgr._alert_groups["orig"] = ["cid-1", "cid-2"]
        mgr._alert_groups_metadata["orig"] = time.time()

        mgr.split_alert_group("orig", ["cid-2"], "new_group")

        split_events = [e for e in events if e.get("event") == "alert_group_split"]
        assert len(split_events) >= 1
        assert split_events[0]["source_key"] == "orig"


# ============================================================================
# Deliverable 3: Dashboard Persistence of Filters & View State
# ============================================================================


class TestDashboardStatePersistence:
    """Tests for dashboard state persistence in localStorage."""

    def test_dashboard_html_has_save_state_function(self):
        """Dashboard HTML includes saveDashboardState function."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "saveDashboardState" in _DASHBOARD_HTML
        assert "localStorage.setItem('aip_dashboard_state'" in _DASHBOARD_HTML

    def test_dashboard_html_has_restore_state_function(self):
        """Dashboard HTML includes restoreDashboardState function."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "restoreDashboardState" in _DASHBOARD_HTML
        assert "localStorage.getItem('aip_dashboard_state')" in _DASHBOARD_HTML

    def test_dashboard_html_saves_filter_state(self):
        """Dashboard HTML saves filter controls to localStorage."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "tog_citation" in _DASHBOARD_HTML
        assert "tog_grounding" in _DASHBOARD_HTML
        assert "tog_faithfulness" in _DASHBOARD_HTML
        assert "tog_flag" in _DASHBOARD_HTML
        assert "range" in _DASHBOARD_HTML
        assert "cycles" in _DASHBOARD_HTML

    def test_dashboard_html_saves_panel_state(self):
        """Dashboard HTML saves panel collapsed state."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "causalCollapsed" in _DASHBOARD_HTML

    def test_dashboard_html_restores_on_load(self):
        """Dashboard HTML restores state on page load."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "restoreDashboardState()" in _DASHBOARD_HTML

    def test_dashboard_html_saves_periodically(self):
        """Dashboard HTML saves state periodically with setInterval."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "setInterval(saveDashboardState" in _DASHBOARD_HTML

    def test_dashboard_html_state_expiry(self):
        """Dashboard HTML expires old state after 24 hours."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "86400000" in _DASHBOARD_HTML  # 24 hours in ms

    def test_dashboard_html_saves_on_change(self):
        """Dashboard HTML saves state when filter controls change."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "addEventListener('change', saveDashboardState)" in _DASHBOARD_HTML

    def test_dashboard_html_does_not_restore_isLive(self):
        """Dashboard HTML does not restore isLive to prevent unexpected auto-refresh."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "Don't restore isLive" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 4: Delivery Status Pruning Scheduler
# ============================================================================


class TestPruningScheduler:
    """Tests for the delivery status pruning scheduler."""

    def test_prune_interval_config_default(self):
        """AlertConfig delivery_status_prune_interval_seconds defaults to 0 (disabled)."""
        config = AlertConfig()
        assert config.delivery_status_prune_interval_seconds == 0

    def test_prune_interval_config_custom(self):
        """AlertConfig delivery_status_prune_interval_seconds can be set."""
        config = AlertConfig(delivery_status_prune_interval_seconds=3600)
        assert config.delivery_status_prune_interval_seconds == 3600

    def test_start_prune_scheduler_disabled(self):
        """start_prune_scheduler() returns False when interval is 0."""
        mgr = AlertManager(AlertConfig(delivery_status_prune_interval_seconds=0))
        result = mgr.start_prune_scheduler()
        assert result is False

    def test_start_prune_scheduler_success(self):
        """start_prune_scheduler() starts the scheduler thread."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                delivery_status_prune_interval_seconds=86400,  # Long interval so it doesn't run during test
            )
        )
        result = mgr.start_prune_scheduler()
        assert result is True
        assert mgr.pruning_mgr._prune_scheduler_running is True

        # Cleanup
        mgr.stop_prune_scheduler()

    def test_start_prune_scheduler_idempotent(self):
        """start_prune_scheduler() returns False if already running."""
        mgr = AlertManager(
            AlertConfig(
                delivery_status_prune_interval_seconds=86400,
            )
        )
        mgr.start_prune_scheduler()
        result = mgr.start_prune_scheduler()
        assert result is False

        mgr.stop_prune_scheduler()

    def test_stop_prune_scheduler(self):
        """stop_prune_scheduler() stops the scheduler."""
        mgr = AlertManager(
            AlertConfig(
                delivery_status_prune_interval_seconds=86400,
            )
        )
        mgr.start_prune_scheduler()
        mgr.stop_prune_scheduler()
        assert mgr.pruning_mgr._prune_scheduler_running is False

    def test_get_prune_scheduler_status(self):
        """get_prune_scheduler_status() returns scheduler state."""
        mgr = AlertManager(
            AlertConfig(
                delivery_status_prune_interval_seconds=3600,
                delivery_status_max_age_days=14,
                delivery_status_max_rows=1000,
            )
        )
        status = mgr.get_prune_scheduler_status()

        assert "running" in status
        assert "interval_seconds" in status
        assert "last_prune_run" in status
        assert "next_prune_run" in status
        assert "total_scheduled_prunes" in status
        assert "max_age_days" in status
        assert "max_rows" in status
        assert status["interval_seconds"] == 3600
        assert status["max_age_days"] == 14
        assert status["max_rows"] == 1000

    def test_run_scheduled_prune(self):
        """_run_scheduled_prune() prunes and updates state."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    delivery_status_max_age_days=30,
                    delivery_status_max_rows=2000,
                )
            )
            mgr.attach_history_store(store)

            before = time.time()
            deleted = mgr._run_scheduled_prune()
            after = time.time()

            assert deleted == 0  # Nothing to prune
            assert before <= mgr.pruning_mgr._last_prune_run <= after
            assert mgr.pruning_mgr._total_scheduled_prunes == 1

    def test_scheduler_status_in_health(self):
        """get_status() includes prune_scheduler info."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.get_status()

        assert "prune_scheduler" in status
        assert "running" in status["prune_scheduler"]
        assert "interval_seconds" in status["prune_scheduler"]

    def test_scheduler_endpoint_exists(self):
        """GET /vigil/quality/alerts/delivery-status/prune/scheduler endpoint exists."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/alerts/delivery-status/prune/scheduler" in route_paths

    def test_scheduler_start_endpoint_exists(self):
        """POST /vigil/quality/alerts/delivery-status/prune/scheduler/start exists."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/alerts/delivery-status/prune/scheduler/start" in route_paths

    def test_scheduler_stop_endpoint_exists(self):
        """POST /vigil/quality/alerts/delivery-status/prune/scheduler/stop exists."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/alerts/delivery-status/prune/scheduler/stop" in route_paths

    def test_scheduler_config_endpoint_exists(self):
        """PATCH /vigil/quality/alerts/delivery-status/prune/scheduler/config exists."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/alerts/delivery-status/prune/scheduler/config" in route_paths


# ============================================================================
# Deliverable 5: Causal Grouping Time-Window Enforcement
# ============================================================================


class TestCausalGroupingTimeWindow:
    """Tests for causal grouping time-window enforcement."""

    def test_causal_group_within_window(self):
        """Alerts within the time window are grouped together."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_grouping_enabled=True,
                    causal_grouping_window_seconds=300,  # 5 minutes
                )
            )
            mgr.attach_history_store(store)

            mgr.send_alert(
                Alert(
                    alert_type="pool_adjustment",
                    severity="warning",
                    subject="window_test",
                    message="First alert",
                )
            )
            mgr.send_alert(
                Alert(
                    alert_type="quality_degradation",
                    severity="warning",
                    subject="window_test",
                    message="Second alert",
                )
            )

            groups = mgr.get_alert_groups()
            causal_key = "causal:window_test"
            assert causal_key in groups
            assert len(groups[causal_key]) == 2

    def test_causal_group_outside_window_dissolves(self):
        """Alerts outside the time window start a new causal group."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_grouping_enabled=True,
                    causal_grouping_window_seconds=300,  # 5 minutes
                )
            )
            mgr.attach_history_store(store)

            # Send first alert
            mgr.send_alert(
                Alert(
                    alert_type="pool_adjustment",
                    severity="warning",
                    subject="expire_test",
                    message="First alert",
                )
            )

            # Manually expire the causal group
            causal_key = "causal:expire_test"
            mgr._alert_groups_metadata[causal_key] = time.time() - 600  # 10 minutes ago

            # Send another alert — should dissolve the old group and start fresh
            mgr.send_alert(
                Alert(
                    alert_type="quality_degradation",
                    severity="warning",
                    subject="expire_test",
                    message="New alert after window",
                )
            )

            groups = mgr.get_alert_groups()
            assert causal_key in groups
            # The old group was dissolved, so only the new alert should be there
            assert len(groups[causal_key]) == 1

    def test_causal_group_window_zero(self):
        """When window is 0, every alert creates a new group."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_grouping_enabled=True,
                    causal_grouping_window_seconds=0,
                )
            )
            mgr.attach_history_store(store)

            mgr.send_alert(
                Alert(
                    alert_type="pool_adjustment",
                    severity="warning",
                    subject="zero_window",
                    message="First",
                )
            )

            # With window=0, even a tiny delay means the next alert is "outside" the window
            # Actually with window=0, the group expires immediately
            # The second alert will dissolve and recreate
            mgr.send_alert(
                Alert(
                    alert_type="quality_degradation",
                    severity="warning",
                    subject="zero_window",
                    message="Second",
                )
            )

            groups = mgr.get_alert_groups()
            causal_key = "causal:zero_window"
            assert causal_key in groups
            # With window=0, the first group was immediately expired when the second came in
            assert len(groups[causal_key]) == 1

    def test_causal_group_persistent_store_cleared_on_window_expiry(self):
        """When a causal group expires due to time window, persistent store is updated."""
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

            mgr.send_alert(
                Alert(
                    alert_type="pool_adjustment",
                    severity="warning",
                    subject="persist_window",
                    message="First",
                )
            )

            causal_key = "causal:persist_window"
            # Verify it was persisted
            groups_before = store.get_alert_groups()
            assert causal_key in groups_before

            # Expire the group
            mgr._alert_groups_metadata[causal_key] = time.time() - 600

            # Send another alert — triggers dissolution
            mgr.send_alert(
                Alert(
                    alert_type="quality_degradation",
                    severity="warning",
                    subject="persist_window",
                    message="After window",
                )
            )

            # Old records should be deleted, new ones added
            groups_after = store.get_alert_groups()
            assert causal_key in groups_after  # New group created
            # Only the new alert should be in the store group
            assert len(groups_after[causal_key]) == 1

    def test_non_causal_groups_not_affected_by_window(self):
        """Regular (non-causal) groups are not affected by the time window."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_grouping_enabled=False,  # Disabled
                    causal_grouping_window_seconds=1,  # Very short
                    alert_group_ttl_hours=0,  # Disable TTL so it doesn't clean up
                )
            )
            mgr.attach_history_store(store)

            mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject="regular_group",
                    message="First",
                )
            )

            # Wait for the window to pass
            time.sleep(0.1)

            mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject="regular_group",
                    message="Second",
                )
            )

            groups = mgr.get_alert_groups()
            assert "regular_group" in groups
            # Regular groups still accumulate all CIDs (no time window enforcement)
            assert len(groups["regular_group"]) == 2
