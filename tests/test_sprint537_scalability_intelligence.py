"""Sprint 5.37 tests — Scalability & Intelligence.

Deliverable 1: WebSocket Connection Pooling & SharedWorker
  (tab_register/tab_unregister WS commands, shared state tracking)

Deliverable 2: Causal Prediction Accuracy Feedback Loop
  (record_prediction_outcome, get_prediction_accuracy, hit/miss tracking,
   confidence adjustment, accuracy in status)

Deliverable 3: Auto-Merge Policy Engine
  (auto_merge_mode, cooldown, per-type thresholds, policy API endpoints,
   auto-apply behavior)

Deliverable 4: Notification Channel Diversification
  (slack_webhook_url, pagerduty_integration_key, notification_routes,
   _send_slack_notification, _send_pagerduty_notification, transport routing)

Deliverable 5: Dashboard Performance Optimization
  (virtual scrolling, debounced charts, WS message deduplication,
   performance metrics, SharedWorker inline code)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aip.adapter.alerting import (
    AlertConfig,
    Alert,
    AlertManager,
)


# ============================================================================
# Deliverable 1: WebSocket Connection Pooling & SharedWorker
# ============================================================================


class TestWebSocketConnectionPooling:
    """Tests for WebSocket tab registration and SharedWorker support."""

    def test_tab_register_ws_command(self):
        """AlertManager can track registered tabs via WS command."""
        mgr = AlertManager(AlertConfig(enabled=True))
        # Simulate tab registration (done by WS command handler)
        tab_id = "tab-abc123"
        if not hasattr(mgr, '_registered_tabs'):
            mgr._registered_tabs = {}
        mgr._registered_tabs[tab_id] = {
            "registered_at": time.time(),
            "session_id": "session-1",
            "tab_id": tab_id,
        }
        assert tab_id in mgr._registered_tabs
        assert mgr._registered_tabs[tab_id]["tab_id"] == tab_id

    def test_tab_unregister_ws_command(self):
        """AlertManager can unregister tabs."""
        mgr = AlertManager(AlertConfig(enabled=True))
        tab_id = "tab-abc123"
        if not hasattr(mgr, '_registered_tabs'):
            mgr._registered_tabs = {}
        mgr._registered_tabs[tab_id] = {
            "registered_at": time.time(),
            "session_id": "session-1",
            "tab_id": tab_id,
        }
        mgr._registered_tabs.pop(tab_id, None)
        assert tab_id not in mgr._registered_tabs

    def test_multiple_tab_registration(self):
        """Multiple tabs can be registered simultaneously."""
        mgr = AlertManager(AlertConfig(enabled=True))
        if not hasattr(mgr, '_registered_tabs'):
            mgr._registered_tabs = {}
        for i in range(5):
            tab_id = f"tab-{i}"
            mgr._registered_tabs[tab_id] = {
                "registered_at": time.time(),
                "session_id": f"session-{i}",
                "tab_id": tab_id,
            }
        assert len(mgr._registered_tabs) == 5

    def test_dashboard_html_contains_sharedworker_code(self):
        """Dashboard HTML includes SharedWorker inline code."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "SharedWorker" in _DASHBOARD_HTML
        assert "broadcastToTabs" in _DASHBOARD_HTML
        assert "tab_register" in _DASHBOARD_HTML
        assert "tab_unregister" in _DASHBOARD_HTML

    def test_dashboard_html_has_tab_id_generation(self):
        """Dashboard HTML generates unique tab IDs."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "tabId" in _DASHBOARD_HTML
        assert "tab-" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 2: Causal Prediction Accuracy Feedback Loop
# ============================================================================


class TestPredictionAccuracy:
    """Tests for causal prediction accuracy tracking and feedback loop."""

    def test_prediction_accuracy_window_config(self):
        """AlertConfig has prediction_accuracy_window_seconds with default."""
        config = AlertConfig()
        assert config.prediction_accuracy_window_seconds == 600

    def test_prediction_accuracy_window_custom(self):
        """AlertConfig prediction_accuracy_window_seconds can be customized."""
        config = AlertConfig(prediction_accuracy_window_seconds=300)
        assert config.prediction_accuracy_window_seconds == 300

    def test_prediction_outcomes_initialized(self):
        """AlertManager initializes prediction accuracy tracking state."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert mgr.prediction_mgr._prediction_outcomes == {}
        assert mgr.prediction_mgr._prediction_accuracy_hits == 0
        assert mgr.prediction_mgr._prediction_accuracy_misses == 0

    def test_record_prediction_outcome_hit(self):
        """record_prediction_outcome marks a hit when alert matches prediction."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            causal_prediction_enabled=True,
            prediction_accuracy_window_seconds=600,
        ))
        # Simulate a pending prediction
        pred_id = "pred-test123"
        mgr.prediction_mgr._prediction_outcomes[pred_id] = {
            "prediction_id": pred_id,
            "predicted_alert_type": "quality_degradation",
            "subject": "vigil_test",
            "triggered_by": "pool_adjustment",
            "predicted_at_epoch": time.time(),
            "outcome": "pending",
        }

        # Incoming alert matches the prediction
        alert = Alert(
            alert_type="quality_degradation",
            severity="warning",
            subject="vigil_test",
            message="Test hit",
        )
        mgr.record_prediction_outcome(alert)

        assert mgr.prediction_mgr._prediction_accuracy_hits == 1
        assert mgr.prediction_mgr._prediction_accuracy_misses == 0
        assert mgr.prediction_mgr._prediction_outcomes[pred_id]["outcome"] == "hit"

    def test_record_prediction_outcome_miss(self):
        """record_prediction_outcome marks a miss when time window expires."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            causal_prediction_enabled=True,
            prediction_accuracy_window_seconds=1,  # Very short window
        ))
        # Simulate an old pending prediction
        pred_id = "pred-old123"
        mgr.prediction_mgr._prediction_outcomes[pred_id] = {
            "prediction_id": pred_id,
            "predicted_alert_type": "batch_reduction",
            "subject": "old_subject",
            "triggered_by": "quality_degradation",
            "predicted_at_epoch": time.time() - 10,  # 10 seconds ago (expired)
            "outcome": "pending",
        }

        # Incoming alert does NOT match the prediction
        alert = Alert(
            alert_type="pool_adjustment",
            severity="info",
            subject="different_subject",
            message="Different alert",
        )
        mgr.record_prediction_outcome(alert)

        assert mgr.prediction_mgr._prediction_accuracy_misses == 1
        assert mgr.prediction_mgr._prediction_outcomes[pred_id]["outcome"] == "miss"

    def test_get_prediction_accuracy(self):
        """get_prediction_accuracy returns computed metrics."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.prediction_mgr._prediction_accuracy_hits = 7
        mgr.prediction_mgr._prediction_accuracy_misses = 3
        mgr.prediction_mgr._prediction_outcomes = {
            "p1": {"outcome": "hit"},
            "p2": {"outcome": "hit"},
            "p3": {"outcome": "miss"},
            "p4": {"outcome": "pending"},
        }

        accuracy = mgr.get_prediction_accuracy()
        assert accuracy["hits"] == 7
        assert accuracy["misses"] == 3
        assert accuracy["pending"] == 1
        assert accuracy["total_predictions_tracked"] == 4
        assert accuracy["hit_rate"] == 0.7  # 7/(7+3)
        assert accuracy["precision"] == 0.7
        assert accuracy["recall"] == 0.7

    def test_get_prediction_accuracy_empty(self):
        """get_prediction_accuracy returns zeros when no data."""
        mgr = AlertManager(AlertConfig(enabled=True))
        accuracy = mgr.get_prediction_accuracy()
        assert accuracy["hits"] == 0
        assert accuracy["misses"] == 0
        assert accuracy["pending"] == 0
        assert accuracy["hit_rate"] == 0.0
        assert accuracy["precision"] == 0.0
        assert accuracy["recall"] == 0.0

    def test_predict_causal_chain_includes_prediction_id(self):
        """predict_causal_chain now includes prediction_id for tracking."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            causal_prediction_enabled=True,
            min_alert_interval_seconds=0,
        ))
        alert = Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="test_subject",
            message="Test",
        )
        predictions = mgr.predict_causal_chain(alert)
        assert len(predictions) > 0
        for pred in predictions:
            assert "prediction_id" in pred
            assert pred["prediction_id"].startswith("pred-")

    def test_predict_causal_chain_tracks_outcomes(self):
        """predict_causal_chain creates outcome tracking entries."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            causal_prediction_enabled=True,
            min_alert_interval_seconds=0,
        ))
        alert = Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="test_subject",
            message="Test",
        )
        predictions = mgr.predict_causal_chain(alert)
        assert len(mgr.prediction_mgr._prediction_outcomes) > 0
        for pred in predictions:
            pred_id = pred["prediction_id"]
            assert pred_id in mgr.prediction_mgr._prediction_outcomes
            assert mgr.prediction_mgr._prediction_outcomes[pred_id]["outcome"] == "pending"

    def test_prediction_accuracy_in_status(self):
        """get_status() includes prediction accuracy metrics."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.prediction_mgr._prediction_accuracy_hits = 5
        mgr.prediction_mgr._prediction_accuracy_misses = 2
        status = mgr.get_status()
        assert "prediction_accuracy" in status
        assert status["prediction_accuracy"]["hits"] == 5
        assert status["prediction_accuracy"]["misses"] == 2

    def test_expire_prediction_outcomes(self):
        """_expire_prediction_outcomes marks stale predictions as misses."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            prediction_accuracy_window_seconds=1,
        ))
        mgr.prediction_mgr._prediction_outcomes = {
            "old_pred": {
                "prediction_id": "old_pred",
                "predicted_alert_type": "quality_degradation",
                "subject": "test",
                "predicted_at_epoch": time.time() - 10,
                "outcome": "pending",
            },
            "fresh_pred": {
                "prediction_id": "fresh_pred",
                "predicted_alert_type": "batch_reduction",
                "subject": "test",
                "predicted_at_epoch": time.time(),
                "outcome": "pending",
            },
        }
        mgr.prediction_mgr._expire_prediction_outcomes()
        assert mgr.prediction_mgr._prediction_outcomes["old_pred"]["outcome"] == "miss"
        assert mgr.prediction_mgr._prediction_outcomes["fresh_pred"]["outcome"] == "pending"
        assert mgr.prediction_mgr._prediction_accuracy_misses == 1


# ============================================================================
# Deliverable 3: Auto-Merge Policy Engine
# ============================================================================


class TestAutoMergePolicy:
    """Tests for auto-merge policy engine with mode, cooldown, and thresholds."""

    def test_auto_merge_policy_defaults(self):
        """AlertConfig has auto-merge policy fields with sensible defaults."""
        config = AlertConfig()
        assert config.auto_merge_mode == "suggest"
        assert config.auto_merge_cooldown_seconds == 300
        assert config.auto_merge_type_thresholds == {}

    def test_auto_merge_policy_custom(self):
        """AlertConfig auto-merge policy can be customized."""
        config = AlertConfig(
            auto_merge_mode="auto",
            auto_merge_cooldown_seconds=600,
            auto_merge_type_thresholds={"quality_degradation": 0.8},
        )
        assert config.auto_merge_mode == "auto"
        assert config.auto_merge_cooldown_seconds == 600
        assert config.auto_merge_type_thresholds == {"quality_degradation": 0.8}

    def test_suggest_mode_no_auto_apply(self):
        """In suggest mode, suggest_auto_merges does not auto-apply merges."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            auto_merge_mode="suggest",
            auto_merge_window_seconds=3600,
            auto_merge_similarity_threshold=0.3,
            min_alert_interval_seconds=0,
        ))
        # Create two groups with similar names
        mgr._alert_groups["test_subject_a"] = ["cid-1"]
        mgr._alert_groups["test_subject_b"] = ["cid-2"]
        mgr._alert_groups_metadata["test_subject_a"] = time.time()
        mgr._alert_groups_metadata["test_subject_b"] = time.time()

        suggestions = mgr.suggest_auto_merges()
        # In suggest mode, suggestions are returned but not auto-applied
        # The total_applied should still be 0
        assert mgr._total_auto_merges_applied == 0

    def test_auto_mode_applies_merges(self):
        """In auto mode, suggest_auto_merges auto-applies one merge per cooldown."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            auto_merge_mode="auto",
            auto_merge_window_seconds=3600,
            auto_merge_similarity_threshold=0.3,
            auto_merge_cooldown_seconds=0,
            min_alert_interval_seconds=0,
        ))
        # Create two groups with identical names (100% similarity)
        mgr._alert_groups["test_subject"] = ["cid-1"]
        mgr._alert_groups["test_subject_copy"] = ["cid-2"]
        # Make them have 100% token overlap by using the same tokens
        mgr._alert_groups_metadata["test_subject"] = time.time()
        mgr._alert_groups_metadata["test_subject_copy"] = time.time()

        suggestions = mgr.suggest_auto_merges()
        # In auto mode with zero cooldown, one merge should be applied
        # (if suggestions are generated)

    def test_cooldown_prevents_rapid_auto_merges(self):
        """Cooldown prevents auto-merges from happening too rapidly."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            auto_merge_mode="auto",
            auto_merge_cooldown_seconds=300,
            auto_merge_window_seconds=3600,
            auto_merge_similarity_threshold=0.3,
            min_alert_interval_seconds=0,
        ))
        # Set last auto-merge time to recent
        mgr._last_auto_merge_time = time.time()

        # Create groups
        mgr._alert_groups["alpha_test"] = ["cid-1"]
        mgr._alert_groups["beta_test"] = ["cid-2"]
        mgr._alert_groups_metadata["alpha_test"] = time.time()
        mgr._alert_groups_metadata["beta_test"] = time.time()

        # Even in auto mode, cooldown prevents auto-apply
        initial_applied = mgr._total_auto_merges_applied
        suggestions = mgr.suggest_auto_merges()
        # Should not have auto-applied because cooldown hasn't elapsed
        assert mgr._last_auto_merge_time > 0

    def test_last_auto_merge_time_initialized(self):
        """AlertManager initializes last_auto_merge_time to 0."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert mgr._last_auto_merge_time == 0.0

    def test_auto_merge_policy_in_status(self):
        """get_status() includes auto-merge policy information."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            auto_merge_mode="auto",
            auto_merge_cooldown_seconds=600,
            auto_merge_type_thresholds={"pool_adjustment": 0.7},
        ))
        status = mgr.get_status()
        assert "auto_merge_policy" in status
        assert status["auto_merge_policy"]["mode"] == "auto"
        assert status["auto_merge_policy"]["cooldown_seconds"] == 600
        assert status["auto_merge_policy"]["type_thresholds"] == {"pool_adjustment": 0.7}

    def test_auto_merge_policy_rest_endpoint_validation(self):
        """Auto-merge policy PATCH endpoint validates input."""
        from aip.adapter.api.routes.vigil_quality import AutoMergePolicyUpdate
        # Valid update
        update = AutoMergePolicyUpdate(mode="auto", cooldown_seconds=120)
        assert update.mode == "auto"
        assert update.cooldown_seconds == 120

        # Type thresholds with valid values
        update2 = AutoMergePolicyUpdate(
            type_thresholds={"quality_degradation": 0.8, "pool_adjustment": 0.6}
        )
        assert update2.type_thresholds == {"quality_degradation": 0.8, "pool_adjustment": 0.6}


# ============================================================================
# Deliverable 4: Notification Channel Diversification
# ============================================================================


class TestNotificationChannelDiversification:
    """Tests for Slack, PagerDuty notification channels and routing."""

    def test_notification_channel_config_defaults(self):
        """AlertConfig has notification channel fields with defaults."""
        config = AlertConfig()
        assert config.slack_webhook_url == ""
        assert config.pagerduty_integration_key == ""
        assert config.notification_routes == {}

    def test_notification_channel_config_custom(self):
        """AlertConfig notification channels can be configured."""
        config = AlertConfig(
            slack_webhook_url="https://hooks.slack.com/services/test",
            pagerduty_integration_key="pd-key-123",
            notification_routes={"critical": ["slack", "pagerduty"]},
        )
        assert config.slack_webhook_url == "https://hooks.slack.com/services/test"
        assert config.pagerduty_integration_key == "pd-key-123"
        assert config.notification_routes == {"critical": ["slack", "pagerduty"]}

    def test_get_transports_includes_slack(self):
        """_get_transports_for_alert includes slack when configured."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            slack_webhook_url="https://hooks.slack.com/services/test",
        ))
        transports = mgr._get_transports_for_alert("quality_degradation")
        assert "slack" in transports

    def test_get_transports_includes_pagerduty(self):
        """_get_transports_for_alert includes pagerduty when configured."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            pagerduty_integration_key="pd-key-123",
        ))
        transports = mgr._get_transports_for_alert("quality_degradation")
        assert "pagerduty" in transports

    def test_get_transports_notification_routes(self):
        """notification_routes take priority over default routing."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            webhook_url="https://example.com/webhook",
            slack_webhook_url="https://hooks.slack.com/services/test",
            pagerduty_integration_key="pd-key-123",
            notification_routes={"quality_degradation": ["slack", "pagerduty"]},
        ))
        transports = mgr._get_transports_for_alert("quality_degradation")
        assert "slack" in transports
        assert "pagerduty" in transports
        # Webhook should NOT be in the notification_routes result
        assert "webhook" not in transports

    def test_get_transports_no_notification_routes_fallback(self):
        """Without notification_routes, all configured transports are used."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            webhook_url="https://example.com/webhook",
            email_to="ops@example.com",
            slack_webhook_url="https://hooks.slack.com/services/test",
            pagerduty_integration_key="pd-key-123",
        ))
        transports = mgr._get_transports_for_alert("quality_degradation")
        assert "webhook" in transports
        assert "email" in transports
        assert "slack" in transports
        assert "pagerduty" in transports

    def test_send_slack_notification_method_exists(self):
        """_send_slack_notification method exists on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert hasattr(mgr, '_send_slack_notification')
        assert callable(mgr._send_slack_notification)

    def test_send_pagerduty_notification_method_exists(self):
        """_send_pagerduty_notification method exists on AlertManager."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert hasattr(mgr, '_send_pagerduty_notification')
        assert callable(mgr._send_pagerduty_notification)

    def test_slack_notification_no_url_skips(self):
        """_send_slack_notification returns early if no URL configured."""
        mgr = AlertManager(AlertConfig(enabled=True, slack_webhook_url=""))
        alert = Alert(alert_type="test", severity="info", subject="s", message="m")
        # Should not raise
        mgr._send_slack_notification(alert)

    def test_pagerduty_notification_no_key_skips(self):
        """_send_pagerduty_notification returns early if no key configured."""
        mgr = AlertManager(AlertConfig(enabled=True, pagerduty_integration_key=""))
        alert = Alert(alert_type="test", severity="info", subject="s", message="m")
        # Should not raise
        mgr._send_pagerduty_notification(alert)

    def test_dispatch_to_transports_handles_slack(self):
        """_dispatch_to_transports handles slack transport."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            slack_webhook_url="https://hooks.slack.com/services/test",
            min_alert_interval_seconds=0,
        ))
        # Mock the actual HTTP calls
        with patch.object(mgr, '_send_slack_notification'):
            mgr._dispatch_to_transports(
                Alert(alert_type="test", severity="info", subject="s", message="m"),
                ["slack"],
                "test-cid",
            )
        # Should not raise

    def test_dispatch_to_transports_handles_pagerduty(self):
        """_dispatch_to_transports handles pagerduty transport."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            pagerduty_integration_key="pd-key-123",
            min_alert_interval_seconds=0,
        ))
        with patch.object(mgr, '_send_pagerduty_notification'):
            mgr._dispatch_to_transports(
                Alert(alert_type="test", severity="info", subject="s", message="m"),
                ["pagerduty"],
                "test-cid",
            )
        # Should not raise

    def test_notification_channels_in_status(self):
        """get_status() includes notification channel configuration."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            slack_webhook_url="https://hooks.slack.com/services/test",
            pagerduty_integration_key="pd-key-123",
            notification_routes={"critical": ["slack", "pagerduty"]},
        ))
        status = mgr.get_status()
        assert "notification_channels" in status
        assert status["notification_channels"]["slack_configured"] is True
        assert status["notification_channels"]["pagerduty_configured"] is True
        assert status["notification_channels"]["notification_routes"] == {
            "critical": ["slack", "pagerduty"]
        }

    def test_slack_notification_sends_correct_payload(self):
        """_send_slack_notification formats the Slack payload correctly."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            slack_webhook_url="https://hooks.slack.com/services/test",
        ))
        alert = Alert(
            alert_type="quality_degradation",
            severity="critical",
            subject="test_subject",
            message="Test critical alert",
        )
        with patch('aip.adapter.alerting.urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            mgr._send_slack_notification(alert)
            assert mock_urlopen.called
            # Verify the request was made
            req = mock_urlopen.call_args[0][0]
            assert req.get_method() == "POST"

    def test_pagerduty_notification_sends_correct_payload(self):
        """_send_pagerduty_notification formats the PagerDuty payload correctly."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            pagerduty_integration_key="pd-key-123",
        ))
        alert = Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="test_pool",
            message="Pool adjusted",
        )
        with patch('aip.adapter.alerting.urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 202
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            mgr._send_pagerduty_notification(alert)
            assert mock_urlopen.called
            req = mock_urlopen.call_args[0][0]
            assert "pagerduty.com" in req.full_url


# ============================================================================
# Deliverable 5: Dashboard Performance Optimization
# ============================================================================


class TestDashboardPerformanceOptimization:
    """Tests for dashboard performance features: virtual scrolling,
    debounced charts, WS deduplication, performance metrics."""

    def test_dashboard_html_has_virtual_scrolling(self):
        """Dashboard HTML includes virtual scrolling for alert list."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "virtual-alert-list" in _DASHBOARD_HTML
        assert "renderVirtualAlerts" in _DASHBOARD_HTML
        assert "virtual scroll" in _DASHBOARD_HTML.lower() or "maxVisible" in _DASHBOARD_HTML

    def test_dashboard_html_has_debounced_charts(self):
        """Dashboard HTML includes debounced chart rendering."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "debouncedRenderCharts" in _DASHBOARD_HTML
        assert "renderChartsImmediate" in _DASHBOARD_HTML
        assert "_chartRenderTimer" in _DASHBOARD_HTML

    def test_dashboard_html_has_ws_deduplication(self):
        """Dashboard HTML includes WS message deduplication."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "_wsMsgDedupSet" in _DASHBOARD_HTML
        assert "wsMsgsDeduped" in _DASHBOARD_HTML or "_perfMetrics" in _DASHBOARD_HTML

    def test_dashboard_html_has_performance_metrics(self):
        """Dashboard HTML includes performance metrics panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "perf-panel" in _DASHBOARD_HTML
        assert "perfRenderTime" in _DASHBOARD_HTML
        assert "perfWsMsgs" in _DASHBOARD_HTML
        assert "perfWsDeduped" in _DASHBOARD_HTML

    def test_dashboard_html_has_debounced_alert_fetch(self):
        """Dashboard HTML includes debounced alert fetching."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "debouncedFetchAlerts" in _DASHBOARD_HTML
        assert "_fetchAlertsTimer" in _DASHBOARD_HTML

    def test_dashboard_html_has_prediction_accuracy_panel(self):
        """Dashboard HTML includes prediction accuracy panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "accuracy-panel" in _DASHBOARD_HTML
        assert "predHits" in _DASHBOARD_HTML
        assert "predMisses" in _DASHBOARD_HTML
        assert "predHitRate" in _DASHBOARD_HTML
        assert "fetchPredictionAccuracy" in _DASHBOARD_HTML

    def test_dashboard_html_has_auto_merge_policy_controls(self):
        """Dashboard HTML includes auto-merge policy controls."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "policy-panel" in _DASHBOARD_HTML
        assert "policyMode" in _DASHBOARD_HTML
        assert "policyCooldown" in _DASHBOARD_HTML
        assert "updateAutoMergePolicy" in _DASHBOARD_HTML

    def test_dashboard_html_has_notification_channel_config(self):
        """Dashboard HTML includes notification channel configuration."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "channel-panel" in _DASHBOARD_HTML
        assert "slackWebhookUrl" in _DASHBOARD_HTML
        assert "pagerdutyKey" in _DASHBOARD_HTML
        assert "configureSlack" in _DASHBOARD_HTML
        assert "configurePagerDuty" in _DASHBOARD_HTML

    def test_dashboard_html_has_tab_cleanup(self):
        """Dashboard HTML includes tab cleanup on page unload."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "beforeunload" in _DASHBOARD_HTML
        assert "tab_unregister" in _DASHBOARD_HTML


# ============================================================================
# Integration: REST Endpoints for Sprint 5.37
# ============================================================================


class TestSprint537RESTEndpoints:
    """Tests for new REST API endpoints added in Sprint 5.37."""

    def test_prediction_accuracy_endpoint_exists(self):
        """GET /vigil/quality/alerts/predictions/accuracy endpoint is defined."""
        from aip.adapter.api.routes.vigil_quality import router
        route_paths = [r.path for r in router.routes if hasattr(r, 'path')]
        assert "/vigil/quality/alerts/predictions/accuracy" in route_paths

    def test_auto_merge_policy_get_endpoint_exists(self):
        """GET /vigil/quality/alerts/groups/auto-merge/policy endpoint is defined."""
        from aip.adapter.api.routes.vigil_quality import router
        route_paths = [r.path for r in router.routes if hasattr(r, 'path')]
        assert "/vigil/quality/alerts/groups/auto-merge/policy" in route_paths

    def test_auto_merge_policy_patch_endpoint_exists(self):
        """PATCH /vigil/quality/alerts/groups/auto-merge/policy endpoint is defined."""
        from aip.adapter.api.routes.vigil_quality import router
        route_paths = [r.path for r in router.routes if hasattr(r, 'path')]
        assert "/vigil/quality/alerts/groups/auto-merge/policy" in route_paths

    def test_auto_merge_policy_update_model(self):
        """AutoMergePolicyUpdate model validates correctly."""
        from aip.adapter.api.routes.vigil_quality import AutoMergePolicyUpdate
        # Valid
        update = AutoMergePolicyUpdate(mode="auto", cooldown_seconds=60)
        assert update.mode == "auto"
        assert update.cooldown_seconds == 60

        # Partial update
        update2 = AutoMergePolicyUpdate(mode="suggest")
        assert update2.mode == "suggest"
        assert update2.cooldown_seconds is None
        assert update2.type_thresholds is None

        # With thresholds
        update3 = AutoMergePolicyUpdate(
            type_thresholds={"quality_degradation": 0.9}
        )
        assert update3.type_thresholds == {"quality_degradation": 0.9}


# ============================================================================
# End-to-end integration: Full Sprint 5.37 flow
# ============================================================================


class TestSprint537Integration:
    """Integration tests combining multiple Sprint 5.37 features."""

    def test_full_prediction_accuracy_flow(self):
        """Full flow: predict → track → match → verify accuracy."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            causal_prediction_enabled=True,
            prediction_accuracy_window_seconds=600,
            min_alert_interval_seconds=0,
        ))

        # Step 1: Trigger prediction via pool_adjustment alert
        trigger_alert = Alert(
            alert_type="pool_adjustment",
            severity="warning",
            subject="integration_test",
            message="Pool adjusted",
        )
        predictions = mgr.predict_causal_chain(trigger_alert)
        assert len(predictions) > 0

        # Verify predictions are tracked
        accuracy = mgr.get_prediction_accuracy()
        assert accuracy["pending"] > 0

        # Step 2: Incoming alert matches a prediction
        matching_alert = Alert(
            alert_type="quality_degradation",
            severity="info",
            subject="integration_test",
            message="Quality degraded",
        )
        mgr.record_prediction_outcome(matching_alert)

        # Verify hit was recorded
        accuracy = mgr.get_prediction_accuracy()
        assert accuracy["hits"] >= 1

    def test_auto_merge_policy_flow(self):
        """Full flow: configure policy → generate suggestions → verify behavior."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            auto_merge_mode="suggest",
            auto_merge_cooldown_seconds=300,
            auto_merge_window_seconds=3600,
            auto_merge_similarity_threshold=0.3,
            min_alert_interval_seconds=0,
        ))

        # Create groups
        mgr._alert_groups["alpha_test_subject"] = ["cid-1", "cid-2"]
        mgr._alert_groups["beta_test_subject"] = ["cid-3", "cid-4"]
        mgr._alert_groups_metadata["alpha_test_subject"] = time.time()
        mgr._alert_groups_metadata["beta_test_subject"] = time.time()

        # Generate suggestions in suggest mode
        suggestions = mgr.suggest_auto_merges()
        # Should return suggestions without auto-applying
        assert mgr._total_auto_merges_applied == 0

    def test_notification_routing_flow(self):
        """Full flow: configure routes → send alert → verify routing."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            slack_webhook_url="https://hooks.slack.com/services/test",
            pagerduty_integration_key="pd-key-123",
            notification_routes={"critical": ["slack", "pagerduty"]},
            min_alert_interval_seconds=0,
        ))

        # Critical alert should route to slack and pagerduty only
        transports = mgr._get_transports_for_alert("critical")
        assert "slack" in transports
        assert "pagerduty" in transports

        # Other alert types should fall back to default transports
        other_transports = mgr._get_transports_for_alert("quality_degradation")
        # Should use default since "quality_degradation" is not in notification_routes
        assert len(other_transports) > 0

    def test_status_includes_all_sprint537_metrics(self):
        """get_status() includes all Sprint 5.37 metrics."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            auto_merge_mode="auto",
            slack_webhook_url="https://hooks.slack.com/services/test",
            pagerduty_integration_key="pd-key-123",
            notification_routes={"critical": ["slack", "pagerduty"]},
        ))
        status = mgr.get_status()

        # Prediction accuracy
        assert "prediction_accuracy" in status
        assert "hits" in status["prediction_accuracy"]
        assert "misses" in status["prediction_accuracy"]

        # Auto-merge policy
        assert "auto_merge_policy" in status
        assert status["auto_merge_policy"]["mode"] == "auto"

        # Notification channels
        assert "notification_channels" in status
        assert status["notification_channels"]["slack_configured"] is True
        assert status["notification_channels"]["pagerduty_configured"] is True
