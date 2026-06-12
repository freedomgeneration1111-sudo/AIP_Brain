"""Sprint 5.36 tests — WebSocket Message Batching, Alert Group Auto-Merge,
Dashboard Notification Preferences, Pruning Scheduler Observability,
Causal Chain Prediction.

Deliverable 1: WebSocket Message Batching & Connection Pooling
  (batch config, buffering, flush, batch_events message format, metrics)

Deliverable 2: Alert Group Auto-Merge by Similarity
  (suggest_auto_merges, apply_auto_merge, subject similarity, window enforcement)

Deliverable 3: Dashboard Notification Preferences
  (HTML has notification UI, localStorage keys, browser Notification API integration)

Deliverable 4: Pruning Scheduler Observability
  (pruning history recording, get_pruning_history, total_rows_pruned, dashboard chart)

Deliverable 5: Causal Chain Prediction
  (predict_causal_chain, get_causal_predictions, prediction config, event broadcasting)
"""

from __future__ import annotations

import os
import tempfile
import time

from aip.adapter.alert_history_store import AlertHistoryStore, SyncAlertHistoryBridge
from aip.adapter.alerting import (
    Alert,
    AlertConfig,
    AlertManager,
)

# ============================================================================
# Deliverable 1: WebSocket Message Batching & Connection Pooling
# ============================================================================


class TestWebSocketBatching:
    """Tests for WebSocket message batching and connection coordination."""

    def test_batch_config_defaults(self):
        """AlertConfig has batching settings with sensible defaults."""
        config = AlertConfig()
        assert config.ws_batch_window_seconds == 0.5
        assert config.ws_batch_max_size == 20

    def test_batch_config_custom(self):
        """AlertConfig batching settings can be customized."""
        config = AlertConfig(
            ws_batch_window_seconds=1.0,
            ws_batch_max_size=50,
        )
        assert config.ws_batch_window_seconds == 1.0
        assert config.ws_batch_max_size == 50

    def test_batch_buffer_initialized(self):
        """AlertManager initializes batch buffer and counters."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert mgr.realtime_bus._ws_batch_buffer == []
        assert mgr.realtime_bus._ws_batch_flush_scheduled is False
        assert mgr.realtime_bus._ws_batch_total_flushes == 0
        assert mgr.realtime_bus._ws_batch_total_events_sent == 0

    def test_events_are_buffered_when_batching_enabled(self):
        """Events are buffered instead of sent immediately when batching is on."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                ws_batch_window_seconds=10.0,  # Long window so it doesn't flush during test
                min_alert_interval_seconds=0,
            )
        )

        # Add a mock WS subscriber
        events_received = []

        class MockWS:
            def put_nowait(self, item):
                events_received.append(item)

        mgr.realtime_bus.add_ws_subscriber(MockWS())

        # Send an alert — event should be buffered, not sent immediately
        cid = mgr.send_alert(
            Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="batch_test",
                message="Test",
            )
        )
        assert cid  # Alert was sent
        assert len(mgr.realtime_bus._ws_batch_buffer) > 0  # Event was buffered

    def test_flush_ws_batch_sends_batch_message(self):
        """_flush_ws_batch() sends a batch_events message with all buffered events."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                ws_batch_window_seconds=0,  # Disable batching for this test
                min_alert_interval_seconds=0,
            )
        )

        # Manually buffer events
        with mgr.realtime_bus._lock:
            mgr.realtime_bus._ws_batch_buffer = [
                {"event": "alert_delivered", "correlation_id": "cid-1"},
                {"event": "alert_delivered", "correlation_id": "cid-2"},
            ]

        # Add a mock subscriber to capture the batch message
        batch_messages = []

        class MockWS:
            def put_nowait(self, item):
                batch_messages.append(item)

        mgr.realtime_bus.add_ws_subscriber(MockWS())

        mgr.realtime_bus._flush_ws_batch()

        assert len(batch_messages) == 1
        msg = batch_messages[0]
        assert msg["event"] == "batch_events"
        assert msg["count"] == 2
        assert len(msg["events"]) == 2
        assert mgr.realtime_bus._ws_batch_total_flushes == 1
        assert mgr.realtime_bus._ws_batch_total_events_sent == 2

    def test_flush_empty_buffer_is_noop(self):
        """_flush_ws_batch() with empty buffer is a no-op."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.realtime_bus._flush_ws_batch()
        assert mgr.realtime_bus._ws_batch_total_flushes == 0

    def test_batching_disabled_sends_immediately(self):
        """When ws_batch_window_seconds is 0, events are sent immediately."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                ws_batch_window_seconds=0,  # Disabled
                min_alert_interval_seconds=0,
            )
        )

        events_received = []

        class MockWS:
            def put_nowait(self, item):
                events_received.append(item)

        mgr.realtime_bus.add_ws_subscriber(MockWS())

        mgr.send_alert(
            Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="immediate_test",
                message="Test",
            )
        )

        # With batching disabled, event should be sent directly (not buffered)
        assert len(mgr.realtime_bus._ws_batch_buffer) == 0
        assert len(events_received) > 0

    def test_get_status_includes_batching_info(self):
        """get_status() includes ws_batching info."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_batch_window_seconds=1.0))
        status = mgr.get_status()
        assert "ws_batching" in status
        assert status["ws_batching"]["batch_window_seconds"] == 1.0
        assert status["ws_batching"]["batch_max_size"] == 20
        assert status["ws_batching"]["total_flushes"] == 0
        assert status["ws_batching"]["total_events_sent"] == 0

    def test_batching_endpoint_exists(self):
        """GET /vigil/quality/dashboard/ws/batching endpoint exists."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/dashboard/ws/batching" in route_paths

    def test_dashboard_html_handles_batch_events(self):
        """Dashboard HTML includes handleBatchEvents function."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "handleBatchEvents" in _DASHBOARD_HTML
        assert "batch_events" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 2: Alert Group Auto-Merge by Similarity
# ============================================================================


class TestAutoMergeBySimilarity:
    """Tests for alert group auto-merge by similarity."""

    def test_auto_merge_config_defaults(self):
        """AlertConfig has auto-merge settings with sensible defaults."""
        config = AlertConfig()
        assert config.auto_merge_window_seconds == 600
        assert config.auto_merge_similarity_threshold == 0.6

    def test_compute_subject_similarity_identical(self):
        """_compute_subject_similarity returns 1.0 for identical keys."""
        similarity = AlertManager._compute_subject_similarity("vigil_faithfulness", "vigil_faithfulness")
        assert similarity == 1.0

    def test_compute_subject_similarity_partial(self):
        """_compute_subject_similarity returns partial overlap score."""
        similarity = AlertManager._compute_subject_similarity("vigil_faithfulness", "vigil_citation_rate")
        # "vigil" and "faithfulness" vs "vigil" and "citation" and "rate"
        # Overlap: "vigil" = 1 token, union = {vigil, faithfulness, citation, rate} = 4
        assert 0 < similarity < 1.0

    def test_compute_subject_similarity_no_overlap(self):
        """_compute_subject_similarity returns 0.0 for no overlap."""
        similarity = AlertManager._compute_subject_similarity("abc_xyz", "def_uvw")
        assert similarity == 0.0

    def test_suggest_auto_merges_disabled(self):
        """suggest_auto_merges() returns empty list when window is 0."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                auto_merge_window_seconds=0,
            )
        )
        mgr._alert_groups["group_a"] = ["cid-1"]
        mgr._alert_groups["group_b"] = ["cid-2"]
        mgr._alert_groups_metadata["group_a"] = time.time()
        mgr._alert_groups_metadata["group_b"] = time.time()

        suggestions = mgr.suggest_auto_merges()
        assert suggestions == []

    def test_suggest_auto_merges_finds_similar_groups(self):
        """suggest_auto_merges() finds groups with similar subjects."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                auto_merge_window_seconds=600,
                auto_merge_similarity_threshold=0.2,
            )
        )
        # Create groups with overlapping subject names
        # "vigil_faithfulness" and "vigil_citation_rate" share "vigil" (Jaccard=0.25)
        mgr._alert_groups["vigil_faithfulness"] = ["cid-1", "cid-2"]
        mgr._alert_groups["vigil_citation_rate"] = ["cid-3"]
        mgr._alert_groups_metadata["vigil_faithfulness"] = time.time()
        mgr._alert_groups_metadata["vigil_citation_rate"] = time.time()

        suggestions = mgr.suggest_auto_merges()
        # "vigil" overlaps, so similarity should be >= 0.2
        assert len(suggestions) >= 1
        assert suggestions[0]["source_key"] == "vigil_faithfulness"
        assert suggestions[0]["target_key"] == "vigil_citation_rate"
        assert suggestions[0]["similarity"] >= 0.2

    def test_suggest_auto_merges_skips_stale_groups(self):
        """suggest_auto_merges() skips groups with old activity."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                auto_merge_window_seconds=60,
                auto_merge_similarity_threshold=0.2,
            )
        )
        # Use identical names to guarantee similarity >= threshold
        mgr._alert_groups["stale_group"] = ["cid-1"]
        mgr._alert_groups["fresh_group"] = ["cid-2"]
        mgr._alert_groups_metadata["stale_group"] = time.time() - 120  # 2 min ago
        mgr._alert_groups_metadata["fresh_group"] = time.time()

        suggestions = mgr.suggest_auto_merges()
        # Stale group should not appear in suggestions even if it would match
        for s in suggestions:
            assert "stale_group" not in (s["source_key"], s["target_key"])

    def test_suggest_auto_merges_skips_causal_groups(self):
        """suggest_auto_merges() skips causal: groups."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                auto_merge_window_seconds=600,
                auto_merge_similarity_threshold=0.3,
            )
        )
        mgr._alert_groups["causal:test"] = ["cid-1"]
        mgr._alert_groups["regular_test"] = ["cid-2"]
        mgr._alert_groups_metadata["causal:test"] = time.time()
        mgr._alert_groups_metadata["regular_test"] = time.time()

        suggestions = mgr.suggest_auto_merges()
        for s in suggestions:
            assert not s["source_key"].startswith("causal:")
            assert not s["target_key"].startswith("causal:")

    def test_apply_auto_merge(self):
        """apply_auto_merge() merges groups and tracks counter."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr._alert_groups["source"] = ["cid-1", "cid-2"]
        mgr._alert_groups["target"] = ["cid-3"]
        mgr._alert_groups_metadata["source"] = time.time()
        mgr._alert_groups_metadata["target"] = time.time()

        result = mgr.apply_auto_merge("source", "target")
        assert result["status"] == "ok"
        assert mgr._total_auto_merges_applied == 1
        assert "source" not in mgr.get_alert_groups()

    def test_get_auto_merge_suggestions(self):
        """get_auto_merge_suggestions() returns current suggestions."""
        mgr = AlertManager(AlertConfig(enabled=True))
        with mgr._lock:
            mgr._auto_merge_suggestions = [{"source_key": "a", "target_key": "b"}]
        suggestions = mgr.get_auto_merge_suggestions()
        assert len(suggestions) == 1

    def test_auto_merge_endpoints_exist(self):
        """Auto-merge API endpoints exist."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/alerts/groups/auto-merge" in route_paths
        assert "/vigil/quality/alerts/groups/auto-merge/apply" in route_paths

    def test_dashboard_html_has_auto_merge_panel(self):
        """Dashboard HTML includes auto-merge suggestion panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "fetchAutoMergeSuggestions" in _DASHBOARD_HTML
        assert "merge-suggest-panel" in _DASHBOARD_HTML
        assert "applyAutoMerge" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 3: Dashboard Notification Preferences
# ============================================================================


class TestNotificationPreferences:
    """Tests for dashboard notification preferences."""

    def test_dashboard_html_has_notification_panel(self):
        """Dashboard HTML includes notification preferences panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "notifPanel" in _DASHBOARD_HTML
        assert "notif-critical" in _DASHBOARD_HTML
        assert "notif-warning" in _DASHBOARD_HTML
        assert "notif-info" in _DASHBOARD_HTML
        assert "notif-type" in _DASHBOARD_HTML

    def test_dashboard_html_has_save_notif_prefs(self):
        """Dashboard HTML includes saveNotifPrefs function."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "saveNotifPrefs" in _DASHBOARD_HTML
        assert "aip_notif_prefs" in _DASHBOARD_HTML

    def test_dashboard_html_has_restore_notif_prefs(self):
        """Dashboard HTML includes restoreNotifPrefs function."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "restoreNotifPrefs" in _DASHBOARD_HTML

    def test_dashboard_html_has_notification_permission(self):
        """Dashboard HTML includes browser notification permission request."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "requestNotificationPermission" in _DASHBOARD_HTML
        assert "Notification" in _DASHBOARD_HTML

    def test_dashboard_html_has_should_notify(self):
        """Dashboard HTML includes shouldNotify for filtering notifications."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "shouldNotify" in _DASHBOARD_HTML
        assert "showBrowserNotification" in _DASHBOARD_HTML

    def test_dashboard_state_includes_notif_prefs(self):
        """Dashboard state saves notification preferences."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "notifCritical" in _DASHBOARD_HTML
        assert "notifWarning" in _DASHBOARD_HTML
        assert "notifInfo" in _DASHBOARD_HTML
        assert "notifType" in _DASHBOARD_HTML

    def test_ws_command_update_notification_prefs(self):
        """WebSocket command 'update_notification_prefs' is handled."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "update_notification_prefs" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 4: Pruning Scheduler Observability
# ============================================================================


class TestPruningObservability:
    """Tests for pruning scheduler observability."""

    def test_pruning_history_size_config(self):
        """AlertConfig has pruning_history_size with default."""
        config = AlertConfig()
        assert config.pruning_history_size == 20

    def test_pruning_history_initialized_empty(self):
        """AlertManager initializes pruning history as empty."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert mgr.pruning_mgr._pruning_history == []

    def test_run_scheduled_prune_records_history(self):
        """_run_scheduled_prune() records a history entry."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    delivery_status_max_age_days=30,
                    delivery_status_max_rows=2000,
                )
            )
            mgr.attach_history_store(bridge)

            mgr._run_scheduled_prune()

            history = mgr.get_pruning_history()
            assert len(history) == 1
            assert history[0]["records_deleted"] == 0
            assert "timestamp_iso" in history[0]
            assert history[0]["max_age_days"] == 30
            assert history[0]["max_rows"] == 2000

    def test_pruning_history_respects_max_size(self):
        """Pruning history is capped at pruning_history_size."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    pruning_history_size=3,
                )
            )
            mgr.attach_history_store(bridge)

            for _ in range(5):
                mgr._run_scheduled_prune()

            history = mgr.get_pruning_history()
            assert len(history) == 3

    def test_get_pruning_history_with_limit(self):
        """get_pruning_history() respects limit parameter."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    pruning_history_size=20,
                )
            )
            mgr.attach_history_store(bridge)

            for _ in range(5):
                mgr._run_scheduled_prune()

            limited = mgr.get_pruning_history(limit=3)
            assert len(limited) == 3

    def test_prune_scheduler_status_includes_history(self):
        """get_prune_scheduler_status() includes history and total_rows_pruned."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                )
            )
            mgr.attach_history_store(bridge)

            mgr._run_scheduled_prune()

            status = mgr.get_prune_scheduler_status()
            assert "history" in status
            assert "total_rows_pruned" in status
            assert len(status["history"]) == 1

    def test_prune_history_endpoint_exists(self):
        """GET /vigil/quality/alerts/delivery-status/prune/history endpoint exists."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/alerts/delivery-status/prune/history" in route_paths

    def test_dashboard_html_has_pruning_observability(self):
        """Dashboard HTML includes pruning metrics panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "fetchPruningHistory" in _DASHBOARD_HTML
        assert "pruneTotalPruned" in _DASHBOARD_HTML
        assert "pruneTotalRuns" in _DASHBOARD_HTML
        assert "pruneLastRun" in _DASHBOARD_HTML
        assert "prune-history-list" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 5: Causal Chain Prediction
# ============================================================================


class TestCausalChainPrediction:
    """Tests for causal chain prediction (exploratory)."""

    def test_causal_prediction_config(self):
        """AlertConfig has causal_prediction_enabled flag."""
        config = AlertConfig()
        assert config.causal_prediction_enabled is False

    def test_predict_disabled_returns_empty(self):
        """predict_causal_chain() returns empty when disabled."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                causal_prediction_enabled=False,
            )
        )
        alert = Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="pred_test",
            message="Test",
        )
        predictions = mgr.predict_causal_chain(alert)
        assert predictions == []

    def test_predict_pool_adjustment(self):
        """predict_causal_chain() predicts chain from pool_adjustment."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                causal_prediction_enabled=True,
                causal_grouping_window_seconds=300,
            )
        )
        alert = Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="pred_test",
            message="Test",
        )
        predictions = mgr.predict_causal_chain(alert)
        assert len(predictions) == 2
        assert predictions[0]["predicted_alert_type"] == "quality_degradation"
        assert predictions[1]["predicted_alert_type"] == "batch_reduction"
        assert predictions[0]["subject"] == "pred_test"
        assert predictions[0]["confidence"] == 1.0
        assert predictions[1]["confidence"] == 0.75

    def test_predict_quality_degradation(self):
        """predict_causal_chain() predicts chain from quality_degradation."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                causal_prediction_enabled=True,
                causal_grouping_window_seconds=300,
            )
        )
        alert = Alert(
            alert_type="quality_degradation",
            severity="warning",
            subject="pred_test2",
            message="Test",
        )
        predictions = mgr.predict_causal_chain(alert)
        assert len(predictions) == 1
        assert predictions[0]["predicted_alert_type"] == "batch_reduction"

    def test_predict_batch_reduction_no_followup(self):
        """predict_causal_chain() returns empty for batch_reduction (end of chain)."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                causal_prediction_enabled=True,
            )
        )
        alert = Alert(
            alert_type="batch_reduction",
            severity="warning",
            subject="pred_test3",
            message="Test",
        )
        predictions = mgr.predict_causal_chain(alert)
        assert predictions == []

    def test_predict_unknown_type_returns_empty(self):
        """predict_causal_chain() returns empty for unknown alert types."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                causal_prediction_enabled=True,
            )
        )
        alert = Alert(
            alert_type="unknown_type",
            severity="info",
            subject="pred_test4",
            message="Test",
        )
        predictions = mgr.predict_causal_chain(alert)
        assert predictions == []

    def test_predictions_are_stored(self):
        """Predictions are stored and queryable via get_causal_predictions()."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                causal_prediction_enabled=True,
                causal_grouping_window_seconds=300,
            )
        )
        alert = Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="store_test",
            message="Test",
        )
        mgr.predict_causal_chain(alert)

        # Get predictions for specific subject
        preds = mgr.get_causal_predictions(subject="store_test")
        assert len(preds) == 2

        # Get all predictions
        all_preds = mgr.get_causal_predictions()
        assert "store_test" in all_preds

    def test_prediction_counter_increments(self):
        """_total_predictions_made counter increments correctly."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                causal_prediction_enabled=True,
                causal_grouping_window_seconds=300,
            )
        )
        alert = Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="counter_test",
            message="Test",
        )
        mgr.predict_causal_chain(alert)
        assert mgr.prediction_mgr._total_predictions_made == 2  # 2 predictions for pool_adjustment

    def test_prediction_broadcasts_event(self):
        """predict_causal_chain() broadcasts causal_predictions event."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                causal_prediction_enabled=True,
                causal_grouping_window_seconds=300,
            )
        )
        events = []

        class MockQueue:
            def put_nowait(self, item):
                events.append(item)

        mgr.realtime_bus.add_sse_subscriber(MockQueue())

        alert = Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="broadcast_test",
            message="Test",
        )
        mgr.predict_causal_chain(alert)

        pred_events = [e for e in events if e.get("event") == "causal_predictions"]
        assert len(pred_events) >= 1
        assert pred_events[0]["subject"] == "broadcast_test"
        assert len(pred_events[0]["predictions"]) == 2

    def test_predictions_endpoint_exists(self):
        """GET /vigil/quality/alerts/predictions endpoint exists."""
        from aip.adapter.api.routes.vigil_quality import router

        route_paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/vigil/quality/alerts/predictions" in route_paths

    def test_dashboard_html_has_prediction_panel(self):
        """Dashboard HTML includes prediction panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "predictionPanel" in _DASHBOARD_HTML
        assert "updatePredictions" in _DASHBOARD_HTML
        assert "fetchPredictions" in _DASHBOARD_HTML
        assert "prediction-list" in _DASHBOARD_HTML

    def test_get_status_includes_prediction_info(self):
        """get_status() includes causal_prediction info."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                causal_prediction_enabled=True,
            )
        )
        status = mgr.get_status()
        assert "causal_prediction" in status
        assert status["causal_prediction"]["enabled"] is True
        assert status["causal_prediction"]["total_predictions"] == 0

    def test_prediction_with_persistent_store(self):
        """predict_causal_chain() works with persistent store attached."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AlertHistoryStore(os.path.join(tmp_dir, "alerts.db"))
            bridge = SyncAlertHistoryBridge(store)
            bridge.initialize()

            mgr = AlertManager(
                AlertConfig(
                    enabled=True,
                    min_alert_interval_seconds=0,
                    causal_prediction_enabled=True,
                    causal_grouping_window_seconds=300,
                )
            )
            mgr.attach_history_store(bridge)

            alert = Alert(
                alert_type="pool_adjustment",
                severity="warning",
                subject="persist_pred",
                message="Test",
            )
            predictions = mgr.predict_causal_chain(alert)
            assert len(predictions) == 2

            # Verify predictions are stored in memory
            preds = mgr.get_causal_predictions(subject="persist_pred")
            assert len(preds) == 2
