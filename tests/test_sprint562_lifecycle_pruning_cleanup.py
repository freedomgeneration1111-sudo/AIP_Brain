"""Sprint 5.62 tests — Lifecycle/Pruning extraction, wrapper removal, async fan-out.

Deliverable 1: Backward-compatibility wrapper removal
  - All proxy properties removed from AlertManager
  - All method delegation wrappers removed (except 19 public facade methods)
  - Test code updated to access sub-managers directly

Deliverable 2: AlertLifecycleManager extraction
  - AlertLifecycleManager owns _alert_history, _delivery_failures, _delivery_status,
    _mute_rules, _correlation_counter, _occurrence_tracker, _last_alert_time
  - get_status_summary() returns lifecycle metrics

Deliverable 3: PruningManager extraction
  - PruningManager owns _prune_scheduler_*, _pruning_history, _last_prune_run
  - get_status_summary() returns pruning metrics

Deliverable 4: Async fan-out optimization
  - RealtimeEventBus._push_event_to_ws_subscribers uses asyncio.gather()
  - RealtimeEventBus._flush_ws_batch uses asyncio.gather()
  - _safe_send_json async helper added

Deliverable 5: Sprint 539 test fixes
  - Tests updated to use mgr.delivery_mgr._delivery_receipts
  - Tests updated to use mgr.realtime_bus._ws_permessage_deflate_negotiated
"""

from __future__ import annotations

import queue
import time
from unittest.mock import MagicMock

import pytest

from aip.adapter.alerting import (
    Alert,
    AlertConfig,
    AlertLifecycleManager,
    AlertManager,
    PruningManager,
    RealtimeEventBus,
)

# ============================================================================
# Deliverable 1: Backward-compatibility wrapper removal
# ============================================================================


class TestWrapperRemoval:
    """Verify that old proxy attributes are no longer on AlertManager."""

    def test_throttle_proxies_removed(self):
        """Old ThrottleManager proxy properties no longer exist on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        # These should NOT exist as direct attributes on AlertManager
        assert (
            not hasattr(mgr, "_throttle_alert_timestamps")
            or getattr(type(mgr), "_throttle_alert_timestamps", None) is None
        )
        assert (
            not hasattr(mgr, "_circuit_breaker_active") or getattr(type(mgr), "_circuit_breaker_active", None) is None
        )
        assert (
            not hasattr(mgr, "_total_throttled_alerts") or getattr(type(mgr), "_total_throttled_alerts", None) is None
        )

    def test_prediction_proxies_removed(self):
        """Old PredictionManager proxy properties no longer exist on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert not hasattr(mgr, "_causal_predictions") or getattr(type(mgr), "_causal_predictions", None) is None
        assert not hasattr(mgr, "_transition_counts") or getattr(type(mgr), "_transition_counts", None) is None
        assert not hasattr(mgr, "_prediction_outcomes") or getattr(type(mgr), "_prediction_outcomes", None) is None

    def test_digest_proxies_removed(self):
        """Old DigestManager proxy properties no longer exist on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert not hasattr(mgr, "_digest_buffer") or getattr(type(mgr), "_digest_buffer", None) is None
        assert not hasattr(mgr, "_digest_last_flush") or getattr(type(mgr), "_digest_last_flush", None) is None
        assert not hasattr(mgr, "_total_digest_flushes") or getattr(type(mgr), "_total_digest_flushes", None) is None

    def test_ab_experiment_proxies_removed(self):
        """Old ABExperimentManager proxy properties no longer exist on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert not hasattr(mgr, "_ab_experiments") or getattr(type(mgr), "_ab_experiments", None) is None
        assert not hasattr(mgr, "_bandit_state") or getattr(type(mgr), "_bandit_state", None) is None
        assert (
            not hasattr(mgr, "_confidence_calibration_map")
            or getattr(type(mgr), "_confidence_calibration_map", None) is None
        )

    def test_facade_methods_still_exist(self):
        """Public facade methods are preserved for API compatibility."""
        mgr = AlertManager(AlertConfig(enabled=True))
        # ThrottleManager facades
        assert callable(getattr(mgr, "get_circuit_breaker_status", None))
        assert callable(getattr(mgr, "get_cb_auto_tune_status", None))
        assert callable(getattr(mgr, "compute_cb_auto_tune_threshold", None))
        assert callable(getattr(mgr, "get_cb_effective_threshold", None))
        assert callable(getattr(mgr, "update_cb_auto_tune", None))
        # PredictionManager facades
        assert callable(getattr(mgr, "predict_causal_chain", None))
        assert callable(getattr(mgr, "check_retrain_needed", None))
        assert callable(getattr(mgr, "persist_transition_model", None))
        assert callable(getattr(mgr, "load_transition_model", None))
        # DeliveryManager facades
        assert callable(getattr(mgr, "get_delivery_receipts", None))
        # RealtimeEventBus facades
        assert callable(getattr(mgr, "compress_ws_message", None))
        assert callable(getattr(mgr, "get_native_deflate_status", None))
        assert callable(getattr(mgr, "set_ws_permessage_deflate_negotiated", None))

    def test_sub_manager_accessors_work(self):
        """Sub-manager accessors return the correct instances."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert isinstance(mgr.throttle_mgr, type(mgr._throttle_mgr))
        assert isinstance(mgr.prediction_mgr, type(mgr._prediction_mgr))
        assert isinstance(mgr.digest_mgr, type(mgr._digest_mgr))
        assert isinstance(mgr.realtime_bus, RealtimeEventBus)
        assert isinstance(mgr.delivery_mgr, type(mgr._delivery_mgr))
        assert isinstance(mgr.lifecycle_mgr, AlertLifecycleManager)
        assert isinstance(mgr.pruning_mgr, PruningManager)
        assert isinstance(mgr.ab_experiment_mgr, type(mgr._ab_experiment_mgr))


# ============================================================================
# Deliverable 2: AlertLifecycleManager extraction
# ============================================================================


class TestAlertLifecycleManager:
    """Tests for the AlertLifecycleManager sub-manager."""

    def test_lifecycle_manager_has_correct_state(self):
        """AlertLifecycleManager owns the expected state variables."""
        mgr = AlertLifecycleManager(AlertConfig())
        assert hasattr(mgr, "_alert_history")
        assert hasattr(mgr, "_delivery_failures")
        assert hasattr(mgr, "_delivery_status")
        assert hasattr(mgr, "_mute_rules")
        assert hasattr(mgr, "_correlation_counter")
        assert hasattr(mgr, "_occurrence_tracker")
        assert hasattr(mgr, "_last_alert_time")

    def test_lifecycle_manager_initial_state(self):
        """AlertLifecycleManager initializes with empty state."""
        mgr = AlertLifecycleManager(AlertConfig())
        assert mgr._alert_history == []
        assert mgr._delivery_failures == []
        assert mgr._delivery_status == {}
        assert mgr._mute_rules == {}
        assert mgr._correlation_counter == 0
        assert mgr._occurrence_tracker == {}
        assert mgr._last_alert_time == {}

    def test_get_next_correlation_id(self):
        """get_next_correlation_id generates unique IDs."""
        mgr = AlertLifecycleManager(AlertConfig())
        id1 = mgr.get_next_correlation_id()
        id2 = mgr.get_next_correlation_id()
        assert id1.startswith("alert-")
        assert id2.startswith("alert-")
        assert id1 != id2
        assert mgr._correlation_counter == 2

    def test_record_alert(self):
        """record_alert appends to in-memory history."""
        mgr = AlertLifecycleManager(AlertConfig())
        alert_dict = {"alert_type": "test", "severity": "info"}
        mgr.record_alert(alert_dict)
        assert len(mgr._alert_history) == 1
        assert mgr._alert_history[0] == alert_dict

    def test_record_alert_caps_at_50(self):
        """record_alert caps history at MAX_ALERT_HISTORY."""
        mgr = AlertLifecycleManager(AlertConfig())
        for i in range(60):
            mgr.record_alert({"alert_type": "test", "i": i})
        assert len(mgr._alert_history) == 50

    def test_check_rate_limit(self):
        """check_rate_limit returns True when within min interval."""
        mgr = AlertLifecycleManager(AlertConfig(min_alert_interval_seconds=300))
        mgr.record_rate_limit_time("quality_degradation", "test_subject")
        # Immediately after recording, should be rate-limited
        assert mgr.check_rate_limit("quality_degradation", "test_subject") is True

    def test_check_rate_limit_allows_after_interval(self):
        """check_rate_limit returns False after interval elapses."""
        mgr = AlertLifecycleManager(AlertConfig(min_alert_interval_seconds=0))
        mgr.record_rate_limit_time("quality_degradation", "test_subject")
        assert mgr.check_rate_limit("quality_degradation", "test_subject") is False

    def test_check_mute_rule(self):
        """check_mute_rule returns 'muted' for muted pairs, None otherwise."""
        mgr = AlertLifecycleManager(AlertConfig())
        mgr.add_mute_rule("quality_degradation", "test_subject", duration_seconds=3600)
        assert mgr.check_mute_rule("quality_degradation", "test_subject") == "muted"
        assert mgr.check_mute_rule("pool_adjustment", "test_subject") is None

    def test_check_escalation(self):
        """check_escalation returns True when threshold is reached."""
        mgr = AlertLifecycleManager(
            AlertConfig(
                escalation_threshold=3,
                escalation_window_seconds=3600,
            )
        )
        alert = Alert(alert_type="test", severity="info", subject="subj", message="m")
        now = time.time()
        assert mgr.check_escalation(alert, now) is False
        assert mgr.check_escalation(alert, now) is False
        assert mgr.check_escalation(alert, now) is True  # 3rd occurrence triggers

    def test_get_status_summary(self):
        """get_status_summary returns expected keys."""
        mgr = AlertLifecycleManager(AlertConfig())
        summary = mgr.get_status_summary()
        assert "active_mute_rules" in summary
        assert "alert_history_count" in summary
        assert "delivery_failure_count" in summary
        assert "delivery_status_count" in summary
        assert "correlation_counter" in summary
        assert "rate_limited_subjects" in summary
        assert "occurrence_tracking_subjects" in summary

    def test_lifecycle_accessible_from_alert_manager(self):
        """AlertManager exposes lifecycle_mgr with correct state."""
        am = AlertManager(AlertConfig(enabled=True))
        # Send an alert to populate lifecycle state
        alert = Alert(
            alert_type="quality_degradation",
            severity="info",
            subject="lifecycle_test",
            message="Test lifecycle extraction",
        )
        am.send_alert(alert)
        # Verify lifecycle manager has the data
        assert len(am.lifecycle_mgr._alert_history) > 0
        assert am.lifecycle_mgr._correlation_counter > 0

    def test_set_history_store(self):
        """set_history_store propagates to the lifecycle manager."""
        mgr = AlertLifecycleManager(AlertConfig())
        assert mgr._history_store is None
        mock_store = MagicMock()
        mgr.set_history_store(mock_store)
        assert mgr._history_store is mock_store


# ============================================================================
# Deliverable 3: PruningManager extraction
# ============================================================================


class TestPruningManager:
    """Tests for the PruningManager sub-manager."""

    def test_pruning_manager_has_correct_state(self):
        """PruningManager owns the expected state variables."""
        mgr = PruningManager(AlertConfig())
        assert hasattr(mgr, "_prune_scheduler_thread")
        assert hasattr(mgr, "_prune_scheduler_running")
        assert hasattr(mgr, "_last_prune_run")
        assert hasattr(mgr, "_next_prune_run")
        assert hasattr(mgr, "_total_scheduled_prunes")
        assert hasattr(mgr, "_pruning_history")

    def test_pruning_manager_initial_state(self):
        """PruningManager initializes with empty/default state."""
        mgr = PruningManager(AlertConfig())
        assert mgr._prune_scheduler_thread is None
        assert mgr._prune_scheduler_running is False
        assert mgr._last_prune_run == 0.0
        assert mgr._next_prune_run == 0.0
        assert mgr._total_scheduled_prunes == 0
        assert mgr._pruning_history == []

    def test_start_prune_scheduler_disabled(self):
        """start_prune_scheduler returns False when interval is 0."""
        mgr = PruningManager(
            AlertConfig(
                delivery_status_prune_interval_seconds=0,
            )
        )
        assert mgr.start_prune_scheduler() is False

    def test_start_stop_prune_scheduler(self):
        """start_prune_scheduler starts and stop_prune_scheduler stops."""
        mgr = PruningManager(
            AlertConfig(
                delivery_status_prune_interval_seconds=600,
            )
        )
        assert mgr.start_prune_scheduler() is True
        assert mgr._prune_scheduler_running is True
        mgr.stop_prune_scheduler()
        assert mgr._prune_scheduler_running is False

    def test_get_prune_scheduler_status(self):
        """get_prune_scheduler_status returns expected keys."""
        mgr = PruningManager(
            AlertConfig(
                delivery_status_prune_interval_seconds=600,
            )
        )
        status = mgr.get_prune_scheduler_status()
        assert "running" in status
        assert "interval_seconds" in status
        assert "last_prune_run" in status
        assert "total_scheduled_prunes" in status
        assert "history" in status

    def test_get_pruning_history(self):
        """get_pruning_history returns pruning records."""
        mgr = PruningManager(AlertConfig())
        mgr._pruning_history = [
            {"timestamp": 1.0, "records_deleted": 5},
            {"timestamp": 2.0, "records_deleted": 3},
        ]
        history = mgr.get_pruning_history()
        assert len(history) == 2

    def test_get_pruning_history_with_limit(self):
        """get_pruning_history respects limit parameter."""
        mgr = PruningManager(AlertConfig())
        mgr._pruning_history = [{"timestamp": float(i), "records_deleted": i} for i in range(10)]
        history = mgr.get_pruning_history(limit=3)
        assert len(history) == 3

    def test_get_status_summary(self):
        """get_status_summary returns pruning scheduler status."""
        mgr = PruningManager(AlertConfig())
        summary = mgr.get_status_summary()
        assert "running" in summary
        assert "total_scheduled_prunes" in summary

    def test_pruning_accessible_from_alert_manager(self):
        """AlertManager exposes pruning_mgr with correct state."""
        am = AlertManager(AlertConfig(enabled=True))
        assert isinstance(am.pruning_mgr, PruningManager)
        assert am.pruning_mgr._prune_scheduler_running is False

    def test_set_history_store(self):
        """set_history_store propagates to the pruning manager."""
        mgr = PruningManager(AlertConfig())
        assert mgr._history_store is None
        mock_store = MagicMock()
        mgr.set_history_store(mock_store)
        assert mgr._history_store is mock_store


# ============================================================================
# Deliverable 4: Async fan-out optimization
# ============================================================================


class TestAsyncFanOut:
    """Tests for the asyncio.gather() fan-out optimization."""

    def test_safe_send_json_exists(self):
        """RealtimeEventBus has _safe_send_json async method."""
        assert hasattr(RealtimeEventBus, "_safe_send_json")

    @pytest.mark.asyncio
    async def test_safe_send_json_handles_success(self):
        """_safe_send_json sends data to WebSocket successfully."""
        bus = RealtimeEventBus(AlertConfig())
        mock_ws = MagicMock()

        async def _noop():
            pass

        mock_ws.send_json = _noop
        await bus._safe_send_json(mock_ws, {"event": "test"})

    @pytest.mark.asyncio
    async def test_safe_send_json_handles_failure(self):
        """_safe_send_json catches exceptions without raising."""
        bus = RealtimeEventBus(AlertConfig())
        mock_ws = MagicMock()

        async def _fail():
            raise RuntimeError("Connection closed")

        mock_ws.send_json = _fail
        # Should not raise
        await bus._safe_send_json(mock_ws, {"event": "test"})

    def test_push_event_to_ws_subscribers_works(self):
        """_push_event_to_ws_subscribers delivers to all subscribers."""
        config = AlertConfig(enabled=True, ws_batch_window_seconds=0.0)
        mgr = AlertManager(config)
        # Add mock WebSocket subscribers
        q1: queue.Queue = queue.Queue()
        q2: queue.Queue = queue.Queue()
        mgr.realtime_bus.add_ws_subscriber(q1)
        mgr.realtime_bus.add_ws_subscriber(q2)
        # Push event
        event = {"event": "test"}
        mgr.realtime_bus._push_event_to_ws_subscribers(event)
        # Verify both queues received the event
        assert q1.get(timeout=1.0) == event
        assert q2.get(timeout=1.0) == event


# ============================================================================
# Integration: Verify full Sprint 5.62 flow
# ============================================================================


class TestSprint562Integration:
    """Integration tests combining Sprint 5.62 features."""

    def test_status_includes_lifecycle_and_pruning(self):
        """get_status() includes lifecycle and pruning sub-manager data."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.get_status()
        # These should exist in the status output (they come from StatusAggregator)
        # which now queries lifecycle_mgr and pruning_mgr
        assert "active_mute_rules" in status or "lifecycle" in str(status)

    def test_send_alert_uses_lifecycle_mgr(self):
        """send_alert() delegates to lifecycle_mgr for rate-limiting and history."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                min_alert_interval_seconds=0,
            )
        )
        alert = Alert(
            alert_type="quality_degradation",
            severity="info",
            subject="integration_test",
            message="Test lifecycle integration",
        )
        mgr.send_alert(alert)
        # Verify lifecycle_mgr has the data
        assert len(mgr.lifecycle_mgr._alert_history) > 0
        assert mgr.lifecycle_mgr._correlation_counter > 0

    def test_attach_history_store_propagates(self):
        """attach_history_store propagates to lifecycle and pruning managers."""
        import os
        import tempfile

        from aip.adapter.alert_history_store import AlertHistoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            mgr = AlertManager(AlertConfig(enabled=True))
            mgr.attach_history_store(store)

            assert mgr._history_store is store
            assert mgr.lifecycle_mgr._history_store is store
            assert mgr.pruning_mgr._history_store is store

    def test_ab_experiment_via_sub_manager(self):
        """AB experiment operations work via sub-manager directly."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                ab_experiment_enabled=True,
            )
        )
        result = mgr.ab_experiment_mgr.start_ab_experiment(
            name="test-exp",
            control_config={"threshold": 0.5},
            variant_config={"threshold": 0.7},
        )
        assert "test-exp" in mgr.ab_experiment_mgr._ab_experiments

    def test_throttle_via_sub_manager(self):
        """Throttle operations work via sub-manager directly."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
            )
        )
        now = time.time()
        mgr.throttle_mgr.record_throttle_window(now)
        status = mgr.throttle_mgr.get_circuit_breaker_status()
        assert "active" in status
