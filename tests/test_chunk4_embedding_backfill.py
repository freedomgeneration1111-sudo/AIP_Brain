"""Chunk 4 — Automatic Embedding Backfill Verification + Repair Tests.

Validates:
  1. Sexton embedding backfill state machine transitions correctly
  2. Startup with embedding provider configured → backfill happens
  3. Startup without provider → reports not_configured
  4. Runtime model/provider assignment updates Sexton
  5. Unembedded items are detected correctly
  6. Successful embedding increases embedded/vector count
  7. Embedding failure is reported and does not fake success
  8. Health/dogfood endpoint reports embedding state honestly
  9. adapter/health.py no longer fakes "healthy" embedding status
  10. Mock/fake providers are reported as "degraded" not "healthy"
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aip.foundation.schemas import SextonConfig
from aip.orchestration.actors.sexton import Sexton

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_store() -> MagicMock:
    """Create a mock store with common async methods."""
    store = MagicMock()
    store.write_event = AsyncMock(return_value=None)
    store.query_events = AsyncMock(return_value=[])
    return store


def _make_mock_embedding_provider(is_mock: bool = True) -> object:
    """Create an embedding provider for testing.

    Args:
        is_mock: If True, returns a provider whose class name contains "Mock"
                 (simulating mock provider). If False, returns a provider whose
                 class name looks like a real provider.
    """
    if is_mock:

        class MockOllamaEmbeddingClient:
            async def embed(self, text: str) -> list[float]:
                return [0.1] * 768

        return MockOllamaEmbeddingClient()
    else:

        class OpenAICompatibleEmbeddingClient:
            model = "text-embedding-3-small"

            async def embed(self, text: str) -> list[float]:
                return [0.1] * 768

        return OpenAICompatibleEmbeddingClient()


def _make_full_sexton(embedding_provider=None, is_mock_provider=True) -> Sexton:
    """Create a Sexton with all core dependencies present."""
    event_store = _make_mock_store()
    if embedding_provider is None:
        embedding_provider = _make_mock_embedding_provider(is_mock=is_mock_provider)
    return Sexton(
        sexton_provider=MagicMock(),
        corpus_turn_store=MagicMock(),
        embedding_provider=embedding_provider,
        vector_store=MagicMock(),
        artifact_store=MagicMock(),
        ecs_store=MagicMock(),
        event_store=event_store,
        trace_store=MagicMock(),
        lexical_store=MagicMock(),
        config=SextonConfig(),
        graph_store=MagicMock(),
        alert_manager=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Test 1: Embedding backfill state machine
# ---------------------------------------------------------------------------


class TestEmbeddingBackfillStateMachine:
    """Embedding backfill state must transition correctly."""

    def test_not_configured_when_no_provider(self):
        """Sexton without embedding provider reports 'not_configured'."""
        actor = Sexton(event_store=_make_mock_store())
        assert actor._compute_embedding_backfill_state() == "not_configured"

    def test_degraded_when_mock_provider(self):
        """Sexton with mock embedding provider reports 'degraded'."""
        mock_provider = _make_mock_embedding_provider(is_mock=True)
        actor = _make_full_sexton(embedding_provider=mock_provider)
        state = actor._compute_embedding_backfill_state()
        assert state == "degraded", f"Mock provider should yield 'degraded', got '{state}'"

    def test_configured_idle_when_real_provider_but_no_cycle(self):
        """Sexton with real provider but no cycle run reports 'configured_idle'."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        state = actor._compute_embedding_backfill_state()
        assert state == "configured_idle", f"Real provider with no cycle should yield 'configured_idle', got '{state}'"

    def test_backfill_running_when_embedding_pass_active(self):
        """Sexton during active embedding pass reports 'backfill_running'."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        actor._embedding_pass_state["running"] = True
        state = actor._compute_embedding_backfill_state()
        assert state == "backfill_running"

    def test_partially_embedded_after_successful_cycle(self):
        """Sexton after a successful cycle with remaining unembedded reports 'partially_embedded'."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        actor._last_cycle_time = 12345.0
        actor._cycle_count = 1
        actor._last_unembedded_count = 100  # Still have unembedded turns
        actor._embedding_pass_state["last_batch_embedded"] = 50
        state = actor._compute_embedding_backfill_state()
        assert state == "partially_embedded"

    def test_embedded_when_no_unembedded(self):
        """Sexton with zero unembedded turns reports 'embedded'."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        actor._last_cycle_time = 12345.0
        actor._cycle_count = 1
        actor._last_unembedded_count = 0
        state = actor._compute_embedding_backfill_state()
        assert state == "embedded"

    def test_failed_when_provider_outage(self):
        """Sexton with provider outage and consecutive failures reports 'failed'."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        actor._provider_outage_detected = True
        actor._consecutive_embed_failures = 10
        state = actor._compute_embedding_backfill_state()
        assert state == "failed"


# ---------------------------------------------------------------------------
# Test 2: Startup with provider → backfill happens
# ---------------------------------------------------------------------------


class TestStartupWithProvider:
    """When an embedding provider is configured at startup, backfill should happen."""

    def test_startup_with_real_provider_schedules_backfill(self):
        """Sexton with real provider on startup should have backfill state 'configured_idle'."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        summary = actor.get_status_summary()
        # Before first cycle, state should be 'degraded' but backfill should be 'configured_idle'
        assert summary["embedding_backfill_state"] == "configured_idle"
        assert summary["state"] == "degraded"  # No cycle has run yet

    def test_after_cycle_backfill_progresses(self):
        """After a cycle with real provider, backfill state should progress."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        actor._last_cycle_time = 12345.0
        actor._cycle_count = 1
        actor._last_unembedded_count = 100
        actor._embedding_pass_state["last_batch_embedded"] = 50
        summary = actor.get_status_summary()
        assert summary["embedding_backfill_state"] == "partially_embedded"
        assert summary["state"] == "active"


# ---------------------------------------------------------------------------
# Test 3: Startup without provider → not_configured
# ---------------------------------------------------------------------------


class TestStartupWithoutProvider:
    """When no embedding provider is configured, status must say not_configured."""

    def test_no_provider_reports_not_configured(self):
        """Sexton without embedding provider reports 'not_configured' in backfill state."""
        actor = Sexton(
            sexton_provider=MagicMock(),
            embedding_provider=None,
            vector_store=MagicMock(),
            event_store=_make_mock_store(),
        )
        summary = actor.get_status_summary()
        assert summary["embedding_backfill_state"] == "not_configured"

    def test_mock_provider_reports_degraded_not_healthy(self):
        """Sexton with mock provider reports 'degraded' not 'healthy'."""
        mock_provider = _make_mock_embedding_provider(is_mock=True)
        actor = _make_full_sexton(embedding_provider=mock_provider)
        summary = actor.get_status_summary()
        assert summary["embedding_backfill_state"] == "degraded"
        # Should not claim to be "configured_idle" or "healthy"
        assert summary["embedding_backfill_state"] not in ("configured_idle", "healthy")


# ---------------------------------------------------------------------------
# Test 4: Runtime model/provider assignment updates Sexton
# ---------------------------------------------------------------------------


class TestRuntimeProviderAssignment:
    """Runtime embedding provider changes must propagate to Sexton."""

    def test_update_embedding_provider_sets_new_provider(self):
        """update_embedding_provider() updates the internal provider reference."""
        actor = _make_full_sexton(is_mock_provider=True)
        # Initially mock provider
        assert actor._compute_embedding_backfill_state() == "degraded"

        # Switch to real provider at runtime
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor.update_embedding_provider(real_provider)

        # Provider should be updated and backfill state should reflect it
        assert actor._embed is real_provider
        assert actor._embedding_backfill_state == "configured_idle"

    def test_update_provider_resets_outage_tracking(self):
        """update_embedding_provider() resets outage and failure tracking."""
        actor = _make_full_sexton(is_mock_provider=True)
        actor._provider_outage_detected = True
        actor._consecutive_embed_failures = 10

        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor.update_embedding_provider(real_provider)

        assert actor._provider_outage_detected is False
        assert actor._consecutive_embed_failures == 0
        assert actor._last_backfill_error is None

    def test_update_provider_to_none_goes_not_configured(self):
        """Setting provider to None transitions to not_configured."""
        actor = _make_full_sexton(is_mock_provider=False)
        assert actor._compute_embedding_backfill_state() == "configured_idle"

        actor.update_embedding_provider(None)
        assert actor._embedding_backfill_state == "not_configured"


# ---------------------------------------------------------------------------
# Test 5: Unembedded items detected correctly
# ---------------------------------------------------------------------------


class TestUnembeddedDetection:
    """Unembedded items must be discovered correctly."""

    @pytest.mark.asyncio
    async def test_refresh_queries_count_unembedded(self):
        """refresh_embedding_backfill_state() queries the corpus turn store."""
        actor = _make_full_sexton(is_mock_provider=False)
        actor._corpus_turns.count_unembedded = AsyncMock(return_value=42)

        await actor.refresh_embedding_backfill_state()

        actor._corpus_turns.count_unembedded.assert_called_once()
        assert actor._last_unembedded_count == 42

    @pytest.mark.asyncio
    async def test_refresh_updates_state_to_embedded(self):
        """When no unembedded remain, state becomes 'embedded'."""
        actor = _make_full_sexton(is_mock_provider=False)
        actor._last_cycle_time = 12345.0
        actor._cycle_count = 1
        actor._corpus_turns.count_unembedded = AsyncMock(return_value=0)

        await actor.refresh_embedding_backfill_state()

        assert actor._embedding_backfill_state == "embedded"

    @pytest.mark.asyncio
    async def test_refresh_updates_state_to_partially_embedded(self):
        """When unembedded remain, state becomes 'partially_embedded'."""
        actor = _make_full_sexton(is_mock_provider=False)
        actor._last_cycle_time = 12345.0
        actor._cycle_count = 1
        actor._corpus_turns.count_unembedded = AsyncMock(return_value=100)

        await actor.refresh_embedding_backfill_state()

        assert actor._embedding_backfill_state == "partially_embedded"

    @pytest.mark.asyncio
    async def test_refresh_handles_store_error(self):
        """refresh_embedding_backfill_state() handles store errors gracefully."""
        actor = _make_full_sexton(is_mock_provider=False)
        actor._corpus_turns.count_unembedded = AsyncMock(side_effect=Exception("DB error"))

        await actor.refresh_embedding_backfill_state()

        # Should not crash, state should still be computed from available data
        assert actor._embedding_backfill_state in ("configured_idle", "partially_embedded", "backfill_pending")


# ---------------------------------------------------------------------------
# Test 6: Successful embedding increases count
# ---------------------------------------------------------------------------


class TestEmbeddingSuccessIncreasesCount:
    """Successful embedding must increase the embedded/vector count."""

    @pytest.mark.asyncio
    async def test_embedding_pass_marks_embedded(self):
        """_run_embedding_pass() marks turns as embedded after successful upsert."""
        actor = _make_full_sexton(is_mock_provider=False)
        # Mock corpus turns
        mock_turn = MagicMock()
        mock_turn.turn_id = "turn-1"
        mock_turn.searchable_text = "Some text to embed"
        mock_turn.needs_reembed = 0
        actor._corpus_turns.get_unembedded_turns = AsyncMock(return_value=[mock_turn])
        actor._corpus_turns.mark_embedded = AsyncMock(return_value=None)
        actor._corpus_turns.clear_embed_failure = AsyncMock(return_value=None)
        actor._corpus_turns.count_unembedded = AsyncMock(return_value=0)

        # Mock vector store
        actor._vector.upsert = AsyncMock(return_value=None)

        # Mock embed provider
        actor._embed.embed = AsyncMock(return_value=[0.1] * 768)
        actor._embed.model = "test-model"

        result = await actor._run_embedding_pass(limit=10)

        assert result["embedded"] == 1
        assert result["failed"] == 0
        actor._vector.upsert.assert_called_once()
        actor._corpus_turns.mark_embedded.assert_called_once()

    @pytest.mark.asyncio
    async def test_embedding_pass_skips_empty_text(self):
        """_run_embedding_pass() skips turns with empty searchable_text."""
        actor = _make_full_sexton(is_mock_provider=False)
        mock_turn = MagicMock()
        mock_turn.turn_id = "turn-1"
        mock_turn.searchable_text = "   "
        mock_turn.needs_reembed = 0
        actor._corpus_turns.get_unembedded_turns = AsyncMock(return_value=[mock_turn])
        actor._corpus_turns.count_unembedded = AsyncMock(return_value=0)

        result = await actor._run_embedding_pass(limit=10)

        assert result["embedded"] == 0
        assert result["skipped"] == 1


# ---------------------------------------------------------------------------
# Test 7: Embedding failure reported honestly
# ---------------------------------------------------------------------------


class TestEmbeddingFailureHonesty:
    """Embedding failures must be reported, not hidden."""

    @pytest.mark.asyncio
    async def test_embedding_failure_not_counted_as_success(self):
        """Failed embedding must increment 'failed', not 'embedded'."""
        actor = _make_full_sexton(is_mock_provider=False)
        mock_turn = MagicMock()
        mock_turn.turn_id = "turn-1"
        mock_turn.searchable_text = "Some text"
        mock_turn.needs_reembed = 0
        actor._corpus_turns.get_unembedded_turns = AsyncMock(return_value=[mock_turn])
        actor._corpus_turns.count_unembedded = AsyncMock(return_value=1)
        actor._corpus_turns.batch_mark_embedded = AsyncMock(return_value=None)

        # Embed provider raises an error
        actor._embed.embed = AsyncMock(side_effect=ValueError("API error"))

        result = await actor._run_embedding_pass(limit=10)

        assert result["embedded"] == 0
        assert result["failed"] == 1
        # backfill state should reflect failure
        assert actor._last_backfill_error is not None

    @pytest.mark.asyncio
    async def test_no_vector_store_means_failure(self):
        """Without vector store, embedding cannot succeed — counted as failed."""
        actor = _make_full_sexton(is_mock_provider=False)
        actor._vector = None  # No vector store
        mock_turn = MagicMock()
        mock_turn.turn_id = "turn-1"
        mock_turn.searchable_text = "Some text"
        mock_turn.needs_reembed = 0
        actor._corpus_turns.get_unembedded_turns = AsyncMock(return_value=[mock_turn])
        actor._corpus_turns.count_unembedded = AsyncMock(return_value=1)

        result = await actor._run_embedding_pass(limit=10)

        # Without vector store, the pass should skip since corpus_turns and embed exist
        # but the embedding won't be stored — turns remain unembedded
        assert result.get("embedded", 0) == 0

    @pytest.mark.asyncio
    async def test_embedding_failure_recorded_in_errors(self):
        """Embedding failures should be tracked in _embedding_failures."""
        actor = _make_full_sexton(is_mock_provider=False)
        mock_turn = MagicMock()
        mock_turn.turn_id = "turn-fail"
        mock_turn.searchable_text = "Text"
        mock_turn.needs_reembed = 0
        actor._corpus_turns.get_unembedded_turns = AsyncMock(return_value=[mock_turn])
        actor._corpus_turns.count_unembedded = AsyncMock(return_value=1)

        # Embed provider raises
        actor._embed.embed = AsyncMock(side_effect=ValueError("API error"))

        await actor._run_embedding_pass(limit=10)

        assert len(actor._embedding_failures) > 0
        assert "turn-fail" in actor._embedding_failures


# ---------------------------------------------------------------------------
# Test 8: Health/dogfood endpoint reports embedding state honestly
# ---------------------------------------------------------------------------


class TestHealthEndpointEmbeddingHonesty:
    """Health endpoints must report real embedding state."""

    def test_backfill_state_in_status_summary(self):
        """get_status_summary() includes embedding_backfill_state."""
        actor = _make_full_sexton(is_mock_provider=True)
        summary = actor.get_status_summary()
        assert "embedding_backfill_state" in summary
        assert summary["embedding_backfill_state"] == "degraded"

    def test_embedding_provider_type_in_summary(self):
        """get_status_summary() includes embedding_provider_type."""
        mock_provider = _make_mock_embedding_provider(is_mock=True)
        actor = _make_full_sexton(embedding_provider=mock_provider)
        summary = actor.get_status_summary()
        assert "embedding_provider_type" in summary
        assert summary["embedding_provider_type"] == "MockOllamaEmbeddingClient"

    def test_real_provider_type_in_summary(self):
        """get_status_summary() shows real provider class name."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        summary = actor.get_status_summary()
        assert summary["embedding_provider_type"] == "OpenAICompatibleEmbeddingClient"
        assert summary["embedding_backfill_state"] == "configured_idle"

    def test_backfill_error_in_summary(self):
        """get_status_summary() includes last_backfill_error."""
        actor = _make_full_sexton(is_mock_provider=False)
        actor._last_backfill_error = "All 50 turns failed"
        summary = actor.get_status_summary()
        assert "last_backfill_error" in summary
        assert summary["last_backfill_error"] == "All 50 turns failed"


# ---------------------------------------------------------------------------
# Test 9: adapter/health.py no longer fakes embedding status
# ---------------------------------------------------------------------------


class TestAdapterHealthHonesty:
    """adapter/health.py must not fake 'healthy' embedding status."""

    def test_health_module_reports_honest_embedding_status(self):
        """Verify that adapter/health.py no longer has hardcoded 'healthy' embedding."""
        import inspect

        from aip.adapter import health as health_mod

        source = inspect.getsource(health_mod.system_health_check)
        # The old hardcoded fake status should NOT be present
        assert '"status": "healthy"' not in source or "not_configured" in source, (
            "adapter/health.py still has hardcoded 'healthy' embedding status"
        )
        # Must contain 'not_configured' for mock providers
        assert "not_configured" in source, "adapter/health.py must report 'not_configured' for mock/fake providers"
        # Must contain 'degraded' field
        assert "degraded" in source, "adapter/health.py must include 'degraded' field in embedding status"

    def test_health_module_no_hardcoded_ollama(self):
        """adapter/health.py must not hardcode 'ollama' backend."""
        import inspect

        from aip.adapter import health as health_mod

        source = inspect.getsource(health_mod.system_health_check)
        # The old hardcoded "backend": "ollama" should be gone
        assert '"ollama"' not in source or "OllamaEmbeddingClient" in source, (
            "adapter/health.py still has hardcoded 'ollama' backend in embedding status"
        )
        # The hardcoded model name should be gone
        assert "nomic-embed-text:v1.5" not in source, (
            "adapter/health.py still has hardcoded 'nomic-embed-text:v1.5' model name"
        )


# ---------------------------------------------------------------------------
# Test 10: Mock/fake providers reported as degraded
# ---------------------------------------------------------------------------


class TestMockProviderDegraded:
    """Mock/fake providers must be reported as 'degraded', not 'healthy'."""

    def test_mock_provider_backfill_state_degraded(self):
        """Sexton with MockOllamaEmbeddingClient reports 'degraded'."""
        mock_provider = _make_mock_embedding_provider(is_mock=True)
        actor = _make_full_sexton(embedding_provider=mock_provider)
        assert actor._compute_embedding_backfill_state() == "degraded"

    def test_fake_embed_provider_backfill_state_degraded(self):
        """Sexton with FakeEmbedProvider reports 'degraded'."""

        class FakeEmbedProvider:
            async def embed(self, text: str) -> list[float]:
                return [0.1] * 768

        fake_provider = FakeEmbedProvider()
        actor = _make_full_sexton(embedding_provider=fake_provider)
        assert actor._compute_embedding_backfill_state() == "degraded"

    def test_real_provider_not_degraded(self):
        """Sexton with real provider does NOT report 'degraded'."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        state = actor._compute_embedding_backfill_state()
        assert state != "degraded"
        assert state in ("configured_idle", "backfill_pending", "partially_embedded", "embedded")


# ---------------------------------------------------------------------------
# Test: Container.set_embedding_provider propagates to Sexton
# ---------------------------------------------------------------------------


class TestContainerProviderPropagation:
    """Container.set_embedding_provider() must propagate to Sexton actor."""

    def test_update_embedding_provider_method_exists(self):
        """Sexton has update_embedding_provider() method for runtime updates."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        assert hasattr(actor, "update_embedding_provider")
        assert callable(actor.update_embedding_provider)

    def test_sexton_update_embedding_provider_transitions_state(self):
        """Calling update_embedding_provider on Sexton transitions the backfill state."""
        # Start with mock provider (degraded)
        mock_provider = _make_mock_embedding_provider(is_mock=True)
        actor = _make_full_sexton(embedding_provider=mock_provider)
        # Manually trigger state computation since constructor doesn't
        actor._embedding_backfill_state = actor._compute_embedding_backfill_state()
        assert actor._embedding_backfill_state == "degraded"

        # Switch to real provider
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor.update_embedding_provider(real_provider)
        assert actor._embedding_backfill_state == "configured_idle"

    def test_sexton_update_to_none_transitions_to_not_configured(self):
        """Setting provider to None via update_embedding_provider transitions to not_configured."""
        real_provider = _make_mock_embedding_provider(is_mock=False)
        actor = _make_full_sexton(embedding_provider=real_provider)
        actor.update_embedding_provider(None)
        assert actor._embedding_backfill_state == "not_configured"
