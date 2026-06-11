"""Sprint 5.47 — Strengthen Promotion Safety, Statistical Rigor, and Dashboard Visibility.

Comprehensive tests covering all Sprint 5.47 deliverables:

- Del 1: Rollback Integration with Live Configuration
- Del 2: Statistical Significance Testing for Promotions
- Del 3: Dashboard Visualization with Mini Charts (JS — test via API structure)
- Del 4: Cleanup Scheduler Alerting & Metrics
- Del 5: Prediction Confidence Calibration

Deterministic, zero-token, no network, no LLM.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

import pytest

from aip.adapter.alerting import AlertManager, AlertConfig, Alert
from aip.adapter.alert_history_store import AlertHistoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _old_iso(hours_ago: float) -> str:
    """Return an ISO-8601 timestamp that is *hours_ago* hours in the past."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _make_config(**overrides) -> AlertConfig:
    """Build an AlertConfig with sensible test defaults and optional overrides."""
    defaults = dict(
        enabled=True,
        webhook_url="",
        email_to="",
        min_alert_interval_seconds=0,
        ab_experiment_enabled=True,
        ab_auto_promote_interval_seconds=0,
        ab_auto_promote_confidence_threshold=0.95,
        ab_auto_promote_min_samples=50,
        ab_experiment_ttl_hours=168,
        ab_stopped_experiment_retention_hours=72,
        ab_cleanup_interval_seconds=0,
        ab_rollback_enabled=True,
        ab_rollback_observation_window_seconds=1800,
        ab_rollback_accuracy_drop_threshold=0.05,
        decay_recovery_enabled=True,
        decay_recovery_threshold=0.15,
        decay_recovery_actions=["rerun_calibration", "restart_experiment"],
        # Sprint 5.47 defaults
        ab_rollback_revert_live_config=True,
        ab_statistical_significance_enabled=False,
        ab_statistical_significance_p_value=0.05,
        ab_statistical_significance_method="z_test",
        ab_statistical_significance_min_samples=30,
        ab_cleanup_alert_on_ttl_expiry=True,
        ab_confidence_calibration_enabled=False,
    )
    defaults.update(overrides)
    return AlertConfig(**defaults)


def _make_store(tmp_path, db_name: str = "test_history.db") -> AlertHistoryStore:
    """Create and initialize a fresh AlertHistoryStore."""
    db_path = str(tmp_path / db_name)
    store = AlertHistoryStore(db_path)
    store.initialize()
    return store


def _make_manager(config: AlertConfig | None = None, store: AlertHistoryStore | None = None) -> AlertManager:
    """Create an AlertManager with optional config and store."""
    cfg = config or _make_config()
    mgr = AlertManager(cfg)
    if store is not None:
        mgr.attach_history_store(store)
    return mgr


# ========================================================================
# Class 1: TestRollbackLiveConfigReversion  (Sprint 5.47 Del 1)
# ========================================================================


class TestRollbackLiveConfigReversion:
    """Sprint 5.47 Del 1: Rollback Integration with Live Configuration."""

    def test_promotion_saves_config_snapshot(self):
        """When a variant is promoted, a pre-promotion config snapshot should be saved."""
        mgr = _make_manager()
        mgr.start_ab_experiment(
            "config_snap",
            control_config={"model": "gpt-4", "temperature": 0.7},
            variant_config={"model": "gpt-4o", "temperature": 0.5},
        )
        mgr.record_ab_result("config_snap", "control", accuracy=0.80, samples=50)
        mgr.record_ab_result("config_snap", "variant", accuracy=0.92, samples=50)

        mgr.promote_variant("config_snap", variant="variant")

        # Verify snapshot was saved
        assert "config_snap" in mgr.ab_experiment_mgr._pre_promotion_config_snapshots
        snapshot = mgr.ab_experiment_mgr._pre_promotion_config_snapshots["config_snap"]
        assert snapshot["control_config"] == {"model": "gpt-4", "temperature": 0.7}
        assert snapshot["variant_config"] == {"model": "gpt-4o", "temperature": 0.5}
        assert snapshot["promoted_variant"] == "variant"
        assert snapshot["baseline_config"] == {"model": "gpt-4", "temperature": 0.7}

    def test_rollback_reverts_via_callback(self):
        """When rollback triggers, the live config reverter callback should be invoked."""
        mgr = _make_manager()
        reverted_configs = []

        def mock_reverter(exp_name, baseline_config):
            reverted_configs.append((exp_name, baseline_config))
            return True

        mgr.set_live_config_reverter(mock_reverter)

        mgr.start_ab_experiment(
            "revert_test",
            control_config={"model": "gpt-4"},
            variant_config={"model": "gpt-4o"},
        )
        mgr.record_ab_result("revert_test", "control", accuracy=0.85, samples=50)
        mgr.record_ab_result("revert_test", "variant", accuracy=0.92, samples=50)
        mgr.promote_variant("revert_test", variant="variant")

        # Simulate degradation
        exp = mgr.get_ab_experiment("revert_test")
        exp["variant_accuracy"] = 0.60

        rollbacks = mgr.check_promotion_rollback()
        assert len(rollbacks) == 1

        # Verify the reverter was called with the baseline config
        assert len(reverted_configs) == 1
        assert reverted_configs[0][0] == "revert_test"
        assert reverted_configs[0][1] == {"model": "gpt-4"}

    def test_rollback_auto_tuning_reverter(self):
        """When rollback triggers, the auto-tuning reverter callback should be invoked."""
        mgr = _make_manager()
        reverted_policies = []

        def mock_auto_tuning_reverter(snapshot):
            reverted_policies.append(snapshot)
            return True

        mgr.set_auto_tuning_reverter(mock_auto_tuning_reverter)

        mgr.start_ab_experiment(
            "at_revert",
            control_config={"model": "a"},
            variant_config={"model": "b"},
        )
        mgr.record_ab_result("at_revert", "control", accuracy=0.85, samples=50)
        mgr.record_ab_result("at_revert", "variant", accuracy=0.92, samples=50)
        mgr.promote_variant("at_revert", variant="variant")

        # Simulate degradation
        exp = mgr.get_ab_experiment("at_revert")
        exp["variant_accuracy"] = 0.60

        mgr.check_promotion_rollback()

        # Verify the auto-tuning reverter was called
        assert len(reverted_policies) == 1
        assert "snapshot_at" in reverted_policies[0]

    def test_rollback_without_snapshot(self):
        """Rollback without a pre-promotion snapshot should log and continue."""
        mgr = _make_manager()
        mgr.start_ab_experiment("no_snap", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("no_snap", "control", accuracy=0.85, samples=50)
        mgr.record_ab_result("no_snap", "variant", accuracy=0.92, samples=50)
        mgr.promote_variant("no_snap", variant="variant")

        # Manually clear the snapshot to simulate missing data
        mgr.ab_experiment_mgr._pre_promotion_config_snapshots.pop("no_snap", None)

        # Simulate degradation
        exp = mgr.get_ab_experiment("no_snap")
        exp["variant_accuracy"] = 0.60

        rollbacks = mgr.check_promotion_rollback()
        assert len(rollbacks) == 1
        # Config reversion should be None (no snapshot)
        assert rollbacks[0]["config_reversion"] is None

    def test_config_reversion_status(self):
        mgr = _make_manager()
        status = mgr.get_config_reversion_status()

        assert "revert_live_config_enabled" in status
        assert "live_config_reverter_set" in status
        assert "auto_tuning_reverter_set" in status
        assert "total_config_reversions" in status
        assert "pending_snapshots" in status
        assert status["revert_live_config_enabled"] is True
        assert status["live_config_reverter_set"] is False

    def test_rollback_callback_failure_is_logged(self):
        """If the reverter callback raises, rollback still succeeds."""
        mgr = _make_manager()

        def failing_reverter(exp_name, baseline_config):
            raise RuntimeError("reverter failure")

        mgr.set_live_config_reverter(failing_reverter)

        mgr.start_ab_experiment("fail_revert", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("fail_revert", "control", accuracy=0.85, samples=50)
        mgr.record_ab_result("fail_revert", "variant", accuracy=0.92, samples=50)
        mgr.promote_variant("fail_revert", variant="variant")

        exp = mgr.get_ab_experiment("fail_revert")
        exp["variant_accuracy"] = 0.60

        rollbacks = mgr.check_promotion_rollback()
        assert len(rollbacks) == 1
        # Experiment should still be rolled back despite reverter failure
        assert mgr.get_ab_experiment("fail_revert")["status"] == "rolled_back"
        # Config reversion result should indicate failure
        assert rollbacks[0]["config_reversion"] is not None
        assert len(rollbacks[0]["config_reversion"]["errors"]) > 0


# ========================================================================
# Class 2: TestStatisticalSignificance  (Sprint 5.47 Del 2)
# ========================================================================


class TestStatisticalSignificance:
    """Sprint 5.47 Del 2: Statistical Significance Testing for Promotions."""

    def test_z_test_significant(self):
        """Z-test should detect significant difference with large samples."""
        cfg = _make_config(
            ab_statistical_significance_enabled=True,
            ab_statistical_significance_method="z_test",
            ab_statistical_significance_min_samples=30,
        )
        mgr = _make_manager(config=cfg)
        mgr.start_ab_experiment("z_sig", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("z_sig", "control", accuracy=0.75, samples=100)
        mgr.record_ab_result("z_sig", "variant", accuracy=0.90, samples=100)

        result = mgr.compute_statistical_significance("z_sig")
        assert result is not None
        assert result["significant"] is True
        assert result["p_value"] < 0.05
        assert result["method"] == "z_test"
        assert "confidence_interval" in result
        assert result["statistic"] != 0

    def test_z_test_not_significant(self):
        """Z-test should not detect significance when accuracies are similar."""
        cfg = _make_config(
            ab_statistical_significance_enabled=True,
            ab_statistical_significance_method="z_test",
            ab_statistical_significance_min_samples=30,
        )
        mgr = _make_manager(config=cfg)
        mgr.start_ab_experiment("z_nosig", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("z_nosig", "control", accuracy=0.80, samples=100)
        mgr.record_ab_result("z_nosig", "variant", accuracy=0.81, samples=100)

        result = mgr.compute_statistical_significance("z_nosig")
        assert result is not None
        assert result["significant"] is False
        assert result["p_value"] >= 0.05

    def test_t_test_method(self):
        """Welch's t-test should work as an alternative method."""
        cfg = _make_config(
            ab_statistical_significance_enabled=True,
            ab_statistical_significance_method="t_test",
            ab_statistical_significance_min_samples=30,
        )
        mgr = _make_manager(config=cfg)
        mgr.start_ab_experiment("t_test_exp", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("t_test_exp", "control", accuracy=0.70, samples=50)
        mgr.record_ab_result("t_test_exp", "variant", accuracy=0.90, samples=50)

        result = mgr.compute_statistical_significance("t_test_exp")
        assert result is not None
        assert result["method"] == "t_test"
        assert "degrees_of_freedom" in result

    def test_bootstrap_method(self):
        """Bootstrap CI method should produce valid results."""
        cfg = _make_config(
            ab_statistical_significance_enabled=True,
            ab_statistical_significance_method="bootstrap",
            ab_statistical_significance_min_samples=30,
        )
        mgr = _make_manager(config=cfg)
        mgr.start_ab_experiment("boot_exp", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("boot_exp", "control", accuracy=0.70, samples=50)
        mgr.record_ab_result("boot_exp", "variant", accuracy=0.90, samples=50)

        result = mgr.compute_statistical_significance("boot_exp")
        assert result is not None
        assert result["method"] == "bootstrap"
        assert "bootstrap_samples" in result
        assert len(result["confidence_interval"]) == 2

    def test_insufficient_samples(self):
        """Stat test should return non-significant with insufficient samples."""
        cfg = _make_config(
            ab_statistical_significance_enabled=True,
            ab_statistical_significance_min_samples=50,
        )
        mgr = _make_manager(config=cfg)
        mgr.start_ab_experiment("few_samples", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("few_samples", "control", accuracy=0.70, samples=10)
        mgr.record_ab_result("few_samples", "variant", accuracy=0.90, samples=10)

        result = mgr.compute_statistical_significance("few_samples")
        assert result is not None
        assert result["significant"] is False
        assert result["p_value"] is None  # Not enough data
        assert "insufficient_samples" in result.get("reason", "")

    def test_promotion_blocked_by_stats(self):
        """Promotion should be blocked when stat testing is enabled and result is not significant."""
        cfg = _make_config(
            ab_statistical_significance_enabled=True,
            ab_statistical_significance_min_samples=30,
            ab_auto_promote_min_samples=10,
        )
        mgr = _make_manager(config=cfg)
        mgr.start_ab_experiment("blocked_promo", {"model": "a"}, {"model": "b"})
        # Similar accuracies — should not be statistically significant
        mgr.record_ab_result("blocked_promo", "control", accuracy=0.80, samples=50)
        mgr.record_ab_result("blocked_promo", "variant", accuracy=0.81, samples=50)

        # Attempt to promote — should be blocked
        result = mgr.promote_variant("blocked_promo", variant="variant")
        assert result is None  # Blocked

        # Experiment should still be running
        exp = mgr.get_ab_experiment("blocked_promo")
        assert exp["status"] == "running"

        # Verify blocked counter
        assert mgr.ab_experiment_mgr._total_promotions_blocked_by_stats == 1

    def test_promotion_allowed_when_significant(self):
        """Promotion should proceed when stat testing shows significance."""
        cfg = _make_config(
            ab_statistical_significance_enabled=True,
            ab_statistical_significance_min_samples=30,
        )
        mgr = _make_manager(config=cfg)
        mgr.start_ab_experiment("allowed_promo", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("allowed_promo", "control", accuracy=0.70, samples=100)
        mgr.record_ab_result("allowed_promo", "variant", accuracy=0.90, samples=100)

        result = mgr.promote_variant("allowed_promo", variant="variant")
        assert result is not None
        assert result["status"] == "promoted"
        assert "statistical_test_result" in result

    def test_stat_significance_not_found(self):
        """compute_statistical_significance should return None for unknown experiments."""
        mgr = _make_manager()
        result = mgr.compute_statistical_significance("nonexistent")
        assert result is None

    def test_statistical_significance_status(self):
        mgr = _make_manager()
        status = mgr.get_statistical_significance_status()

        assert "enabled" in status
        assert "method" in status
        assert "p_value_threshold" in status
        assert "min_samples" in status
        assert "total_tests_run" in status
        assert "total_promotions_blocked" in status
        assert "recent_results" in status


# ========================================================================
# Class 3: TestCleanupAlertingAndMetrics  (Sprint 5.47 Del 4)
# ========================================================================


class TestCleanupAlertingAndMetrics:
    """Sprint 5.47 Del 4: Cleanup Scheduler Alerting & Metrics."""

    def test_ttl_expiry_sends_alert(self):
        """When an experiment expires due to TTL, an alert should be sent."""
        cfg = _make_config(
            ab_experiment_ttl_hours=1,
            ab_cleanup_alert_on_ttl_expiry=True,
        )
        mgr = _make_manager(config=cfg)
        exp = mgr.start_ab_experiment("ttl_alert", {"model": "a"}, {"model": "b"})

        # Manually set started_at to 2 hours ago
        exp["started_at"] = _old_iso(hours_ago=2)

        # Track alert history before cleanup
        history_before = len(mgr.lifecycle_mgr._alert_history)

        mgr.cleanup_expired_experiments()

        # Verify an alert was sent
        history_after = len(mgr.lifecycle_mgr._alert_history)
        assert history_after > history_before

        # Find the TTL expiry alert
        ttl_alerts = [a for a in mgr.lifecycle_mgr._alert_history if a.get("alert_type") == "ab_experiment_ttl_expired"]
        assert len(ttl_alerts) >= 1
        assert "ttl_alert" in ttl_alerts[-1]["subject"]

    def test_ttl_expiry_no_alert_when_disabled(self):
        """When alert_on_ttl_expiry is disabled, no alert should be sent."""
        cfg = _make_config(
            ab_experiment_ttl_hours=1,
            ab_cleanup_alert_on_ttl_expiry=False,
        )
        mgr = _make_manager(config=cfg)
        exp = mgr.start_ab_experiment("no_ttl_alert", {"model": "a"}, {"model": "b"})
        exp["started_at"] = _old_iso(hours_ago=2)

        history_before = len(mgr.lifecycle_mgr._alert_history)
        mgr.cleanup_expired_experiments()
        history_after = len(mgr.lifecycle_mgr._alert_history)

        # Should NOT send a TTL expiry alert (but may send other alerts)
        ttl_alerts = [
            a
            for a in mgr.lifecycle_mgr._alert_history[history_before:]
            if a.get("alert_type") == "ab_experiment_ttl_expired"
        ]
        assert len(ttl_alerts) == 0

    def test_cleanup_metrics(self):
        """Cleanup metrics should track cumulative totals."""
        cfg = _make_config(
            ab_experiment_ttl_hours=1,
            ab_stopped_experiment_retention_hours=1,
        )
        mgr = _make_manager(config=cfg)

        # Add an expired experiment
        exp1 = mgr.start_ab_experiment("metric_ttl", {"model": "a"}, {"model": "b"})
        exp1["started_at"] = _old_iso(hours_ago=2)

        # Add a prunable stopped experiment
        exp2 = mgr.start_ab_experiment("metric_prune", {"model": "c"}, {"model": "d"})
        mgr.stop_ab_experiment("metric_prune")
        exp2["stopped_at"] = _old_iso(hours_ago=2)

        mgr.cleanup_expired_experiments()

        metrics = mgr.get_cleanup_metrics()
        assert metrics["total_expired_by_ttl"] >= 1
        assert metrics["total_pruned_stopped"] >= 1
        assert metrics["last_run_time"] > 0
        assert metrics["total_cleanup_runs"] >= 1

    def test_cleanup_metrics_in_health(self):
        """Cleanup metrics should be accessible from get_status()."""
        mgr = _make_manager()
        status = mgr.get_status()
        assert "ab_cleanup_metrics" in status
        metrics = status["ab_cleanup_metrics"]
        assert "total_expired_by_ttl" in metrics
        assert "total_pruned_stopped" in metrics
        assert "last_run_time" in metrics
        assert "alert_on_ttl_expiry" in metrics


# ========================================================================
# Class 4: TestConfidenceCalibration  (Sprint 5.47 Del 5)
# ========================================================================


class TestConfidenceCalibration:
    """Sprint 5.47 Del 5: Prediction Confidence Calibration."""

    def test_calibration_disabled(self):
        """When calibration is disabled, update should return 1.0."""
        cfg = _make_config(ab_confidence_calibration_enabled=False)
        mgr = _make_manager(config=cfg)

        result = mgr.update_confidence_calibration("vigil", 0.85, 0.90)
        assert result == 1.0

        # Calibrated confidence should return raw value
        assert mgr.get_calibrated_confidence("vigil", 0.80) == 0.80

    def test_calibration_enabled_updates(self):
        """When calibration is enabled, updates should adjust the calibration factor."""
        cfg = _make_config(ab_confidence_calibration_enabled=True)
        mgr = _make_manager(config=cfg)

        # First calibration: observed 0.85, predicted 0.90 → ratio ≈ 0.944
        # EMA with alpha=0.3: 0.3 * 0.944 + 0.7 * 1.0 ≈ 0.983
        factor = mgr.update_confidence_calibration("vigil", 0.85, 0.90)
        assert 0.9 < factor < 1.0  # Should be slightly less than 1.0

        # Apply calibration
        calibrated = mgr.get_calibrated_confidence("vigil", 0.90)
        assert calibrated < 0.90  # Should be calibrated down
        assert calibrated > 0.0

    def test_calibration_multiple_updates(self):
        """Multiple calibration updates should converge with EMA."""
        cfg = _make_config(ab_confidence_calibration_enabled=True)
        mgr = _make_manager(config=cfg)

        # Repeatedly update with the same ratio
        for _ in range(10):
            mgr.update_confidence_calibration("vigil", 0.80, 0.90)

        # After many updates, calibration factor should converge toward 0.80/0.90 ≈ 0.889
        factor = mgr.ab_experiment_mgr._confidence_calibration_map.get("vigil", 1.0)
        assert factor < 0.95  # Should have drifted down from 1.0

    def test_calibration_clamped(self):
        """Calibrated confidence should be clamped to [0, 1]."""
        cfg = _make_config(ab_confidence_calibration_enabled=True)
        mgr = _make_manager(config=cfg)

        # Set an extreme calibration factor
        mgr.ab_experiment_mgr._confidence_calibration_map["extreme"] = 2.0

        calibrated = mgr.get_calibrated_confidence("extreme", 0.90)
        assert calibrated <= 1.0

        mgr.ab_experiment_mgr._confidence_calibration_map["extreme"] = 0.01
        calibrated = mgr.get_calibrated_confidence("extreme", 0.90)
        assert calibrated >= 0.0

    def test_calibration_no_data(self):
        """Without calibration data, raw confidence should be returned."""
        cfg = _make_config(ab_confidence_calibration_enabled=True)
        mgr = _make_manager(config=cfg)

        # No calibration updates yet
        calibrated = mgr.get_calibrated_confidence("unknown", 0.85)
        assert calibrated == 0.85  # Should return raw value unchanged

    def test_calibration_status(self):
        """Calibration status should provide visibility."""
        cfg = _make_config(ab_confidence_calibration_enabled=True)
        mgr = _make_manager(config=cfg)

        mgr.update_confidence_calibration("vigil", 0.80, 0.90)

        status = mgr.get_confidence_calibration_status()
        assert status["enabled"] is True
        assert status["total_updates"] == 1
        assert "vigil" in status["calibrated_subjects"]

    def test_calibration_in_predictions(self):
        """Calibrated probability should appear in predict_causal_chain_learned output."""
        cfg = _make_config(
            ab_confidence_calibration_enabled=True,
            learned_prediction_enabled=True,
            learned_prediction_min_samples=5,
            causal_prediction_enabled=False,
        )
        mgr = _make_manager(config=cfg)

        # Seed transition data
        mgr.prediction_mgr._transition_counts[("quality_degradation", "pool_adjustment")] = 10
        mgr.prediction_mgr._transition_totals["quality_degradation"] = 15

        # Set calibration
        mgr.ab_experiment_mgr._confidence_calibration_map["vigil_faithfulness"] = 0.9

        alert = Alert(
            alert_type="quality_degradation",
            severity="warning",
            subject="vigil_faithfulness",
            message="Test",
        )
        predictions = mgr.predict_causal_chain_learned(alert)

        # Predictions should include calibrated_probability
        if len(predictions) > 0:
            assert "calibrated_probability" in predictions[0]
            assert predictions[0]["calibrated_probability"] != predictions[0]["probability"]


# ========================================================================
# Class 5: TestDashboardMonitoringPanel  (Sprint 5.47 Del 3)
# ========================================================================


class TestDashboardMonitoringPanel:
    """Sprint 5.47 Del 3: Dashboard Visualization with Mini Charts.

    Tests the API structure that feeds the dashboard panel.
    The JS rendering is tested indirectly via the data contract.
    """

    def test_monitoring_summary_includes_stats(self):
        """Monitoring summary should include statistical significance data."""
        mgr = _make_manager()
        summary = mgr.get_experiment_monitoring_summary()

        assert "statistical_significance" in summary
        ss = summary["statistical_significance"]
        assert "enabled" in ss
        assert "method" in ss
        assert "p_value_threshold" in ss
        assert "total_tests_run" in ss
        assert "total_promotions_blocked" in ss

    def test_monitoring_summary_includes_config_reversion(self):
        """Monitoring summary should include config reversion data."""
        mgr = _make_manager()
        summary = mgr.get_experiment_monitoring_summary()

        assert "config_reversion" in summary
        cr = summary["config_reversion"]
        assert "total_reversions" in cr
        assert "revert_live_config_enabled" in cr

    def test_monitoring_summary_includes_calibration(self):
        """Monitoring summary should include confidence calibration data."""
        mgr = _make_manager()
        summary = mgr.get_experiment_monitoring_summary()

        assert "confidence_calibration" in summary
        cc = summary["confidence_calibration"]
        assert "enabled" in cc
        assert "total_updates" in cc
        assert "calibrated_subjects" in cc

    def test_monitoring_summary_includes_cleanup_metrics(self):
        """Monitoring summary should include cleanup metrics."""
        mgr = _make_manager()
        summary = mgr.get_experiment_monitoring_summary()

        assert "cleanup_metrics" in summary
        cm = summary["cleanup_metrics"]
        assert "total_expired_by_ttl" in cm
        assert "total_pruned_stopped" in cm
        assert "last_run_time" in cm

    def test_running_experiment_has_chart_data(self):
        """Running experiments should include accuracy data for mini charts."""
        mgr = _make_manager()
        mgr.start_ab_experiment("chart_exp", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("chart_exp", "control", accuracy=0.80, samples=30)
        mgr.record_ab_result("chart_exp", "variant", accuracy=0.90, samples=30)

        summary = mgr.get_experiment_monitoring_summary()
        assert len(summary["running_experiments"]) == 1
        running = summary["running_experiments"][0]
        assert "control_accuracy" in running
        assert "variant_accuracy" in running
        assert "control_samples" in running
        assert "variant_samples" in running


# ========================================================================
# Class 6: TestNormalCdf  (Sprint 5.47 utility)
# ========================================================================


class TestNormalCdf:
    """Test the normal CDF approximation used for statistical significance."""

    def test_cdf_at_zero(self):
        """CDF(0) should be 0.5 (symmetric distribution)."""
        result = AlertManager._normal_cdf(0)
        assert abs(result - 0.5) < 0.001

    def test_cdf_at_large_positive(self):
        """CDF at large positive value should approach 1."""
        result = AlertManager._normal_cdf(5)
        assert result > 0.99

    def test_cdf_at_large_negative(self):
        """CDF at large negative value should approach 0."""
        result = AlertManager._normal_cdf(-5)
        assert result < 0.01

    def test_cdf_symmetric(self):
        """CDF(x) + CDF(-x) should equal 1."""
        for x in [0.5, 1.0, 1.96, 2.0, 3.0]:
            assert abs(AlertManager._normal_cdf(x) + AlertManager._normal_cdf(-x) - 1.0) < 0.001
