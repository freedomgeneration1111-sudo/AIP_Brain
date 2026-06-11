"""Sprint 5.50 tests: Observability, adaptability, and data hygiene.

Covers all 5 deliverables:
1. Bandit Decision Logging & Replay
2. Dashboard Historical Promotion Timeline
3. Adaptive Bandit Method Selection
4. Snapshot Garbage Collection
5. Calibration Drift Detection
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from aip.adapter.alert_history_store import AlertHistoryStore
from aip.adapter.alerting import AlertConfig, AlertManager

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
        # Sprint 5.50 defaults
        "ab_bandit_decision_logging_enabled": False,
        "ab_bandit_adaptive_method_enabled": False,
        "ab_snapshot_gc_enabled": False,
        "ab_snapshot_gc_max_age_hours": 72,
        "ab_snapshot_gc_interval_seconds": 0,
        "ab_calibration_drift_threshold": 0.20,
        "ab_calibration_drift_check_enabled": False,
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
# Deliverable 1: Bandit Decision Logging & Replay
# ---------------------------------------------------------------------------


class TestBanditDecisionLogging:
    """Tests for bandit decision logging and replay."""

    def test_schema_v12_creates_bandit_decision_log_table(self):
        """Schema v12 migration creates the bandit_decision_log table."""
        store = _make_store()
        with sqlite3.connect(store._db_path) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bandit_decision_log'")
            assert cursor.fetchone() is not None

    def test_record_bandit_decision(self):
        """Bandit decisions can be persisted and retrieved."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        decision = {
            "experiment_name": "log-exp",
            "method": "thompson",
            "allocation": {"control": 0.4, "variant": 0.6},
            "confidence": 0.2,
            "context_features": {"alert_type": "quality_degradation"},
            "sample_sizes": {"control": 100, "variant": 100},
            "timestamp": now_iso,
        }

        assert store.record_bandit_decision(decision) is True

        results = store.get_bandit_decisions(experiment_name="log-exp")
        assert len(results) == 1
        assert results[0]["experiment_name"] == "log-exp"
        assert results[0]["method"] == "thompson"
        assert results[0]["allocation"]["control"] == 0.4
        assert results[0]["confidence"] == 0.2

    def test_bandit_decision_filters(self):
        """Bandit decisions support filtering by method and date range."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        for method in ["thompson", "ucb", "epsilon_greedy"]:
            store.record_bandit_decision(
                {
                    "experiment_name": f"filter-{method}",
                    "method": method,
                    "allocation": {"control": 0.5, "variant": 0.5},
                    "timestamp": now_iso,
                }
            )

        # Filter by method
        ucb_results = store.get_bandit_decisions(method="ucb")
        assert len(ucb_results) == 1
        assert ucb_results[0]["method"] == "ucb"

    def test_bandit_decision_logging_in_get_bandit_allocation(self):
        """When decision logging is enabled, get_bandit_allocation logs decisions."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_method="thompson",
                ab_bandit_decision_logging_enabled=True,
            )
        )
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr, "decision-log-exp")
        _add_results(mgr, "decision-log-exp", n=100)

        # Call get_bandit_allocation which should log
        mgr.get_bandit_allocation("decision-log-exp")

        # Verify decision was logged
        decisions = store.get_bandit_decisions(experiment_name="decision-log-exp")
        assert len(decisions) >= 1
        assert decisions[0]["method"] == "thompson"

    def test_replay_bandit_decisions(self):
        """Replay returns decisions in chronological order."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_method="thompson",
                ab_bandit_decision_logging_enabled=True,
            )
        )
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr, "replay-exp")
        _add_results(mgr, "replay-exp", n=50)

        # Make multiple allocation calls
        for _ in range(3):
            mgr.get_bandit_allocation("replay-exp")

        # Replay should return in chronological order (oldest first)
        decisions = mgr.replay_bandit_decisions("replay-exp", limit=10)
        assert len(decisions) >= 3
        # Verify chronological ordering
        for i in range(1, len(decisions)):
            assert decisions[i]["timestamp"] >= decisions[i - 1]["timestamp"]

    def test_decision_logging_disabled_by_default(self):
        """When decision logging is disabled, decisions are not logged."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_decision_logging_enabled=False,
            )
        )
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr, "no-log-exp")
        _add_results(mgr, "no-log-exp", n=50)
        mgr.get_bandit_allocation("no-log-exp")

        decisions = store.get_bandit_decisions()
        assert len(decisions) == 0

    def test_decision_log_counter_incremented(self):
        """The total_bandit_decisions_logged counter is incremented on each log."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_decision_logging_enabled=True,
            )
        )
        store = _make_store()
        mgr.attach_history_store(store)

        _start_experiment(mgr, "counter-exp")
        _add_results(mgr, "counter-exp", n=50)

        initial = mgr.ab_experiment_mgr._total_bandit_decisions_logged
        mgr.get_bandit_allocation("counter-exp")
        assert mgr.ab_experiment_mgr._total_bandit_decisions_logged > initial


# ---------------------------------------------------------------------------
# Deliverable 2: Dashboard Historical Promotion Timeline
# ---------------------------------------------------------------------------


class TestHistoricalPromotionTimeline:
    """Tests for the unified event timeline view."""

    def test_event_timeline_returns_promotion_events(self):
        """Event timeline includes promotion events from ab_experiments."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Create a promoted experiment in the store
        with sqlite3.connect(store._db_path) as conn:
            conn.execute(
                """
                INSERT INTO ab_experiments (name, control_config, variant_config, status, started_at,
                    control_samples, variant_samples, control_accuracy, variant_accuracy,
                    promoted_variant, promotion_timestamp, auto_promoted, created_at)
                VALUES (?, '{}', '{}', 'promoted', ?, 50, 50, 0.85, 0.92, 'variant', ?, 1, ?)
            """,
                ("timeline-promo-exp", now_iso, now_iso, now_iso),
            )

        events = store.get_experiment_event_timeline(event_type="promotion")
        assert len(events) >= 1
        promo = [e for e in events if e["event_type"] == "promotion"]
        assert len(promo) >= 1
        assert promo[0]["experiment_name"] == "timeline-promo-exp"

    def test_event_timeline_returns_rollback_events(self):
        """Event timeline includes rollback events."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        with sqlite3.connect(store._db_path) as conn:
            conn.execute(
                """
                INSERT INTO ab_rollback_history (experiment_name, rolled_back_variant, rolled_back_at,
                    control_accuracy, variant_accuracy, auto, created_at)
                VALUES (?, 'variant', ?, 0.85, 0.70, 1, ?)
            """,
                ("timeline-rollback-exp", now_iso, now_iso),
            )

        events = store.get_experiment_event_timeline(event_type="rollback")
        assert len(events) >= 1
        rb = [e for e in events if e["event_type"] == "rollback"]
        assert len(rb) >= 1
        assert rb[0]["experiment_name"] == "timeline-rollback-exp"

    def test_event_timeline_returns_bandit_decision_events(self):
        """Event timeline includes bandit decision events."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        store.record_bandit_decision(
            {
                "experiment_name": "timeline-bandit-exp",
                "method": "thompson",
                "allocation": {"control": 0.35, "variant": 0.65},
                "confidence": 0.30,
                "timestamp": now_iso,
            }
        )

        events = store.get_experiment_event_timeline(event_type="bandit_decision")
        assert len(events) >= 1
        bd = [e for e in events if e["event_type"] == "bandit_decision"]
        assert len(bd) >= 1
        assert bd[0]["experiment_name"] == "timeline-bandit-exp"
        assert bd[0]["method"] == "thompson"

    def test_event_timeline_filter_by_experiment_name(self):
        """Event timeline supports filtering by experiment name."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        store.record_bandit_decision(
            {
                "experiment_name": "filter-exp-a",
                "method": "thompson",
                "allocation": {"control": 0.5, "variant": 0.5},
                "timestamp": now_iso,
            }
        )
        store.record_bandit_decision(
            {
                "experiment_name": "filter-exp-b",
                "method": "ucb",
                "allocation": {"control": 0.5, "variant": 0.5},
                "timestamp": now_iso,
            }
        )

        events = store.get_experiment_event_timeline(experiment_name="filter-exp-a")
        assert all(e["experiment_name"] == "filter-exp-a" for e in events)

    def test_event_timeline_all_types_combined(self):
        """Event timeline returns all event types when no filter is specified."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Add a bandit decision
        store.record_bandit_decision(
            {
                "experiment_name": "combined-exp",
                "method": "thompson",
                "allocation": {"control": 0.5, "variant": 0.5},
                "timestamp": now_iso,
            }
        )

        # Add a decay recovery
        with sqlite3.connect(store._db_path) as conn:
            conn.execute(
                """
                INSERT INTO decay_recovery_history (subject, decay_amount, current_confidence,
                    actions_taken, created_at)
                VALUES (?, 0.15, 0.80, '[]', ?)
            """,
                ("combined-exp", now_iso),
            )

        events = store.get_experiment_event_timeline()
        types = {e["event_type"] for e in events}
        assert "bandit_decision" in types
        assert "decay_recovery" in types

    def test_manager_get_experiment_event_timeline(self):
        """AlertManager.get_experiment_event_timeline delegates to the store."""
        mgr = AlertManager(_make_config())
        store = _make_store()
        mgr.attach_history_store(store)

        events = mgr.get_experiment_event_timeline()
        assert isinstance(events, list)


# ---------------------------------------------------------------------------
# Deliverable 3: Adaptive Bandit Method Selection
# ---------------------------------------------------------------------------


class TestAdaptiveBanditMethodSelection:
    """Tests for adaptive bandit method selection."""

    def test_adaptive_method_selects_ucb_for_small_samples(self):
        """Adaptive selection chooses UCB for experiments with <100 samples."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_adaptive_method_enabled=True,
                ab_bandit_method="thompson",
            )
        )
        _start_experiment(mgr, "small-sample-exp")
        _add_results(mgr, "small-sample-exp", n=30)

        method = mgr._select_adaptive_bandit_method("small-sample-exp", 15, 15, 0.80, 0.85)
        assert method == "ucb"

    def test_adaptive_method_selects_epsilon_greedy_for_low_variance(self):
        """Adaptive selection chooses epsilon-greedy when accuracy gap is small."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_adaptive_method_enabled=True,
            )
        )
        _start_experiment(mgr, "low-var-exp")
        _add_results(mgr, "low-var-exp", n=200, c_acc=0.85, v_acc=0.86)

        method = mgr._select_adaptive_bandit_method("low-var-exp", 100, 100, 0.85, 0.86)
        assert method == "epsilon_greedy"

    def test_adaptive_method_selects_thompson_for_high_variance(self):
        """Adaptive selection chooses Thompson Sampling for large samples with variance."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_adaptive_method_enabled=True,
            )
        )
        _start_experiment(mgr, "high-var-exp")

        method = mgr._select_adaptive_bandit_method("high-var-exp", 100, 100, 0.70, 0.90)
        assert method == "thompson"

    def test_adaptive_method_prefers_thompson_with_context(self):
        """When contextual bandits have data, adaptive selection prefers Thompson."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_adaptive_method_enabled=True,
                ab_bandit_contextual_enabled=True,
            )
        )
        _start_experiment(mgr, "ctx-adapt-exp", alert_type="quality")
        _add_results(mgr, "ctx-adapt-exp", n=100)

        # Record context rewards
        for _ in range(5):
            mgr.record_bandit_context_reward("ctx-adapt-exp", "variant", 0.90)
            mgr.record_bandit_context_reward("ctx-adapt-exp", "control", 0.80)

        method = mgr._select_adaptive_bandit_method("ctx-adapt-exp", 50, 50, 0.80, 0.90)
        assert method == "thompson"

    def test_adaptive_method_tracks_switches(self):
        """Adaptive method selection tracks when methods are switched."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_adaptive_method_enabled=True,
            )
        )
        _start_experiment(mgr, "switch-exp")

        # First call with small samples -> UCB
        mgr._select_adaptive_bandit_method("switch-exp", 10, 10, 0.80, 0.85)
        # Second call with large samples, high variance -> Thompson
        mgr._select_adaptive_bandit_method("switch-exp", 100, 100, 0.70, 0.90)

        assert mgr._total_adaptive_method_switches >= 1

    def test_get_adaptive_bandit_status(self):
        """Adaptive bandit status endpoint returns correct structure."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_adaptive_method_enabled=True,
            )
        )
        _start_experiment(mgr, "status-exp")
        mgr._select_adaptive_bandit_method("status-exp", 50, 50, 0.80, 0.90)

        status = mgr.get_adaptive_bandit_status()
        assert status["enabled"] is True
        assert "active_methods" in status
        assert "status-exp" in status["active_methods"]
        assert "total_switches" in status

    def test_adaptive_disabled_uses_configured_method(self):
        """When adaptive method is disabled, configured method is always used."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_adaptive_method_enabled=False,
                ab_bandit_method="ucb",
            )
        )
        _start_experiment(mgr, "disabled-adapt-exp")
        _add_results(mgr, "disabled-adapt-exp", n=30)

        # Even with small samples, should use the configured method
        alloc = mgr.get_bandit_allocation("disabled-adapt-exp")
        assert alloc is not None
        # The method used should be "ucb" as configured
        assert mgr.ab_experiment_mgr._adaptive_method_history.get("disabled-adapt-exp") is None


# ---------------------------------------------------------------------------
# Deliverable 4: Snapshot Garbage Collection
# ---------------------------------------------------------------------------


class TestSnapshotGarbageCollection:
    """Tests for automatic cleanup of stale pre-promotion snapshots."""

    def test_gc_removes_stale_snapshots_by_age(self):
        """Snapshot GC removes snapshots older than max_age_hours."""
        store = _make_store()

        # Create an old snapshot
        old_time = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
        with sqlite3.connect(store._db_path) as conn:
            conn.execute(
                """
                INSERT INTO pre_promotion_snapshots (experiment_name, snapshot_data, created_at, updated_at)
                VALUES (?, '{}', ?, ?)
            """,
                ("old-exp", old_time, old_time),
            )

        removed = store.cleanup_stale_pre_promotion_snapshots(
            active_experiment_names=set(),
            max_age_hours=72,
        )
        assert removed >= 1

        # Verify it was removed
        results = store.get_pre_promotion_snapshots()
        names = {r["experiment_name"] for r in results}
        assert "old-exp" not in names

    def test_gc_keeps_active_experiment_snapshots(self):
        """Snapshot GC keeps snapshots for active experiments."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        store.record_pre_promotion_snapshot("active-exp", {"test": True})

        removed = store.cleanup_stale_pre_promotion_snapshots(
            active_experiment_names={"active-exp"},
            max_age_hours=72,
        )
        assert removed == 0

        # Verify it was kept
        results = store.get_pre_promotion_snapshots()
        names = {r["experiment_name"] for r in results}
        assert "active-exp" in names

    def test_gc_removes_inactive_experiment_snapshots(self):
        """Snapshot GC removes snapshots for experiments not in the active set."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        store.record_pre_promotion_snapshot("inactive-exp", {"test": True})

        removed = store.cleanup_stale_pre_promotion_snapshots(
            active_experiment_names={"some-other-exp"},
            max_age_hours=72,
        )
        assert removed >= 1

    def test_manager_snapshot_gc_run(self):
        """AlertManager._run_snapshot_gc cleans in-memory stale snapshots."""
        mgr = AlertManager(
            _make_config(
                ab_snapshot_gc_enabled=True,
                ab_snapshot_gc_max_age_hours=72,
            )
        )
        store = _make_store()
        mgr.attach_history_store(store)

        # Create an experiment that's been stopped
        _start_experiment(mgr, "stopped-gc-exp")
        _add_results(mgr, "stopped-gc-exp", n=50)
        mgr.promote_variant("stopped-gc-exp", variant="variant")

        # Now stop it
        mgr.ab_experiment_mgr._ab_experiments["stopped-gc-exp"]["status"] = "stopped"

        # The snapshot should be in memory
        assert "stopped-gc-exp" in mgr.ab_experiment_mgr._pre_promotion_config_snapshots

        # Run GC
        removed = mgr._run_snapshot_gc()
        assert removed >= 1
        assert "stopped-gc-exp" not in mgr.ab_experiment_mgr._pre_promotion_config_snapshots

    def test_snapshot_gc_status(self):
        """Snapshot GC status endpoint returns correct structure."""
        mgr = AlertManager(
            _make_config(
                ab_snapshot_gc_enabled=True,
                ab_snapshot_gc_max_age_hours=48,
            )
        )

        status = mgr.get_snapshot_gc_status()
        assert status["enabled"] is True
        assert status["max_age_hours"] == 48
        assert status["total_runs"] == 0
        assert status["total_cleaned"] == 0

    def test_snapshot_gc_configurable_retention(self):
        """Snapshot GC retention policy is configurable."""
        config = _make_config(ab_snapshot_gc_max_age_hours=24)
        assert config.ab_snapshot_gc_max_age_hours == 24


# ---------------------------------------------------------------------------
# Deliverable 5: Calibration Drift Detection
# ---------------------------------------------------------------------------


class TestCalibrationDriftDetection:
    """Tests for calibration drift detection and alerting."""

    def test_detect_drift_when_factor_exceeds_threshold(self):
        """Drift is detected when calibration factor deviates >20% from 1.0."""
        mgr = AlertManager(
            _make_config(
                ab_calibration_drift_check_enabled=True,
                ab_calibration_drift_threshold=0.20,
            )
        )

        # Create a calibration factor with >20% deviation
        mgr.ab_experiment_mgr._confidence_calibration_map["drifted_subject"] = 1.30  # 30% deviation

        drifted = mgr.check_calibration_drift()
        assert len(drifted) >= 1
        assert drifted[0]["subject"] == "drifted_subject"
        assert drifted[0]["deviation"] == pytest.approx(0.30, abs=0.01)
        assert drifted[0]["direction"] == "over_confident"

    def test_no_drift_when_factor_within_threshold(self):
        """No drift alert when calibration factor is within threshold."""
        mgr = AlertManager(
            _make_config(
                ab_calibration_drift_check_enabled=True,
                ab_calibration_drift_threshold=0.20,
            )
        )

        # Calibration factor within 20% of 1.0
        mgr.ab_experiment_mgr._confidence_calibration_map["ok_subject"] = 1.10  # 10% deviation

        drifted = mgr.check_calibration_drift()
        assert len(drifted) == 0

    def test_drift_detection_under_confident_direction(self):
        """Drift detection identifies under-confident direction correctly."""
        mgr = AlertManager(
            _make_config(
                ab_calibration_drift_check_enabled=True,
                ab_calibration_drift_threshold=0.20,
            )
        )

        mgr.ab_experiment_mgr._confidence_calibration_map["under_subject"] = 0.70  # 30% deviation

        drifted = mgr.check_calibration_drift()
        assert len(drifted) >= 1
        assert drifted[0]["direction"] == "under_confident"

    def test_drift_detection_disabled(self):
        """Drift detection returns empty when disabled."""
        mgr = AlertManager(
            _make_config(
                ab_calibration_drift_check_enabled=False,
            )
        )

        mgr.ab_experiment_mgr._confidence_calibration_map["any_subject"] = 2.0  # 100% deviation

        drifted = mgr.check_calibration_drift()
        assert len(drifted) == 0

    def test_drift_alert_sends_notification(self):
        """Drift detection sends an alert via the notification system."""
        mgr = AlertManager(
            _make_config(
                enabled=True,
                ab_calibration_drift_check_enabled=True,
                ab_calibration_drift_threshold=0.20,
                alert_on_quality_degradation=True,
            )
        )

        mgr.ab_experiment_mgr._confidence_calibration_map["alert_subject"] = 1.50

        # Mock send_alert to track calls
        original_send = mgr.send_alert
        sent_alerts = []

        def mock_send(alert):
            sent_alerts.append(alert)
            return "test-correlation-id"

        mgr.send_alert = mock_send

        mgr.check_calibration_drift()
        assert len(sent_alerts) >= 1
        assert sent_alerts[0].alert_type == "calibration_drift"
        assert "alert_subject" in sent_alerts[0].subject

    def test_drift_status_includes_recent_alerts(self):
        """Calibration drift status includes recent alerts and current drifted factors."""
        mgr = AlertManager(
            _make_config(
                ab_calibration_drift_check_enabled=True,
                ab_calibration_drift_threshold=0.20,
            )
        )

        mgr.ab_experiment_mgr._confidence_calibration_map["status_subject"] = 1.40
        mgr.check_calibration_drift()

        status = mgr.get_calibration_drift_status()
        assert status["enabled"] is True
        assert status["total_drift_alerts"] >= 1
        assert len(status["recent_alerts"]) >= 1
        assert "status_subject" in status["current_factors"]

    def test_drift_alerts_capped_at_50(self):
        """Calibration drift alerts are capped at 50 in memory."""
        mgr = AlertManager(
            _make_config(
                ab_calibration_drift_check_enabled=True,
                ab_calibration_drift_threshold=0.20,
            )
        )

        for i in range(60):
            mgr.ab_experiment_mgr._confidence_calibration_map[f"subject_{i}"] = 1.50

        mgr.check_calibration_drift()
        assert len(mgr.ab_experiment_mgr._calibration_drift_alerts) <= 50

    def test_configurable_drift_threshold(self):
        """Calibration drift threshold is configurable."""
        config = _make_config(ab_calibration_drift_threshold=0.10)
        assert config.ab_calibration_drift_threshold == 0.10

    def test_drift_metrics_in_health_endpoint(self):
        """Calibration drift metrics are visible in the health endpoint data."""
        mgr = AlertManager(
            _make_config(
                ab_calibration_drift_check_enabled=True,
                ab_calibration_drift_threshold=0.20,
            )
        )
        mgr.ab_experiment_mgr._confidence_calibration_map["health_subject"] = 1.30
        mgr.check_calibration_drift()

        # Check via monitoring summary which feeds into health
        summary = mgr.get_experiment_monitoring_summary()
        assert "calibration_drift" in summary
        assert summary["calibration_drift"]["enabled"] is True
        assert summary["calibration_drift"]["total_drift_alerts"] >= 1


# ---------------------------------------------------------------------------
# Integration: Bandit status includes Sprint 5.50 data
# ---------------------------------------------------------------------------


class TestBanditStatusSprint550:
    """Tests that bandit status includes Sprint 5.50 data."""

    def test_bandit_status_includes_decision_logging(self):
        """Bandit status includes decision logging information."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_decision_logging_enabled=True,
            )
        )

        status = mgr.get_bandit_status()
        assert status["decision_logging_enabled"] is True
        assert "total_decisions_logged" in status

    def test_bandit_status_includes_adaptive_method(self):
        """Bandit status includes adaptive method selection information."""
        mgr = AlertManager(
            _make_config(
                ab_bandit_enabled=True,
                ab_bandit_adaptive_method_enabled=True,
            )
        )

        status = mgr.get_bandit_status()
        assert "adaptive_method" in status
        assert status["adaptive_method"]["enabled"] is True

    def test_monitoring_summary_includes_snapshot_gc(self):
        """Monitoring summary includes snapshot GC status."""
        mgr = AlertManager(_make_config())
        summary = mgr.get_experiment_monitoring_summary()
        assert "snapshot_gc" in summary
        assert "enabled" in summary["snapshot_gc"]

    def test_monitoring_summary_includes_calibration_drift(self):
        """Monitoring summary includes calibration drift status."""
        mgr = AlertManager(_make_config())
        summary = mgr.get_experiment_monitoring_summary()
        assert "calibration_drift" in summary
        assert "enabled" in summary["calibration_drift"]


# ---------------------------------------------------------------------------
# Integration: Store-level methods work end-to-end
# ---------------------------------------------------------------------------


class TestStoreSprint550Integration:
    """Integration tests for store-level Sprint 5.50 methods."""

    def test_bandit_decision_with_context_features(self):
        """Bandit decision log preserves context features."""
        store = _make_store()

        decision = {
            "experiment_name": "ctx-log-exp",
            "method": "thompson",
            "allocation": {"control": 0.3, "variant": 0.7},
            "confidence": 0.4,
            "context_features": {"alert_type": "quality", "subject": "vigil"},
            "sample_sizes": {"control": 100, "variant": 100},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        store.record_bandit_decision(decision)
        results = store.get_bandit_decisions(experiment_name="ctx-log-exp")
        assert len(results) == 1
        assert results[0]["context_features"]["alert_type"] == "quality"
        assert results[0]["sample_sizes"]["control"] == 100

    def test_timeline_events_sorted_by_timestamp(self):
        """Timeline events are sorted by timestamp descending."""
        store = _make_store()

        # Add events at different times
        for i in range(5):
            ts = (datetime.now(timezone.utc) - timedelta(hours=5 - i)).isoformat()
            store.record_bandit_decision(
                {
                    "experiment_name": f"sort-exp-{i}",
                    "method": "thompson",
                    "allocation": {"control": 0.5, "variant": 0.5},
                    "timestamp": ts,
                }
            )

        events = store.get_experiment_event_timeline(limit=10)
        # Most recent should be first
        for i in range(1, len(events)):
            assert events[i]["timestamp"] <= events[i - 1]["timestamp"]

    def test_snapshot_gc_with_multiple_experiments(self):
        """Snapshot GC correctly handles multiple experiments with mixed status."""
        store = _make_store()
        now_iso = datetime.now(timezone.utc).isoformat()
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()

        # Active experiment (recent)
        store.record_pre_promotion_snapshot("active-recent", {"test": "recent"})
        # Inactive experiment (old)
        with sqlite3.connect(store._db_path) as conn:
            conn.execute(
                """
                INSERT INTO pre_promotion_snapshots (experiment_name, snapshot_data, created_at, updated_at)
                VALUES (?, '{}', ?, ?)
            """,
                ("inactive-old", old_iso, old_iso),
            )

        removed = store.cleanup_stale_pre_promotion_snapshots(
            active_experiment_names={"active-recent"},
            max_age_hours=72,
        )
        assert removed >= 1

        # Active should remain
        results = store.get_pre_promotion_snapshots()
        names = {r["experiment_name"] for r in results}
        assert "active-recent" in names
        assert "inactive-old" not in names
