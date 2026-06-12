"""Sprint 5.30 tests — Async alert dispatch, acknowledgment/dismissal,
escalation logic, restart deduplication, and retention config API.

Deliverable 1: Async Alert Dispatch (non-blocking send_alert, correlation ID)
Deliverable 2: Alert Acknowledgment/Dismissal (API + persistent state + dashboard)
Deliverable 3: Alert Severity Escalation (configurable occurrence-based escalation)
Deliverable 4: Alert Deduplication Across Restarts (rebuild rate-limit state)
Deliverable 5: Retention/Rollup Admin API Enhancement (PATCH/GET config endpoints)
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from aip.adapter.alert_history_store import AlertHistoryStore, SyncAlertHistoryBridge
from aip.adapter.alerting import (
    Alert,
    AlertConfig,
    AlertManager,
)
from aip.adapter.vigil.vigil_quality_store import VigilQualityStore

# ============================================================================
# Deliverable 1: Async Alert Dispatch
# ============================================================================


class TestAsyncAlertDispatch:
    """Tests for non-blocking send_alert() with correlation ID."""

    def test_send_alert_returns_correlation_id(self):
        """send_alert() returns a correlation ID string."""
        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
        result = mgr.send_alert(
            Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="test",
                message="Test alert",
            )
        )
        assert isinstance(result, str)
        assert result.startswith("alert-")
        assert len(result) > 10

    def test_send_alert_returns_empty_when_disabled(self):
        """send_alert() returns empty string when alerting is disabled."""
        mgr = AlertManager(AlertConfig(enabled=False))
        result = mgr.send_alert(
            Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="test",
                message="Test alert",
            )
        )
        assert result == ""

    def test_send_alert_returns_rate_limited(self):
        """send_alert() returns 'rate_limited' when rate-limited."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                min_alert_interval_seconds=300,
            )
        )
        # First alert should go through
        result1 = mgr.send_alert(
            Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="test",
                message="First alert",
            )
        )
        assert result1 != "rate_limited"
        assert result1 != ""

        # Second alert (same type+subject) should be rate-limited
        result2 = mgr.send_alert(
            Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="test",
                message="Second alert",
            )
        )
        assert result2 == "rate_limited"

    def test_send_alert_correlation_ids_are_unique(self):
        """Each send_alert() call produces a unique correlation ID."""
        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
        ids = set()
        for i in range(10):
            result = mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject=f"test_{i}",  # Different subjects to avoid rate-limiting
                    message=f"Alert {i}",
                )
            )
            assert result not in ids
            ids.add(result)
        assert len(ids) == 10

    def test_send_alert_does_not_block(self):
        """send_alert() returns quickly even when transports are slow.

        Since dispatch happens in a background thread, send_alert() should
        return almost immediately even if the webhook URL is invalid.
        """
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                webhook_url="https://invalid-host-that-does-not-exist.local/hook",
                min_alert_interval_seconds=0,
                webhook_max_retries=0,
            )
        )
        start = time.time()
        result = mgr.send_alert(
            Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="test",
                message="Should not block",
            )
        )
        elapsed = time.time() - start
        # send_alert should return in < 1 second (dispatch is in background)
        assert elapsed < 1.0
        assert isinstance(result, str)
        assert result.startswith("alert-")

    def test_correlation_id_persisted_to_store(self):
        """Correlation ID is persisted to the AlertHistoryStore."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(bridge)

            correlation_id = mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject="test",
                    message="With correlation ID",
                )
            )

            # Wait for background dispatch to complete
            time.sleep(0.1)

            alerts = bridge.get_alert_history()
            assert len(alerts) == 1
            assert alerts[0]["correlation_id"] == correlation_id


# ============================================================================
# Deliverable 2: Alert Acknowledgment / Dismissal
# ============================================================================


class TestAlertAcknowledgment:
    """Tests for alert acknowledgment and dismissal."""

    def test_acknowledge_alert_in_store(self):
        """AlertHistoryStore.acknowledge_alert() updates the alert state."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            bridge.record_alert(
                {
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "test",
                    "message": "Test alert",
                    "timestamp": "2025-06-01T12:00:00Z",
                }
            )

            result = bridge.acknowledge_alert(1, acknowledged_by="operator")
            assert result is True

            alert = bridge.get_alert_by_id(1)
            assert alert is not None
            assert alert["acknowledged"] == 1
            assert alert["acknowledged_by"] == "operator"
            assert alert["acknowledged_at"] != ""

    def test_dismiss_alert_in_store(self):
        """AlertHistoryStore.dismiss_alert() updates the alert state."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            bridge.record_alert(
                {
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "test",
                    "message": "Test alert",
                    "timestamp": "2025-06-01T12:00:00Z",
                }
            )

            result = bridge.dismiss_alert(1, dismissed_by="admin")
            assert result is True

            alert = bridge.get_alert_by_id(1)
            assert alert is not None
            assert alert["acknowledged"] == 2  # 2 = dismissed
            assert alert["acknowledged_by"] == "admin"

    def test_acknowledge_nonexistent_alert(self):
        """Acknowledging a non-existent alert returns False."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            result = bridge.acknowledge_alert(9999)
            assert result is False

    def test_get_alert_by_id(self):
        """AlertHistoryStore.get_alert_by_id() returns alert details."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            bridge.record_alert(
                {
                    "alert_type": "quality_degradation",
                    "severity": "critical",
                    "subject": "faithfulness",
                    "message": "Score dropped",
                    "timestamp": "2025-06-01T12:00:00Z",
                }
            )

            alert = bridge.get_alert_by_id(1)
            assert alert is not None
            assert alert["alert_type"] == "quality_degradation"
            assert alert["acknowledged"] == 0  # Not yet acknowledged

    def test_get_alert_by_id_not_found(self):
        """get_alert_by_id() returns None for non-existent ID."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            alert = bridge.get_alert_by_id(9999)
            assert alert is None

    def test_acknowledge_via_alert_manager(self):
        """AlertManager.acknowledge_alert() delegates to the store."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(bridge)

            mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject="test",
                    message="Test alert",
                )
            )
            time.sleep(0.1)

            # Get the alert ID from the store
            alerts = bridge.get_alert_history()
            assert len(alerts) == 1
            alert_id = alerts[0]["id"]

            result = mgr.acknowledge_alert(alert_id, "operator")
            assert result is True

            alert = bridge.get_alert_by_id(alert_id)
            assert alert["acknowledged"] == 1

    def test_dismiss_via_alert_manager(self):
        """AlertManager.dismiss_alert() delegates to the store."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(bridge)

            mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject="test",
                    message="Test alert",
                )
            )
            time.sleep(0.1)

            alerts = bridge.get_alert_history()
            alert_id = alerts[0]["id"]

            result = mgr.dismiss_alert(alert_id, "admin")
            assert result is True

            alert = bridge.get_alert_by_id(alert_id)
            assert alert["acknowledged"] == 2

    def test_acknowledge_without_store_returns_false(self):
        """AlertManager.acknowledge_alert() returns False when no store attached."""
        mgr = AlertManager(AlertConfig(enabled=True))
        result = mgr.acknowledge_alert(1)
        assert result is False

    def test_acknowledged_status_in_history_query(self):
        """Acknowledged alerts include the status in get_alert_history results."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            bridge.record_alert(
                {
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "test",
                    "message": "Test alert",
                    "timestamp": "2025-06-01T12:00:00Z",
                }
            )

            bridge.acknowledge_alert(1, "operator")

            alerts = bridge.get_alert_history()
            assert len(alerts) == 1
            assert alerts[0]["acknowledged"] == 1
            assert alerts[0]["acknowledged_by"] == "operator"

    @pytest.mark.asyncio
    async def test_acknowledge_endpoint(self):
        """POST /vigil/quality/alerts/{id}/acknowledge endpoint works."""
        from aip.adapter.api.routes.vigil_quality import AcknowledgeRequest, vigil_alert_acknowledge

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(bridge)

            container = MagicMock()
            container._alert_manager = mgr

            # Record an alert directly
            bridge.record_alert(
                {
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "test",
                    "message": "Test alert",
                    "timestamp": "2025-06-01T12:00:00Z",
                }
            )

            request = AcknowledgeRequest(acknowledged_by="test_operator")
            result = await vigil_alert_acknowledge(alert_id=1, request=request, container=container)
            assert result["status"] == "ok"
            assert result["acknowledged"] is True

    @pytest.mark.asyncio
    async def test_dismiss_endpoint(self):
        """POST /vigil/quality/alerts/{id}/dismiss endpoint works."""
        from aip.adapter.api.routes.vigil_quality import DismissRequest, vigil_alert_dismiss

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(bridge)

            container = MagicMock()
            container._alert_manager = mgr

            bridge.record_alert(
                {
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "test",
                    "message": "Test alert",
                    "timestamp": "2025-06-01T12:00:00Z",
                }
            )

            request = DismissRequest(dismissed_by="test_admin")
            result = await vigil_alert_dismiss(alert_id=1, request=request, container=container)
            assert result["status"] == "ok"
            assert result["dismissed"] is True


# ============================================================================
# Deliverable 3: Alert Severity Escalation
# ============================================================================


class TestAlertEscalation:
    """Tests for configurable severity escalation logic."""

    def test_escalation_config_accepted(self):
        """AlertConfig accepts escalation parameters."""
        config = AlertConfig(
            enabled=True,
            escalation_threshold=3,
            escalation_window_seconds=1800,
            escalation_severity="critical",
            escalation_additional_transports=["email"],
        )
        assert config.escalation_threshold == 3
        assert config.escalation_window_seconds == 1800
        assert config.escalation_severity == "critical"
        assert config.escalation_additional_transports == ["email"]

    def test_escalation_default_values(self):
        """AlertConfig escalation defaults are sensible."""
        config = AlertConfig()
        assert config.escalation_threshold == 3
        assert config.escalation_window_seconds == 3600
        assert config.escalation_severity == "critical"
        assert config.escalation_additional_transports == []

    def test_escalation_triggers_after_threshold(self):
        """Alert is escalated after threshold occurrences within the window."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
                escalation_threshold=3,
                escalation_window_seconds=3600,
                escalation_severity="critical",
            )
        )

        # Send 2 alerts below threshold — should not escalate
        for i in range(2):
            result = mgr.send_alert(
                Alert(
                    alert_type="quality_degradation",
                    severity="warning",
                    subject="faithfulness",
                    message=f"Alert {i + 1}",
                )
            )
            assert isinstance(result, str) and result.startswith("alert-")

        # The 3rd alert should trigger escalation
        result = mgr.send_alert(
            Alert(
                alert_type="quality_degradation",
                severity="warning",
                subject="faithfulness",
                message="Alert 3 — should escalate",
            )
        )
        assert isinstance(result, str) and result.startswith("alert-")

        # Check that the alert was escalated in history
        history = mgr.get_alert_history(limit=1)
        assert len(history) == 1
        assert history[0]["severity"] == "critical"
        assert "ESCALATED" in history[0]["message"]
        assert history[0]["data"].get("escalated") is True

    def test_escalation_disabled_with_threshold_zero(self):
        """Escalation is disabled when threshold is 0."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
                escalation_threshold=0,
            )
        )

        # Send many alerts — none should escalate
        for i in range(10):
            mgr.send_alert(
                Alert(
                    alert_type="quality_degradation",
                    severity="warning",
                    subject="faithfulness",
                    message=f"Alert {i + 1}",
                )
            )

        history = mgr.get_alert_history()
        for alert in history:
            assert alert["severity"] == "warning"

    def test_escalation_in_get_status(self):
        """AlertManager.get_status() includes escalation configuration."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                escalation_threshold=5,
                escalation_severity="critical",
            )
        )

        status = mgr.get_status()
        assert "escalation" in status
        assert status["escalation"]["threshold"] == 5
        assert status["escalation"]["severity"] == "critical"


# ============================================================================
# Deliverable 4: Alert Deduplication Across Restarts
# ============================================================================


class TestRestartDeduplication:
    """Tests for rebuilding rate-limiting state from persistent store."""

    def test_rate_limit_state_rebuilt_on_attach(self):
        """Attaching a history store rebuilds _last_alert_time from recent alerts."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")

            # First instance: record alerts to the store
            store1 = AlertHistoryStore(db_path)
            bridge1 = SyncAlertHistoryBridge(store1)
            bridge1.initialize()

            # Record an alert with a recent timestamp (within rate-limit window)
            recent_ts = datetime.now(timezone.utc).isoformat()
            bridge1.record_alert(
                {
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "graph_extraction",
                    "message": "Recent alert",
                    "timestamp": recent_ts,
                }
            )

            # Second instance: create a new AlertManager and attach the store
            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=300,
                )
            )
            mgr.attach_history_store(bridge1)

            # The rate-limit state should be rebuilt from the store
            assert len(mgr.lifecycle_mgr._last_alert_time) > 0

    def test_no_duplicate_storm_after_restart(self):
        """After restart, alerts within the rate-limit window are suppressed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")

            store = AlertHistoryStore(db_path)
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            # Record a very recent alert directly into the store
            recent_ts = datetime.now(timezone.utc).isoformat()
            bridge.record_alert(
                {
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "graph_extraction",
                    "message": "Just sent alert",
                    "timestamp": recent_ts,
                }
            )

            # Create a new AlertManager with the store
            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=300,
                )
            )
            mgr.attach_history_store(bridge)

            # Try to send the same alert — should be rate-limited
            result = mgr.send_alert(
                Alert(
                    alert_type="batch_reduction",
                    severity="warning",
                    subject="graph_extraction",
                    message="Duplicate after restart",
                )
            )
            assert result == "rate_limited"

    def test_get_recent_alerts_for_dedup(self):
        """AlertHistoryStore.get_recent_alerts_for_dedup() returns recent alerts."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            # Record a recent alert
            recent_ts = datetime.now(timezone.utc).isoformat()
            bridge.record_alert(
                {
                    "alert_type": "batch_reduction",
                    "severity": "warning",
                    "subject": "graph_extraction",
                    "message": "Recent alert",
                    "timestamp": recent_ts,
                }
            )

            # Record an old alert (outside the window)
            bridge.record_alert(
                {
                    "alert_type": "quality_degradation",
                    "severity": "warning",
                    "subject": "faithfulness",
                    "message": "Old alert",
                    "timestamp": "2020-01-01T00:00:00Z",
                }
            )

            # Query with a 5-minute window
            recent = bridge.get_recent_alerts_for_dedup(window_seconds=300)

            # Only the recent alert should be returned
            assert ("batch_reduction", "graph_extraction") in recent
            # The old alert should not be in the result (outside the window)
            assert ("quality_degradation", "faithfulness") not in recent


# ============================================================================
# Deliverable 5: Retention/Rollup Admin API Enhancement
# ============================================================================


class TestRetentionConfigAPI:
    """Tests for GET/PATCH /vigil/quality/retention/config endpoints."""

    def _create_store(self, tmp_path, **kwargs):
        db_path = os.path.join(str(tmp_path), "quality.db")
        store = VigilQualityStore(db_path, **kwargs)
        store.initialize()
        return store

    def test_get_config_returns_current_values(self, tmp_path):
        """VigilQualityStore.get_config() returns current configuration."""
        store = self._create_store(tmp_path, retention_days=90, rollup_age_days=7)
        config = store.get_config()

        assert config["retention_days"] == 90
        assert config["rollup_age_days"] == 7
        assert config["weekly_rollup_age_weeks"] == 4
        assert "max_history_rows" in config

    def test_update_config_changes_retention_days(self, tmp_path):
        """update_config() changes retention_days immediately."""
        store = self._create_store(tmp_path, retention_days=90)

        result = store.update_config(retention_days=60)
        assert result["retention_days"] == 60
        assert result["validation_errors"] == []

        # Verify the change persisted on the store instance
        assert store._retention_days == 60

    def test_update_config_changes_rollup_age_days(self, tmp_path):
        """update_config() changes rollup_age_days immediately."""
        store = self._create_store(tmp_path, rollup_age_days=7)

        result = store.update_config(rollup_age_days=14)
        assert result["rollup_age_days"] == 14
        assert store._rollup_age_days == 14

    def test_update_config_changes_weekly_rollup_age_weeks(self, tmp_path):
        """update_config() changes weekly_rollup_age_weeks immediately."""
        store = self._create_store(tmp_path, weekly_rollup_age_weeks=4)

        result = store.update_config(weekly_rollup_age_weeks=8)
        assert result["weekly_rollup_age_weeks"] == 8
        assert store._weekly_rollup_age_weeks == 8

    def test_update_config_rejects_negative_values(self, tmp_path):
        """update_config() rejects negative values with validation errors."""
        store = self._create_store(tmp_path, retention_days=90)

        result = store.update_config(retention_days=-1)
        assert len(result["validation_errors"]) == 1
        assert "retention_days must be >= 0" in result["validation_errors"][0]
        # The original value should be unchanged
        assert store._retention_days == 90

    def test_update_config_partial_update(self, tmp_path):
        """update_config() only updates provided fields."""
        store = self._create_store(tmp_path, retention_days=90, rollup_age_days=7)

        result = store.update_config(retention_days=60)
        assert result["retention_days"] == 60
        assert result["rollup_age_days"] == 7  # Unchanged

    def test_update_config_none_values_ignored(self, tmp_path):
        """update_config() ignores None values (keeps current settings)."""
        store = self._create_store(tmp_path, retention_days=90)

        result = store.update_config(
            retention_days=None,
            rollup_age_days=None,
            weekly_rollup_age_weeks=None,
        )
        assert result["retention_days"] == 90
        assert result["validation_errors"] == []

    @pytest.mark.asyncio
    async def test_retention_config_get_endpoint(self, tmp_path):
        """GET /vigil/quality/retention/config returns current configuration."""
        from aip.adapter.api.routes.vigil_quality import vigil_retention_config

        store = self._create_store(tmp_path, retention_days=90, rollup_age_days=7)

        container = MagicMock()
        container._vigil_quality_store = store

        result = await vigil_retention_config(container=container)
        assert result["status"] == "ok"
        assert result["config"]["retention_days"] == 90
        assert result["config"]["rollup_age_days"] == 7

    @pytest.mark.asyncio
    async def test_retention_config_patch_endpoint(self, tmp_path):
        """PATCH /vigil/quality/retention/config updates configuration."""
        from aip.adapter.api.routes.vigil_quality import (
            RetentionConfigUpdate,
            vigil_retention_config_update,
        )

        store = self._create_store(tmp_path, retention_days=90, rollup_age_days=7)

        container = MagicMock()
        container._vigil_quality_store = store

        update = RetentionConfigUpdate(retention_days=60, rollup_age_days=14)
        result = await vigil_retention_config_update(update=update, container=container)
        assert result["status"] == "ok"
        assert result["config"]["retention_days"] == 60
        assert result["config"]["rollup_age_days"] == 14

    @pytest.mark.asyncio
    async def test_retention_config_patch_negative_validation(self, tmp_path):
        """PATCH /vigil/quality/retention/config rejects negative values."""
        from aip.adapter.api.routes.vigil_quality import (
            RetentionConfigUpdate,
            vigil_retention_config_update,
        )

        store = self._create_store(tmp_path, retention_days=90)

        container = MagicMock()
        container._vigil_quality_store = store

        update = RetentionConfigUpdate(retention_days=-5)
        result = await vigil_retention_config_update(update=update, container=container)
        assert result["status"] == "validation_error"
        assert len(result["config"]["validation_errors"]) > 0

    @pytest.mark.asyncio
    async def test_retention_config_get_no_store(self):
        """GET /vigil/quality/retention/config returns gracefully when no store."""
        from aip.adapter.api.routes.vigil_quality import vigil_retention_config

        container = MagicMock()
        container._vigil_quality_store = None

        result = await vigil_retention_config(container=container)
        assert result["status"] == "quality_store_not_configured"

    @pytest.mark.asyncio
    async def test_retention_config_patch_no_store(self):
        """PATCH /vigil/quality/retention/config returns gracefully when no store."""
        from aip.adapter.api.routes.vigil_quality import (
            RetentionConfigUpdate,
            vigil_retention_config_update,
        )

        container = MagicMock()
        container._vigil_quality_store = None

        update = RetentionConfigUpdate(retention_days=60)
        result = await vigil_retention_config_update(update=update, container=container)
        assert result["status"] == "quality_store_not_configured"

    @pytest.mark.asyncio
    async def test_retention_status_includes_weekly_rollup_age(self, tmp_path):
        """get_retention_status() includes weekly_rollup_age_weeks."""
        store = self._create_store(tmp_path, weekly_rollup_age_weeks=8)
        # initialize() is async and wasn't awaited in _create_store;
        # we need DB tables for get_retention_status() which queries SQLite
        await store.initialize()
        status = await store.get_retention_status()
        assert status["weekly_rollup_age_weeks"] == 8


# ============================================================================
# Dashboard Updates (Sprint 5.30: acknowledgment status in dashboard)
# ============================================================================


class TestDashboardAcknowledgment:
    """Tests verifying the dashboard includes acknowledgment status display."""

    def test_dashboard_html_contains_ack_styling(self):
        """The dashboard HTML includes CSS for acknowledged/dismissed alerts."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "acknowledged" in _DASHBOARD_HTML
        assert "dismissed" in _DASHBOARD_HTML
        assert "ack-badge" in _DASHBOARD_HTML

    def test_dashboard_html_contains_ack_actions(self):
        """The dashboard HTML includes Ack/Dismiss buttons for open alerts."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "btn-ack" in _DASHBOARD_HTML
        assert "btn-dismiss" in _DASHBOARD_HTML
        assert "ackAlert" in _DASHBOARD_HTML
        assert "dismissAlert" in _DASHBOARD_HTML

    def test_dashboard_html_contains_acknowledge_api_url(self):
        """The dashboard JavaScript calls the acknowledge/dismiss endpoints."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "/acknowledge" in _DASHBOARD_HTML
        assert "/dismiss" in _DASHBOARD_HTML


# ============================================================================
# Schema Migration v1 → v2
# ============================================================================


class TestSchemaMigrationV2:
    """Tests for AlertHistoryStore schema migration from v1 to v2."""

    def test_migration_adds_acknowledged_columns(self):
        """Schema v1→v2 migration adds acknowledged columns."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")

            # Create a v1 schema manually
            with sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE alert_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE alert_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alert_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        message TEXT NOT NULL,
                        data TEXT NOT NULL DEFAULT '{}',
                        timestamp TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    INSERT INTO alert_meta (key, value) VALUES ('schema_version', '1')
                """)
                # Insert a v1 alert
                conn.execute("""
                    INSERT INTO alert_history (alert_type, severity, subject, message, data, timestamp, created_at)
                    VALUES ('test', 'info', 'test', 'v1 alert', '{}', '2025-06-01T12:00:00Z', '2025-06-01T12:00:00Z')
                """)

            # Now open with AlertHistoryStore which should migrate v1→v2
            store = AlertHistoryStore(db_path)
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            # The v1 alert should be queryable with acknowledged fields
            alerts = bridge.get_alert_history()
            assert len(alerts) == 1
            assert alerts[0]["acknowledged"] == 0
            assert alerts[0]["acknowledged_by"] == ""
            assert alerts[0]["correlation_id"] == ""
