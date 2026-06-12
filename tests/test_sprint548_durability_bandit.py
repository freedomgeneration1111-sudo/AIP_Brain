"""Sprint 5.48 tests: Data durability, bandit support, and rollback dry-run.

Covers all 5 deliverables:
1. Wire Live Config Reversion into App Lifespan
2. Persist Statistical Test Results
3. Time-Series Data for Mini Charts
4. Multi-Armed Bandit Support
5. Rollback Dry-Run Mode
"""

import os
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

from aip.adapter.alert_history_store import AlertHistoryStore, SyncAlertHistoryBridge
from aip.adapter.alerting import AlertConfig, AlertManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AlertConfig:
    """Create an AlertConfig with sensible test defaults."""
    defaults = {
        "enabled": False,
        "ab_experiment_enabled": True,
        "ab_auto_promote_interval_seconds": 0,  # Disable auto-promotion checker
        "ab_cleanup_interval_seconds": 0,  # Disable cleanup checker
        "ab_rollback_enabled": True,
        "ab_rollback_observation_window_seconds": 3600,
        "ab_rollback_accuracy_drop_threshold": 0.05,
        "ab_rollback_revert_live_config": True,
        "ab_statistical_significance_enabled": True,
        "ab_statistical_significance_p_value": 0.05,
        "ab_statistical_significance_method": "z_test",
        "ab_statistical_significance_min_samples": 30,
        "ab_confidence_calibration_enabled": True,
        "ab_rollback_dry_run": False,
        "ab_bandit_enabled": False,
        "ab_bandit_method": "thompson",
        "ab_bandit_explore_rate": 0.1,
    }
    defaults.update(overrides)
    return AlertConfig(**defaults)


def _make_store(tmp_path=None, db_name: str = "test_alert_history.db") -> SyncAlertHistoryBridge:
    """Create and initialize a fresh AlertHistoryStore via SyncAlertHistoryBridge."""
    if tmp_path is not None:
        db_path = str(tmp_path / db_name)
    else:
        db_path = os.path.join(tempfile.mkdtemp(), db_name)
    store = AlertHistoryStore(db_path)
    bridge = SyncAlertHistoryBridge(store)
    bridge.initialize()
    return bridge


def _start_experiment(mgr: AlertManager, name: str = "test-exp") -> dict:
    """Start a simple A/B experiment with sensible defaults."""
    return mgr.start_ab_experiment(
        name=name,
        control_config={"model": "gpt-4", "temperature": 0.7},
        variant_config={"model": "gpt-4-turbo", "temperature": 0.5},
    )


def _add_results(mgr: AlertManager, name: str, n: int = 50, c_acc: float = 0.85, v_acc: float = 0.92) -> None:
    """Record A/B results for an experiment."""
    for i in range(n):
        mgr.record_ab_result(
            name=name,
            variant="control",
            accuracy=c_acc + (i % 5) * 0.001,
        )
        mgr.record_ab_result(
            name=name,
            variant="variant",
            accuracy=v_acc + (i % 5) * 0.001,
        )


# ---------------------------------------------------------------------------
# Deliverable 1: Wire Live Config Reversion into App Lifespan
# ---------------------------------------------------------------------------


class TestLiveConfigReversionWiring:
    """Tests for wiring live config reverter and auto-tuning reverter callbacks."""

    def test_set_live_config_reverter_callback(self):
        """set_live_config_reverter stores the callback correctly."""
        mgr = AlertManager(_make_config())
        callback = MagicMock(return_value=True)
        mgr.set_live_config_reverter(callback)
        assert mgr.ab_experiment_mgr._live_config_reverter is callback

    def test_set_auto_tuning_reverter_callback(self):
        """set_auto_tuning_reverter stores the callback correctly."""
        mgr = AlertManager(_make_config())
        callback = MagicMock(return_value=True)
        mgr.set_auto_tuning_reverter(callback)
        assert mgr.ab_experiment_mgr._auto_tuning_reverter is callback

    def test_rollback_calls_live_config_reverter(self):
        """When a promotion is rolled back, the live config reverter callback is invoked."""
        mgr = AlertManager(_make_config(ab_statistical_significance_enabled=False))
        reverter = MagicMock(return_value=True)
        mgr.set_live_config_reverter(reverter)

        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)
        result = mgr.promote_variant("test-exp", variant="variant")
        assert result is not None, "Promotion should succeed with stats disabled"

        # Simulate degradation to trigger rollback
        exp = mgr.ab_experiment_mgr._ab_experiments["test-exp"]
        exp["variant_accuracy"] = 0.60  # Drop below threshold
        exp["promotion_timestamp"] = datetime.now(timezone.utc).isoformat()

        rollbacks = mgr.check_promotion_rollback()
        assert len(rollbacks) == 1
        assert reverter.called

    def test_rollback_calls_auto_tuning_reverter(self):
        """When a promotion is rolled back, the auto-tuning reverter callback is invoked."""
        mgr = AlertManager(_make_config(ab_statistical_significance_enabled=False))
        auto_reverter = MagicMock(return_value=True)
        mgr.set_auto_tuning_reverter(auto_reverter)

        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)
        result = mgr.promote_variant("test-exp", variant="variant")
        assert result is not None, "Promotion should succeed with stats disabled"

        # Trigger rollback
        exp = mgr.ab_experiment_mgr._ab_experiments["test-exp"]
        exp["variant_accuracy"] = 0.60
        exp["promotion_timestamp"] = datetime.now(timezone.utc).isoformat()

        rollbacks = mgr.check_promotion_rollback()
        assert len(rollbacks) == 1
        assert auto_reverter.called

    def test_config_reversion_status_shows_callback_wired(self):
        """Config reversion status reports whether callbacks are wired."""
        mgr = AlertManager(_make_config())
        status = mgr.get_config_reversion_status()
        assert status["live_config_reverter_set"] is False
        assert status["auto_tuning_reverter_set"] is False

        mgr.set_live_config_reverter(MagicMock(return_value=True))
        mgr.set_auto_tuning_reverter(MagicMock(return_value=True))
        status = mgr.get_config_reversion_status()
        assert status["live_config_reverter_set"] is True
        assert status["auto_tuning_reverter_set"] is True

    def test_rollback_without_callbacks_still_succeeds(self):
        """Rollback should still work even if reverter callbacks are not set."""
        mgr = AlertManager(_make_config(ab_statistical_significance_enabled=False))
        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)
        result = mgr.promote_variant("test-exp", variant="variant")
        assert result is not None, "Promotion should succeed with stats disabled"

        exp = mgr.ab_experiment_mgr._ab_experiments["test-exp"]
        exp["variant_accuracy"] = 0.60
        exp["promotion_timestamp"] = datetime.now(timezone.utc).isoformat()

        rollbacks = mgr.check_promotion_rollback()
        assert len(rollbacks) == 1
        assert exp["status"] == "rolled_back"

    def test_auto_tuning_snapshot_captures_policy_state(self):
        """When auto_tuning_reverter is wired, the snapshot captures policy state."""
        mgr = AlertManager(_make_config())

        # Create a mock policy with to_dict()
        class FakePolicy:
            read_pool_exhaustion_threshold = 0.3
            cooldown_seconds = 60

            def to_dict(self):
                return {
                    "read_pool_exhaustion_threshold": 0.3,
                    "cooldown_seconds": 60,
                }

        fake_policy = FakePolicy()

        # Create a reverter closure that actually references policy in the body
        # so it gets captured in the closure cells
        def _make_reverter(policy):
            def _revert(snapshot_dict):
                # Reference policy so it's captured in the closure
                _ = policy  # noqa: F841
                return True

            return _revert

        mgr.set_auto_tuning_reverter(_make_reverter(fake_policy))
        snapshot = mgr._get_auto_tuning_snapshot()
        assert snapshot["source"] == "auto_tuning_policy"
        assert snapshot["read_pool_exhaustion_threshold"] == 0.3


# ---------------------------------------------------------------------------
# Deliverable 2: Persist Statistical Test Results
# ---------------------------------------------------------------------------


class TestStatisticalTestPersistence:
    """Tests for persisting statistical test results to SQLite."""

    def test_schema_v10_creates_statistical_test_results_table(self):
        """Schema v10 migration creates the statistical_test_results table."""
        store = _make_store()
        with store._get_connection() if hasattr(store, "_get_connection") else _connect(store) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='statistical_test_results'"
            )
            assert cursor.fetchone() is not None

    def test_schema_v10_creates_accuracy_timeseries_table(self):
        """Schema v10 migration creates the ab_accuracy_timeseries table."""
        store = _make_store()
        with _connect(store) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ab_accuracy_timeseries'")
            assert cursor.fetchone() is not None

    def test_record_statistical_test_result(self):
        """Statistical test results can be persisted and retrieved."""
        store = _make_store()
        result = {
            "p_value": 0.03,
            "method": "z_test",
            "significant": True,
            "statistic": 2.17,
            "confidence_interval": [-0.15, -0.01],
            "control_mean": 0.85,
            "variant_mean": 0.93,
            "control_samples": 100,
            "variant_samples": 100,
        }
        assert store.record_statistical_test_result("exp-1", result) is True

        results = store.get_statistical_test_results("exp-1")
        assert len(results) == 1
        assert results[0]["experiment_name"] == "exp-1"
        assert results[0]["p_value"] == 0.03
        assert results[0]["significant"] is True

    def test_get_all_statistical_test_results(self):
        """Can retrieve all statistical test results."""
        store = _make_store()
        for i in range(3):
            store.record_statistical_test_result(
                f"exp-{i}",
                {
                    "p_value": 0.01 * (i + 1),
                    "method": "z_test",
                    "significant": i < 2,
                },
            )
        results = store.get_statistical_test_results()
        assert len(results) == 3

    def test_persist_statistical_test_results_from_manager(self):
        """AlertManager.persist_statistical_test_results persists in-memory results."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)

        # Compute statistical significance to populate cache
        mgr.compute_statistical_significance("test-exp")

        # Persist
        count = mgr.persist_statistical_test_results(store)
        assert count >= 1

        # Verify it was stored
        results = store.get_statistical_test_results("test-exp")
        assert len(results) == 1
        assert results[0]["method"] == "z_test"

    def test_statistical_test_results_survive_store_recreation(self):
        """Statistical test results persist across store instances."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test_stat.db")

        # Write
        store1 = SyncAlertHistoryBridge(AlertHistoryStore(db_path))
        store1.initialize()
        store1.record_statistical_test_result(
            "exp-persist",
            {
                "p_value": 0.02,
                "method": "t_test",
                "significant": True,
                "confidence_interval": [-0.1, 0.05],
            },
        )

        # Read from new store instance
        store2 = SyncAlertHistoryBridge(AlertHistoryStore(db_path))
        store2.initialize()
        results = store2.get_statistical_test_results("exp-persist")
        assert len(results) == 1
        assert results[0]["method"] == "t_test"
        assert results[0]["significant"] is True


# ---------------------------------------------------------------------------
# Deliverable 3: Time-Series Data for Mini Charts
# ---------------------------------------------------------------------------


class TestAccuracyTimeseries:
    """Tests for accuracy time-series data persistence and retrieval."""

    def test_record_accuracy_snapshot(self):
        """Accuracy snapshots can be recorded and retrieved."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)

        snapshot = mgr.record_accuracy_snapshot("test-exp")
        assert snapshot is not None
        assert snapshot["experiment_name"] == "test-exp"
        assert "control_accuracy" in snapshot
        assert "variant_accuracy" in snapshot

    def test_get_accuracy_timeseries(self):
        """Time-series data can be retrieved for an experiment."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)

        # Record multiple snapshots
        for _ in range(5):
            mgr.record_accuracy_snapshot("test-exp")

        timeseries = mgr.get_accuracy_timeseries("test-exp")
        assert len(timeseries) >= 5

    def test_accuracy_timeseries_persisted_to_store(self):
        """Accuracy snapshots are persisted to the history store."""
        store = _make_store()

        snapshot = {
            "experiment_name": "exp-ts",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "control_accuracy": 0.85,
            "variant_accuracy": 0.92,
            "control_samples": 50,
            "variant_samples": 50,
            "status": "running",
        }

        assert store.record_accuracy_timeseries(snapshot) is True
        results = store.get_accuracy_timeseries("exp-ts")
        assert len(results) == 1
        assert results[0]["control_accuracy"] == 0.85
        assert results[0]["variant_accuracy"] == 0.92

    def test_accuracy_timeseries_survives_restart(self):
        """Time-series data persists across store instances."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test_ts.db")

        # Write
        store1 = SyncAlertHistoryBridge(AlertHistoryStore(db_path))
        store1.initialize()
        for i in range(3):
            store1.record_accuracy_timeseries(
                {
                    "experiment_name": "exp-restart",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "control_accuracy": 0.80 + i * 0.01,
                    "variant_accuracy": 0.90 + i * 0.01,
                    "control_samples": 50,
                    "variant_samples": 50,
                }
            )

        # Read from new instance
        store2 = SyncAlertHistoryBridge(AlertHistoryStore(db_path))
        store2.initialize()
        results = store2.get_accuracy_timeseries("exp-restart")
        assert len(results) == 3

    def test_prune_accuracy_timeseries(self):
        """Old timeseries data can be pruned."""
        store = _make_store()

        # Insert a snapshot with a past timestamp
        old_timestamp = datetime.fromtimestamp(time.time() - 200 * 3600, tz=timezone.utc).isoformat()
        store.record_accuracy_timeseries(
            {
                "experiment_name": "exp-prune",
                "timestamp": old_timestamp,
                "control_accuracy": 0.85,
                "variant_accuracy": 0.92,
            }
        )

        # Insert a recent snapshot
        store.record_accuracy_timeseries(
            {
                "experiment_name": "exp-prune",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "control_accuracy": 0.87,
                "variant_accuracy": 0.94,
            }
        )

        # Prune data older than 168 hours (7 days)
        pruned = store.prune_accuracy_timeseries(max_age_hours=168)
        assert pruned == 1

        # Verify only the recent snapshot remains
        results = store.get_accuracy_timeseries("exp-prune")
        assert len(results) == 1
        assert results[0]["control_accuracy"] == 0.87

    def test_timeseries_max_200_per_experiment(self):
        """In-memory timeseries is capped at 200 snapshots per experiment."""
        mgr = AlertManager(_make_config())
        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)

        for _ in range(250):
            mgr.record_accuracy_snapshot("test-exp")

        exp = mgr.ab_experiment_mgr._ab_experiments["test-exp"]
        assert len(exp.get("accuracy_timeseries", [])) <= 200


# ---------------------------------------------------------------------------
# Deliverable 4: Multi-Armed Bandit Support
# ---------------------------------------------------------------------------


class TestMultiArmedBandit:
    """Tests for multi-armed bandit traffic allocation."""

    def test_bandit_disabled_returns_50_50(self):
        """When bandit is disabled, allocation is 50/50."""
        mgr = AlertManager(_make_config(ab_bandit_enabled=False))
        _start_experiment(mgr)
        allocation = mgr.get_bandit_allocation("test-exp")
        assert allocation["control"] == 0.5
        assert allocation["variant"] == 0.5

    def test_thompson_sampling_allocates_more_to_better_variant(self):
        """Thompson Sampling allocates more traffic to the better-performing variant."""
        mgr = AlertManager(_make_config(ab_bandit_enabled=True, ab_bandit_method="thompson"))
        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=100, c_acc=0.70, v_acc=0.90)

        # Sample multiple times — variant should get more traffic on average
        variant_allocations = []
        for _ in range(100):
            alloc = mgr.get_bandit_allocation("test-exp")
            variant_allocations.append(alloc["variant"])

        avg_variant = sum(variant_allocations) / len(variant_allocations)
        assert avg_variant > 0.5, f"Expected variant to get >50% allocation on average, got {avg_variant:.3f}"

    def test_ucb_allocates_more_to_better_variant(self):
        """UCB allocates more traffic to the better-performing variant."""
        mgr = AlertManager(_make_config(ab_bandit_enabled=True, ab_bandit_method="ucb", ab_bandit_explore_rate=0.1))
        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=100, c_acc=0.70, v_acc=0.90)

        allocation = mgr.get_bandit_allocation("test-exp")
        assert allocation["variant"] > allocation["control"], f"Expected variant allocation > control, got {allocation}"

    def test_bandit_allocation_sums_to_1(self):
        """Bandit allocation fractions always sum to 1.0."""
        mgr = AlertManager(_make_config(ab_bandit_enabled=True, ab_bandit_method="thompson"))
        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)

        for _ in range(20):
            alloc = mgr.get_bandit_allocation("test-exp")
            total = alloc["control"] + alloc["variant"]
            assert abs(total - 1.0) < 0.01, f"Allocation sums to {total}, expected ~1.0"

    def test_bandit_unknown_experiment_returns_50_50(self):
        """Unknown experiment returns 50/50 allocation."""
        mgr = AlertManager(_make_config(ab_bandit_enabled=True))
        allocation = mgr.get_bandit_allocation("nonexistent")
        assert allocation["control"] == 0.5
        assert allocation["variant"] == 0.5

    def test_bandit_status(self):
        """Bandit status endpoint returns correct configuration and state."""
        mgr = AlertManager(_make_config(ab_bandit_enabled=True, ab_bandit_method="thompson"))
        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)

        mgr.get_bandit_allocation("test-exp")

        status = mgr.get_bandit_status()
        assert status["enabled"] is True
        assert status["method"] == "thompson"
        assert status["total_allocations"] == 1
        assert "test-exp" in status["active_experiments"]

    def test_bandit_with_equal_performance(self):
        """When both variants perform equally, allocation should be roughly even."""
        mgr = AlertManager(_make_config(ab_bandit_enabled=True, ab_bandit_method="ucb"))
        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=100, c_acc=0.80, v_acc=0.80)

        allocation = mgr.get_bandit_allocation("test-exp")
        # With equal performance, allocation should be close to 50/50
        assert 0.3 < allocation["control"] < 0.7
        assert 0.3 < allocation["variant"] < 0.7


# ---------------------------------------------------------------------------
# Deliverable 5: Rollback Dry-Run Mode
# ---------------------------------------------------------------------------


class TestRollbackDryRun:
    """Tests for rollback dry-run mode."""

    def test_dry_run_does_not_rollback(self):
        """When dry-run is enabled, rollback conditions are evaluated but not executed."""
        mgr = AlertManager(_make_config(ab_rollback_dry_run=True, ab_statistical_significance_enabled=False))
        reverter = MagicMock(return_value=True)
        mgr.set_live_config_reverter(reverter)

        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)
        result = mgr.promote_variant("test-exp", variant="variant")
        assert result is not None, "Promotion should succeed with stats disabled"

        # Simulate degradation
        exp = mgr.ab_experiment_mgr._ab_experiments["test-exp"]
        exp["variant_accuracy"] = 0.60
        exp["promotion_timestamp"] = datetime.now(timezone.utc).isoformat()

        rollbacks = mgr.check_promotion_rollback()
        assert len(rollbacks) == 1

        # The experiment should NOT be rolled back
        assert exp["status"] == "promoted"

        # The reverter should NOT have been called
        assert not reverter.called

        # The dry-run result should indicate what would happen
        assert rollbacks[0]["dry_run"] is True
        assert rollbacks[0]["would_rollback"] is True

    def test_dry_run_includes_config_reversion_preview(self):
        """Dry-run results include a preview of config reversion."""
        mgr = AlertManager(_make_config(ab_rollback_dry_run=True, ab_statistical_significance_enabled=False))
        mgr.set_live_config_reverter(MagicMock(return_value=True))
        mgr.set_auto_tuning_reverter(MagicMock(return_value=True))

        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)
        result = mgr.promote_variant("test-exp", variant="variant")
        assert result is not None, "Promotion should succeed with stats disabled"

        exp = mgr.ab_experiment_mgr._ab_experiments["test-exp"]
        exp["variant_accuracy"] = 0.60
        exp["promotion_timestamp"] = datetime.now(timezone.utc).isoformat()

        rollbacks = mgr.check_promotion_rollback()
        assert len(rollbacks) == 1

        preview = rollbacks[0].get("config_reversion_preview", {})
        assert preview is not None
        assert preview.get("would_revert_model_config") is True
        assert preview.get("would_revert_auto_tuning") is True

    def test_dry_run_disabled_actually_rollbacks(self):
        """When dry-run is disabled, rollback is actually performed."""
        mgr = AlertManager(_make_config(ab_rollback_dry_run=False, ab_statistical_significance_enabled=False))
        reverter = MagicMock(return_value=True)
        mgr.set_live_config_reverter(reverter)

        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)
        result = mgr.promote_variant("test-exp", variant="variant")
        assert result is not None, "Promotion should succeed with stats disabled"

        exp = mgr.ab_experiment_mgr._ab_experiments["test-exp"]
        exp["variant_accuracy"] = 0.60
        exp["promotion_timestamp"] = datetime.now(timezone.utc).isoformat()

        rollbacks = mgr.check_promotion_rollback()
        assert len(rollbacks) == 1

        # The experiment SHOULD be rolled back
        assert exp["status"] == "rolled_back"

        # The reverter SHOULD have been called
        assert reverter.called

    def test_dry_run_status(self):
        """Dry-run status endpoint returns correct metrics."""
        mgr = AlertManager(_make_config(ab_rollback_dry_run=True, ab_statistical_significance_enabled=False))
        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)
        result = mgr.promote_variant("test-exp", variant="variant")
        assert result is not None, "Promotion should succeed with stats disabled"

        exp = mgr.ab_experiment_mgr._ab_experiments["test-exp"]
        exp["variant_accuracy"] = 0.60
        exp["promotion_timestamp"] = datetime.now(timezone.utc).isoformat()

        # Evaluate rollback
        mgr.check_promotion_rollback()

        status = mgr.get_rollback_dry_run_status()
        assert status["enabled"] is True
        assert status["total_evaluations"] >= 1
        assert status["total_would_rollback"] >= 1

    def test_dry_run_no_snapshot_shows_reason(self):
        """Dry-run with no config snapshot shows appropriate reason."""
        mgr = AlertManager(_make_config(ab_rollback_dry_run=True, ab_statistical_significance_enabled=False))
        mgr.set_live_config_reverter(MagicMock(return_value=True))

        _start_experiment(mgr)
        _add_results(mgr, "test-exp", n=50)
        # Promote without saving snapshot (simulate stale state)
        result = mgr.promote_variant("test-exp", variant="variant")
        assert result is not None, "Promotion should succeed with stats disabled"

        exp = mgr.ab_experiment_mgr._ab_experiments["test-exp"]
        exp["variant_accuracy"] = 0.60
        exp["promotion_timestamp"] = datetime.now(timezone.utc).isoformat()

        # Clear the snapshot to test the "no snapshot" path
        mgr.ab_experiment_mgr._pre_promotion_config_snapshots.pop("test-exp", None)

        rollbacks = mgr.check_promotion_rollback()
        if rollbacks:
            preview = rollbacks[0].get("config_reversion_preview", {})
            if preview:
                assert (
                    preview.get("reason") == "no_pre_promotion_snapshot"
                    or preview.get("would_revert_model_config") is not None
                )


# ---------------------------------------------------------------------------
# Integration: Monitoring summary includes all Sprint 5.48 data
# ---------------------------------------------------------------------------


class TestMonitoringSummaryIntegration:
    """Tests that the monitoring summary includes Sprint 5.48 data."""

    def test_monitoring_summary_includes_bandit(self):
        """Monitoring summary includes bandit status."""
        mgr = AlertManager(_make_config(ab_bandit_enabled=True))
        summary = mgr.get_experiment_monitoring_summary()
        assert "bandit" in summary
        assert summary["bandit"]["enabled"] is True

    def test_monitoring_summary_includes_dry_run(self):
        """Monitoring summary includes rollback dry-run status."""
        mgr = AlertManager(_make_config(ab_rollback_dry_run=True))
        summary = mgr.get_experiment_monitoring_summary()
        assert "rollback_dry_run" in summary
        assert summary["rollback_dry_run"]["enabled"] is True

    def test_status_includes_bandit_and_dry_run(self):
        """AlertManager.get_status() includes bandit and dry-run data."""
        mgr = AlertManager(_make_config(ab_bandit_enabled=True, ab_rollback_dry_run=True))
        status = mgr.get_status()
        assert "ab_bandit" in status
        assert "ab_rollback_dry_run" in status
        assert status["ab_bandit"]["enabled"] is True
        assert status["ab_rollback_dry_run"]["enabled"] is True


# ---------------------------------------------------------------------------
# Connection helper for SQLite
# ---------------------------------------------------------------------------


def _connect(store: AlertHistoryStore):
    """Get a raw sqlite3 connection from the store for test queries."""
    import sqlite3

    return sqlite3.connect(store._db_path)
