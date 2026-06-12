"""Sprint 5.26 tests — Alerting hardening, quality persistence, policy engine,
dashboard, and hot-reload safety.

Deliverable 1: Alerting Transport Hardening (retry, URL validation, SMTP auth, error history)
Deliverable 2: Historical Quality Data Persistence (SQLite for Vigil cycle reports)
Deliverable 3: Auto-Tuning Policy Engine (config-driven thresholds, validation)
Deliverable 4: Vigil Quality Dashboard Visualization (HTML/JS page)
Deliverable 5: Config Hot-Reload Safety Audit (validation, rejection, admin endpoint)
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from unittest.mock import MagicMock

import pytest

from aip.adapter.alerting import (
    Alert,
    AlertConfig,
    AlertManager,
    DeliveryFailure,
    validate_webhook_url,
)
from aip.adapter.auto_tuning_policy import (
    AutoTuningPolicy,
    apply_policy_to_auto_sizer,
    load_policy_from_config,
)
from aip.adapter.config_watcher import (
    _HOT_RELOADABLE_KEYS,
    ConfigRejectedEvent,
    ConfigWatcher,
)
from aip.adapter.read_pool import ReadPoolAutoSizer, ReadPoolHealth

# ============================================================================
# Shared fakes
# ============================================================================


class FakeReadPoolMixin:
    """Fake ReadPoolMixin for testing auto-apply and rollback."""

    def __init__(self, pool_size: int = 3):
        self._read_pool_size = pool_size
        self._read_pool = []
        self._read_pool_available = []


# ============================================================================
# Deliverable 1: Alerting Transport Hardening
# ============================================================================


class TestAlertingTransportHardening:
    """Tests for alerting transport hardening (Sprint 5.26)."""

    def test_webhook_url_validation_valid(self):
        """Valid webhook URLs pass validation."""
        is_valid, reason = validate_webhook_url("https://hooks.slack.com/services/abc123")
        assert is_valid is True
        assert reason == ""

    def test_webhook_url_validation_empty(self):
        """Empty webhook URLs fail validation."""
        is_valid, reason = validate_webhook_url("")
        assert is_valid is False
        assert "empty" in reason.lower()

    def test_webhook_url_validation_no_scheme(self):
        """URLs without scheme fail validation."""
        is_valid, reason = validate_webhook_url("hooks.slack.com/services/abc")
        assert is_valid is False
        assert "scheme" in reason.lower()

    def test_webhook_url_validation_bad_scheme(self):
        """URLs with non-http(s) scheme fail validation."""
        is_valid, reason = validate_webhook_url("ftp://hooks.slack.com/abc")
        assert is_valid is False
        assert "scheme" in reason.lower()

    def test_webhook_url_validation_http(self):
        """HTTP URLs are accepted (though HTTPS is preferred)."""
        is_valid, reason = validate_webhook_url("http://example.com/webhook")
        assert is_valid is True

    def test_smtp_auth_config(self):
        """AlertConfig supports SMTP authentication fields."""
        config = AlertConfig(
            smtp_username="user@example.com",
            smtp_password="secret",
            smtp_use_tls=False,
        )
        assert config.smtp_username == "user@example.com"
        assert config.smtp_password == "secret"
        assert config.smtp_use_tls is False

    def test_webhook_retry_config(self):
        """AlertConfig supports webhook retry configuration."""
        config = AlertConfig(
            webhook_max_retries=5,
            webhook_retry_base_delay_seconds=2.0,
        )
        assert config.webhook_max_retries == 5
        assert config.webhook_retry_base_delay_seconds == 2.0

    def test_delivery_failure_record(self):
        """DeliveryFailure records store all expected fields."""
        failure = DeliveryFailure(
            transport="webhook",
            alert_type="pool_adjustment",
            subject="read_pool.graph_store",
            error_message="Connection refused",
            retry_attempt=2,
            final=True,
        )
        d = failure.to_dict()
        assert d["transport"] == "webhook"
        assert d["alert_type"] == "pool_adjustment"
        assert d["retry_attempt"] == 2
        assert d["final"] is True
        assert "timestamp" in d

    def test_alert_manager_delivery_failure_history(self):
        """AlertManager tracks delivery failures."""
        config = AlertConfig(
            enabled=True,
            webhook_url="http://nonexistent.local:99999/hook",
            webhook_max_retries=2,
            webhook_retry_base_delay_seconds=0.01,  # Fast for testing
        )
        manager = AlertManager(config)

        alert = Alert(
            alert_type="quality_degradation",
            severity="warning",
            subject="test_failure_tracking",
            message="Test",
        )
        # This will fail to deliver but should record the failure
        # Sprint 5.30: dispatch is async, need to wait for background thread
        # With fast retry delays (0.01s base), 0.5s is plenty
        manager.send_alert(alert)
        import time

        time.sleep(0.5)  # Wait for background dispatch + fast retries

        # Should have at least one failure recorded
        assert manager.delivery_mgr._total_send_failures >= 1
        failures = manager.get_delivery_failures()
        assert len(failures) >= 1

    def test_alert_manager_validate_config_warnings(self):
        """AlertManager.validate_config returns warnings for misconfiguration."""
        # Enabled but no transports
        config = AlertConfig(enabled=True)
        manager = AlertManager(config)
        warnings = manager.validate_config()
        assert any("no transports" in w.lower() for w in warnings)

    def test_alert_manager_validate_config_webhook_invalid(self):
        """validate_config warns about invalid webhook URLs."""
        config = AlertConfig(enabled=True, webhook_url="not-a-url")
        manager = AlertManager(config)
        warnings = manager.validate_config()
        assert any("invalid" in w.lower() for w in warnings)

    def test_alert_manager_validate_config_email_no_smtp(self):
        """validate_config warns about email without smtp_host."""
        config = AlertConfig(enabled=True, email_to="ops@example.com")
        manager = AlertManager(config)
        warnings = manager.validate_config()
        assert any("smtp" in w.lower() for w in warnings)

    def test_alert_manager_validate_config_valid(self):
        """Valid configuration returns no warnings."""
        config = AlertConfig(
            enabled=True,
            webhook_url="https://hooks.slack.com/services/abc",
            email_to="ops@example.com",
            smtp_host="smtp.example.com",
        )
        manager = AlertManager(config)
        warnings = manager.validate_config()
        assert len(warnings) == 0

    def test_get_status_includes_new_fields(self):
        """get_status includes Sprint 5.26 fields."""
        config = AlertConfig(enabled=True, webhook_url="https://example.com/hook")
        manager = AlertManager(config)
        status = manager.get_status()

        assert "webhook_url_valid" in status
        assert "smtp_auth_configured" in status
        assert "total_webhook_retries" in status
        assert "recent_failures" in status
        assert "webhook_retry_config" in status
        assert status["webhook_retry_config"]["max_retries"] == 3

    def test_webhook_retry_tracks_retries(self):
        """Failed webhook delivery increments retry counter."""
        config = AlertConfig(
            enabled=True,
            webhook_url="http://nonexistent-host.local:99999/hook",
            webhook_max_retries=2,
            webhook_retry_base_delay_seconds=0.01,  # Fast for testing
        )
        manager = AlertManager(config)
        alert = Alert(alert_type="batch_reduction", severity="warning", subject="test", message="Test")

        # Sprint 5.30: dispatch is async, need to wait for background thread
        manager.send_alert(alert)
        import time

        time.sleep(0.5)  # Wait for background dispatch + fast retries
        # Should have attempted retries
        assert manager.delivery_mgr._total_webhook_retries >= 1

    def test_get_delivery_failures_with_filter(self):
        """get_delivery_failures supports transport filtering."""
        config = AlertConfig(
            enabled=True,
            webhook_url="http://nonexistent.local:99999/hook",
            webhook_max_retries=2,
            webhook_retry_base_delay_seconds=0.01,  # Fast for testing
        )
        manager = AlertManager(config)

        alert = Alert(alert_type="pool_adjustment", severity="info", subject="test", message="Test")
        # Sprint 5.30: dispatch is async, need to wait for background thread
        manager.send_alert(alert)
        import time

        time.sleep(0.5)  # Wait for background dispatch + fast retries

        webhook_failures = manager.get_delivery_failures(transport="webhook")
        email_failures = manager.get_delivery_failures(transport="email")

        assert len(webhook_failures) >= 1
        assert len(email_failures) == 0  # No email configured


# ============================================================================
# Deliverable 2: Historical Quality Data Persistence
# ============================================================================


class TestVigilQualityStore:
    """Tests for VigilQualityStore SQLite persistence (Sprint 5.26)."""

    async def _create_store(self, tmp_path):
        """Helper to create a VigilQualityStore in a temp directory."""
        from aip.adapter.vigil.vigil_quality_store import VigilQualityStore

        db_path = os.path.join(tmp_path, "vigil_quality.db")
        # Use retention_days=0 so test timestamps from 2025 are not pruned
        store = VigilQualityStore(db_path, retention_days=0)
        await store.initialize()
        return store

    @pytest.mark.asyncio
    async def test_initialize_creates_table(self, tmp_path):
        """VigilQualityStore creates the quality history table."""
        await self._create_store(tmp_path)

        # Verify table exists
        db_path = os.path.join(tmp_path, "vigil_quality.db")
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vigil_quality_history'")
            assert cursor.fetchone() is not None

    @pytest.mark.asyncio
    async def test_record_cycle_persists_data(self, tmp_path):
        """record_cycle persists a quality report to SQLite."""
        store = await self._create_store(tmp_path)

        report = {
            "timestamp": "2025-06-01T12:00:00Z",
            "avg_citation_rate": 0.85,
            "avg_grounding_rate": 0.90,
            "avg_llm_faithfulness": 0.88,
            "evaluated_count": 15,
            "flagged_count": 2,
            "hedging_detected_count": 1,
            "llm_eval_count": 3,
            "llm_hallucinations": 0,
            "cycle_elapsed_seconds": 1.5,
            "trend_indicators": {"citation_rate_trend": "stable"},
        }

        result = await store.record_cycle(report)
        assert result is True

        # Verify data is persisted
        cycles = await store.get_cycles()
        assert len(cycles) == 1
        assert cycles[0]["avg_citation_rate"] == 0.85
        assert cycles[0]["evaluated_count"] == 15

    @pytest.mark.asyncio
    async def test_get_cycles_with_since_filter(self, tmp_path):
        """get_cycles supports 'since' filtering."""
        store = await self._create_store(tmp_path)

        await store.record_cycle(
            {
                "timestamp": "2025-05-01T00:00:00Z",
                "avg_citation_rate": 0.7,
                "avg_grounding_rate": 0.8,
                "avg_llm_faithfulness": 0.75,
                "evaluated_count": 10,
                "flagged_count": 3,
            }
        )
        await store.record_cycle(
            {
                "timestamp": "2025-06-01T00:00:00Z",
                "avg_citation_rate": 0.9,
                "avg_grounding_rate": 0.95,
                "avg_llm_faithfulness": 0.92,
                "evaluated_count": 20,
                "flagged_count": 1,
            }
        )

        cycles = await store.get_cycles(since="2025-05-15T00:00:00Z")
        assert len(cycles) == 1
        assert cycles[0]["avg_citation_rate"] == 0.9

    @pytest.mark.asyncio
    async def test_get_cycles_with_last_n_filter(self, tmp_path):
        """get_cycles supports 'last_n_cycles' filtering."""
        store = await self._create_store(tmp_path)

        for i in range(5):
            await store.record_cycle(
                {
                    "timestamp": f"2025-06-0{i + 1}T00:00:00Z",
                    "avg_citation_rate": 0.8 + i * 0.02,
                    "avg_grounding_rate": 0.9,
                    "avg_llm_faithfulness": 0.85,
                    "evaluated_count": 10,
                    "flagged_count": 1,
                }
            )

        cycles = await store.get_cycles(last_n_cycles=3)
        assert len(cycles) == 3
        # Should be the 3 most recent
        assert abs(cycles[0]["avg_citation_rate"] - 0.84) < 0.001  # 3rd from last

    @pytest.mark.asyncio
    async def test_get_cycle_count(self, tmp_path):
        """get_cycle_count returns the total number of stored cycles."""
        store = await self._create_store(tmp_path)

        assert await store.get_cycle_count() == 0
        await store.record_cycle(
            {
                "timestamp": "2025-06-01T00:00:00Z",
                "avg_citation_rate": 0.85,
                "avg_grounding_rate": 0.9,
                "avg_llm_faithfulness": 0.88,
                "evaluated_count": 10,
                "flagged_count": 2,
            }
        )
        assert await store.get_cycle_count() == 1

    @pytest.mark.asyncio
    async def test_schema_version(self, tmp_path):
        """Schema version is recorded in metadata table."""
        store = await self._create_store(tmp_path)
        assert await store.get_schema_version() >= 1

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_bad_db(self, tmp_path):
        """VigilQualityStore handles DB errors gracefully."""
        import sqlite3

        from aip.adapter.vigil.vigil_quality_store import VigilQualityStore

        # Point to a nonexistent directory
        store = VigilQualityStore("/nonexistent/path/quality.db")
        # Initialize should not crash — it catches and logs the error
        await store.initialize()
        # Operations on an uninitialized store should either return empty
        # results or raise sqlite3.OperationalError (acceptable for an
        # unreachable path — the test verifies no unhandled crash).
        try:
            cycles = await store.get_cycles()
            assert isinstance(cycles, list)
        except sqlite3.OperationalError:
            pass  # Expected: DB file cannot be created

    @pytest.mark.asyncio
    async def test_vigil_with_quality_store(self, tmp_path):
        """Vigil can be constructed with a quality_store and persists cycles."""
        from aip.foundation.schemas import VigilConfig
        from aip.orchestration.actors.vigil import Vigil

        store = await self._create_store(tmp_path)

        class FakeVigilStore:
            async def record_vigil_check(self, **kwargs):
                pass

        class FakeCanonicalStore:
            pass

        class FakeEntityStore:
            pass

        class FakeModelProvider:
            pass

        class FakeTraceStore:
            pass

        vigil = Vigil(
            config=VigilConfig(),
            vigil_store=FakeVigilStore(),
            canonical_store=FakeCanonicalStore(),
            entity_store=FakeEntityStore(),
            model_provider=FakeModelProvider(),
            trace_store=FakeTraceStore(),
            quality_store=store,
        )

        # Vigil should have the quality store wired
        assert vigil._quality_store is store


# ============================================================================
# Deliverable 3: Auto-Tuning Policy Engine
# ============================================================================


class TestAutoTuningPolicy:
    """Tests for the auto-tuning policy engine (Sprint 5.26)."""

    def test_default_policy_is_valid(self):
        """Default policy values pass validation."""
        policy = AutoTuningPolicy()
        assert policy.is_valid()
        assert len(policy.validate()) == 0

    def test_invalid_threshold_detected(self):
        """Policy validation catches out-of-range thresholds."""
        policy = AutoTuningPolicy(read_pool_exhaustion_threshold=0.05)  # Below min 0.1
        errors = policy.validate()
        assert len(errors) >= 1
        assert any("0.05" in e for e in errors)

    def test_invalid_max_pool_detected(self):
        """Policy validation catches out-of-range max pool."""
        policy = AutoTuningPolicy(read_pool_auto_apply_max_pool=50)  # Above max 20
        errors = policy.validate()
        assert len(errors) >= 1
        assert any("50" in e for e in errors)

    def test_cross_field_validation_rollback_vs_threshold(self):
        """Policy validation catches rollback_healthy >= exhaustion_threshold."""
        policy = AutoTuningPolicy(
            read_pool_auto_rollback_healthy=0.4,
            read_pool_exhaustion_threshold=0.3,
        )
        errors = policy.validate()
        assert any("less than" in e.lower() for e in errors)

    def test_cross_field_validation_increase_vs_decrease(self):
        """Policy validation catches increase_threshold >= decrease_threshold."""
        policy = AutoTuningPolicy(
            graph_batch_increase_threshold=0.4,
            graph_batch_decrease_threshold=0.3,
        )
        errors = policy.validate()
        assert any("less than" in e.lower() for e in errors)

    def test_min_size_vs_max_size(self):
        """Policy validation catches min_size >= max_size."""
        policy = AutoTuningPolicy(
            graph_batch_min_size=5,
            graph_batch_max_size=4,
        )
        errors = policy.validate()
        assert any("less than" in e.lower() for e in errors)

    def test_load_policy_from_config(self):
        """load_policy_from_config reads [auto_tuning_policy] section."""
        config = {
            "auto_tuning_policy": {
                "read_pool_exhaustion_threshold": 0.4,
                "cooldown_seconds": 120,
            }
        }
        policy = load_policy_from_config(config)
        assert policy.read_pool_exhaustion_threshold == 0.4
        assert policy.cooldown_seconds == 120
        # Other values should be defaults
        assert policy.read_pool_auto_apply_consecutive == 5

    def test_load_policy_from_config_missing_section(self):
        """load_policy_from_config returns defaults when section is missing."""
        policy = load_policy_from_config({})
        assert policy.read_pool_exhaustion_threshold == 0.3
        assert policy.is_valid()

    def test_load_policy_from_config_invalid_type(self):
        """load_policy_from_config handles invalid types gracefully."""
        config = {
            "auto_tuning_policy": {
                "read_pool_exhaustion_threshold": "not_a_number",
            }
        }
        # Should not crash — just skip the invalid value
        policy = load_policy_from_config(config)
        # Should use default since the value was invalid
        assert policy.read_pool_exhaustion_threshold == 0.3

    def test_policy_to_dict(self):
        """AutoTuningPolicy.to_dict returns all values."""
        policy = AutoTuningPolicy()
        d = policy.to_dict()
        assert "read_pool_exhaustion_threshold" in d
        assert "graph_batch_decrease_threshold" in d
        assert "cooldown_seconds" in d

    def test_apply_policy_to_auto_sizer(self):
        """apply_policy_to_auto_sizer updates sizer parameters."""
        policy = AutoTuningPolicy(
            read_pool_auto_apply_max_pool=8,
            read_pool_auto_rollback_consecutive=3,
        )
        sizer = ReadPoolAutoSizer()
        applied = apply_policy_to_auto_sizer(policy, sizer)

        assert "auto_apply_max_pool" in applied
        assert sizer._auto_apply_max_pool == 8
        assert sizer._auto_rollback_consecutive_threshold == 3

    def test_apply_invalid_policy_skipped(self):
        """apply_policy_to_auto_sizer skips invalid policies."""
        policy = AutoTuningPolicy(read_pool_auto_apply_max_pool=50)  # Invalid
        sizer = ReadPoolAutoSizer()
        applied = apply_policy_to_auto_sizer(policy, sizer)
        assert len(applied) == 0  # Nothing applied


# ============================================================================
# Deliverable 4: Vigil Quality Dashboard Visualization
# ============================================================================


class TestVigilQualityDashboard:
    """Tests for the Vigil quality dashboard page (Sprint 5.26)."""

    @pytest.mark.asyncio
    async def test_dashboard_endpoint_exists(self):
        """The dashboard endpoint returns HTML content."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_dashboard

        container = MagicMock()
        result = await vigil_quality_dashboard(container=container)

        assert isinstance(result, str)
        assert "<!DOCTYPE html>" in result
        assert "Vigil Quality Dashboard" in result
        assert "canvas" in result  # Has chart canvas

    @pytest.mark.asyncio
    async def test_dashboard_has_javascript(self):
        """The dashboard page includes JavaScript for fetching data."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality_dashboard

        container = MagicMock()
        result = await vigil_quality_dashboard(container=container)

        assert "fetchData" in result
        assert "vigil/quality" in result

    @pytest.mark.asyncio
    async def test_quality_endpoint_with_persistent_store(self):
        """The quality endpoint uses persistent store when available."""
        from aip.adapter.api.routes.vigil_quality import vigil_quality
        from aip.adapter.vigil.vigil_quality_store import VigilQualityStore

        # Create a store with data
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = VigilQualityStore(os.path.join(tmp_dir, "quality.db"), retention_days=0)
            await store.initialize()
            await store.record_cycle(
                {
                    "timestamp": "2025-06-01T00:00:00Z",
                    "avg_citation_rate": 0.88,
                    "avg_grounding_rate": 0.92,
                    "avg_llm_faithfulness": 0.90,
                    "evaluated_count": 20,
                    "flagged_count": 1,
                }
            )

            container = MagicMock()
            container.vigil = MagicMock()
            container.vigil._cycle_report_history = []
            container.vigil._llm_faithfulness_telemetry = {}
            container.vigil.config = MagicMock()
            container.vigil.config.llm_faithfulness_enabled = True
            container.vigil.config.llm_faithfulness_model_slot = "evaluation"
            container.vigil.config.llm_faithfulness_sample_size = 10
            container._vigil_quality_store = store

            result = await vigil_quality(last_n_cycles=50, since=None, container=container)

            assert result["status"] == "ok"
            assert len(result["cycles"]) == 1
            assert result["data_source"] == "persistent_store"
            assert result["total_persisted_cycles"] == 1


# ============================================================================
# Deliverable 5: Config Hot-Reload Safety Audit
# ============================================================================


class TestConfigHotReloadSafety:
    """Tests for config hot-reload safety audit (Sprint 5.26)."""

    def test_auto_tuning_policy_is_hot_reloadable(self):
        """auto_tuning_policy is in the hot-reloadable keys set."""
        assert "auto_tuning_policy" in _HOT_RELOADABLE_KEYS

    def test_rejected_event_records_details(self):
        """ConfigRejectedEvent records key, value, and reason."""
        event = ConfigRejectedEvent(
            key="read_pool.pool_size",
            rejected_value=25,
            reason="pool_size must be between 1 and 20",
        )
        d = event.to_dict()
        assert d["key"] == "read_pool.pool_size"
        assert d["rejected_value"] == "25"
        assert "1 and 20" in d["reason"]
        assert "timestamp" in d

    def test_config_watcher_validates_pool_size(self):
        """ConfigWatcher validates pool_size values before applying."""
        watcher = ConfigWatcher(config_path="/nonexistent/config.toml")

        # Test valid pool_size
        is_valid, reason = watcher._validate_value("read_pool.pool_size", 5)
        assert is_valid is True

        # Test invalid pool_size (too high)
        is_valid, reason = watcher._validate_value("read_pool.pool_size", 25)
        assert is_valid is False
        assert "1 and 20" in reason

        # Test invalid pool_size (too low)
        is_valid, reason = watcher._validate_value("read_pool.pool_size", 0)
        assert is_valid is False

    def test_config_watcher_validates_batch_size(self):
        """ConfigWatcher validates batch_size values."""
        watcher = ConfigWatcher(config_path="/nonexistent/config.toml")

        is_valid, _ = watcher._validate_value("sexton.graph_extraction_batch_size", 4)
        assert is_valid is True

        is_valid, reason = watcher._validate_value("sexton.graph_extraction_batch_size", 20)
        assert is_valid is False

    def test_config_watcher_validates_per_store_pool_size(self):
        """ConfigWatcher validates per-store pool_size overrides."""
        watcher = ConfigWatcher(config_path="/nonexistent/config.toml")

        is_valid, _ = watcher._validate_value("read_pool.stores.graph_store.pool_size", 5)
        assert is_valid is True

        is_valid, reason = watcher._validate_value("read_pool.stores.graph_store.pool_size", 25)
        assert is_valid is False
        assert "1 and 20" in reason

    def test_config_watcher_validates_policy_values(self):
        """ConfigWatcher validates auto_tuning_policy values."""
        watcher = ConfigWatcher(config_path="/nonexistent/config.toml")

        is_valid, _ = watcher._validate_value("auto_tuning_policy.read_pool_exhaustion_threshold", 0.5)
        assert is_valid is True

        is_valid, reason = watcher._validate_value("auto_tuning_policy.read_pool_exhaustion_threshold", 0.05)
        assert is_valid is False

    def test_config_watcher_rejects_invalid_on_reload(self):
        """ConfigWatcher rejects invalid values and records them."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write("[read_pool]\npool_size = 3\n")
            f.flush()

            container = MagicMock()
            container.config = {"read_pool": {"pool_size": 3}}
            watcher = ConfigWatcher(
                config_path=f.name,
                container=container,
                poll_interval=0,
            )
            watcher._last_check = 0
            watcher._last_reload = 0

            # Modify file with invalid pool_size
            time.sleep(0.1)
            with open(f.name, "w") as f2:
                f2.write("[read_pool]\npool_size = 25\n")
                f2.flush()

            events = watcher.check_and_reload()

            # The invalid value should be rejected
            # (not in the applied events)
            for event in events:
                assert "pool_size" not in event.key or event.new_value != 25

            # Should have a rejection recorded
            assert watcher._total_rejected >= 1
            rejections = [r.to_dict() for r in watcher._rejected_history]
            assert any("25" in r.get("rejected_value", "") for r in rejections)

            os.unlink(f.name)

    def test_config_watcher_status_includes_rejections(self):
        """ConfigWatcher.get_status includes rejection history."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write("[read_pool]\npool_size = 3\n")
            f.flush()

            watcher = ConfigWatcher(config_path=f.name)
            status = watcher.get_status()

            assert "total_rejected" in status
            assert "recent_rejections" in status
            os.unlink(f.name)

    def test_admin_hot_reload_status_endpoint(self):
        """The admin hot-reload status endpoint returns expected structure."""
        from aip.adapter.api.routes.admin import get_hot_reload_status

        container = MagicMock()
        container._config_watcher = None
        container._alert_manager = None
        container.config = {}

        # This is an async function, need to run it
        import asyncio

        result = asyncio.run(get_hot_reload_status(container=container))

        assert "config_watcher" in result
        assert "auto_tuning_policy" in result
        assert "alerting_validation" in result


# ============================================================================
# Integration: Policy Engine + Auto-Sizer
# ============================================================================


class TestPolicyEngineIntegration:
    """Integration tests for policy engine with live auto-sizer."""

    def test_policy_updates_sizer_behavior(self):
        """Applying a policy changes auto-sizer behavior."""
        policy = AutoTuningPolicy(
            read_pool_auto_apply_consecutive=3,
            read_pool_auto_apply_max_increase=2,
            read_pool_auto_apply_max_pool=8,
            read_pool_auto_rollback_consecutive=4,
            read_pool_auto_rollback_healthy=0.1,
        )
        sizer = ReadPoolAutoSizer()
        applied = apply_policy_to_auto_sizer(policy, sizer)

        assert len(applied) > 0
        assert sizer._auto_apply_consecutive_threshold == 3
        assert sizer._auto_apply_max_increase == 2
        assert sizer._auto_apply_max_pool == 8

        # Test that the sizer now uses the updated thresholds
        store = FakeReadPoolMixin(pool_size=3)
        high_health: ReadPoolHealth = {
            "pool_size": 3,
            "pool_active": 3,
            "checkout_count": 100,
            "fallback_count": 50,
            "exhaustion_count": 50,
            "exhaustion_rate": 0.5,
            "avg_checkout_latency_ms": 5.0,
            "p95_checkout_latency_ms": 10.0,
            "recommendation": "",
        }
        # With consecutive threshold of 3, should auto-apply after 3 observations
        for _ in range(3):
            sizer.observe("test_store", high_health, store=store)

        # Should have auto-applied (increase)
        assert store._read_pool_size > 3
        # Max pool should be 8 (from policy)
        assert store._read_pool_size <= 8

    def test_policy_config_roundtrip(self):
        """Policy loaded from config, validated, and applied to sizer works."""
        config = {
            "auto_tuning_policy": {
                "read_pool_auto_apply_max_pool": 10,
                "graph_batch_max_size": 12,
                "cooldown_seconds": 30,
            }
        }

        policy = load_policy_from_config(config)
        assert policy.is_valid()

        sizer = ReadPoolAutoSizer()
        applied = apply_policy_to_auto_sizer(policy, sizer)
        assert "auto_apply_max_pool" in applied
        assert sizer._auto_apply_max_pool == 10
