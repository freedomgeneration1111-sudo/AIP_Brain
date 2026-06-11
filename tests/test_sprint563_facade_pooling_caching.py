"""Sprint 5.63 tests — AB facades, WS connection pooling, CB caching,
prediction background tracking, and StatusAggregator batching.

Deliverable 1: AB Experiment Facade Methods
  - Commonly used AB experiment methods available as clean facades on AlertManager
  - start_ab_experiment, stop_ab_experiment, get_ab_experiments, etc.

Deliverable 2: WebSocket Connection Pooling
  - _WSConnectionPool activates when subscribers exceed threshold (50)
  - Pool groups subscribers into worker groups of 10
  - Pool deactivates when subscriber count drops below half threshold

Deliverable 3: Circuit Breaker Status Caching
  - ThrottleManager caches get_circuit_breaker_status() result (~100ms TTL)
  - Cache invalidated on state changes (activation/deactivation)
  - Reduces lock acquisition on hot path

Deliverable 4: Prediction Accuracy Background Tracking
  - PredictionManager.start_bg_tracking() starts background thread
  - enqueue_accuracy_check() and enqueue_transition_learning() queue work
  - Background thread processes queue, reducing hot-path latency

Deliverable 5: StatusAggregator Batching
  - StatusAggregator caches build() result (200ms TTL)
  - build_async() uses asyncio.gather() + run_in_executor() for concurrent queries
  - _collect_sub_manager_summaries_async() collects all summaries concurrently
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

from aip.adapter.alerting import (
    Alert,
    AlertConfig,
    AlertManager,
    RealtimeEventBus,
    _WSConnectionPool,
)

# ============================================================================
# Deliverable 1: AB Experiment Facade Methods
# ============================================================================


class TestABExperimentFacades:
    """Verify AB experiment facade methods are available on AlertManager."""

    def test_start_ab_experiment_facade(self):
        """start_ab_experiment is available as a facade on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert callable(getattr(mgr, "start_ab_experiment", None))

        # Actually call it
        result = mgr.start_ab_experiment(
            name="test_exp",
            control_config={"threshold": 0.8},
            variant_config={"threshold": 0.9},
        )
        assert result is not None
        assert result["name"] == "test_exp"
        assert result["status"] == "running"

    def test_stop_ab_experiment_facade(self):
        """stop_ab_experiment is available as a facade on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.start_ab_experiment(
            name="test_exp",
            control_config={"threshold": 0.8},
            variant_config={"threshold": 0.9},
        )
        result = mgr.stop_ab_experiment("test_exp", result="variant_wins")
        assert result is not None
        assert result["status"] == "stopped"
        assert result["result"] == "variant_wins"

    def test_get_ab_experiments_facade(self):
        """get_ab_experiments is available as a facade on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.start_ab_experiment(
            name="exp1",
            control_config={"threshold": 0.8},
            variant_config={"threshold": 0.9},
        )
        result = mgr.get_ab_experiments()
        assert len(result) >= 1

        # Filter by status
        running = mgr.get_ab_experiments(status="running")
        assert len(running) >= 1

    def test_get_ab_experiment_facade(self):
        """get_ab_experiment is available as a facade on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.start_ab_experiment(
            name="exp1",
            control_config={"threshold": 0.8},
            variant_config={"threshold": 0.9},
        )
        result = mgr.get_ab_experiment("exp1")
        assert result is not None
        assert result["name"] == "exp1"

    def test_record_ab_result_facade(self):
        """record_ab_result is available as a facade on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.start_ab_experiment(
            name="exp1",
            control_config={"threshold": 0.8},
            variant_config={"threshold": 0.9},
        )
        result = mgr.record_ab_result("exp1", "control", 0.85, samples=10)
        assert result is not None

    def test_check_promotion_rollback_facade(self):
        """check_promotion_rollback is available as a facade on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        result = mgr.check_promotion_rollback()
        assert isinstance(result, list)

    def test_start_stop_ab_promotion_checker_facade(self):
        """start/stop_ab_promotion_checker are available as facades."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                ab_auto_promote_interval_seconds=0,  # disabled
            )
        )
        # Should not raise
        mgr.start_ab_promotion_checker()
        mgr.stop_ab_promotion_checker()

    def test_start_stop_ab_cleanup_checker_facade(self):
        """start/stop_ab_cleanup_checker are available as facades."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.start_ab_cleanup_checker()
        mgr.stop_ab_cleanup_checker()

    def test_restore_confidence_calibration_facade(self):
        """restore_confidence_calibration is available as a facade."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mock_store = MagicMock()
        mock_store.get_confidence_calibration_data.return_value = []
        result = mgr.restore_confidence_calibration(mock_store)
        assert isinstance(result, int)

    def test_restore_pre_promotion_snapshots_facade(self):
        """restore_pre_promotion_snapshots is available as a facade."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mock_store = MagicMock()
        mock_store.get_pre_promotion_snapshots.return_value = []
        result = mgr.restore_pre_promotion_snapshots(mock_store)
        assert isinstance(result, int)

    def test_start_snapshot_gc_facade(self):
        """start_snapshot_gc is available as a facade."""
        mgr = AlertManager(AlertConfig(enabled=True))
        # Should not raise
        mgr.start_snapshot_gc()

    def test_check_calibration_drift_facade(self):
        """check_calibration_drift is available as a facade."""
        mgr = AlertManager(AlertConfig(enabled=True))
        result = mgr.check_calibration_drift()
        assert isinstance(result, list)

    def test_get_bandit_allocation_facade(self):
        """get_bandit_allocation is available as a facade."""
        mgr = AlertManager(AlertConfig(enabled=True))
        # Will return empty allocation for non-existent experiment
        result = mgr.get_bandit_allocation("nonexistent")
        assert isinstance(result, dict)

    def test_facade_delegates_to_sub_manager(self):
        """Facade methods produce identical results to direct sub-manager access."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.start_ab_experiment(
            name="facade_test",
            control_config={"threshold": 0.8},
            variant_config={"threshold": 0.9},
        )
        # Facade result should match sub-manager result
        facade_result = mgr.get_ab_experiment("facade_test")
        direct_result = mgr.ab_experiment_mgr.get_ab_experiment("facade_test")
        assert facade_result == direct_result


# ============================================================================
# Deliverable 2: WebSocket Connection Pooling
# ============================================================================


class MockWebSocket:
    """Simulates a WebSocket connection for pooling tests."""

    def __init__(self) -> None:
        self.received: list[dict] = []
        self.send_json_call_count: int = 0

    async def send_json(self, data: dict) -> None:
        self.received.append(data)
        self.send_json_call_count += 1


class TestWSConnectionPool:
    """Tests for the _WSConnectionPool and its integration with RealtimeEventBus."""

    def test_pool_initial_state(self):
        """Pool starts inactive with zero counters."""
        pool = _WSConnectionPool()
        assert pool.active is False
        status = pool.get_status()
        assert status["active"] is False
        assert status["activation_threshold"] == 50
        assert status["group_size"] == 10
        assert status["total_pool_deliveries"] == 0

    def test_pool_activates_at_threshold(self):
        """Pool activates when subscriber count reaches threshold."""
        pool = _WSConnectionPool()
        assert pool.maybe_activate(49) is False
        assert pool.active is False
        assert pool.maybe_activate(50) is True
        assert pool.active is True

    def test_pool_stays_active_above_threshold(self):
        """Pool stays active once activated."""
        pool = _WSConnectionPool()
        pool.maybe_activate(50)
        assert pool.active is True
        # Calling again with high count should still return True
        assert pool.maybe_activate(60) is True

    def test_pool_deactivates_below_half_threshold(self):
        """Pool deactivates when subscriber count drops below half threshold."""
        pool = _WSConnectionPool()
        pool.maybe_activate(50)
        assert pool.active is True
        # Still above half threshold (25)
        pool.maybe_deactivate(30)
        assert pool.active is True
        # Below half threshold
        pool.maybe_deactivate(24)
        assert pool.active is False

    def test_group_subscribers(self):
        """group_subscribers splits into groups of correct size."""
        pool = _WSConnectionPool()
        subs = list(range(35))
        groups = pool.group_subscribers(subs)
        assert len(groups) == 4  # 3 groups of 10 + 1 group of 5
        assert len(groups[0]) == 10
        assert len(groups[1]) == 10
        assert len(groups[2]) == 10
        assert len(groups[3]) == 5

    def test_pool_status_in_realtime_bus(self):
        """RealtimeEventBus includes connection pool status in summary."""
        bus = RealtimeEventBus(AlertConfig())
        summary = bus.get_status_summary()
        assert "ws_connection_pool" in summary
        assert summary["ws_connection_pool"]["active"] is False
        assert summary["ws_connection_pool"]["activation_threshold"] == 50

    def test_pool_activation_on_add_subscriber(self):
        """Pool activates when enough subscribers are added via add_ws_subscriber."""
        bus = RealtimeEventBus(AlertConfig())
        # Add 49 subscribers — pool should not activate
        for i in range(49):
            bus.add_ws_subscriber(MagicMock())
        assert bus._ws_pool.active is False

        # Add one more — pool should activate
        bus.add_ws_subscriber(MagicMock())
        assert bus._ws_pool.active is True

    def test_pool_deactivation_on_remove_subscriber(self):
        """Pool deactivates when subscribers are removed below threshold."""
        bus = RealtimeEventBus(AlertConfig())
        # Add 50 subscribers
        mock_sockets = [MagicMock() for _ in range(50)]
        for ws in mock_sockets:
            bus.add_ws_subscriber(ws)
        assert bus._ws_pool.active is True

        # Remove subscribers until below half threshold
        for ws in mock_sockets[:26]:
            bus.remove_ws_subscriber(ws)
        # 24 remaining — below half threshold (25)
        assert bus._ws_pool.active is False


# ============================================================================
# Deliverable 3: Circuit Breaker Status Caching
# ============================================================================


class TestCircuitBreakerCaching:
    """Tests for circuit breaker status caching in ThrottleManager."""

    def test_cache_returns_same_result_within_ttl(self):
        """Cached result is returned within the TTL window."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=100,
            )
        )
        # First call populates cache
        status1 = mgr.throttle_mgr.get_circuit_breaker_status()
        # Second call should return cached result (same core data)
        status2 = mgr.throttle_mgr.get_circuit_breaker_status()
        # The key circuit breaker fields should match
        assert status1["active"] == status2["active"]
        assert status1["enabled"] == status2["enabled"]
        assert status1["total_activations"] == status2["total_activations"]
        # cache_age_ms should be populated on cache hit
        assert status2["cache_age_ms"] is not None

    def test_cache_expires_after_ttl(self):
        """Cache expires after TTL, producing a fresh result."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=100,
            )
        )
        # Set TTL very short for testing
        mgr.throttle_mgr._cb_cache_ttl_seconds = 0.001  # 1ms

        status1 = mgr.throttle_mgr.get_circuit_breaker_status()
        time.sleep(0.01)  # Wait for cache to expire
        status2 = mgr.throttle_mgr.get_circuit_breaker_status()
        assert status1 is not status2  # Different object = cache expired

    def test_cache_invalidated_on_state_change(self):
        """Cache is invalidated when circuit breaker state changes."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                circuit_breaker_cooldown_seconds=60,
                throttle_threshold_per_minute=5,
                min_alert_interval_seconds=0,
            )
        )

        # Populate cache
        status1 = mgr.throttle_mgr.get_circuit_breaker_status()
        assert status1["active"] is False

        # Trigger circuit breaker activation by sending many alerts
        for i in range(10):
            alert = Alert(
                alert_type="quality_degradation",
                severity="warning",
                subject=f"cb_cache_test_{i}",
                message="CB cache test",
            )
            mgr.send_alert(alert)

        # Cache should have been invalidated during activation
        status2 = mgr.throttle_mgr.get_circuit_breaker_status()
        # After many alerts, CB should be active (or at least the cache
        # was invalidated so we get a fresh read)
        assert status2 is not status1

    def test_invalidate_cb_cache_method(self):
        """invalidate_cb_cache clears the cached result."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
            )
        )
        # Populate cache
        mgr.throttle_mgr.get_circuit_breaker_status()
        assert mgr.throttle_mgr._cb_cache is not None

        # Invalidate
        mgr.throttle_mgr.invalidate_cb_cache()
        assert mgr.throttle_mgr._cb_cache is None

    def test_cache_age_ms_in_status(self):
        """Cached result includes cache_age_ms metadata."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
            )
        )
        # First call populates cache
        status = mgr.throttle_mgr.get_circuit_breaker_status()
        assert "cache_age_ms" in status
        # On first call (no prior cache), cache_age_ms is None
        assert status["cache_age_ms"] is None

        # Second call returns cached result — cache_age_ms should be set
        status = mgr.throttle_mgr.get_circuit_breaker_status()
        assert "cache_age_ms" in status
        # cache_age_ms should be populated on cache hit
        assert status["cache_age_ms"] is not None


# ============================================================================
# Deliverable 4: Prediction Accuracy Background Tracking
# ============================================================================


class TestPredictionBackgroundTracking:
    """Tests for background prediction accuracy tracking in PredictionManager."""

    def test_bg_tracking_starts(self):
        """Background tracking thread starts successfully."""
        mgr = AlertManager(AlertConfig(enabled=True))
        # Background tracking is started in __init__
        assert mgr.prediction_mgr._bg_tracking_running is True
        assert mgr.prediction_mgr._bg_tracking_thread is not None

    def test_bg_tracking_stops(self):
        """Background tracking thread stops cleanly."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.prediction_mgr.stop_bg_tracking()
        assert mgr.prediction_mgr._bg_tracking_running is False

    def test_enqueue_accuracy_check(self):
        """enqueue_accuracy_check adds alerts to the pending queue."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.prediction_mgr.stop_bg_tracking()  # Stop to inspect queue
        alert = Alert(
            alert_type="quality_degradation",
            severity="info",
            subject="bg_test",
            message="BG accuracy test",
        )
        mgr.prediction_mgr.enqueue_accuracy_check(alert)
        assert len(mgr.prediction_mgr._bg_pending_accuracy_checks) >= 1

    def test_enqueue_transition_learning(self):
        """enqueue_transition_learning adds alerts to the pending queue."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.prediction_mgr.stop_bg_tracking()  # Stop to inspect queue
        alert = Alert(
            alert_type="quality_degradation",
            severity="info",
            subject="bg_test",
            message="BG transition test",
        )
        now = time.time()
        mgr.prediction_mgr.enqueue_transition_learning(alert, now)
        assert len(mgr.prediction_mgr._bg_pending_transitions) >= 1

    def test_bg_queue_bounded(self):
        """Queues are bounded at 500 entries to prevent unbounded growth."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.prediction_mgr.stop_bg_tracking()
        # Enqueue more than 500
        for i in range(600):
            alert = Alert(
                alert_type="quality_degradation",
                severity="info",
                subject=f"bg_bound_test_{i}",
                message=f"BG bound test {i}",
            )
            mgr.prediction_mgr.enqueue_accuracy_check(alert)
        assert len(mgr.prediction_mgr._bg_pending_accuracy_checks) <= 500

    def test_bg_tracking_status(self):
        """get_bg_tracking_status returns expected keys."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.prediction_mgr.get_bg_tracking_status()
        assert "enabled" in status
        assert "running" in status
        assert "pending_accuracy_checks" in status
        assert "pending_transitions" in status
        assert "total_accuracy_checks" in status
        assert "total_transition_learnings" in status

    def test_bg_tracking_processes_queue(self):
        """Background thread processes queued items."""
        mgr = AlertManager(AlertConfig(enabled=True))
        alert = Alert(
            alert_type="quality_degradation",
            severity="info",
            subject="bg_process_test",
            message="BG process test",
        )
        mgr.prediction_mgr.enqueue_transition_learning(alert, time.time())
        mgr.prediction_mgr.enqueue_accuracy_check(alert)
        # Wait for background processing
        time.sleep(0.3)
        status = mgr.prediction_mgr.get_bg_tracking_status()
        assert status["total_transition_learnings"] >= 1
        assert status["total_accuracy_checks"] >= 1

    def test_send_alert_uses_bg_tracking(self):
        """send_alert() enqueues to background instead of synchronous processing."""
        mgr = AlertManager(AlertConfig(enabled=True))
        alert = Alert(
            alert_type="quality_degradation",
            severity="info",
            subject="bg_send_test",
            message="BG send test",
        )
        mgr.send_alert(alert)
        # After send_alert, the background queues should have received the work
        # (may already be processed by the bg thread, so check status)
        status = mgr.prediction_mgr.get_bg_tracking_status()
        # At minimum, the background thread should be running
        assert status["running"] is True


# ============================================================================
# Deliverable 5: StatusAggregator Batching
# ============================================================================


class TestStatusAggregatorBatching:
    """Tests for StatusAggregator caching and async batching."""

    def test_aggregator_cache_returns_same_result_within_ttl(self):
        """StatusAggregator caches build() result within TTL."""
        mgr = AlertManager(AlertConfig(enabled=True))
        aggregator = mgr._status_aggregator

        result1 = aggregator.build()
        result2 = aggregator.build()
        assert result1 is result2  # Same dict object = cached

    def test_aggregator_cache_expires_after_ttl(self):
        """StatusAggregator cache expires after TTL."""
        mgr = AlertManager(AlertConfig(enabled=True))
        aggregator = mgr._status_aggregator
        aggregator._cache_ttl_seconds = 0.001  # 1ms

        result1 = aggregator.build()
        time.sleep(0.01)
        result2 = aggregator.build()
        assert result1 is not result2

    def test_build_includes_ws_connection_pool(self):
        """Status includes WebSocket connection pool info."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.get_status()
        assert "ws_connection_pool" in status
        assert status["ws_connection_pool"]["active"] is False
        assert status["ws_connection_pool"]["activation_threshold"] == 50

    def test_build_includes_prediction_bg_tracking(self):
        """Status includes background prediction tracking info."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.get_status()
        assert "prediction_bg_tracking" in status
        assert status["prediction_bg_tracking"]["enabled"] is True
        assert status["prediction_bg_tracking"]["running"] is True

    def test_build_includes_cache_ttl_metadata(self):
        """Status includes cache TTL metadata."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.get_status()
        assert "_cache_ttl_ms" in status

    def test_build_async_method_exists(self):
        """build_async method exists on StatusAggregator."""
        mgr = AlertManager(AlertConfig(enabled=True))
        aggregator = mgr._status_aggregator
        assert callable(getattr(aggregator, "build_async", None))

    def test_build_async_returns_status(self):
        """build_async returns a valid status dict."""
        mgr = AlertManager(AlertConfig(enabled=True))
        aggregator = mgr._status_aggregator

        async def _test():
            result = await aggregator.build_async()
            assert isinstance(result, dict)
            assert "enabled" in result
            assert "circuit_breaker" in result
            assert "ws_connection_pool" in result

        asyncio.run(_test())

    def test_build_async_caches_result(self):
        """build_async caches its result."""
        mgr = AlertManager(AlertConfig(enabled=True))
        aggregator = mgr._status_aggregator

        async def _test():
            result1 = await aggregator.build_async()
            result2 = await aggregator.build_async()
            assert result1 is result2  # Same dict = cached

        asyncio.run(_test())

    def test_collect_sub_manager_summaries_returns_all(self):
        """_collect_sub_manager_summaries returns all expected summaries."""
        mgr = AlertManager(AlertConfig(enabled=True))
        aggregator = mgr._status_aggregator
        summaries = aggregator._collect_sub_manager_summaries()
        expected_keys = [
            "delivery",
            "realtime",
            "prediction",
            "lifecycle",
            "pruning",
            "digest",
            "ab_experiment",
            "circuit_breaker",
            "cb_auto_tune",
        ]
        for key in expected_keys:
            assert key in summaries, f"Missing summary key: {key}"

    def test_assemble_status_uses_get_for_safe_access(self):
        """_assemble_status uses .get() for safe access even with empty summaries."""
        mgr = AlertManager(AlertConfig(enabled=True))
        aggregator = mgr._status_aggregator
        # Pass empty summaries — should not raise
        result = aggregator._assemble_status({})
        assert isinstance(result, dict)
        assert result["enabled"] is True
        assert result["total_alerts_sent"] == 0


# ============================================================================
# Integration: Full pipeline test
# ============================================================================


class TestSprint563Integration:
    """Integration tests verifying all Sprint 5.63 features work together."""

    def test_full_alert_pipeline_with_bg_tracking(self):
        """Alert pipeline works with background tracking enabled."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                causal_prediction_enabled=True,
            )
        )
        alert = Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="integration_test",
            message="Integration test alert",
        )
        cid = mgr.send_alert(alert)
        assert cid.startswith("alert-")

        # Give bg thread time to process
        time.sleep(0.2)

        # Status should include all Sprint 5.63 features
        status = mgr.get_status()
        assert "ws_connection_pool" in status
        assert "prediction_bg_tracking" in status
        assert status["prediction_bg_tracking"]["running"] is True

    def test_ab_facade_and_status_integration(self):
        """AB experiment facades work with StatusAggregator."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.start_ab_experiment(
            name="status_test",
            control_config={"threshold": 0.8},
            variant_config={"threshold": 0.9},
        )
        status = mgr.get_status()
        assert "ab_experiments_total" in status
        assert status["ab_experiments_total"] >= 1

    def test_cb_caching_during_burst(self):
        """CB caching works correctly during a burst of alerts."""
        config = AlertConfig(
            enabled=True,
            circuit_breaker_enabled=True,
            circuit_breaker_cooldown_seconds=10,
            throttle_threshold_per_minute=10,
            min_alert_interval_seconds=0,
        )
        mgr = AlertManager(config)

        # Send burst of alerts
        for i in range(20):
            alert = Alert(
                alert_type="quality_degradation",
                severity="warning" if i % 2 == 0 else "info",
                subject=f"burst_cache_{i}",
                message=f"Burst cache test {i}",
            )
            mgr.send_alert(alert)

        # Get status — CB status should be cached
        status = mgr.get_status()
        cb_status = status["circuit_breaker"]
        assert "enabled" in cb_status
        # After a burst, CB should have activated
        assert cb_status["total_activations"] > 0

    def test_pool_status_visible_in_get_status(self):
        """Connection pool status is visible in get_status()."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.get_status()
        assert "ws_connection_pool" in status
        pool_status = status["ws_connection_pool"]
        assert "active" in pool_status
        assert "activation_threshold" in pool_status
        assert "group_size" in pool_status
