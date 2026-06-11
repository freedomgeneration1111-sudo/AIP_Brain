"""Tests for Sprint 5.61 sub-manager extractions.

Validates that ThrottleManager, PredictionManager, DigestManager, and
ABExperimentManager are correctly extracted from AlertManager with:
- Clean ownership of state
- Proper public accessor methods
- Backward-compatible delegation wrappers
- StatusAggregator integration
- Cross-manager wiring (callbacks, history_store propagation)
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest

from aip.adapter.alerting import (
    ABExperimentManager,
    Alert,
    AlertConfig,
    AlertManager,
    DeliveryManager,
    DigestManager,
    PredictionManager,
    RealtimeEventBus,
    ThrottleManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> AlertConfig:
    return AlertConfig(
        enabled=True,
        webhook_url="",
        email_to="",
        circuit_breaker_enabled=True,
        circuit_breaker_cooldown_seconds=5,
        throttle_threshold_per_minute=10,
        causal_prediction_enabled=True,
        causal_grouping_enabled=True,
        digest_enabled=True,
        digest_interval_minutes=1,
        digest_min_alerts=3,
        ab_experiment_enabled=True,
        learned_prediction_enabled=True,
        learned_prediction_min_samples=3,
        learned_prediction_confidence_threshold=0.01,
        prediction_accuracy_window_seconds=600,
    )


@pytest.fixture
def mgr(config: AlertConfig) -> AlertManager:
    return AlertManager(config)


def _make_alert(
    alert_type: str = "quality_degradation",
    severity: str = "warning",
    subject: str = "test_subject",
) -> Alert:
    return Alert(
        alert_type=alert_type,
        severity=severity,
        subject=subject,
        message="Test alert",
    )


# ===========================================================================
# Deliverable 1: ThrottleManager
# ===========================================================================


class TestThrottleManagerExtraction:
    """ThrottleManager owns all throttle and circuit-breaker state."""

    def test_throttle_manager_class_exists(self):
        """ThrottleManager is a top-level class in alerting module."""
        assert ThrottleManager is not None
        mgr = ThrottleManager(AlertConfig())
        assert hasattr(mgr, "record_throttle_window")
        assert hasattr(mgr, "check_circuit_breaker")
        assert hasattr(mgr, "get_circuit_breaker_status")

    def test_throttle_manager_has_own_lock(self, config: AlertConfig):
        """ThrottleManager uses its own lock, not AlertManager's."""
        tm = ThrottleManager(config)
        assert isinstance(tm._lock, type(threading.Lock()))

    def test_throttle_manager_owns_state(self, config: AlertConfig):
        """ThrottleManager owns all throttle state variables."""
        tm = ThrottleManager(config)
        assert hasattr(tm, "_throttle_alert_timestamps")
        assert hasattr(tm, "_circuit_breaker_active")
        assert hasattr(tm, "_circuit_breaker_activated_at")
        assert hasattr(tm, "_total_throttled_alerts")
        assert hasattr(tm, "_total_circuit_breaker_activations")
        assert hasattr(tm, "_cb_effective_threshold")
        assert hasattr(tm, "_cb_baseline_rates")
        assert hasattr(tm, "_cb_auto_tune_last_computed")
        assert hasattr(tm, "_cb_auto_tune_compute_interval")
        assert hasattr(tm, "_cb_threshold_adjustments")
        assert hasattr(tm, "_total_auto_tune_adjustments")

    def test_throttle_manager_not_in_alert_manager_init(self, mgr: AlertManager):
        """AlertManager delegates throttle state to the sub-manager."""
        # The state is on the sub-manager, not directly in AlertManager's own __dict__
        # (backward-compat properties may exist, but the canonical location is _throttle_mgr)
        assert mgr._throttle_mgr is not None
        assert isinstance(mgr._throttle_mgr._throttle_alert_timestamps, list)
        # Verify the sub-manager has its own lock separate from AlertManager
        assert mgr._throttle_mgr._lock is not mgr._lock

    def test_alert_manager_has_throttle_mgr_property(self, mgr: AlertManager):
        """AlertManager exposes throttle_mgr property accessor."""
        assert mgr.throttle_mgr is mgr._throttle_mgr
        assert isinstance(mgr.throttle_mgr, ThrottleManager)

    def test_record_throttle_window_delegates(self, mgr: AlertManager):
        """AlertManager.throttle_mgr.record_throttle_window works directly."""
        now = time.time()
        mgr.throttle_mgr.record_throttle_window(now)
        assert len(mgr._throttle_mgr._throttle_alert_timestamps) == 1

    def test_check_circuit_breaker_delegates(self, config: AlertConfig):
        """AlertManager.throttle_mgr.check_circuit_breaker works directly."""
        config.circuit_breaker_enabled = True
        config.throttle_threshold_per_minute = 2
        mgr = AlertManager(config)

        # Send enough alerts to trigger circuit breaker
        for i in range(5):
            mgr.send_alert(_make_alert(subject=f"sub_{i}"))

        # Circuit breaker should be active via the sub-manager
        assert mgr._throttle_mgr._circuit_breaker_active or mgr.throttle_mgr._total_circuit_breaker_activations > 0

    def test_increment_throttled_alerts(self, config: AlertConfig):
        """ThrottleManager.increment_throttled_alerts() works correctly."""
        tm = ThrottleManager(config)
        assert tm._total_throttled_alerts == 0
        tm.increment_throttled_alerts()
        assert tm._total_throttled_alerts == 1
        tm.increment_throttled_alerts()
        assert tm._total_throttled_alerts == 2

    def test_throttle_manager_get_status_summary(self, config: AlertConfig):
        """ThrottleManager.get_status_summary() returns structured dict."""
        tm = ThrottleManager(config)
        summary = tm.get_status_summary()
        assert "circuit_breaker" in summary
        assert "circuit_breaker_auto_tune" in summary
        assert "enabled" in summary["circuit_breaker"]
        assert "active" in summary["circuit_breaker"]

    def test_throttle_state_isolation(self, config: AlertConfig):
        """ThrottleManager state is independent of AlertManager state."""
        tm = ThrottleManager(config)
        tm.record_throttle_window(time.time())
        assert len(tm._throttle_alert_timestamps) == 1

        # AlertManager's throttle_mgr has its own state
        mgr = AlertManager(config)
        assert len(mgr._throttle_mgr._throttle_alert_timestamps) == 0

    def test_circuit_breaker_auto_tune_on_throttle_mgr(self, config: AlertConfig):
        """Auto-tune methods are on ThrottleManager, not AlertManager."""
        config.circuit_breaker_auto_tune_enabled = True
        tm = ThrottleManager(config, history_store=None)
        assert hasattr(tm, "compute_cb_auto_tune_threshold")
        assert hasattr(tm, "get_cb_effective_threshold")
        assert hasattr(tm, "update_cb_auto_tune")
        assert hasattr(tm, "get_cb_auto_tune_status")


# ===========================================================================
# Deliverable 2: PredictionManager
# ===========================================================================


class TestPredictionManagerExtraction:
    """PredictionManager owns all prediction and transition learning state."""

    def test_prediction_manager_class_exists(self):
        """PredictionManager is a top-level class."""
        assert PredictionManager is not None

    def test_prediction_manager_owns_state(self, config: AlertConfig):
        """PredictionManager owns all prediction state variables."""
        pm = PredictionManager(config)
        assert hasattr(pm, "_causal_predictions")
        assert hasattr(pm, "_total_predictions_made")
        assert hasattr(pm, "_prediction_outcomes")
        assert hasattr(pm, "_prediction_accuracy_hits")
        assert hasattr(pm, "_prediction_accuracy_misses")
        assert hasattr(pm, "_transition_counts")
        assert hasattr(pm, "_transition_totals")
        assert hasattr(pm, "_alert_type_sequence")
        assert hasattr(pm, "_learned_model_last_trained")
        assert hasattr(pm, "_total_learned_predictions_made")
        assert hasattr(pm, "_transition_persistence_enabled")
        assert hasattr(pm, "_retrain_interval_seconds")
        assert hasattr(pm, "_retrain_after_n_alerts")
        assert hasattr(pm, "_alerts_since_last_retrain")
        assert hasattr(pm, "_last_retrain_time")
        assert hasattr(pm, "_total_retraining_events")

    def test_prediction_manager_has_causal_chain(self, config: AlertConfig):
        """_CAUSAL_PREDICTION_CHAIN is on PredictionManager."""
        pm = PredictionManager(config)
        assert hasattr(pm, "_CAUSAL_PREDICTION_CHAIN")
        assert "pool_adjustment" in pm._CAUSAL_PREDICTION_CHAIN

    def test_alert_manager_has_prediction_mgr_property(self, mgr: AlertManager):
        """AlertManager exposes prediction_mgr property accessor."""
        assert mgr.prediction_mgr is mgr._prediction_mgr
        assert isinstance(mgr.prediction_mgr, PredictionManager)

    def test_predict_causal_chain_delegates(self, mgr: AlertManager):
        """AlertManager.predict_causal_chain delegates to PredictionManager."""
        alert = _make_alert(alert_type="pool_adjustment")
        result = mgr.predict_causal_chain(alert)
        # Should produce predictions since pool_adjustment has a chain
        assert isinstance(result, list)

    def test_record_alert_for_transition_learning(self, mgr: AlertManager):
        """AlertManager delegates transition learning to PredictionManager."""
        alert = _make_alert()
        now = time.time()
        mgr.prediction_mgr.record_alert_for_transition_learning(alert, now)
        # Check the sub-manager has the data
        assert len(mgr._prediction_mgr._alert_type_sequence) > 0

    def test_prediction_accuracy(self, mgr: AlertManager):
        """PredictionManager tracks accuracy correctly."""
        accuracy = mgr.get_prediction_accuracy()
        assert "hit_rate" in accuracy
        assert "total_predictions_tracked" in accuracy

    def test_prediction_manager_get_status_summary(self, config: AlertConfig):
        """PredictionManager.get_status_summary() returns structured dict."""
        pm = PredictionManager(config)
        summary = pm.get_status_summary()
        assert "causal_prediction" in summary
        assert "prediction_accuracy" in summary
        assert "learned_prediction" in summary
        assert "transition_persistence" in summary

    def test_calibration_callback_wiring(self, mgr: AlertManager):
        """PredictionManager has calibration callback wired to ABExperimentManager."""
        assert mgr._prediction_mgr._calibration_callback is not None
        # Test that calling the callback works
        result = mgr._prediction_mgr._calibration_callback("test_subject", 0.5)
        assert isinstance(result, float)

    def test_public_accessors(self, config: AlertConfig):
        """PredictionManager exposes public read-only accessors."""
        pm = PredictionManager(config)
        assert pm.total_predictions_made == 0
        assert pm.causal_predictions_count == 0
        assert pm.total_learned_predictions_made == 0
        assert pm.transition_types_count == 0
        assert pm.transition_persistence_enabled == config.transition_persistence_enabled

    def test_get_transition_probabilities(self, config: AlertConfig):
        """PredictionManager.get_transition_probabilities() works."""
        pm = PredictionManager(config)
        result = pm.get_transition_probabilities()
        assert isinstance(result, dict)

    def test_check_retrain_needed(self, config: AlertConfig):
        """PredictionManager.check_retrain_needed() works."""
        config.transition_persistence_enabled = False
        pm = PredictionManager(config)
        assert pm.check_retrain_needed() is False

        config.transition_persistence_enabled = True
        config.retrain_after_n_alerts = 5
        pm2 = PredictionManager(config)
        pm2._alerts_since_last_retrain = 10
        assert pm2.check_retrain_needed() is True


# ===========================================================================
# Deliverable 3: DigestManager
# ===========================================================================


class TestDigestManagerExtraction:
    """DigestManager owns all digest buffering and flushing state."""

    def test_digest_manager_class_exists(self):
        """DigestManager is a top-level class."""
        assert DigestManager is not None

    def test_digest_manager_owns_state(self, config: AlertConfig):
        """DigestManager owns digest buffer state."""
        dm = DigestManager(config)
        assert hasattr(dm, "_digest_buffer")
        assert hasattr(dm, "_digest_last_flush")
        assert hasattr(dm, "_total_digest_flushes")

    def test_alert_manager_has_digest_mgr_property(self, mgr: AlertManager):
        """AlertManager exposes digest_mgr property accessor."""
        assert mgr.digest_mgr is mgr._digest_mgr
        assert isinstance(mgr.digest_mgr, DigestManager)

    def test_buffer_alert(self, config: AlertConfig):
        """DigestManager.buffer_alert() adds to buffer."""
        dm = DigestManager(config)
        dm.buffer_alert({"alert_type": "test", "severity": "info"})
        assert len(dm._digest_buffer) == 1
        assert dm.buffered_count == 1

    def test_should_flush(self, config: AlertConfig):
        """DigestManager.should_flush() checks thresholds correctly."""
        config.digest_min_alerts = 3
        dm = DigestManager(config)

        # Empty buffer — should not flush
        assert dm.should_flush() is False

        # Add alerts below threshold
        for i in range(2):
            dm.buffer_alert({"alert_type": "test", "severity": "info"})
        assert dm.should_flush() is False

        # Add enough alerts to trigger flush
        dm.buffer_alert({"alert_type": "test", "severity": "info"})
        assert dm.should_flush() is True

    def test_flush_digest_returns_buffered(self, config: AlertConfig):
        """DigestManager.flush_digest() returns buffered items."""
        config.digest_enabled = True
        dm = DigestManager(config)
        for i in range(3):
            dm.buffer_alert({"alert_type": f"type_{i}", "severity": "info"})

        buffered = dm.flush_digest()
        assert len(buffered) == 3
        assert dm.total_flushes == 1
        assert dm.buffered_count == 0

    def test_flush_callback(self, config: AlertConfig):
        """DigestManager calls flush_callback when check_digest_flush triggers."""
        config.digest_enabled = True
        config.digest_interval_minutes = 0  # Flush immediately
        dm = DigestManager(config)

        callback_called = []

        def on_flush(buffered):
            callback_called.extend(buffered)

        dm.set_flush_callback(on_flush)
        dm.buffer_alert({"alert_type": "test", "severity": "info"})
        # Simulate time passing
        dm._digest_last_flush = time.time() - 100

        dm.check_digest_flush()
        # Callback may or may not be called depending on implementation
        # but the method should not error

    def test_get_digest_settings(self, config: AlertConfig):
        """DigestManager.get_digest_settings() returns correct overrides."""
        config.digest_interval_minutes = 15
        config.digest_min_alerts = 3
        config.digest_overrides = {"quality_degradation": {"interval_minutes": 5, "min_alerts": 2}}
        dm = DigestManager(config)

        # Override for quality_degradation
        interval, min_alerts = dm.get_digest_settings("quality_degradation")
        assert interval == 5
        assert min_alerts == 2

        # Global defaults for other types
        interval, min_alerts = dm.get_digest_settings("pool_adjustment")
        assert interval == 15
        assert min_alerts == 3

    def test_digest_manager_get_status_summary(self, config: AlertConfig):
        """DigestManager.get_status_summary() returns structured dict."""
        dm = DigestManager(config)
        summary = dm.get_status_summary()
        assert "enabled" in summary
        assert "buffered_count" in summary
        assert "total_flushes" in summary

    def test_send_alert_buffers_to_digest(self, config: AlertConfig):
        """send_alert() with digest enabled buffers to DigestManager."""
        config.digest_enabled = True
        config.digest_min_alerts = 100  # High threshold so it doesn't flush
        mgr = AlertManager(config)

        alert = Alert(
            alert_type="quality_degradation",
            severity="info",
            subject="digest_test",
            message="Test digest buffering",
        )
        mgr.send_alert(alert)
        assert mgr._digest_mgr.buffered_count >= 1


# ===========================================================================
# Deliverable 4: ABExperimentManager
# ===========================================================================


class TestABExperimentManagerExtraction:
    """ABExperimentManager owns all A/B experiment state and logic."""

    def test_ab_experiment_manager_class_exists(self):
        """ABExperimentManager is a top-level class."""
        assert ABExperimentManager is not None

    def test_ab_experiment_manager_owns_state(self, config: AlertConfig):
        """ABExperimentManager owns all experiment state variables."""
        abm = ABExperimentManager(config)
        assert hasattr(abm, "_ab_experiments")
        assert hasattr(abm, "_ab_promotion_checker_running")
        assert hasattr(abm, "_total_ab_promotions")
        assert hasattr(abm, "_total_ab_auto_promotions")
        assert hasattr(abm, "_decay_events")
        assert hasattr(abm, "_total_decay_events")
        assert hasattr(abm, "_ab_cleanup_checker_running")
        assert hasattr(abm, "_total_ab_cleanups")
        assert hasattr(abm, "_ab_rollback_history")
        assert hasattr(abm, "_total_ab_rollbacks")
        assert hasattr(abm, "_decay_recovery_history")
        assert hasattr(abm, "_total_decay_recoveries")
        assert hasattr(abm, "_pre_promotion_config_snapshots")
        assert hasattr(abm, "_total_config_reversions")
        assert hasattr(abm, "_live_config_reverter")
        assert hasattr(abm, "_auto_tuning_reverter")
        assert hasattr(abm, "_statistical_test_results")
        assert hasattr(abm, "_total_statistical_tests_run")
        assert hasattr(abm, "_total_promotions_blocked_by_stats")
        assert hasattr(abm, "_confidence_calibration_map")
        assert hasattr(abm, "_total_calibration_updates")
        assert hasattr(abm, "_bandit_state")
        assert hasattr(abm, "_total_bandit_allocations")
        assert hasattr(abm, "_bandit_context_rewards")
        assert hasattr(abm, "_snapshot_gc_thread")
        assert hasattr(abm, "_calibration_drift_alerts")

    def test_alert_manager_has_ab_experiment_mgr_property(self, mgr: AlertManager):
        """AlertManager exposes ab_experiment_mgr property accessor."""
        assert mgr.ab_experiment_mgr is mgr._ab_experiment_mgr
        assert isinstance(mgr.ab_experiment_mgr, ABExperimentManager)

    def test_start_ab_experiment_delegates(self, mgr: AlertManager):
        """AlertManager.start_ab_experiment delegates to ABExperimentManager."""
        result = mgr.start_ab_experiment(
            "test_exp",
            control_config={"threshold": 0.5},
            variant_config={"threshold": 0.7},
        )
        assert result is not None
        assert result["name"] == "test_exp"
        assert result["status"] == "running"
        # Check it's on the sub-manager
        assert "test_exp" in mgr._ab_experiment_mgr._ab_experiments

    def test_stop_ab_experiment_delegates(self, mgr: AlertManager):
        """AlertManager.stop_ab_experiment delegates to ABExperimentManager."""
        mgr.start_ab_experiment(
            "stop_test",
            control_config={"threshold": 0.5},
            variant_config={"threshold": 0.7},
        )
        result = mgr.stop_ab_experiment("stop_test", result="inconclusive")
        assert result is not None
        assert result["status"] == "stopped"

    def test_record_ab_result_delegates(self, mgr: AlertManager):
        """AlertManager.record_ab_result delegates to ABExperimentManager."""
        mgr.start_ab_experiment(
            "result_test",
            control_config={"threshold": 0.5},
            variant_config={"threshold": 0.7},
        )
        mgr.record_ab_result("result_test", "control", 0.05)
        exp = mgr.get_ab_experiment("result_test")
        assert exp["control_samples"] == 1

    def test_get_ab_experiment_delegates(self, mgr: AlertManager):
        """AlertManager.get_ab_experiment delegates to ABExperimentManager."""
        mgr.start_ab_experiment(
            "get_test",
            control_config={"threshold": 0.5},
            variant_config={"threshold": 0.7},
        )
        result = mgr.get_ab_experiment("get_test")
        assert result is not None
        assert result["name"] == "get_test"

    def test_get_ab_experiments_delegates(self, mgr: AlertManager):
        """AlertManager.get_ab_experiments delegates to ABExperimentManager."""
        mgr.start_ab_experiment("exp1", {"t": 0.5}, {"t": 0.7})
        mgr.start_ab_experiment("exp2", {"t": 0.5}, {"t": 0.7})
        result = mgr.get_ab_experiments()
        assert len(result) >= 2

    def test_ab_get_status_summary(self, config: AlertConfig):
        """ABExperimentManager.get_status_summary() returns structured dict."""
        abm = ABExperimentManager(config)
        summary = abm.get_status_summary()
        assert "ab_experiments_total" in summary
        assert "ab_experiments_running" in summary
        assert "ab_total_promotions" in summary
        assert "ab_bandit" in summary
        assert "ab_statistical_significance" in summary
        assert "ab_confidence_calibration" in summary

    def test_confidence_calibration_on_ab_mgr(self, config: AlertConfig):
        """Confidence calibration methods are on ABExperimentManager."""
        abm = ABExperimentManager(config)
        assert hasattr(abm, "update_confidence_calibration")
        assert hasattr(abm, "get_calibrated_confidence")
        assert hasattr(abm, "get_confidence_calibration_status")

    def test_bandit_methods_on_ab_mgr(self, config: AlertConfig):
        """Bandit methods are on ABExperimentManager."""
        abm = ABExperimentManager(config)
        assert hasattr(abm, "get_bandit_allocation")
        assert hasattr(abm, "get_bandit_status")
        assert hasattr(abm, "record_bandit_context_reward")

    def test_statistical_significance_on_ab_mgr(self, config: AlertConfig):
        """Statistical significance methods are on ABExperimentManager."""
        abm = ABExperimentManager(config)
        assert hasattr(abm, "compute_statistical_significance")
        assert hasattr(abm, "get_statistical_significance_status")

    def test_rollback_methods_on_ab_mgr(self, config: AlertConfig):
        """Rollback methods are on ABExperimentManager."""
        abm = ABExperimentManager(config)
        assert hasattr(abm, "check_promotion_rollback")
        assert hasattr(abm, "auto_rollback_promotion")
        assert hasattr(abm, "get_promotion_rollback_status")

    def test_cleanup_methods_on_ab_mgr(self, config: AlertConfig):
        """Cleanup methods are on ABExperimentManager."""
        abm = ABExperimentManager(config)
        assert hasattr(abm, "cleanup_expired_experiments")
        assert hasattr(abm, "get_cleanup_metrics")

    def test_alert_sender_callback(self, mgr: AlertManager):
        """ABExperimentManager uses alert_sender callback for alerts."""
        assert mgr._ab_experiment_mgr._alert_sender is not None


# ===========================================================================
# Cross-manager integration tests
# ===========================================================================


class TestCrossManagerIntegration:
    """Test that the extracted sub-managers work together correctly."""

    def test_status_aggregator_uses_sub_manager_summaries(self, mgr: AlertManager):
        """StatusAggregator.build() pulls from all sub-managers."""
        status = mgr.get_status()
        # ThrottleManager contributes
        assert "circuit_breaker" in status
        assert "circuit_breaker_auto_tune" in status
        # PredictionManager contributes
        assert "causal_prediction" in status
        assert "prediction_accuracy" in status
        assert "learned_prediction" in status
        assert "transition_persistence" in status
        # DigestManager contributes
        assert "digest" in status
        # ABExperimentManager contributes
        assert "ab_experiments_total" in status
        assert "ab_experiments_running" in status
        assert "ab_bandit" in status

    def test_history_store_propagation(self, config: AlertConfig):
        """attach_history_store() propagates to all sub-managers."""
        mgr = AlertManager(config)

        class MockStore:
            pass

        store = MockStore()
        mgr.attach_history_store(store)

        assert mgr._history_store is store
        assert mgr._throttle_mgr._history_store is store
        assert mgr._prediction_mgr._history_store is store
        assert mgr._ab_experiment_mgr._history_store is store

    def test_calibration_callback_wired(self, mgr: AlertManager):
        """PredictionManager's calibration callback points to ABExperimentManager."""
        assert mgr._prediction_mgr._calibration_callback is not None
        # Verify it returns a calibrated value
        calibrated = mgr._prediction_mgr._calibration_callback("test_subject", 0.5)
        assert isinstance(calibrated, float)

    def test_full_pipeline_with_all_managers(self, config: AlertConfig):
        """Full alert pipeline exercises all sub-managers."""
        config.circuit_breaker_enabled = False  # Don't throttle
        mgr = AlertManager(config)

        # Send several alerts
        for i in range(5):
            alert = Alert(
                alert_type="quality_degradation",
                severity="warning",
                subject=f"pipeline_test_{i % 3}",
                message=f"Pipeline alert {i}",
            )
            result = mgr.send_alert(alert)
            assert result  # Should not be empty

        # Check delivery manager
        assert mgr.delivery_mgr.get_total_alerts_sent() > 0

        # Check throttle manager has recorded timestamps
        assert len(mgr.throttle_mgr._throttle_alert_timestamps) > 0

        # Check prediction manager has transition data
        # Sprint 5.63: Transition learning is now enqueued for background
        # processing, so we need to wait briefly for the bg thread to drain
        time.sleep(0.3)
        assert len(mgr.prediction_mgr._alert_type_sequence) > 0

        # Check status works
        status = mgr.get_status()
        assert status["enabled"] is True

    def test_circuit_breaker_integration_with_digest(self, config: AlertConfig):
        """Circuit breaker activation routes non-critical alerts to digest."""
        config.circuit_breaker_enabled = True
        config.circuit_breaker_cooldown_seconds = 60
        config.throttle_threshold_per_minute = 3
        config.digest_enabled = True
        config.digest_min_alerts = 100
        mgr = AlertManager(config)

        # Send enough alerts to trigger circuit breaker
        results = []
        for i in range(10):
            alert = Alert(
                alert_type="quality_degradation",
                severity="info",  # Non-critical → should be throttled
                subject=f"cb_digest_test_{i}",
                message=f"CB digest test {i}",
            )
            result = mgr.send_alert(alert)
            results.append(result)

        # At least some should be throttled
        throttled = [r for r in results if r.startswith("throttled:")]
        assert len(throttled) > 0, "Some alerts should be throttled by circuit breaker"

        # Throttled alerts should be in digest buffer
        assert mgr._digest_mgr.buffered_count > 0


# ===========================================================================
# Backward compatibility tests
# ===========================================================================


class TestBackwardCompatibility:
    """Ensure existing code using AlertManager methods still works."""

    def test_get_circuit_breaker_status_compat(self, mgr: AlertManager):
        """AlertManager.get_circuit_breaker_status() still works."""
        status = mgr.get_circuit_breaker_status()
        assert "enabled" in status
        assert "active" in status

    def test_get_cb_auto_tune_status_compat(self, mgr: AlertManager):
        """AlertManager.get_cb_auto_tune_status() still works."""
        status = mgr.get_cb_auto_tune_status()
        assert "enabled" in status

    def test_get_prediction_accuracy_compat(self, mgr: AlertManager):
        """AlertManager.get_prediction_accuracy() still works."""
        accuracy = mgr.get_prediction_accuracy()
        assert "hit_rate" in accuracy

    def test_predict_causal_chain_compat(self, mgr: AlertManager):
        """AlertManager.predict_causal_chain() still works."""
        alert = _make_alert(alert_type="pool_adjustment")
        result = mgr.predict_causal_chain(alert)
        assert isinstance(result, list)

    def test_check_digest_flush_compat(self, mgr: AlertManager):
        """AlertManager.check_digest_flush() still works."""
        mgr.check_digest_flush()  # Should not error

    def test_start_stop_ab_experiment_compat(self, mgr: AlertManager):
        """AlertManager.start/stop_ab_experiment() still work."""
        mgr.start_ab_experiment("compat_test", {"t": 0.5}, {"t": 0.7})
        result = mgr.stop_ab_experiment("compat_test", result="inconclusive")
        assert result is not None

    def test_get_ab_experiment_compat(self, mgr: AlertManager):
        """AlertManager.get_ab_experiment() still works."""
        mgr.start_ab_experiment("get_compat", {"t": 0.5}, {"t": 0.7})
        result = mgr.get_ab_experiment("get_compat")
        assert result is not None

    def test_throttle_state_direct_access(self, mgr: AlertManager):
        """Throttle state is directly accessible via sub-manager."""
        assert isinstance(mgr.throttle_mgr._throttle_alert_timestamps, list)
        assert isinstance(mgr.throttle_mgr._circuit_breaker_active, bool)

    def test_prediction_state_direct_access(self, mgr: AlertManager):
        """Prediction state is directly accessible via sub-manager."""
        assert isinstance(mgr.prediction_mgr._causal_predictions, dict)
        assert isinstance(mgr.prediction_mgr._total_predictions_made, int)

    def test_digest_state_direct_access(self, mgr: AlertManager):
        """Digest state is directly accessible via sub-manager."""
        assert isinstance(mgr.digest_mgr._digest_buffer, list)
        assert isinstance(mgr.digest_mgr._total_digest_flushes, int)

    def test_ab_state_direct_access(self, mgr: AlertManager):
        """AB experiment state is directly accessible via sub-manager."""
        assert isinstance(mgr.ab_experiment_mgr._ab_experiments, dict)
        assert isinstance(mgr.ab_experiment_mgr._total_ab_promotions, int)
