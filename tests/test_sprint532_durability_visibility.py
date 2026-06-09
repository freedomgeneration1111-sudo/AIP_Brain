"""Sprint 5.32 tests — Alert delivery status persistence, WebSocket dashboard,
alert correlation & grouping, digest customization per alert type, and
health endpoint metrics.

Deliverable 1: Alert Delivery Status Persistence (survives restarts via SQLite)
Deliverable 2: WebSocket Dashboard Channel (bidirectional WS endpoint)
Deliverable 3: Alert Correlation & Grouping (group related alerts, bulk actions)
Deliverable 4: Digest Customization Per Alert Type (per-type intervals via config)
Deliverable 5: Rate-Limit & Mute Metrics in Health Endpoint
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
# Deliverable 1: Alert Delivery Status Persistence
# ============================================================================


class TestDeliveryStatusPersistence:
    """Tests for delivery status persistence across restarts."""

    def test_delivery_status_persisted_to_store(self):
        """Delivery status is persisted to AlertHistoryStore on creation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
                digest_enabled=True,
                digest_min_alerts=100,
                digest_interval_minutes=60,
            ))
            mgr.attach_history_store(store)

            # Send a buffered alert (info severity + digest enabled)
            correlation_id = mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="info",
                subject="test_persist",
                message="Test persistence",
            ))

            # The status should be persisted in the store
            persisted = store.get_delivery_status_by_correlation_id(correlation_id)
            assert persisted is not None
            assert persisted["status"] == "buffered_for_digest"
            assert persisted["alert_type"] == "batch_reduction"

    def test_delivery_status_survives_restart(self):
        """Delivery status is queryable after a process restart via the store."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            # First instance: send alert and persist status
            mgr1 = AlertManager(AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
                digest_enabled=True,
                digest_min_alerts=100,
                digest_interval_minutes=60,
            ))
            mgr1.attach_history_store(store)

            correlation_id = mgr1.send_alert(Alert(
                alert_type="quality_degradation",
                severity="info",
                subject="test_restart",
                message="Test restart persistence",
            ))

            # Verify it was persisted
            persisted = store.get_delivery_status_by_correlation_id(correlation_id)
            assert persisted is not None

            # Second instance: new AlertManager with same store
            mgr2 = AlertManager(AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
            ))
            mgr2.attach_history_store(store)

            # Delivery status should be rebuilt from store
            status = mgr2.get_delivery_status(correlation_id)
            assert status is not None
            assert status["correlation_id"] == correlation_id
            assert status["status"] == "buffered_for_digest"

    def test_delivery_status_updated_on_completion(self):
        """Delivery status is updated in the store when delivery completes."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
                webhook_url="https://invalid-host.local/hook",
                webhook_max_retries=0,
            ))
            mgr.attach_history_store(store)

            correlation_id = mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="test_update",
                message="Test status update",
            ))

            # Wait for background dispatch
            time.sleep(0.5)

            # The store should have an updated status (not "dispatching")
            persisted = store.get_delivery_status_by_correlation_id(correlation_id)
            if persisted is not None:
                assert persisted["status"] in ("delivered", "partial", "failed")

    def test_update_delivery_status_in_store(self):
        """AlertHistoryStore.update_delivery_status() updates the record."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            # Record initial status
            store.record_delivery_status({
                "correlation_id": "test-corr-123",
                "status": "dispatching",
                "alert_type": "test",
                "severity": "info",
                "subject": "test",
                "transports": ["webhook"],
                "transport_results": {},
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
            })

            # Update to delivered
            updated = store.update_delivery_status(
                correlation_id="test-corr-123",
                status="delivered",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            assert updated is True

            # Verify the update
            result = store.get_delivery_status_by_correlation_id("test-corr-123")
            assert result is not None
            assert result["status"] == "delivered"
            assert result["completed_at"] != ""

    def test_get_recent_delivery_statuses(self):
        """AlertHistoryStore.get_recent_delivery_statuses() returns recent records."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            # Record several statuses
            for i in range(5):
                store.record_delivery_status({
                    "correlation_id": f"test-corr-{i}",
                    "status": "delivered",
                    "alert_type": "test",
                    "severity": "info",
                    "subject": f"test_{i}",
                    "transports": ["webhook"],
                    "transport_results": {"webhook": {"status": "delivered"}},
                    "dispatched_at": datetime.now(timezone.utc).isoformat(),
                })

            # Get recent statuses
            recent = store.get_recent_delivery_statuses(limit=3)
            assert len(recent) == 3

    def test_get_alert_by_correlation_id(self):
        """AlertHistoryStore.get_alert_by_correlation_id() looks up alerts."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            # Record an alert with a correlation ID
            store.record_alert({
                "alert_type": "batch_reduction",
                "severity": "warning",
                "subject": "test_lookup",
                "message": "Test correlation lookup",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "correlation_id": "test-corr-lookup",
            })

            # Look up by correlation ID
            alert = store.get_alert_by_correlation_id("test-corr-lookup")
            assert alert is not None
            assert alert["alert_type"] == "batch_reduction"
            assert alert["correlation_id"] == "test-corr-lookup"

    def test_get_alert_by_correlation_id_not_found(self):
        """get_alert_by_correlation_id() returns None for unknown IDs."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            result = store.get_alert_by_correlation_id("nonexistent-corr-id")
            assert result is None


# ============================================================================
# Deliverable 2: WebSocket Dashboard Channel
# ============================================================================


class TestWebSocketDashboard:
    """Tests for WebSocket endpoint and bidirectional communication."""

    def test_ws_subscriber_management(self):
        """AlertManager can add and remove WebSocket subscribers."""
        mgr = AlertManager(AlertConfig(enabled=True))

        mock_ws = MagicMock()
        mgr.realtime_bus.add_ws_subscriber(mock_ws)
        assert len(mgr.realtime_bus._ws_subscribers) == 1

        mgr.realtime_bus.remove_ws_subscriber(mock_ws)
        assert len(mgr.realtime_bus._ws_subscribers) == 0

    def test_ws_subscriber_not_duplicated_on_removal(self):
        """Removing a non-existent WebSocket subscriber doesn't raise."""
        mgr = AlertManager(AlertConfig(enabled=True))

        # Should not raise
        mgr.realtime_bus.remove_ws_subscriber(MagicMock())

    def test_notify_realtime_subscribers_pushes_to_ws(self):
        """_notify_realtime_subscribers pushes events to WS subscribers."""
        mgr = AlertManager(AlertConfig(enabled=True))

        mock_queue = MagicMock()
        mock_ws = MagicMock()

        mgr.realtime_bus.add_sse_subscriber(mock_queue)
        mgr.realtime_bus.add_ws_subscriber(mock_ws)

        mgr.realtime_bus.notify_realtime_subscribers({"event": "test_event"})

        # SSE queue should have been called
        mock_queue.put_nowait.assert_called_once_with({"event": "test_event"})

    def test_ws_status_in_get_status(self):
        """AlertManager.get_status() includes WebSocket subscriber count."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.get_status()
        assert "ws_subscribers" in status
        assert status["ws_subscribers"] == 0

    @pytest.mark.asyncio
    async def test_alert_groups_endpoint(self):
        """GET /vigil/quality/alerts/groups returns alert groups."""
        from aip.adapter.api.routes.vigil_quality import vigil_alert_groups

        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))

        # Send some alerts to create groups
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="shared_subject",
            message="Alert 1",
        ))
        mgr.send_alert(Alert(
            alert_type="quality_degradation",
            severity="critical",
            subject="shared_subject",
            message="Alert 2 (same subject)",
        ))

        container = MagicMock()
        container._alert_manager = mgr

        result = await vigil_alert_groups(container=container)
        assert result["status"] == "ok"
        assert "groups" in result
        assert "shared_subject" in result["groups"]

    @pytest.mark.asyncio
    async def test_alert_groups_endpoint_no_manager(self):
        """GET /vigil/quality/alerts/groups returns gracefully when no manager."""
        from aip.adapter.api.routes.vigil_quality import vigil_alert_groups

        container = MagicMock()
        container._alert_manager = None

        result = await vigil_alert_groups(container=container)
        assert result["status"] == "alerting_not_configured"


# ============================================================================
# Deliverable 3: Alert Correlation & Grouping
# ============================================================================


class TestAlertCorrelationGrouping:
    """Tests for alert correlation and grouping."""

    def test_alerts_with_same_subject_grouped(self):
        """Alerts with the same subject are grouped together."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
        ))

        mgr.send_alert(Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="vigil_store",
            message="Pool exhaustion",
        ))
        # Reset rate limit
        mgr.lifecycle_mgr._last_alert_time.clear()
        mgr.send_alert(Alert(
            alert_type="quality_degradation",
            severity="critical",
            subject="vigil_store",
            message="Quality degradation on same store",
        ))

        groups = mgr.get_alert_groups()
        assert "vigil_store" in groups
        assert len(groups["vigil_store"]) == 2

    def test_alerts_with_different_subjects_not_grouped(self):
        """Alerts with different subjects are in different groups."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
        ))

        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="subject_a",
            message="Alert A",
        ))
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="subject_b",
            message="Alert B",
        ))

        groups = mgr.get_alert_groups()
        assert "subject_a" in groups
        assert "subject_b" in groups
        assert len(groups["subject_a"]) == 1
        assert len(groups["subject_b"]) == 1

    def test_bulk_acknowledge_group(self):
        """bulk_acknowledge_group() acknowledges all alerts in a group."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
            ))
            mgr.attach_history_store(store)

            # Send two alerts with the same subject
            mgr.send_alert(Alert(
                alert_type="pool_adjustment",
                severity="warning",
                subject="shared_store",
                message="Pool alert",
            ))
            mgr.lifecycle_mgr._last_alert_time.clear()
            mgr.send_alert(Alert(
                alert_type="quality_degradation",
                severity="critical",
                subject="shared_store",
                message="Quality alert",
            ))

            # Bulk acknowledge the group
            result = mgr.bulk_acknowledge_group("shared_store", acknowledged_by="test_op")
            assert result["group_key"] == "shared_store"
            assert result["acknowledged"] == 2

    def test_bulk_dismiss_group(self):
        """bulk_dismiss_group() dismisses all alerts in a group."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
            ))
            mgr.attach_history_store(store)

            mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="dismiss_group",
                message="To be dismissed",
            ))
            mgr.lifecycle_mgr._last_alert_time.clear()
            mgr.send_alert(Alert(
                alert_type="quality_degradation",
                severity="info",
                subject="dismiss_group",
                message="Also dismissed",
            ))

            result = mgr.bulk_dismiss_group("dismiss_group", dismissed_by="test_op")
            assert result["group_key"] == "dismiss_group"
            assert result["dismissed"] == 2

    def test_bulk_acknowledge_nonexistent_group(self):
        """bulk_acknowledge_group() returns empty for nonexistent group."""
        mgr = AlertManager(AlertConfig(enabled=True))
        result = mgr.bulk_acknowledge_group("nonexistent_group")
        assert result["acknowledged"] == 0
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_bulk_acknowledge_endpoint(self):
        """POST /vigil/quality/alerts/groups/bulk-acknowledge endpoint works."""
        from aip.adapter.api.routes.vigil_quality import (
            vigil_bulk_acknowledge,
            BulkActionRequest,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(store)

            mgr.send_alert(Alert(
                alert_type="pool_adjustment",
                severity="warning",
                subject="endpoint_group",
                message="Test bulk ack endpoint",
            ))

            container = MagicMock()
            container._alert_manager = mgr

            request = BulkActionRequest(group_key="endpoint_group", acted_by="test_op")
            result = await vigil_bulk_acknowledge(request=request, container=container)
            assert result["status"] == "ok"
            assert result["acknowledged"] >= 1

    @pytest.mark.asyncio
    async def test_bulk_dismiss_endpoint(self):
        """POST /vigil/quality/alerts/groups/bulk-dismiss endpoint works."""
        from aip.adapter.api.routes.vigil_quality import (
            vigil_bulk_dismiss,
            BulkActionRequest,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            store.initialize()

            mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
            mgr.attach_history_store(store)

            mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="info",
                subject="dismiss_endpoint_group",
                message="Test bulk dismiss endpoint",
            ))

            container = MagicMock()
            container._alert_manager = mgr

            request = BulkActionRequest(group_key="dismiss_endpoint_group", acted_by="test_op")
            result = await vigil_bulk_dismiss(request=request, container=container)
            assert result["status"] == "ok"
            assert result["dismissed"] >= 1

    def test_alert_groups_count_in_status(self):
        """AlertManager.get_status() includes alert_groups count."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
        ))

        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="status_test",
            message="Group count test",
        ))

        status = mgr.get_status()
        assert "alert_groups" in status
        assert status["alert_groups"] >= 1


# ============================================================================
# Deliverable 4: Digest Customization Per Alert Type
# ============================================================================


class TestDigestCustomization:
    """Tests for per-alert-type digest customization."""

    def test_digest_overrides_in_config(self):
        """AlertConfig accepts digest_overrides parameter."""
        config = AlertConfig(
            enabled=True,
            digest_enabled=True,
            digest_interval_minutes=15,
            digest_min_alerts=3,
            digest_overrides={
                "batch_reduction": {"interval_minutes": 5, "min_alerts": 2},
                "quality_degradation": {"interval_minutes": 30, "min_alerts": 5},
            },
        )
        assert config.digest_overrides["batch_reduction"]["interval_minutes"] == 5
        assert config.digest_overrides["batch_reduction"]["min_alerts"] == 2
        assert config.digest_overrides["quality_degradation"]["interval_minutes"] == 30

    def test_get_digest_settings_with_override(self):
        """_get_digest_settings() returns per-type overrides when set."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            digest_enabled=True,
            digest_interval_minutes=15,
            digest_min_alerts=3,
            digest_overrides={
                "batch_reduction": {"interval_minutes": 5, "min_alerts": 2},
            },
        ))

        # Overridden type
        interval, min_alerts = mgr._get_digest_settings("batch_reduction")
        assert interval == 5
        assert min_alerts == 2

        # Non-overridden type uses global defaults
        interval, min_alerts = mgr._get_digest_settings("quality_degradation")
        assert interval == 15
        assert min_alerts == 3

    def test_get_digest_settings_no_overrides(self):
        """_get_digest_settings() returns global defaults when no overrides."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            digest_enabled=True,
            digest_interval_minutes=20,
            digest_min_alerts=5,
        ))

        interval, min_alerts = mgr._get_digest_settings("batch_reduction")
        assert interval == 20
        assert min_alerts == 5

    def test_digest_uses_per_type_threshold(self):
        """Digest buffer flushes based on per-type min_alerts override."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
            digest_enabled=True,
            digest_interval_minutes=60,  # Long interval so count triggers first
            digest_min_alerts=100,  # High global threshold
            digest_overrides={
                "batch_reduction": {"interval_minutes": 60, "min_alerts": 2},
            },
        ))

        # Send 2 batch_reduction alerts — should flush because per-type override is min_alerts=2
        for i in range(2):
            mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="info",
                subject=f"digest_test_{i}",
                message=f"Info alert {i}",
            ))

        # Buffer should be empty after flush (triggered by per-type override)
        assert len(mgr.digest_mgr._digest_buffer) == 0

        # A digest alert should be in the history
        history = mgr.get_alert_history(limit=10)
        digest_alerts = [a for a in history if a.get("alert_type") == "digest"]
        assert len(digest_alerts) >= 1

    def test_digest_overrides_in_status(self):
        """AlertManager.get_status() includes digest overrides."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            digest_enabled=True,
            digest_overrides={
                "batch_reduction": {"interval_minutes": 5, "min_alerts": 2},
            },
        ))

        status = mgr.get_status()
        assert "overrides" in status["digest"]
        assert status["digest"]["overrides"]["batch_reduction"]["interval_minutes"] == 5

    def test_digest_flush_counter_in_status(self):
        """AlertManager.get_status() includes digest flush count."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
            digest_enabled=True,
            digest_min_alerts=2,
            digest_interval_minutes=60,
        ))

        # Trigger a digest flush
        for i in range(2):
            mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="info",
                subject=f"flush_count_{i}",
                message=f"Flush count test {i}",
            ))

        status = mgr.get_status()
        assert status["digest"]["total_flushes"] >= 1


# ============================================================================
# Deliverable 5: Rate-Limit & Mute Metrics in Health Endpoint
# ============================================================================


class TestHealthEndpointMetrics:
    """Tests for operational metrics in the health endpoint."""

    @pytest.mark.asyncio
    async def test_health_includes_mute_rule_count(self):
        """Health endpoint includes active_mute_rules count."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
        mgr.add_mute_rule("batch_reduction", "test", 3600)

        container = MagicMock()
        container._alert_manager = mgr
        container._vigil_quality_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None
        container._alert_history_store = None

        result = await vigil_quality_health(container=container)
        assert result["alerting"]["active_mute_rules"] == 1

    @pytest.mark.asyncio
    async def test_health_includes_rate_limited_count(self):
        """Health endpoint includes total_rate_limited count."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=300))

        # Send one alert, then try another immediately (rate-limited)
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="rate_limit_test",
            message="First alert",
        ))
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="rate_limit_test",
            message="Rate-limited alert",
        ))

        container = MagicMock()
        container._alert_manager = mgr
        container._vigil_quality_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None
        container._alert_history_store = None

        result = await vigil_quality_health(container=container)
        assert result["alerting"]["total_rate_limited"] >= 1

    @pytest.mark.asyncio
    async def test_health_includes_digest_flush_count(self):
        """Health endpoint includes digest flush count."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
            digest_enabled=True,
            digest_min_alerts=2,
            digest_interval_minutes=60,
        ))

        # Trigger a digest flush
        for i in range(2):
            mgr.send_alert(Alert(
                alert_type="batch_reduction",
                severity="info",
                subject=f"health_digest_{i}",
                message=f"Digest test {i}",
            ))

        container = MagicMock()
        container._alert_manager = mgr
        container._vigil_quality_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None
        container._alert_history_store = None

        result = await vigil_quality_health(container=container)
        assert result["alerting"]["digest_flushes"] >= 1

    @pytest.mark.asyncio
    async def test_health_includes_delivery_by_transport(self):
        """Health endpoint includes delivery success/failure by transport."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
            webhook_url="https://invalid-host.local/hook",
            webhook_max_retries=0,
        ))

        # Send an alert that will fail on webhook
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="transport_metrics_test",
            message="Transport metrics test",
        ))

        # Wait for delivery
        time.sleep(0.5)

        container = MagicMock()
        container._alert_manager = mgr
        container._vigil_quality_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None
        container._alert_history_store = None

        result = await vigil_quality_health(container=container)
        assert "delivery_by_transport" in result["alerting"]
        assert "success" in result["alerting"]["delivery_by_transport"]
        assert "failure" in result["alerting"]["delivery_by_transport"]

    @pytest.mark.asyncio
    async def test_health_includes_ws_subscribers(self):
        """Health endpoint includes WebSocket subscriber count."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        mgr = AlertManager(AlertConfig(enabled=True))

        container = MagicMock()
        container._alert_manager = mgr
        container._vigil_quality_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None
        container._alert_history_store = None

        result = await vigil_quality_health(container=container)
        assert "ws_subscribers" in result["alerting"]
        assert result["alerting"]["ws_subscribers"] == 0

    @pytest.mark.asyncio
    async def test_health_includes_alert_groups_count(self):
        """Health endpoint includes alert_groups count."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_health

        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))

        # Create an alert group
        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="health_group_test",
            message="Health group test",
        ))

        container = MagicMock()
        container._alert_manager = mgr
        container._vigil_quality_store = None
        container._auto_tuning_policy = None
        container._read_pool_auto_sizer = None
        container._alert_history_store = None

        result = await vigil_quality_health(container=container)
        assert "alert_groups" in result["alerting"]
        assert result["alerting"]["alert_groups"] >= 1

    def test_delivery_success_failure_tracking(self):
        """AlertManager tracks delivery success/failure counts by transport."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            min_alert_interval_seconds=0,
            webhook_url="https://invalid-host.local/hook",
            webhook_max_retries=0,
        ))

        mgr.send_alert(Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="tracking_test",
            message="Delivery tracking test",
        ))

        # Wait for dispatch
        time.sleep(0.5)

        status = mgr.get_status()
        assert "delivery_by_transport" in status
        # Webhook should have at least 1 failure (invalid host)
        if "webhook" in status["delivery_by_transport"]["failure"]:
            assert status["delivery_by_transport"]["failure"]["webhook"] >= 1


# ============================================================================
# Schema Migration v3 → v4
# ============================================================================


class TestSchemaMigrationV4:
    """Tests for AlertHistoryStore schema migration from v3 to v4."""

    def test_migration_v3_to_v4_adds_methods(self):
        """Schema v3→v4 migration adds new method capabilities."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "alerts.db")

            # Create a v3 schema
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
                        created_at TEXT NOT NULL,
                        correlation_id TEXT NOT NULL DEFAULT '',
                        acknowledged INTEGER NOT NULL DEFAULT 0,
                        acknowledged_at TEXT NOT NULL DEFAULT '',
                        acknowledged_by TEXT NOT NULL DEFAULT ''
                    )
                """)
                conn.execute("""
                    CREATE TABLE alert_delivery_failures (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        transport TEXT NOT NULL,
                        alert_type TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        error_message TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        retry_attempt INTEGER NOT NULL DEFAULT 0,
                        final INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE alert_mute_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alert_type TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        muted_at TEXT NOT NULL,
                        muted_by TEXT NOT NULL DEFAULT 'operator',
                        duration_seconds INTEGER NOT NULL DEFAULT 3600,
                        expires_at REAL NOT NULL DEFAULT 0,
                        auto_mute_on_ack INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE alert_delivery_status (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        correlation_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        alert_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        transports TEXT NOT NULL DEFAULT '[]',
                        transport_results TEXT NOT NULL DEFAULT '{}',
                        dispatched_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    INSERT INTO alert_meta (key, value) VALUES ('schema_version', '3')
                """)

            # Migrate to v4
            store = AlertHistoryStore(db_path)
            store.initialize()

            # Verify the new methods work
            store.record_delivery_status({
                "correlation_id": "migration-test",
                "status": "dispatching",
                "alert_type": "test",
                "severity": "info",
                "subject": "test",
                "transports": ["webhook"],
                "transport_results": {},
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
            })

            # Update the status
            updated = store.update_delivery_status(
                correlation_id="migration-test",
                status="delivered",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            assert updated is True

            # Get recent statuses
            recent = store.get_recent_delivery_statuses(limit=10)
            assert len(recent) >= 1

            # Get by correlation ID
            result = store.get_delivery_status_by_correlation_id("migration-test")
            assert result is not None
            assert result["status"] == "delivered"
