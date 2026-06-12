"""Sprint 5.45 / 5.46 — A/B Experiment Lifecycle, Cleanup, Rollback & Recovery.

Comprehensive tests covering all Sprint 5.45 and 5.46 deliverables:

- Sprint 5.45: A/B Experiment Lifecycle (start, stop, record, promote, auto-promotion)
- Sprint 5.46 Del 1: Experiment Result Expiry & Cleanup
- Sprint 5.46 Del 2: Promotion Rollback Automation
- Sprint 5.46 Del 3: Decay Recovery Orchestrator
- Sprint 5.46 Del 4: Dashboard Experiment Monitoring Panel
- Sprint 5.46 Del 5: Graceful Shutdown Persistence

Deterministic, zero-token, no network, no LLM.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from aip.adapter.alert_history_store import AlertHistoryStore, SyncAlertHistoryBridge
from aip.adapter.alerting import AlertConfig, AlertManager

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
        webhook_url="",  # No real transport needed
        email_to="",
        min_alert_interval_seconds=0,  # Disable rate-limiting for tests
        ab_experiment_enabled=True,
        ab_auto_promote_interval_seconds=0,  # Disabled by default (manual checks)
        ab_auto_promote_confidence_threshold=0.95,
        ab_auto_promote_min_samples=50,
        ab_experiment_ttl_hours=168,
        ab_stopped_experiment_retention_hours=72,
        ab_cleanup_interval_seconds=0,  # Disabled by default (manual checks)
        ab_rollback_enabled=True,
        ab_rollback_observation_window_seconds=1800,
        ab_rollback_accuracy_drop_threshold=0.05,
        decay_recovery_enabled=True,
        decay_recovery_threshold=0.15,
        decay_recovery_actions=["rerun_calibration", "restart_experiment"],
    )
    defaults.update(overrides)
    return AlertConfig(**defaults)


def _make_store(tmp_path, db_name: str = "test_history.db") -> SyncAlertHistoryBridge:
    """Create and initialize a fresh AlertHistoryStore via SyncAlertHistoryBridge."""
    db_path = str(tmp_path / db_name)
    store = AlertHistoryStore(db_path)
    bridge = SyncAlertHistoryBridge(store)
    bridge.initialize()
    return bridge


def _make_manager(config: AlertConfig | None = None, store: SyncAlertHistoryBridge | None = None) -> AlertManager:
    """Create an AlertManager with optional config and store."""
    cfg = config or _make_config()
    mgr = AlertManager(cfg)
    if store is not None:
        mgr.attach_history_store(store)
    return mgr


# ========================================================================
# Class 1: TestABExperimentLifecycle  (Sprint 5.45)
# ========================================================================


class TestABExperimentLifecycle:
    """Sprint 5.45: Core A/B experiment lifecycle operations."""

    def test_start_experiment(self):
        mgr = _make_manager()
        exp = mgr.start_ab_experiment(
            name="exp_alpha",
            control_config={"model": "gpt-4"},
            variant_config={"model": "gpt-4o"},
        )
        assert exp["name"] == "exp_alpha"
        assert exp["status"] == "running"
        assert exp["control_config"] == {"model": "gpt-4"}
        assert exp["variant_config"] == {"model": "gpt-4o"}
        assert exp["started_at"] != ""

        # Verify it appears in get_ab_experiments
        all_exps = mgr.get_ab_experiments()
        names = [e["name"] for e in all_exps]
        assert "exp_alpha" in names

    def test_start_duplicate_experiment(self):
        mgr = _make_manager()
        first = mgr.start_ab_experiment(
            name="dup_exp",
            control_config={"model": "a"},
            variant_config={"model": "b"},
        )
        assert first["status"] == "running"

        # Starting again with same name while running returns existing
        second = mgr.start_ab_experiment(
            name="dup_exp",
            control_config={"model": "a"},
            variant_config={"model": "b"},
        )
        assert second is first  # Same object returned

    def test_stop_experiment(self):
        mgr = _make_manager()
        mgr.start_ab_experiment("stop_exp", {"model": "a"}, {"model": "b"})
        result = mgr.stop_ab_experiment("stop_exp", result="inconclusive")
        assert result is not None
        assert result["status"] == "stopped"
        assert result["result"] == "inconclusive"
        assert result["stopped_at"] != ""

    def test_record_result_control(self):
        mgr = _make_manager()
        mgr.start_ab_experiment("rec_ctrl", {"model": "a"}, {"model": "b"})

        exp = mgr.record_ab_result("rec_ctrl", "control", accuracy=0.8, samples=10)
        assert exp is not None
        assert exp["control_samples"] == 10
        assert exp["control_accuracy"] == pytest.approx(0.8)

        # Second batch — running average
        exp = mgr.record_ab_result("rec_ctrl", "control", accuracy=0.9, samples=10)
        assert exp["control_samples"] == 20
        # (0.8*10 + 0.9*10) / 20 = 0.85
        assert exp["control_accuracy"] == pytest.approx(0.85)

    def test_record_result_variant(self):
        mgr = _make_manager()
        mgr.start_ab_experiment("rec_var", {"model": "a"}, {"model": "b"})

        exp = mgr.record_ab_result("rec_var", "variant", accuracy=0.92, samples=25)
        assert exp is not None
        assert exp["variant_samples"] == 25
        assert exp["variant_accuracy"] == pytest.approx(0.92)

    def test_promote_variant(self):
        mgr = _make_manager()
        mgr.start_ab_experiment("promo_exp", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("promo_exp", "control", accuracy=0.75, samples=50)
        mgr.record_ab_result("promo_exp", "variant", accuracy=0.90, samples=50)

        exp = mgr.promote_variant("promo_exp", variant="variant")
        assert exp is not None
        assert exp["status"] == "promoted"
        assert exp["promoted_variant"] == "variant"
        assert exp["promotion_timestamp"] != ""

    def test_get_experiment(self):
        mgr = _make_manager()
        mgr.start_ab_experiment("get_exp", {"model": "a"}, {"model": "b"})

        exp = mgr.get_ab_experiment("get_exp")
        assert exp is not None
        assert exp["name"] == "get_exp"

        assert mgr.get_ab_experiment("nonexistent") is None

    def test_get_experiments_by_status(self):
        mgr = _make_manager()
        mgr.start_ab_experiment("running1", {"model": "a"}, {"model": "b"})
        mgr.start_ab_experiment("running2", {"model": "c"}, {"model": "d"})
        mgr.start_ab_experiment("to_stop", {"model": "e"}, {"model": "f"})
        mgr.stop_ab_experiment("to_stop")

        running = mgr.get_ab_experiments(status="running")
        assert len(running) == 2
        stopped = mgr.get_ab_experiments(status="stopped")
        assert len(stopped) == 1
        assert stopped[0]["name"] == "to_stop"


# ========================================================================
# Class 2: TestExperimentExpiryCleanup  (Sprint 5.46 Del 1)
# ========================================================================


class TestExperimentExpiryCleanup:
    """Sprint 5.46 Del 1: Experiment Result Expiry & Cleanup."""

    def test_cleanup_ttl_expired(self):
        cfg = _make_config(ab_experiment_ttl_hours=1)
        mgr = _make_manager(config=cfg)
        exp = mgr.start_ab_experiment("ttl_exp", {"model": "a"}, {"model": "b"})

        # Manually set started_at to 2 hours ago (exceeds 1-hour TTL)
        exp["started_at"] = _old_iso(hours_ago=2)

        result = mgr.cleanup_expired_experiments()
        assert result["expired_stopped"] == 1
        assert result["total"] >= 1

        # Verify experiment was stopped
        updated = mgr.get_ab_experiment("ttl_exp")
        assert updated is not None
        assert updated["status"] == "stopped"
        assert updated["result"] == "ttl_expired"

    def test_cleanup_prune_stopped(self):
        cfg = _make_config(
            ab_experiment_ttl_hours=0,  # Disable TTL check
            ab_stopped_experiment_retention_hours=1,
        )
        mgr = _make_manager(config=cfg)
        exp = mgr.start_ab_experiment("prune_exp", {"model": "a"}, {"model": "b"})
        mgr.stop_ab_experiment("prune_exp")

        # Manually set stopped_at to 2 hours ago (exceeds 1-hour retention)
        exp["stopped_at"] = _old_iso(hours_ago=2)

        result = mgr.cleanup_expired_experiments()
        assert result["pruned"] == 1

        # Verify experiment was pruned from in-memory dict
        assert mgr.get_ab_experiment("prune_exp") is None

    def test_cleanup_no_action_for_recent(self):
        cfg = _make_config(
            ab_experiment_ttl_hours=168,
            ab_stopped_experiment_retention_hours=72,
        )
        mgr = _make_manager(config=cfg)

        # Recent running experiment — should not be TTL-expired
        mgr.start_ab_experiment("recent_running", {"model": "a"}, {"model": "b"})

        # Recent stopped experiment — should not be pruned
        mgr.start_ab_experiment("recent_stopped", {"model": "c"}, {"model": "d"})
        mgr.stop_ab_experiment("recent_stopped")

        result = mgr.cleanup_expired_experiments()
        assert result["expired_stopped"] == 0
        assert result["pruned"] == 0
        assert result["total"] == 0

        # Both experiments still exist
        assert mgr.get_ab_experiment("recent_running") is not None
        assert mgr.get_ab_experiment("recent_stopped") is not None

    def test_cleanup_status(self):
        cfg = _make_config(ab_cleanup_interval_seconds=60)
        mgr = _make_manager(config=cfg)

        status = mgr.get_ab_cleanup_status()
        assert "running" in status
        assert "interval_seconds" in status
        assert "ttl_hours" in status
        assert "retention_hours" in status
        assert "total_cleanups" in status
        assert "last_cleanup_run" in status
        assert "running_experiments" in status
        assert "stopped_experiments" in status
        assert status["interval_seconds"] == 60
        assert status["ttl_hours"] == 168

    def test_cleanup_with_store(self, tmp_path):
        """Cleanup should also prune experiments from the persistent store."""
        store = _make_store(tmp_path)
        cfg = _make_config(
            ab_experiment_ttl_hours=0,
            ab_stopped_experiment_retention_hours=1,
        )
        mgr = _make_manager(config=cfg, store=store)

        exp = mgr.start_ab_experiment("store_prune", {"model": "a"}, {"model": "b"})
        mgr.stop_ab_experiment("store_prune")
        exp["stopped_at"] = _old_iso(hours_ago=2)

        # Verify it's in the store before cleanup
        stored = store.get_ab_experiments()
        assert any(e["name"] == "store_prune" for e in stored)

        result = mgr.cleanup_expired_experiments()
        assert result["pruned"] == 1

        # Verify it's gone from the store after cleanup
        stored = store.get_ab_experiments()
        assert not any(e["name"] == "store_prune" for e in stored)


# ========================================================================
# Class 3: TestPromotionRollback  (Sprint 5.46 Del 2)
# ========================================================================


class TestPromotionRollback:
    """Sprint 5.46 Del 2: Promotion Rollback Automation."""

    def test_check_rollback_no_degradation(self):
        mgr = _make_manager()
        mgr.start_ab_experiment("no_degrade", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("no_degrade", "control", accuracy=0.80, samples=50)
        mgr.record_ab_result("no_degrade", "variant", accuracy=0.90, samples=50)
        mgr.promote_variant("no_degrade", variant="variant")

        rollbacks = mgr.check_promotion_rollback()
        assert rollbacks == []

        exp = mgr.get_ab_experiment("no_degrade")
        assert exp["status"] == "promoted"

    def test_check_rollback_with_degradation(self):
        cfg = _make_config(ab_rollback_accuracy_drop_threshold=0.05)
        mgr = _make_manager(config=cfg)

        mgr.start_ab_experiment("degrade_exp", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("degrade_exp", "control", accuracy=0.85, samples=50)
        mgr.record_ab_result("degrade_exp", "variant", accuracy=0.92, samples=50)
        mgr.promote_variant("degrade_exp", variant="variant")

        # Simulate degradation: variant accuracy drops well below control
        exp = mgr.get_ab_experiment("degrade_exp")
        exp["variant_accuracy"] = 0.70  # Now control (0.85) - variant (0.70) = 0.15 > 0.05 threshold

        rollbacks = mgr.check_promotion_rollback()
        assert len(rollbacks) == 1
        assert rollbacks[0]["experiment_name"] == "degrade_exp"
        assert rollbacks[0]["rolled_back_variant"] == "variant"
        assert rollbacks[0]["auto"] is True

        # Verify experiment status changed
        updated = mgr.get_ab_experiment("degrade_exp")
        assert updated["status"] == "rolled_back"

    def test_rollback_disabled(self):
        cfg = _make_config(ab_rollback_enabled=False)
        mgr = _make_manager(config=cfg)

        mgr.start_ab_experiment("disabled_rb", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("disabled_rb", "control", accuracy=0.85, samples=50)
        mgr.record_ab_result("disabled_rb", "variant", accuracy=0.92, samples=50)
        mgr.promote_variant("disabled_rb", variant="variant")

        # Simulate severe degradation
        exp = mgr.get_ab_experiment("disabled_rb")
        exp["variant_accuracy"] = 0.50

        rollbacks = mgr.check_promotion_rollback()
        assert rollbacks == []

        # Experiment should still be promoted
        assert mgr.get_ab_experiment("disabled_rb")["status"] == "promoted"

    def test_rollback_status(self):
        mgr = _make_manager()
        status = mgr.get_promotion_rollback_status()

        assert "enabled" in status
        assert "observation_window_seconds" in status
        assert "accuracy_drop_threshold" in status
        assert "total_rollbacks" in status
        assert "rollback_history" in status
        assert "promoted_experiments" in status
        assert status["enabled"] is True
        assert isinstance(status["rollback_history"], list)

    def test_rollback_notification(self):
        """Rollback should send an alert notification."""
        mgr = _make_manager()
        mgr.start_ab_experiment("notify_rb", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("notify_rb", "control", accuracy=0.85, samples=50)
        mgr.record_ab_result("notify_rb", "variant", accuracy=0.92, samples=50)
        mgr.promote_variant("notify_rb", variant="variant")

        # Simulate degradation
        exp = mgr.get_ab_experiment("notify_rb")
        exp["variant_accuracy"] = 0.60

        # Track alert history before rollback
        history_before = len(mgr.lifecycle_mgr._alert_history)

        mgr.check_promotion_rollback()

        # Verify an alert was sent (alert history should have grown)
        history_after = len(mgr.lifecycle_mgr._alert_history)
        assert history_after > history_before

        # Find the rollback alert
        rollback_alerts = [
            a for a in mgr.lifecycle_mgr._alert_history if a.get("alert_type") == "ab_experiment_rollback"
        ]
        assert len(rollback_alerts) >= 1
        assert rollback_alerts[-1]["subject"] == "experiment:notify_rb"


# ========================================================================
# Class 4: TestDecayRecovery  (Sprint 5.46 Del 3)
# ========================================================================


class TestDecayRecovery:
    """Sprint 5.46 Del 3: Decay Recovery Orchestrator."""

    def test_decay_recovery_disabled(self):
        cfg = _make_config(decay_recovery_enabled=False)
        mgr = _make_manager(config=cfg)

        # Notify a decay event (should still record it)
        mgr.notify_decay_event("test_subject", 0.3, 0.5)

        recoveries = mgr.run_decay_recovery_orchestrator()
        assert recoveries == []

    def test_decay_recovery_triggered(self):
        mgr = _make_manager()
        mgr.start_ab_experiment("vigil_test", {"model": "a"}, {"model": "b"})

        # Notify a decay event that exceeds the threshold (0.15)
        mgr.notify_decay_event("vigil", 0.25, 0.55)

        recoveries = mgr.run_decay_recovery_orchestrator()
        assert len(recoveries) >= 1
        assert recoveries[0]["subject"] == "vigil"
        assert recoveries[0]["decay_amount"] == pytest.approx(0.25)

    def test_decay_recovery_rerun_calibration(self):
        cfg = _make_config(decay_recovery_actions=["rerun_calibration"])
        mgr = _make_manager(config=cfg)

        # Experiment whose name contains the decay subject
        mgr.start_ab_experiment("vigil_calibration", {"model": "a"}, {"model": "b"})

        mgr.notify_decay_event("vigil", 0.20, 0.60)
        recoveries = mgr.run_decay_recovery_orchestrator()

        assert len(recoveries) >= 1
        actions = recoveries[0]["actions_taken"]
        assert any(a["action"] == "rerun_calibration" for a in actions)

        # Verify the experiment was marked for re-calibration
        exp = mgr.get_ab_experiment("vigil_calibration")
        assert exp.get("needs_recalibration") is True

    def test_decay_recovery_restart_experiment(self):
        cfg = _make_config(decay_recovery_actions=["restart_experiment"])
        mgr = _make_manager(config=cfg)

        # Create a stopped experiment whose name contains the decay subject
        mgr.start_ab_experiment("vigil_restart", {"model": "a"}, {"model": "b"})
        mgr.stop_ab_experiment("vigil_restart")

        # Verify it's stopped
        exp = mgr.get_ab_experiment("vigil_restart")
        assert exp["status"] == "stopped"

        mgr.notify_decay_event("vigil", 0.20, 0.60)
        recoveries = mgr.run_decay_recovery_orchestrator()

        assert len(recoveries) >= 1
        actions = recoveries[0]["actions_taken"]
        assert any(a["action"] == "restart_experiment" for a in actions)

        # Verify the experiment was restarted
        exp = mgr.get_ab_experiment("vigil_restart")
        assert exp["status"] == "running"
        assert exp["stopped_at"] is None

    def test_decay_recovery_status(self):
        mgr = _make_manager()
        status = mgr.get_decay_recovery_status()

        assert "enabled" in status
        assert "threshold" in status
        assert "actions" in status
        assert "total_recoveries" in status
        assert "recovery_history" in status
        assert "recent_decay_events" in status
        assert status["enabled"] is True
        assert status["threshold"] == pytest.approx(0.15)
        assert isinstance(status["recovery_history"], list)


# ========================================================================
# Class 5: TestGracefulShutdown  (Sprint 5.46 Del 5)
# ========================================================================


class TestGracefulShutdown:
    """Sprint 5.46 Del 5: Graceful Shutdown Persistence."""

    def test_persist_all_experiments(self, tmp_path):
        store = _make_store(tmp_path)
        mgr = _make_manager(store=store)

        mgr.start_ab_experiment("persist1", {"model": "a"}, {"model": "b"})
        mgr.start_ab_experiment("persist2", {"model": "c"}, {"model": "d"})

        count = mgr.persist_all_ab_experiments()
        assert count == 2

    def test_persist_stops_checkers(self):
        cfg = _make_config(
            ab_auto_promote_interval_seconds=300,
            ab_cleanup_interval_seconds=300,
        )
        mgr = _make_manager(config=cfg)

        # Start the checkers
        mgr.start_ab_promotion_checker()
        mgr.start_ab_cleanup_checker()
        assert mgr.ab_experiment_mgr._ab_promotion_checker_running is True
        assert mgr.ab_experiment_mgr._ab_cleanup_checker_running is True

        # persist_all_ab_experiments should stop them
        mgr.start_ab_experiment("shutdown_exp", {"model": "a"}, {"model": "b"})
        mgr.persist_all_ab_experiments()

        # Allow a brief moment for thread loop to notice the flag
        time.sleep(0.1)
        assert mgr.ab_experiment_mgr._ab_promotion_checker_running is False
        assert mgr.ab_experiment_mgr._ab_cleanup_checker_running is False

    def test_persist_with_store(self, tmp_path):
        store = _make_store(tmp_path)
        mgr = _make_manager(store=store)

        mgr.start_ab_experiment("store_persist", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("store_persist", "control", accuracy=0.80, samples=30)
        mgr.record_ab_result("store_persist", "variant", accuracy=0.88, samples=30)

        mgr.persist_all_ab_experiments()

        # Verify data was written to the store
        stored = store.get_ab_experiments()
        assert any(e["name"] == "store_persist" for e in stored)

        stored_exp = [e for e in stored if e["name"] == "store_persist"][0]
        assert stored_exp["control_samples"] == 30
        assert stored_exp["variant_samples"] == 30
        assert stored_exp["control_accuracy"] == pytest.approx(0.80)
        assert stored_exp["variant_accuracy"] == pytest.approx(0.88)


# ========================================================================
# Class 6: TestExperimentMonitoring  (Sprint 5.46 Del 4)
# ========================================================================


class TestExperimentMonitoring:
    """Sprint 5.46 Del 4: Dashboard Experiment Monitoring Panel."""

    def test_monitoring_summary_empty(self):
        mgr = _make_manager()
        summary = mgr.get_experiment_monitoring_summary()

        assert summary["total_experiments"] == 0
        assert summary["running_count"] == 0
        assert summary["stopped_count"] == 0
        assert summary["promoted_count"] == 0
        assert summary["rolled_back_count"] == 0
        assert summary["running_experiments"] == []
        assert summary["promotion_history"] == []

    def test_monitoring_summary_with_data(self):
        mgr = _make_manager()

        # Running experiment
        mgr.start_ab_experiment("mon_running", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("mon_running", "control", accuracy=0.80, samples=20)

        # Stopped experiment
        mgr.start_ab_experiment("mon_stopped", {"model": "c"}, {"model": "d"})
        mgr.stop_ab_experiment("mon_stopped")

        # Promoted experiment
        mgr.start_ab_experiment("mon_promoted", {"model": "e"}, {"model": "f"})
        mgr.record_ab_result("mon_promoted", "control", accuracy=0.80, samples=60)
        mgr.record_ab_result("mon_promoted", "variant", accuracy=0.96, samples=60)
        mgr.promote_variant("mon_promoted", variant="variant")

        summary = mgr.get_experiment_monitoring_summary()

        assert summary["total_experiments"] == 3
        assert summary["running_count"] == 1
        assert summary["stopped_count"] == 1
        assert summary["promoted_count"] == 1
        assert len(summary["running_experiments"]) == 1
        assert summary["running_experiments"][0]["name"] == "mon_running"
        assert len(summary["promotion_history"]) >= 1

    def test_monitoring_summary_includes_checker_status(self):
        mgr = _make_manager()
        summary = mgr.get_experiment_monitoring_summary()

        # The summary should include auto-promotion checker status
        assert "auto_promotion_checker" in summary
        checker = summary["auto_promotion_checker"]
        assert "running" in checker
        assert "confidence_threshold" in checker
        assert "min_samples" in checker
        assert "total_promotions" in checker

        # Also include cleanup and rollback sub-statuses
        assert "cleanup_status" in summary
        assert "rollback_status" in summary
        assert "decay_recovery_status" in summary


# ========================================================================
# Class 7: TestAutoPromotionChecker  (Sprint 5.45)
# ========================================================================


class TestAutoPromotionChecker:
    """Sprint 5.45: Auto-promotion checker logic."""

    def test_auto_promotion_not_eligible(self):
        cfg = _make_config(
            ab_auto_promote_min_samples=50,
            ab_auto_promote_confidence_threshold=0.95,
        )
        mgr = _make_manager(config=cfg)

        mgr.start_ab_experiment("no_auto", {"model": "a"}, {"model": "b"})
        # Only 10 samples each — not enough
        mgr.record_ab_result("no_auto", "control", accuracy=0.80, samples=10)
        mgr.record_ab_result("no_auto", "variant", accuracy=0.96, samples=10)

        mgr._check_auto_promotion()

        exp = mgr.get_ab_experiment("no_auto")
        assert exp["status"] == "running"  # Not promoted

    def test_auto_promotion_eligible(self):
        cfg = _make_config(
            ab_auto_promote_min_samples=50,
            ab_auto_promote_confidence_threshold=0.95,
        )
        mgr = _make_manager(config=cfg)

        mgr.start_ab_experiment("auto_promo", {"model": "a"}, {"model": "b"})
        # Sufficient samples and variant accuracy is high enough
        # The condition is: v_acc >= threshold AND v_acc - c_acc >= (1.0 - threshold)
        # With threshold=0.95: v_acc >= 0.95 AND v_acc - c_acc >= 0.05
        # Let control=0.85, variant=0.96: 0.96 >= 0.95 ✓ and 0.96 - 0.85 = 0.11 >= 0.05 ✓
        mgr.record_ab_result("auto_promo", "control", accuracy=0.85, samples=50)
        mgr.record_ab_result("auto_promo", "variant", accuracy=0.96, samples=50)

        mgr._check_auto_promotion()

        exp = mgr.get_ab_experiment("auto_promo")
        assert exp["status"] == "promoted"
        assert exp["promoted_variant"] == "variant"
        assert exp.get("auto_promoted") is True

    def test_promotion_checker_status(self):
        mgr = _make_manager()
        status = mgr.get_ab_promotion_checker_status()

        assert "running" in status
        assert "interval_seconds" in status
        assert "confidence_threshold" in status
        assert "min_samples" in status
        assert "total_promotions" in status
        assert "total_auto_promotions" in status
        assert "running_experiments" in status
        assert isinstance(status["running"], bool)
        assert isinstance(status["total_promotions"], int)


# ========================================================================
# Class 8: TestStorePersistence  (Sprint 5.45 + 5.46)
# ========================================================================


class TestStorePersistence:
    """Sprint 5.45 + 5.46: AlertHistoryStore persistence for experiments."""

    def test_store_record_and_get_experiment(self, tmp_path):
        store = _make_store(tmp_path)

        experiment = {
            "name": "store_test",
            "control_config": {"model": "a"},
            "variant_config": {"model": "b"},
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "stopped_at": "",
            "result": "",
            "control_samples": 10,
            "variant_samples": 15,
            "control_accuracy": 0.80,
            "variant_accuracy": 0.90,
            "promoted_variant": "",
            "promotion_timestamp": "",
            "auto_promoted": False,
            "metadata": {"env": "test"},
        }
        assert store.record_ab_experiment(experiment) is True

        results = store.get_ab_experiments()
        assert len(results) >= 1
        found = [e for e in results if e["name"] == "store_test"][0]
        assert found["control_samples"] == 10
        assert found["variant_samples"] == 15
        assert found["control_accuracy"] == pytest.approx(0.80)
        assert found["variant_accuracy"] == pytest.approx(0.90)
        assert found["metadata"] == {"env": "test"}

    def test_store_delete_experiment(self, tmp_path):
        store = _make_store(tmp_path)

        experiment = {
            "name": "del_test",
            "control_config": {},
            "variant_config": {},
            "status": "stopped",
        }
        store.record_ab_experiment(experiment)
        assert len(store.get_ab_experiments(status="stopped")) >= 1

        assert store.delete_ab_experiment("del_test") is True
        assert store.delete_ab_experiment("del_test") is False  # Already deleted

        remaining = [e for e in store.get_ab_experiments() if e["name"] == "del_test"]
        assert len(remaining) == 0

    def test_store_prune_stopped(self, tmp_path):
        store = _make_store(tmp_path)

        # Insert a stopped experiment with an old stopped_at timestamp
        old_ts = _old_iso(hours_ago=48)
        experiment = {
            "name": "old_stopped",
            "control_config": {},
            "variant_config": {},
            "status": "stopped",
            "started_at": _old_iso(hours_ago=72),
            "stopped_at": old_ts,
        }
        store.record_ab_experiment(experiment)

        # Insert a recently stopped experiment (should not be pruned)
        recent_ts = datetime.now(timezone.utc).isoformat()
        experiment2 = {
            "name": "recent_stopped",
            "control_config": {},
            "variant_config": {},
            "status": "stopped",
            "started_at": recent_ts,
            "stopped_at": recent_ts,
        }
        store.record_ab_experiment(experiment2)

        pruned = store.prune_stopped_ab_experiments(retention_hours=24)
        assert pruned >= 1

        remaining = store.get_ab_experiments()
        names = [e["name"] for e in remaining]
        assert "old_stopped" not in names
        assert "recent_stopped" in names

    def test_store_rollback_history(self, tmp_path):
        store = _make_store(tmp_path)

        rollback = {
            "experiment_name": "rb_hist",
            "rolled_back_variant": "variant",
            "rolled_back_at": datetime.now(timezone.utc).isoformat(),
            "control_accuracy": 0.85,
            "variant_accuracy": 0.70,
            "auto": True,
        }
        assert store.record_ab_rollback(rollback) is True

        history = store.get_ab_rollback_history()
        assert len(history) >= 1
        found = [h for h in history if h["experiment_name"] == "rb_hist"][0]
        assert found["rolled_back_variant"] == "variant"
        assert found["auto"] is True
        assert found["control_accuracy"] == pytest.approx(0.85)
        assert found["variant_accuracy"] == pytest.approx(0.70)

    def test_store_recovery_history(self, tmp_path):
        store = _make_store(tmp_path)

        recovery = {
            "subject": "vigil_faithfulness",
            "decay_amount": 0.25,
            "current_confidence": 0.55,
            "actions_taken": [{"action": "rerun_calibration", "experiment": "vigil_test"}],
        }
        assert store.record_decay_recovery(recovery) is True

        history = store.get_decay_recovery_history()
        assert len(history) >= 1
        found = [h for h in history if h["subject"] == "vigil_faithfulness"][0]
        assert found["decay_amount"] == pytest.approx(0.25)
        assert found["current_confidence"] == pytest.approx(0.55)
        assert len(found["actions_taken"]) >= 1

    def test_restore_from_store(self, tmp_path):
        store = _make_store(tmp_path)
        mgr = _make_manager(store=store)

        # Create and persist an experiment
        mgr.start_ab_experiment("restore_exp", {"model": "a"}, {"model": "b"})
        mgr.record_ab_result("restore_exp", "control", accuracy=0.82, samples=40)

        # Simulate a restart: create a fresh manager with the same store
        mgr2 = _make_manager(config=_make_config(), store=store)

        # Before restore, new manager has no experiments
        assert mgr2.get_ab_experiment("restore_exp") is None

        # Restore from store
        count = mgr2.restore_ab_experiments_from_store()
        assert count >= 1

        restored = mgr2.get_ab_experiment("restore_exp")
        assert restored is not None
        assert restored["name"] == "restore_exp"
        assert restored["control_accuracy"] == pytest.approx(0.82)
        assert restored["control_samples"] == 40
