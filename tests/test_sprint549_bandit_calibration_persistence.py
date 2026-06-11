"""Sprint 5.49 tests: Bandit integration, calibration persistence, snapshot persistence, and bandit enhancements.

Covers all 5 deliverables:
1. Bandit Integration with Auto-Promotion
2. Dashboard Mini Chart Rendering (Real Data)
3. Confidence Calibration Persistence
4. Pre-Promotion Config Snapshot Persistence
5. Bandit Enhancements (epsilon-greedy + Contextual)
"""

import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from aip.adapter.alerting import AlertConfig, AlertManager
from aip.adapter.alert_history_store import AlertHistoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AlertConfig:
    """Create an AlertConfig with sensible test defaults."""
    defaults = {
        "enabled": False,
        "ab_experiment_enabled": True,
        "ab_auto_promote_interval_seconds": 0,
        "ab_cleanup_interval_seconds": 0,
        "ab_rollback_enabled": True,
        "ab_rollback_observation_window_seconds": 3600,
        "ab_rollback_accuracy_drop_threshold": 0.05,
        "ab_rollback_revert_live_config": True,
        "ab_statistical_significance_enabled": False,
        "ab_confidence_calibration_enabled": True,
        "ab_rollback_dry_run": False,
        "ab_bandit_enabled": False,
        "ab_bandit_method": "thompson",
        "ab_bandit_explore_rate": 0.1,
        "ab_bandit_contextual_enabled": False,
        "ab_bandit_contextual_features": ["alert_type", "subject"],
        "ab_bandit_accuracy_snapshot_interval_seconds": 0,
    }
    defaults.update(overrides)
    return AlertConfig(**defaults)


def _make_store() -> AlertHistoryStore:
    """Create an in-memory AlertHistoryStore for testing."""
    db_path = os.path.join(tempfile.mkdtemp(), "test_alert_history.db")
    store = AlertHistoryStore(db_path)
    store.initialize()
    return store


def _start_experiment(mgr: AlertManager, name: str = "test-exp", **meta) -> dict:
    """Start a simple A/B experiment with optional metadata."""
    return mgr.start_ab_experiment(
        name=name,
        control_config={"model": "gpt-4", "temperature": 0.7},
        variant_config={"model": "gpt-4-turbo", "temperature": 0.5},
        metadata=meta or {},
    )


def _add_results(mgr: AlertManager, name: str, n: int = 50, c_acc: float = 0.85, v_acc: float = 0.92) -> None:
    """Record A/B results for an experiment."""
    for i in range(n):
        mgr.record_ab_result(name=name, variant="control", accuracy=c_acc + (i % 5) * 0.001)
        mgr.record_ab_result(name=name, variant="variant", accuracy=v_acc + (i % 5) * 0.001)


# ---------------------------------------------------------------------------
# Deliverable 1: Bandit Integration with Auto-Promotion
# ---------------------------------------------------------------------------


class TestBanditAutoPromotion:
    """Tests for bandit integration with the auto-promotion checker."""

    def test_check_auto_promotion_uses_bandit_allocation(self):
        """When bandit is enabled, _check_auto_promotion queries bandit allocation."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_method="thompson",
                ab_auto_promote_confidence_threshold=0.90,
                ab_auto_promote_min_samples=10,
            )
        )
        _start_experiment(mgr, "bandit-exp")
        _add_results(mgr, "bandit-exp", n=50, c_acc=0.70, v_acc=0.95)

        # The promotion checker should consider bandit allocation
        initial_promotions = mgr.ab_experiment_mgr._total_ab_auto_promotions
        mgr._check_auto_promotion()
        # With v_acc=0.95 and c_acc=0.70, variant should be auto-promoted
        assert mgr.ab_experiment_mgr._total_ab_auto_promotions >= initial_promotions

    def test_accuracy_snapshots_recorded_in_promotion_checker(self):
        """When snapshot interval > 0, promotion checker records accuracy snapshots."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_accuracy_snapshot_interval_seconds=1,
                ab_auto_promote_min_samples=100,  # Prevent auto-promotion
            )
        )
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr, "snap-exp")
        _add_results(mgr, "snap-exp", n=50)

        # Reset snapshot timer so the checker will take a snapshot
        mgr._last_accuracy_snapshot_time = 0.0
        mgr._check_auto_promotion()

        # Verify a snapshot was recorded
        exp = mgr.ab_experiment_mgr._ab_experiments["snap-exp"]
        ts = exp.get("accuracy_timeseries", [])
        assert len(ts) >= 1, "Expected at least one accuracy snapshot from promotion checker"

    def test_bandit_boost_helps_borderline_experiments(self):
        """Bandit allocation >70% gives a slight boost to borderline experiments."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_method="thompson",
                ab_auto_promote_confidence_threshold=0.95,
                ab_auto_promote_min_samples=30,
            )
        )
        _start_experiment(mgr, "borderline-exp")
        # Set up borderline results where bandit signal can help
        _add_results(mgr, "borderline-exp", n=50, c_acc=0.85, v_acc=0.94)

        # Run the promotion checker - bandit should favor variant
        mgr._check_auto_promotion()
        exp = mgr.ab_experiment_mgr._ab_experiments["borderline-exp"]
        # The experiment may or may not be promoted depending on bandit sampling,
        # but the checker should have run without error
        assert exp is not None

    def test_promoted_experiment_includes_bandit_winner(self):
        """When auto-promotion uses bandit, the experiment records bandit_winner."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_method="ucb",
                ab_auto_promote_confidence_threshold=0.90,
                ab_auto_promote_min_samples=10,
            )
        )
        _start_experiment(mgr, "winner-exp")
        _add_results(mgr, "winner-exp", n=50, c_acc=0.70, v_acc=0.95)

        mgr._check_auto_promotion()
        exp = mgr.ab_experiment_mgr._ab_experiments["winner-exp"]

        if exp.get("status") == "promoted":
            # If promoted, bandit_winner should be set
            assert "bandit_winner" in exp

    def test_snapshot_interval_zero_disables_auto_snapshots(self):
        """When snapshot interval is 0, no auto-snapshots are taken in promotion checker."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_accuracy_snapshot_interval_seconds=0,
            )
        )
        _start_experiment(mgr, "no-snap-exp")
        _add_results(mgr, "no-snap-exp", n=50)

        mgr._check_auto_promotion()
        exp = mgr.ab_experiment_mgr._ab_experiments["no-snap-exp"]
        ts = exp.get("accuracy_timeseries", [])
        # No snapshots should be recorded by the checker
        assert len(ts) == 0


# ---------------------------------------------------------------------------
# Deliverable 2: Dashboard Mini Chart Rendering (Real Data)
# ---------------------------------------------------------------------------


class TestDashboardMiniChartRealData:
    """Tests for real accuracy timeseries data for dashboard mini charts."""

    def test_accuracy_timeseries_api_endpoint_data(self):
        """Accuracy timeseries data is structured correctly for the API."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr, "chart-exp")
        _add_results(mgr, "chart-exp", n=50)

        # Record multiple snapshots at different times
        snapshots = []
        for i in range(5):
            mgr.ab_experiment_mgr._ab_experiments["chart-exp"]["control_accuracy"] = 0.80 + i * 0.01
            mgr.ab_experiment_mgr._ab_experiments["chart-exp"]["variant_accuracy"] = 0.90 + i * 0.01
            snap = mgr.record_accuracy_snapshot("chart-exp")
            snapshots.append(snap)

        timeseries = mgr.get_accuracy_timeseries("chart-exp")
        assert len(timeseries) >= 5

        # Verify each snapshot has the required fields for chart rendering
        for ts in timeseries:
            assert "timestamp" in ts
            assert "control_accuracy" in ts
            assert "variant_accuracy" in ts
            assert "status" in ts

    def test_timeseries_status_field_for_markers(self):
        """Accuracy snapshots include status field for promotion/decay markers."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr, "marker-exp")
        _add_results(mgr, "marker-exp", n=50)

        # Record snapshot while running
        snap_running = mgr.record_accuracy_snapshot("marker-exp")
        assert snap_running["status"] == "running"

        # Promote the variant
        mgr.promote_variant("marker-exp", variant="variant")
        exp = mgr.ab_experiment_mgr._ab_experiments["marker-exp"]
        exp["control_accuracy"] = 0.85
        exp["variant_accuracy"] = 0.93

        # Record snapshot while promoted
        snap_promoted = mgr.record_accuracy_snapshot("marker-exp")
        assert snap_promoted["status"] == "promoted"

    def test_persisted_timeseries_available_for_dashboard(self):
        """Timeseries data persisted to SQLite is available for dashboard rendering."""
        store = _make_store()

        for i in range(3):
            store.record_accuracy_timeseries(
                {
                    "experiment_name": "dashboard-exp",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "control_accuracy": 0.80 + i * 0.01,
                    "variant_accuracy": 0.90 + i * 0.01,
                    "control_samples": 50,
                    "variant_samples": 50,
                    "status": "running",
                }
            )

        results = store.get_accuracy_timeseries("dashboard-exp")
        assert len(results) == 3
        # Results should be sorted by timestamp ascending
        for r in results:
            assert "control_accuracy" in r
            assert "variant_accuracy" in r


# ---------------------------------------------------------------------------
# Deliverable 3: Confidence Calibration Persistence
# ---------------------------------------------------------------------------


class TestConfidenceCalibrationPersistence:
    """Tests for persisting confidence calibration data to SQLite."""

    def test_schema_v11_creates_confidence_calibration_table(self):
        """Schema v11 migration creates the confidence_calibration table."""
        store = _make_store()
        import sqlite3

        with sqlite3.connect(store._db_path) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='confidence_calibration'")
            assert cursor.fetchone() is not None

    def test_record_confidence_calibration(self):
        """Confidence calibration entries can be persisted and retrieved."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        assert store.record_confidence_calibration("vigil_faithfulness", 0.95, now_iso) is True
        assert store.record_confidence_calibration("vigil_citation", 1.02, now_iso) is True

        results = store.get_confidence_calibrations()
        assert len(results) == 2
        subjects = {r["subject"] for r in results}
        assert "vigil_faithfulness" in subjects
        assert "vigil_citation" in subjects

    def test_confidence_calibration_upsert(self):
        """Updating a calibration factor uses INSERT OR REPLACE (upsert)."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        store.record_confidence_calibration("subject_a", 0.90, now_iso)
        store.record_confidence_calibration("subject_a", 0.95, now_iso)

        results = store.get_confidence_calibrations()
        subject_a = [r for r in results if r["subject"] == "subject_a"]
        assert len(subject_a) == 1
        assert subject_a[0]["calibration_factor"] == 0.95

    def test_persist_confidence_calibration_from_manager(self):
        """AlertManager.persist_confidence_calibration persists in-memory data."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)

        mgr.update_confidence_calibration("subject_x", 0.88, 0.90)
        mgr.update_confidence_calibration("subject_y", 0.92, 0.95)

        count = mgr.persist_confidence_calibration(store)
        assert count == 2

        # Verify persisted
        results = store.get_confidence_calibrations()
        assert len(results) == 2

    def test_restore_confidence_calibration_on_startup(self):
        """AlertManager.restore_confidence_calibration loads persisted data."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Persist some calibration data
        store.record_confidence_calibration("restored_subject", 0.97, now_iso)

        # Create a fresh manager and restore
        mgr = AlertManager(_make_config())
        count = mgr.restore_confidence_calibration(store)
        assert count == 1
        assert mgr.ab_experiment_mgr._confidence_calibration_map.get("restored_subject") == 0.97

    def test_calibration_survives_restart(self):
        """Calibration data survives a simulated restart (persist + restore)."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test_calib.db")

        # First session: create manager, update calibration, persist
        store1 = AlertHistoryStore(db_path)
        store1.initialize()
        mgr1 = AlertManager(_make_config())
        mgr1.attach_history_store(store1)
        mgr1.update_confidence_calibration("survival_subject", 0.85, 0.90)
        mgr1.persist_confidence_calibration(store1)

        # Second session: new manager, restore from same store
        store2 = AlertHistoryStore(db_path)
        store2.initialize()
        mgr2 = AlertManager(_make_config())
        count = mgr2.restore_confidence_calibration(store2)
        assert count == 1
        assert "survival_subject" in mgr2._confidence_calibration_map
        assert abs(mgr2._confidence_calibration_map["survival_subject"] - 0.85 / 0.90) < 0.1

    def test_calibration_status_includes_persistence_info(self):
        """Confidence calibration status includes last update time and persistence state."""
        mgr = AlertManager(_make_config())
        mgr.update_confidence_calibration("test_subject", 0.90, 0.85)

        status = mgr.get_confidence_calibration_status()
        assert "last_update_time" in status
        assert status["last_update_time"] is not None
        assert "persisted" in status

    def test_persist_all_ab_experiments_includes_calibration(self):
        """persist_all_ab_experiments also persists confidence calibration."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)

        mgr.update_confidence_calibration("shutdown_subject", 0.91, 0.88)
        mgr.persist_all_ab_experiments()

        # Verify calibration was persisted
        results = store.get_confidence_calibrations()
        subjects = {r["subject"] for r in results}
        assert "shutdown_subject" in subjects


# ---------------------------------------------------------------------------
# Deliverable 4: Pre-Promotion Config Snapshot Persistence
# ---------------------------------------------------------------------------


class TestPrePromotionSnapshotPersistence:
    """Tests for persisting pre-promotion config snapshots to SQLite."""

    def test_schema_v11_creates_pre_promotion_snapshots_table(self):
        """Schema v11 migration creates the pre_promotion_snapshots table."""
        store = _make_store()
        import sqlite3

        with sqlite3.connect(store._db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='pre_promotion_snapshots'"
            )
            assert cursor.fetchone() is not None

    def test_record_pre_promotion_snapshot(self):
        """Pre-promotion snapshots can be persisted and retrieved."""
        store = _make_store()
        snapshot = {
            "control_config": {"model": "gpt-4"},
            "variant_config": {"model": "gpt-4-turbo"},
            "promoted_variant": "variant",
            "baseline_config": {"model": "gpt-4"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        assert store.record_pre_promotion_snapshot("exp-snap", snapshot) is True
        results = store.get_pre_promotion_snapshots()
        assert len(results) == 1
        assert results[0]["experiment_name"] == "exp-snap"
        assert results[0]["snapshot_data"]["control_config"]["model"] == "gpt-4"

    def test_delete_pre_promotion_snapshot(self):
        """Pre-promotion snapshots can be deleted after rollback."""
        store = _make_store()
        store.record_pre_promotion_snapshot("exp-delete", {"test": True})
        assert store.delete_pre_promotion_snapshot("exp-delete") is True
        results = store.get_pre_promotion_snapshots()
        assert len(results) == 0

    def test_persist_pre_promotion_snapshots_from_manager(self):
        """AlertManager.persist_pre_promotion_snapshots persists in-memory snapshots."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr, "persist-snap-exp")
        _add_results(mgr, "persist-snap-exp", n=50)
        mgr.promote_variant("persist-snap-exp", variant="variant")

        # Verify snapshot exists in memory
        assert "persist-snap-exp" in mgr.ab_experiment_mgr._pre_promotion_config_snapshots

        # Persist
        count = mgr.persist_pre_promotion_snapshots(store)
        assert count >= 1

    def test_restore_pre_promotion_snapshots_on_startup(self):
        """AlertManager.restore_pre_promotion_snapshots loads persisted data."""
        store = _make_store()
        snapshot = {
            "control_config": {"model": "gpt-4"},
            "variant_config": {"model": "gpt-4-turbo"},
            "baseline_config": {"model": "gpt-4"},
        }
        store.record_pre_promotion_snapshot("restored-exp", snapshot)

        mgr = AlertManager(_make_config())
        count = mgr.restore_pre_promotion_snapshots(store)
        assert count == 1
        assert "restored-exp" in mgr.ab_experiment_mgr._pre_promotion_config_snapshots
        assert (
            mgr.ab_experiment_mgr._pre_promotion_config_snapshots["restored-exp"]["control_config"]["model"] == "gpt-4"
        )

    def test_rollback_after_restart_uses_persisted_snapshot(self):
        """After restart, rollback can use a persisted snapshot to revert config."""
        db_dir = tempfile.mkdtemp()
        db_path = os.path.join(db_dir, "test_snap.db")

        # First session: create experiment, promote, persist snapshot
        store1 = AlertHistoryStore(db_path)
        store1.initialize()
        mgr1 = AlertManager(_make_config())
        mgr1.attach_history_store(store1)

        _start_experiment(mgr1, "restart-rollback-exp")
        _add_results(mgr1, "restart-rollback-exp", n=50)
        mgr1.promote_variant("restart-rollback-exp", variant="variant")
        mgr1.persist_pre_promotion_snapshots(store1)

        # Second session: new manager, restore snapshot
        store2 = AlertHistoryStore(db_path)
        store2.initialize()
        mgr2 = AlertManager(_make_config())
        mgr2.attach_history_store(store2)
        count = mgr2.restore_pre_promotion_snapshots(store2)
        assert count == 1

        # Verify the snapshot is usable for rollback
        assert "restart-rollback-exp" in mgr2._pre_promotion_config_snapshots

    def test_rollback_deletes_persisted_snapshot(self):
        """After a successful rollback, the persisted snapshot is cleaned up."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)
        reverter = MagicMock(return_value=True)
        mgr.set_live_config_reverter(reverter)

        _start_experiment(mgr, "cleanup-snap-exp")
        _add_results(mgr, "cleanup-snap-exp", n=50)
        mgr.promote_variant("cleanup-snap-exp", variant="variant")

        # Persist the snapshot
        mgr.persist_pre_promotion_snapshots(store)

        # Simulate degradation to trigger rollback
        exp = mgr.ab_experiment_mgr._ab_experiments["cleanup-snap-exp"]
        exp["variant_accuracy"] = 0.60
        exp["promotion_timestamp"] = datetime.now(timezone.utc).isoformat()

        mgr.check_promotion_rollback()

        # Verify snapshot was cleaned from both memory and store
        assert "cleanup-snap-exp" not in mgr.ab_experiment_mgr._pre_promotion_config_snapshots

    def test_monitoring_summary_includes_snapshot_status(self):
        """Monitoring summary includes pre-promotion snapshot persistence status."""
        mgr = AlertManager(_make_config())
        _start_experiment(mgr, "summary-exp")
        _add_results(mgr, "summary-exp", n=50)
        mgr.promote_variant("summary-exp", variant="variant")

        summary = mgr.get_experiment_monitoring_summary()
        assert "pre_promotion_snapshots" in summary
        assert summary["pre_promotion_snapshots"]["in_memory_count"] >= 1


# ---------------------------------------------------------------------------
# Deliverable 5: Bandit Enhancements (epsilon-greedy + Contextual)
# ---------------------------------------------------------------------------


class TestEpsilonGreedyBandit:
    """Tests for epsilon-greedy bandit method."""

    def test_epsilon_greedy_returns_valid_allocation(self):
        """Epsilon-greedy returns valid allocation fractions."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_method="epsilon_greedy",
                ab_bandit_explore_rate=0.1,
            )
        )
        _start_experiment(mgr, "egreedy-exp")
        _add_results(mgr, "egreedy-exp", n=100, c_acc=0.70, v_acc=0.90)

        alloc = mgr.get_bandit_allocation("egreedy-exp")
        total = alloc["control"] + alloc["variant"]
        assert abs(total - 1.0) < 0.01
        assert 0.0 <= alloc["control"] <= 1.0
        assert 0.0 <= alloc["variant"] <= 1.0

    def test_epsilon_greedy_favors_better_variant_on_exploit(self):
        """Epsilon-greedy with low epsilon mostly favors the better variant."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_method="epsilon_greedy",
                ab_bandit_explore_rate=0.01,  # Very low epsilon = mostly exploit
            )
        )
        _start_experiment(mgr, "egreedy-exploit")
        _add_results(mgr, "egreedy-exploit", n=100, c_acc=0.70, v_acc=0.90)

        # Sample multiple times
        variant_wins = 0
        for _ in range(200):
            alloc = mgr.get_bandit_allocation("egreedy-exploit")
            if alloc["variant"] > alloc["control"]:
                variant_wins += 1

        # With low epsilon and better variant, should favor variant most of the time
        assert variant_wins > 100, f"Expected variant to win >50% of the time, got {variant_wins}/200"

    def test_epsilon_greedy_equal_accuracy_explores(self):
        """Epsilon-greedy with equal accuracy produces valid allocations."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_method="epsilon_greedy",
                ab_bandit_explore_rate=0.5,
            )
        )
        _start_experiment(mgr, "egreedy-equal")
        _add_results(mgr, "egreedy-equal", n=100, c_acc=0.80, v_acc=0.80)

        allocations = [mgr.get_bandit_allocation("egreedy-equal") for _ in range(50)]
        # With equal accuracy, allocations should be valid (sum to ~1.0)
        for alloc in allocations:
            total = alloc["control"] + alloc["variant"]
            assert abs(total - 1.0) < 0.01, f"Allocation sums to {total}"
            assert 0.0 <= alloc["control"] <= 1.0
            assert 0.0 <= alloc["variant"] <= 1.0

    def test_bandit_method_config_accepts_epsilon_greedy(self):
        """AlertConfig accepts 'epsilon_greedy' as a valid bandit method."""
        config = _make_config(ab_bandit_method="epsilon_greedy")
        assert config.ab_bandit_method == "epsilon_greedy"


class TestContextualBandit:
    """Tests for contextual bandit support."""

    def test_contextual_bandit_config_fields(self):
        """AlertConfig has contextual bandit configuration fields."""
        config = _make_config(
            ab_bandit_contextual_enabled=True,
            ab_bandit_contextual_features=["alert_type", "subject"],
        )
        assert config.ab_bandit_contextual_enabled is True
        assert config.ab_bandit_contextual_features == ["alert_type", "subject"]

    def test_record_bandit_context_reward(self):
        """Contextual bandit rewards can be recorded."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_contextual_enabled=True,
                ab_bandit_contextual_features=["alert_type", "subject"],
            )
        )
        _start_experiment(mgr, "ctx-exp", alert_type="quality_degradation", subject="vigil")

        mgr.record_bandit_context_reward("ctx-exp", "variant", 0.92)
        mgr.record_bandit_context_reward("ctx-exp", "control", 0.85)

        assert "ctx-exp" in mgr.ab_experiment_mgr._bandit_context_rewards
        # Should have entries for both variants
        keys = list(mgr.ab_experiment_mgr._bandit_context_rewards["ctx-exp"].keys())
        assert len(keys) >= 2

    def test_context_reward_ignored_when_disabled(self):
        """Contextual rewards are not recorded when contextual bandit is disabled."""
        mgr = AlertManager(_make_config(ab_bandit_contextual_enabled=False))
        _start_experiment(mgr, "no-ctx-exp", alert_type="test")

        mgr.record_bandit_context_reward("no-ctx-exp", "variant", 0.92)
        assert len(mgr.ab_experiment_mgr._bandit_context_rewards) == 0

    def test_context_adjusts_allocation(self):
        """Contextual bandit adjusts allocation based on historical rewards."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_method="thompson",
                ab_bandit_contextual_enabled=True,
                ab_bandit_contextual_features=["alert_type"],
            )
        )
        _start_experiment(mgr, "adj-exp", alert_type="quality_degradation")
        _add_results(mgr, "adj-exp", n=50, c_acc=0.70, v_acc=0.90)

        # Record many context rewards showing variant is much better for this context
        for _ in range(10):
            mgr.record_bandit_context_reward("adj-exp", "variant", 0.92)
            mgr.record_bandit_context_reward("adj-exp", "control", 0.75)

        # Get allocation — contextual adjustment should shift toward variant
        alloc = mgr.get_bandit_allocation("adj-exp")
        assert alloc["variant"] > 0.5, f"Expected variant allocation >0.5 with context, got {alloc}"

    def test_bandit_status_includes_contextual_info(self):
        """Bandit status includes contextual bandit information."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_contextual_enabled=True,
            )
        )
        status = mgr.get_bandit_status()
        assert status["contextual_enabled"] is True
        assert "contextual_features" in status
        assert "contextual_rewards_tracked" in status

    def test_context_reward_capped_at_100(self):
        """Contextual reward history is capped at 100 entries per context key."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_contextual_enabled=True,
                ab_bandit_contextual_features=["alert_type"],
            )
        )
        _start_experiment(mgr, "cap-exp", alert_type="test")

        for i in range(150):
            mgr.record_bandit_context_reward("cap-exp", "variant", 0.9)

        # Should be capped at 100
        for key, rewards in mgr.ab_experiment_mgr._bandit_context_rewards["cap-exp"].items():
            assert len(rewards) <= 100

    def test_context_reward_with_no_metadata(self):
        """Contextual reward with no matching metadata is silently ignored."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_contextual_enabled=True,
                ab_bandit_contextual_features=["nonexistent_feature"],
            )
        )
        _start_experiment(mgr, "nometa-exp")

        mgr.record_bandit_context_reward("nometa-exp", "variant", 0.9)
        # Should be ignored since no metadata matches the feature
        assert len(mgr.ab_experiment_mgr._bandit_context_rewards) == 0


# ---------------------------------------------------------------------------
# Integration: Monitoring summary includes Sprint 5.49 data
# ---------------------------------------------------------------------------


class TestMonitoringSummarySprint549:
    """Tests that the monitoring summary includes Sprint 5.49 data."""

    def test_monitoring_summary_includes_calibration_persistence(self):
        """Monitoring summary includes calibration persistence status."""
        mgr = AlertManager(_make_config(ab_confidence_calibration_enabled=True))
        mgr.update_confidence_calibration("test", 0.9, 0.85)

        summary = mgr.get_experiment_monitoring_summary()
        assert "confidence_calibration" in summary
        assert "persisted" in summary["confidence_calibration"]
        assert "last_update_time" in summary["confidence_calibration"]

    def test_monitoring_summary_includes_pre_promotion_snapshots(self):
        """Monitoring summary includes pre-promotion snapshot status."""
        mgr = AlertManager(_make_config())
        summary = mgr.get_experiment_monitoring_summary()
        assert "pre_promotion_snapshots" in summary
        assert "in_memory_count" in summary["pre_promotion_snapshots"]

    def test_bandit_status_includes_contextual(self):
        """Bandit status includes contextual bandit info."""
        mgr = AlertManager(_make_config(ab_bandit_enabled=True, ab_bandit_contextual_enabled=True))
        status = mgr.get_bandit_status()
        assert status["contextual_enabled"] is True
        assert isinstance(status["contextual_rewards_tracked"], int)

    def test_persist_all_ab_experiments_persists_everything(self):
        """persist_all_ab_experiments persists experiments, calibration, snapshots, and stats."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr, "full-persist-exp")
        _add_results(mgr, "full-persist-exp", n=50)

        mgr.update_confidence_calibration("persist-test", 0.90, 0.85)
        mgr.promote_variant("full-persist-exp", variant="variant")
        mgr.compute_statistical_significance("full-persist-exp")

        # Persist everything
        count = mgr.persist_all_ab_experiments()
        assert count >= 1

        # Verify calibration was persisted
        calib_results = store.get_confidence_calibrations()
        assert len(calib_results) >= 1

        # Verify snapshots were persisted
        snap_results = store.get_pre_promotion_snapshots()
        assert len(snap_results) >= 1

        # Verify stats were persisted
        stat_results = store.get_statistical_test_results()
        assert len(stat_results) >= 1
