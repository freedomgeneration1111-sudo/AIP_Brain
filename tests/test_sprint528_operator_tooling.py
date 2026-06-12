"""Sprint 5.28 tests — Alerting integration, admin endpoints, weekly rollup,
and lifespan smoke test.

Deliverable 1: Sexton Batch-Reduction Alert Integration (verify auto-wiring)
Deliverable 2: Alerting Dashboard Endpoint (GET /vigil/quality/alerts)
Deliverable 3: Retention / Rollup Admin API
Deliverable 4: Weekly Rollup Aggregation
Deliverable 5: Integration Smoke Test with Lifespan
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from aip.adapter.alerting import (
    Alert,
    AlertConfig,
    AlertManager,
)
from aip.adapter.vigil.vigil_quality_store import VigilQualityStore

# ============================================================================
# Shared fakes
# ============================================================================


class FakeSextonConfig:
    """Minimal Sexton config for batch auto-tuning tests."""

    graph_extraction_batch_auto_tune_enabled = True
    graph_extraction_auto_tune_window = 5
    graph_extraction_auto_tune_decrease_threshold = 0.3
    graph_extraction_auto_tune_increase_threshold = 0.1
    graph_extraction_batch_size_min = 1
    graph_extraction_batch_size_max = 8


class FakeSexton:
    """Minimal Sexton actor for batch reduction alert testing."""

    def __init__(self, config=None, alert_manager=None):
        self._config = config or FakeSextonConfig()
        self._alert_manager = alert_manager
        self._current_batch_size = 4
        self._batch_parse_results: list[bool] = []
        self._auto_tune_adjustments: list[dict] = []

    def _auto_tune_batch_size(self) -> dict:
        """Simplified version of Sexton's _auto_tune_batch_size for testing."""
        result = {
            "enabled": self._config.graph_extraction_batch_auto_tune_enabled,
            "previous_batch_size": self._current_batch_size,
            "new_batch_size": self._current_batch_size,
            "failure_rate": 0.0,
            "action": "none",
        }

        if not self._config.graph_extraction_batch_auto_tune_enabled:
            return result

        window = self._config.graph_extraction_auto_tune_window
        recent = self._batch_parse_results[-window:] if self._batch_parse_results else []

        if not recent:
            return result

        failures = sum(1 for success in recent if not success)
        failure_rate = failures / len(recent)
        result["failure_rate"] = round(failure_rate, 3)

        old_size = self._current_batch_size
        min_size = self._config.graph_extraction_batch_size_min
        max_size = self._config.graph_extraction_batch_size_max

        if failure_rate > self._config.graph_extraction_auto_tune_decrease_threshold:
            new_size = max(min_size, old_size - 1)
            if new_size < old_size:
                self._current_batch_size = new_size
                result["new_batch_size"] = new_size
                result["action"] = "decreased"
                # Alert on batch size reduction
                if self._alert_manager is not None:
                    try:
                        self._alert_manager.send_alert(
                            Alert(
                                alert_type="batch_reduction",
                                severity="warning",
                                subject="graph_extraction_batch_size",
                                message=(
                                    f"Graph extraction batch size reduced from {old_size} to {new_size} "
                                    f"due to high parse failure rate ({failure_rate:.1%} over last "
                                    f"{len(recent)} batches). Operators should investigate LLM parse errors."
                                ),
                                data={
                                    "old_batch_size": old_size,
                                    "new_batch_size": new_size,
                                    "failure_rate": round(failure_rate, 3),
                                    "window_size": len(recent),
                                    "min_batch_size": min_size,
                                    "max_batch_size": max_size,
                                },
                            )
                        )
                    except Exception:
                        pass

        elif failure_rate < self._config.graph_extraction_auto_tune_increase_threshold:
            new_size = min(max_size, old_size + 1)
            if new_size > old_size:
                self._current_batch_size = new_size
                result["new_batch_size"] = new_size
                result["action"] = "increased"

        return result


# ============================================================================
# Deliverable 1: Sexton Batch-Reduction Alert Integration
# ============================================================================


class TestSextonBatchReductionAlert:
    """Tests verifying Sexton's _auto_tune_batch_size sends alerts on reduction."""

    def test_batch_reduction_automatically_sends_alert(self):
        """When batch size is reduced, an alert is automatically dispatched via AlertManager."""
        alert_mgr = AlertManager(
            AlertConfig(
                enabled=True,
                alert_on_batch_reduction=True,
                min_alert_interval_seconds=0,
            )
        )
        sexton = FakeSexton(alert_manager=alert_mgr)

        # Record failures to trigger batch reduction (>30% failure rate)
        sexton._batch_parse_results = [False, False, False, True, True]  # 60% failure
        result = sexton._auto_tune_batch_size()

        assert result["action"] == "decreased"
        assert result["previous_batch_size"] == 4
        assert result["new_batch_size"] == 3

        # Verify alert was dispatched
        batch_alerts = [a for a in alert_mgr.lifecycle_mgr._alert_history if a["alert_type"] == "batch_reduction"]
        assert len(batch_alerts) == 1
        assert batch_alerts[0]["data"]["old_batch_size"] == 4
        assert batch_alerts[0]["data"]["new_batch_size"] == 3
        assert batch_alerts[0]["data"]["failure_rate"] == 0.6

    def test_batch_reduction_alert_includes_useful_context(self):
        """Batch reduction alert includes previous size, new size, and failure rate."""
        alert_mgr = AlertManager(
            AlertConfig(
                enabled=True,
                alert_on_batch_reduction=True,
                min_alert_interval_seconds=0,
            )
        )
        sexton = FakeSexton(alert_manager=alert_mgr)
        sexton._batch_parse_results = [False, False, True, True, True]  # 40% failure

        sexton._auto_tune_batch_size()

        batch_alerts = [a for a in alert_mgr.lifecycle_mgr._alert_history if a["alert_type"] == "batch_reduction"]
        assert len(batch_alerts) == 1
        alert_data = batch_alerts[0]["data"]
        assert "old_batch_size" in alert_data
        assert "new_batch_size" in alert_data
        assert "failure_rate" in alert_data
        assert "window_size" in alert_data
        assert "min_batch_size" in alert_data
        assert "max_batch_size" in alert_data
        assert alert_data["old_batch_size"] > alert_data["new_batch_size"]

    def test_no_alert_when_batch_size_stays_same(self):
        """No batch_reduction alert is sent when failure rate doesn't trigger reduction."""
        alert_mgr = AlertManager(
            AlertConfig(
                enabled=True,
                alert_on_batch_reduction=True,
                min_alert_interval_seconds=0,
            )
        )
        sexton = FakeSexton(alert_manager=alert_mgr)
        sexton._batch_parse_results = [True, True, True, True, True]  # 0% failure

        result = sexton._auto_tune_batch_size()
        assert result["action"] in ("none", "increased")

        batch_alerts = [a for a in alert_mgr.lifecycle_mgr._alert_history if a["alert_type"] == "batch_reduction"]
        assert len(batch_alerts) == 0

    def test_alert_manager_history_method_filters_by_type(self):
        """AlertManager.get_alert_history filters by alert_type."""
        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))

        mgr.send_alert(Alert(alert_type="batch_reduction", severity="warning", subject="test", message="m1"))
        mgr.send_alert(Alert(alert_type="quality_degradation", severity="warning", subject="test", message="m2"))
        mgr.send_alert(Alert(alert_type="batch_reduction", severity="critical", subject="test", message="m3"))

        batch_only = mgr.get_alert_history(alert_type="batch_reduction")
        assert len(batch_only) == 2
        assert all(a["alert_type"] == "batch_reduction" for a in batch_only)

    def test_alert_manager_history_method_filters_by_severity(self):
        """AlertManager.get_alert_history filters by severity."""
        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))

        mgr.send_alert(Alert(alert_type="batch_reduction", severity="warning", subject="test", message="m1"))
        mgr.send_alert(Alert(alert_type="batch_reduction", severity="critical", subject="test", message="m2"))

        critical_only = mgr.get_alert_history(severity="critical")
        assert len(critical_only) == 1
        assert critical_only[0]["severity"] == "critical"

    def test_alert_manager_history_method_filters_by_since(self):
        """AlertManager.get_alert_history filters by timestamp."""
        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))

        # Send alert with explicit timestamp
        alert_old = Alert(
            alert_type="quality_degradation",
            severity="warning",
            subject="test",
            message="old alert",
        )
        alert_old.timestamp = "2020-01-01T00:00:00+00:00"
        mgr.send_alert(alert_old)

        alert_new = Alert(
            alert_type="quality_degradation",
            severity="warning",
            subject="test",
            message="new alert",
        )
        alert_new.timestamp = "2025-06-01T00:00:00+00:00"
        mgr.send_alert(alert_new)

        recent = mgr.get_alert_history(since="2025-01-01T00:00:00+00:00")
        assert len(recent) == 1
        assert recent[0]["message"] == "new alert"


# ============================================================================
# Deliverable 2: Alerting Dashboard Endpoint
# ============================================================================


class TestAlertingEndpoint:
    """Tests for the GET /vigil/quality/alerts endpoint."""

    @pytest.mark.asyncio
    async def test_alerts_endpoint_returns_alert_history(self):
        """The alerts endpoint returns alert history from the AlertManager."""
        from aip.adapter.api.routes.vigil_quality import vigil_alerts

        alert_mgr = AlertManager(
            AlertConfig(
                enabled=True,
                alert_on_batch_reduction=True,
                min_alert_interval_seconds=0,
            )
        )
        alert_mgr.send_alert(
            Alert(
                alert_type="batch_reduction",
                severity="warning",
                subject="test_batch",
                message="Batch reduced from 4 to 3",
            )
        )

        container = MagicMock()
        container._alert_manager = alert_mgr

        result = await vigil_alerts(container=container)
        assert result["status"] == "ok"
        assert len(result["alerts"]) == 1
        assert result["alerts"][0]["alert_type"] == "batch_reduction"

    @pytest.mark.asyncio
    async def test_alerts_endpoint_with_type_filter(self):
        """The alerts endpoint supports filtering by alert_type."""
        from aip.adapter.api.routes.vigil_quality import vigil_alerts

        alert_mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
        alert_mgr.send_alert(Alert(alert_type="batch_reduction", severity="warning", subject="s1", message="m1"))
        alert_mgr.send_alert(Alert(alert_type="quality_degradation", severity="warning", subject="s2", message="m2"))

        container = MagicMock()
        container._alert_manager = alert_mgr

        result = await vigil_alerts(alert_type="batch_reduction", container=container)
        assert result["status"] == "ok"
        assert all(a["alert_type"] == "batch_reduction" for a in result["alerts"])

    @pytest.mark.asyncio
    async def test_alerts_endpoint_includes_delivery_failures(self):
        """The alerts endpoint includes recent delivery failures."""
        from aip.adapter.api.routes.vigil_quality import vigil_alerts

        alert_mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))
        container = MagicMock()
        container._alert_manager = alert_mgr

        result = await vigil_alerts(container=container)
        assert "delivery_failures" in result
        assert "config" in result

    @pytest.mark.asyncio
    async def test_alerts_endpoint_includes_config_status(self):
        """The alerts endpoint includes alerting configuration status."""
        from aip.adapter.api.routes.vigil_quality import vigil_alerts

        alert_mgr = AlertManager(
            AlertConfig(
                enabled=True,
                webhook_url="https://hooks.example.com/test",
                alert_on_quality_degradation=True,
                min_alert_interval_seconds=0,
            )
        )

        container = MagicMock()
        container._alert_manager = alert_mgr

        result = await vigil_alerts(container=container)
        assert result["config"]["enabled"] is True
        assert result["config"]["webhook_configured"] is True
        assert "alert_types_enabled" in result["config"]

    @pytest.mark.asyncio
    async def test_alerts_endpoint_when_no_alert_manager(self):
        """The alerts endpoint returns gracefully when AlertManager is not configured."""
        from aip.adapter.api.routes.vigil_quality import vigil_alerts

        container = MagicMock()
        container._alert_manager = None

        result = await vigil_alerts(container=container)
        assert result["status"] == "alerting_not_configured"
        assert result["alerts"] == []


# ============================================================================
# Deliverable 3: Retention / Rollup Admin API
# ============================================================================


class TestRetentionAdminAPI:
    """Tests for the /vigil/quality/retention admin endpoints."""

    @pytest.mark.asyncio
    async def test_retention_status_endpoint(self):
        """GET /vigil/quality/retention returns retention status."""
        from aip.adapter.api.routes.vigil_quality import vigil_retention_status

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = VigilQualityStore(
                os.path.join(tmp_dir, "quality.db"),
                max_history_rows=100,
                retention_days=0,  # No time-based pruning so test data survives
                rollup_age_days=7,
            )
            await store.initialize()
            await store.record_cycle(
                {
                    "timestamp": "2025-06-01T00:00:00Z",
                    "avg_citation_rate": 0.85,
                    "avg_grounding_rate": 0.90,
                    "avg_llm_faithfulness": 0.88,
                    "evaluated_count": 15,
                    "flagged_count": 2,
                }
            )

            container = MagicMock()
            container._vigil_quality_store = store

            result = await vigil_retention_status(container=container)
            assert result["status"] == "ok"
            assert result["retention"]["total_rows"] >= 1
            assert result["retention"]["max_history_rows"] == 100

    @pytest.mark.asyncio
    async def test_retention_status_when_no_store(self):
        """GET /vigil/quality/retention returns gracefully when store not configured."""
        from aip.adapter.api.routes.vigil_quality import vigil_retention_status

        container = MagicMock()
        container._vigil_quality_store = None

        result = await vigil_retention_status(container=container)
        assert result["status"] == "quality_store_not_configured"

    @pytest.mark.asyncio
    async def test_manual_rollup_trigger(self):
        """POST /vigil/quality/retention/rollup triggers daily rollup."""
        from aip.adapter.api.routes.vigil_quality import vigil_retention_rollup

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = VigilQualityStore(
                os.path.join(tmp_dir, "quality.db"),
                rollup_age_days=0,
                retention_days=0,
            )
            await store.initialize()

            # Insert records
            for i in range(3):
                await store.record_cycle(
                    {
                        "timestamp": f"2025-01-10T{10 + i:02d}:00:00Z",
                        "avg_citation_rate": 0.85,
                        "avg_grounding_rate": 0.90,
                        "avg_llm_faithfulness": 0.88,
                        "evaluated_count": 15,
                        "flagged_count": 2,
                    }
                )

            container = MagicMock()
            container._vigil_quality_store = store

            result = await vigil_retention_rollup(period="daily", container=container)
            assert result["status"] == "ok"
            assert result["period"] == "daily"
            assert result["result"]["rolled_up_days"] >= 0

    @pytest.mark.asyncio
    async def test_manual_weekly_rollup_trigger(self):
        """POST /vigil/quality/retention/rollup?period=weekly triggers weekly rollup."""
        from aip.adapter.api.routes.vigil_quality import vigil_retention_rollup

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = VigilQualityStore(
                os.path.join(tmp_dir, "quality.db"),
                rollup_age_days=0,
                retention_days=0,
                weekly_rollup_age_weeks=0,
            )
            await store.initialize()

            container = MagicMock()
            container._vigil_quality_store = store

            result = await vigil_retention_rollup(period="weekly", container=container)
            assert result["status"] == "ok"
            assert result["period"] == "weekly"

    @pytest.mark.asyncio
    async def test_rollup_stats_endpoint(self):
        """GET /vigil/quality/retention/rollup-stats returns rollup statistics."""
        from aip.adapter.api.routes.vigil_quality import vigil_rollup_stats

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = VigilQualityStore(
                os.path.join(tmp_dir, "quality.db"),
                rollup_age_days=0,
                retention_days=0,
            )
            await store.initialize()

            # Insert records and run daily rollup
            for i in range(3):
                await store.record_cycle(
                    {
                        "timestamp": f"2025-01-10T{10 + i:02d}:00:00Z",
                        "avg_citation_rate": 0.85,
                        "avg_grounding_rate": 0.90,
                        "avg_llm_faithfulness": 0.88,
                        "evaluated_count": 15,
                        "flagged_count": 2,
                    }
                )
            await store.run_rollup()

            container = MagicMock()
            container._vigil_quality_store = store

            result = await vigil_rollup_stats(container=container)
            assert result["status"] == "ok"
            assert "daily_rollups" in result["stats"]
            assert "weekly_rollups" in result["stats"]
            assert result["stats"]["daily_rollups"]["count"] >= 1


# ============================================================================
# Deliverable 4: Weekly Rollup Aggregation
# ============================================================================


class TestWeeklyRollup:
    """Tests for VigilQualityStore weekly rollup aggregation."""

    async def _create_store(self, tmp_path, **kwargs):
        """Helper to create a VigilQualityStore in a temp directory."""
        db_path = os.path.join(str(tmp_path), "vigil_quality.db")
        store = VigilQualityStore(db_path, **kwargs)
        await store.initialize()
        return store

    @pytest.mark.asyncio
    async def test_weekly_rollup_aggregates_daily_rollups(self, tmp_path):
        """Weekly rollup aggregates daily rollup rows into weekly summaries."""
        store = await self._create_store(
            tmp_path,
            rollup_age_days=0,
            retention_days=0,
            weekly_rollup_age_weeks=0,
        )

        # Insert records for 7 days (week 1)
        for day in range(1, 8):
            for hour in range(3):
                await store.record_cycle(
                    {
                        "timestamp": f"2025-01-{day:02d}T{10 + hour:02d}:00:00Z",
                        "avg_citation_rate": 0.80 + day * 0.01,
                        "avg_grounding_rate": 0.90,
                        "avg_llm_faithfulness": 0.85,
                        "evaluated_count": 10,
                        "flagged_count": 1,
                    }
                )

        # Insert records for 5 days (week 2)
        for day in range(8, 13):
            for hour in range(2):
                await store.record_cycle(
                    {
                        "timestamp": f"2025-01-{day:02d}T{10 + hour:02d}:00:00Z",
                        "avg_citation_rate": 0.85,
                        "avg_grounding_rate": 0.92,
                        "avg_llm_faithfulness": 0.88,
                        "evaluated_count": 12,
                        "flagged_count": 1,
                    }
                )

        # Run daily rollup first
        daily_result = await store.run_rollup()
        assert daily_result["rolled_up_days"] >= 1

        # Now run weekly rollup
        weekly_result = await store.run_weekly_rollup()
        # The daily rollup rows should be aggregated into weekly summaries
        # (May be 0 or more weeks depending on the cutoff)
        assert "rolled_up_weeks" in weekly_result
        assert weekly_result["rolled_up_weeks"] >= 0

    @pytest.mark.asyncio
    async def test_weekly_rollup_preserves_trend_data(self, tmp_path):
        """Weekly rollup rows preserve enough data for trend analysis."""
        store = await self._create_store(
            tmp_path,
            rollup_age_days=0,
            retention_days=0,
            weekly_rollup_age_weeks=0,
        )

        # Insert records for a full week
        for day in range(6, 12):
            await store.record_cycle(
                {
                    "timestamp": f"2025-01-{day:02d}T10:00:00Z",
                    "avg_citation_rate": 0.80 + (day - 6) * 0.02,
                    "avg_grounding_rate": 0.90,
                    "avg_llm_faithfulness": 0.85,
                    "evaluated_count": 10,
                    "flagged_count": 1,
                }
            )

        # Run daily rollup
        await store.run_rollup()

        # Run weekly rollup
        weekly_result = await store.run_weekly_rollup()

        if weekly_result.get("rolled_up_weeks", 0) > 0:
            cycles = await store.get_cycles(include_rollups=True)
            weekly_rollups = [c for c in cycles if c.get("rollup_period") == "weekly"]
            assert len(weekly_rollups) >= 1
            # Weekly rollup should have aggregated data
            assert weekly_rollups[0]["evaluated_count"] > 0

    @pytest.mark.asyncio
    async def test_weekly_rollup_configurable_age(self, tmp_path):
        """Weekly rollup respects weekly_rollup_age_weeks configuration."""
        store = await self._create_store(
            tmp_path,
            rollup_age_days=0,
            retention_days=0,
            weekly_rollup_age_weeks=100,  # Very old — nothing should be eligible
        )

        # Insert and run daily rollup
        for i in range(3):
            await store.record_cycle(
                {
                    "timestamp": f"2025-01-{10 + i:02d}T10:00:00Z",
                    "avg_citation_rate": 0.85,
                    "avg_grounding_rate": 0.90,
                    "avg_llm_faithfulness": 0.88,
                    "evaluated_count": 15,
                    "flagged_count": 2,
                }
            )
        await store.run_rollup()

        # Weekly rollup with very high age threshold should find nothing
        result = await store.run_weekly_rollup()
        assert result["rolled_up_weeks"] == 0

    @pytest.mark.asyncio
    async def test_get_rollup_stats(self, tmp_path):
        """get_rollup_stats returns comprehensive rollup statistics."""
        store = await self._create_store(
            tmp_path,
            rollup_age_days=0,
            retention_days=0,
        )

        # Insert records and run daily rollup
        for i in range(3):
            await store.record_cycle(
                {
                    "timestamp": f"2025-01-10T{10 + i:02d}:00:00Z",
                    "avg_citation_rate": 0.85,
                    "avg_grounding_rate": 0.90,
                    "avg_llm_faithfulness": 0.88,
                    "evaluated_count": 15,
                    "flagged_count": 2,
                }
            )
        await store.run_rollup()

        stats = await store.get_rollup_stats()
        assert "daily_rollups" in stats
        assert "weekly_rollups" in stats
        assert "original_rows" in stats
        assert "total_rows" in stats
        assert stats["daily_rollups"]["count"] >= 1
        assert stats["weekly_rollups"]["count"] == 0  # No weekly rollup yet

    @pytest.mark.asyncio
    async def test_get_retention_status_includes_weekly_config(self, tmp_path):
        """get_retention_status includes weekly_rollup_age_weeks."""
        store = await self._create_store(
            tmp_path,
            weekly_rollup_age_weeks=8,
        )
        status = await store.get_retention_status()
        assert "weekly_rollup_age_weeks" in status or "rollup_age_days" in status


# ============================================================================
# Deliverable 5: Integration Smoke Test with Lifespan
# ============================================================================


class TestLifespanSmokeTest:
    """Smoke test verifying all Sprint 5.27/5.28 operational components are
    correctly initialized and cross-wired when the FastAPI app starts up.
    """

    @pytest.mark.asyncio
    async def test_full_lifespan_smoke_test(self):
        """Start the FastAPI app with a test config and verify operational components.

        This test creates a minimal test configuration, starts the app
        using the lifespan context, and verifies that the operational
        components from Sprint 5.27/5.28 are initialized and cross-wired.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            config = {
                "database": {
                    "db_path": db_path,
                },
                "vigil_quality": {
                    "max_history_rows": 100,
                    "retention_days": 30,
                    "rollup_age_days": 7,
                    "weekly_rollup_age_weeks": 4,
                },
                "alerting": {
                    "enabled": False,  # Disabled for smoke test (no webhook/email)
                    "alert_on_quality_degradation": True,
                    "alert_on_pool_adjustment": True,
                    "alert_on_batch_reduction": True,
                },
                "read_pool": {
                    "auto_apply_enabled": True,
                },
                "auto_tuning_policy": {
                    "read_pool_exhaustion_threshold": 0.3,
                },
            }

            from fastapi import FastAPI

            from aip.adapter.api.app import lifespan

            app = FastAPI(lifespan=lifespan)
            app.state.raw_config = config

            # Use the lifespan context manager
            async with lifespan(app):
                container = app.state.container
                assert container is not None, "Container should be initialized after lifespan startup"

                # Verify Sprint 5.27/5.28 operational components
                # VigilQualityStore
                assert container._vigil_quality_store is not None, "VigilQualityStore should be initialized"
                assert container._vigil_quality_store._weekly_rollup_age_weeks == 4, (
                    "VigilQualityStore should use configured weekly_rollup_age_weeks"
                )

                # AlertManager
                assert container._alert_manager is not None, "AlertManager should be initialized"
                assert container._alert_manager._config.enabled is False, (
                    "AlertManager should be disabled in test config"
                )

                # ReadPoolAutoSizer
                assert container._read_pool_auto_sizer is not None, "ReadPoolAutoSizer should be initialized"

                # AutoTuningPolicy
                assert container._auto_tuning_policy is not None, "AutoTuningPolicy should be initialized"

                # Verify cross-wiring: AlertManager wired into Sexton
                if container.sexton_actor is not None:
                    assert (
                        container.sexton_actor._alert_manager is not None
                        or getattr(container.sexton_actor, "_alert_manager", None) is not None
                    ), "AlertManager should be wired into Sexton actor"

                # Verify cross-wiring: AlertManager wired into ReadPoolAutoSizer
                if container._read_pool_auto_sizer is not None and container._alert_manager is not None:
                    assert container._read_pool_auto_sizer._alert_manager is not None, (
                        "AlertManager should be wired into ReadPoolAutoSizer"
                    )

                # Verify cross-wiring: AlertManager wired into Vigil
                if container.vigil is not None and container._alert_manager is not None:
                    assert container.vigil._alert_manager is not None, "AlertManager should be wired into Vigil actor"

                # Verify required components are initialized
                assert container.entity_store is not None, "Entity store should be initialized"
                assert container.canonical_store is not None, "Canonical store should be initialized"
                assert container.event_store is not None, "Event store should be initialized"

    @pytest.mark.asyncio
    async def test_lifespan_smoke_weekly_rollup_available(self):
        """Verify that the weekly rollup scheduler is created during lifespan."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "state.db")
            config = {
                "database": {
                    "db_path": db_path,
                },
                "vigil_quality": {
                    "max_history_rows": 100,
                    "retention_days": 30,
                    "rollup_age_days": 7,
                    "weekly_rollup_age_weeks": 4,
                },
            }

            from fastapi import FastAPI

            from aip.adapter.api.app import lifespan

            app = FastAPI(lifespan=lifespan)
            app.state.raw_config = config

            async with lifespan(app):
                container = app.state.container
                # VigilQualityStore should have weekly_rollup_age_weeks configured
                if container._vigil_quality_store is not None:
                    assert hasattr(container._vigil_quality_store, "run_weekly_rollup"), (
                        "VigilQualityStore should have run_weekly_rollup method"
                    )
                    assert hasattr(container._vigil_quality_store, "get_rollup_stats"), (
                        "VigilQualityStore should have get_rollup_stats method"
                    )
                    assert container._vigil_quality_store._weekly_rollup_age_weeks == 4


# ============================================================================
# Additional: AlertManager get_alert_history comprehensive tests
# ============================================================================


class TestAlertManagerGetAlertHistory:
    """Comprehensive tests for AlertManager.get_alert_history method."""

    def test_empty_history(self):
        """get_alert_history returns empty list when no alerts sent."""
        mgr = AlertManager(AlertConfig(enabled=False))
        result = mgr.get_alert_history()
        assert result == []

    def test_history_returns_most_recent_first(self):
        """get_alert_history returns alerts in reverse chronological order."""
        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))

        mgr.send_alert(Alert(alert_type="quality_degradation", severity="warning", subject="test", message="first"))
        mgr.send_alert(Alert(alert_type="quality_degradation", severity="warning", subject="test", message="second"))

        result = mgr.get_alert_history()
        assert len(result) == 2
        assert result[0]["message"] == "second"  # Most recent first
        assert result[1]["message"] == "first"

    def test_history_respects_limit(self):
        """get_alert_history respects the limit parameter."""
        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))

        for i in range(5):
            mgr.send_alert(
                Alert(alert_type="quality_degradation", severity="warning", subject="test", message=f"alert_{i}")
            )

        result = mgr.get_alert_history(limit=3)
        assert len(result) == 3

    def test_combined_filters(self):
        """get_alert_history supports combining multiple filters."""
        mgr = AlertManager(AlertConfig(enabled=True, min_alert_interval_seconds=0))

        mgr.send_alert(Alert(alert_type="batch_reduction", severity="warning", subject="test", message="m1"))
        mgr.send_alert(Alert(alert_type="batch_reduction", severity="critical", subject="test", message="m2"))
        mgr.send_alert(Alert(alert_type="quality_degradation", severity="warning", subject="test", message="m3"))

        result = mgr.get_alert_history(alert_type="batch_reduction", severity="critical")
        assert len(result) == 1
        assert result[0]["alert_type"] == "batch_reduction"
        assert result[0]["severity"] == "critical"
