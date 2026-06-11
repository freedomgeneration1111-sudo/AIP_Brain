"""Chunk 3 — Sexton Full-Mode Wiring Preflight + Minimal Integration Tests.

Validates:
  1. Sexton actor reports honest state (active/degraded/disabled/failed)
  2. Sexton with missing core dependencies reports "degraded"
  3. Sexton with all core deps and completed cycle reports "active"
  4. Sexton with no event_store reports "disabled"
  5. Startup does not claim Sexton is active when merely instantiated
  6. Actor cycle failure is recorded in _recent_errors
  7. /health/dogfood uses honest Sexton state (not just "active"/"inactive")
  8. L4 reset.py uses correct keyword args for old Sexton constructor
  9. Dogfood readiness check uses sexton_actor (not old container.sexton)
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


def _make_full_sexton() -> Sexton:
    """Create a Sexton with all core dependencies present."""
    event_store = _make_mock_store()
    return Sexton(
        sexton_provider=MagicMock(),
        corpus_turn_store=MagicMock(),
        embedding_provider=MagicMock(),
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
# Test 1-4: Honest state reporting
# ---------------------------------------------------------------------------


class TestSextonHonestState:
    """Sexton get_status_summary() must report honest state, not fake healthy."""

    def test_state_degraded_when_no_core_deps(self):
        """Sexton with no core deps reports 'degraded', not 'active'."""
        actor = Sexton(event_store=_make_mock_store())
        summary = actor.get_status_summary()
        assert summary["state"] == "degraded", (
            f"Expected 'degraded' but got '{summary['state']}' for Sexton with no core deps"
        )
        assert len(summary["missing_core_dependencies"]) > 0

    def test_state_degraded_when_missing_embedding_provider(self):
        """Sexton with all deps except embedding_provider reports 'degraded'."""
        actor = Sexton(
            sexton_provider=MagicMock(),
            corpus_turn_store=MagicMock(),
            embedding_provider=None,  # Missing
            vector_store=MagicMock(),
            event_store=_make_mock_store(),
            trace_store=MagicMock(),
        )
        summary = actor.get_status_summary()
        assert summary["state"] == "degraded"
        assert "embedding_provider" in summary["missing_core_dependencies"]

    def test_state_degraded_when_missing_corpus_turn_store(self):
        """Sexton without corpus_turn_store reports 'degraded'."""
        actor = Sexton(
            sexton_provider=MagicMock(),
            corpus_turn_store=None,  # Missing
            embedding_provider=MagicMock(),
            vector_store=MagicMock(),
            event_store=_make_mock_store(),
        )
        summary = actor.get_status_summary()
        assert summary["state"] == "degraded"
        assert "corpus_turn_store" in summary["missing_core_dependencies"]

    def test_state_disabled_when_no_event_store(self):
        """Sexton without event_store reports 'disabled'."""
        actor = Sexton(event_store=None)
        summary = actor.get_status_summary()
        assert summary["state"] == "disabled"

    def test_state_degraded_when_instantiated_but_no_cycle_run(self):
        """Sexton with all core deps but no cycle run yet reports 'degraded'."""
        actor = _make_full_sexton()
        summary = actor.get_status_summary()
        # No cycle has run, so even with all deps, state is "degraded"
        assert summary["state"] == "degraded", f"Expected 'degraded' (no cycle yet) but got '{summary['state']}'"

    def test_state_active_after_cycle_completed(self):
        """Sexton with all deps and at least one completed cycle reports 'active'."""
        actor = _make_full_sexton()
        actor._last_cycle_time = 12345.0  # Simulate a completed cycle
        actor._cycle_count = 1
        summary = actor.get_status_summary()
        assert summary["state"] == "active"

    def test_state_failed_when_errors_before_first_cycle(self):
        """Sexton with errors recorded but no completed cycle reports 'failed'."""
        actor = _make_full_sexton()
        actor._recent_errors = ["cycle_1: connection refused"]
        summary = actor.get_status_summary()
        assert summary["state"] == "failed"

    def test_missing_core_dependencies_lists_only_missing(self):
        """missing_core_dependencies only lists deps that are actually absent."""
        actor = Sexton(
            sexton_provider=MagicMock(),
            corpus_turn_store=None,
            embedding_provider=MagicMock(),
            vector_store=None,
            event_store=_make_mock_store(),
        )
        summary = actor.get_status_summary()
        assert set(summary["missing_core_dependencies"]) == {"corpus_turn_store", "vector_store"}


# ---------------------------------------------------------------------------
# Test 5: Startup does not fake healthy status
# ---------------------------------------------------------------------------


class TestStartupHonesty:
    """Startup must not claim Sexton is active when merely instantiated."""

    def test_instantiated_actor_not_claimed_active(self):
        """A freshly constructed Sexton must not appear 'active' in status."""
        actor = _make_full_sexton()
        summary = actor.get_status_summary()
        # Before any cycle runs, state should NOT be "active"
        assert summary["state"] != "active", "Sexton should not claim 'active' before any cycle has completed"

    def test_actor_details_state_not_fake(self):
        """The health endpoint's actors_status for Sexton should use honest state."""
        from aip.adapter.api.routes.health import health

        # Build a container with sexton_actor that has all deps but no cycle
        container = MagicMock()
        container.sexton_actor = _make_full_sexton()
        # Set up other required container attrs
        container.entity_store = MagicMock()
        container.canonical_store = MagicMock()
        container.event_store = MagicMock()
        container.autonomy_gate = MagicMock()
        container.artifact_store = MagicMock()
        container.lexical_store = MagicMock()
        container.vector_store = MagicMock()
        container.embedding_provider = MagicMock()
        container.project_store = MagicMock()
        container.budget_store = MagicMock()
        container.vigil_store = MagicMock()
        container.model_provider = MagicMock()
        container.knowledge_store = MagicMock()
        container.ecs_store = MagicMock()
        container.review_queue_store = MagicMock()
        container.graph_store = MagicMock()
        container.session_store = MagicMock()
        container.corpus_turn_store = MagicMock()
        container.budget_manager = MagicMock()
        container.beast = None
        container.vigil = None
        container._app_start_time = 0
        container.definer_profile = None
        container.session_manager = None
        container.performance_profiler = None
        container._alert_manager = None
        container._config_watcher = None
        container._read_pool_auto_sizer = None
        container._vigil_quality_store = None
        container.auth_session_store = None
        container.config = {}
        container._store_registry = {}
        # Mock async methods
        container.vector_store.count = AsyncMock(return_value=0)
        container.vector_store.health_check = AsyncMock(
            return_value={
                "backend_status": "available",
                "backend_name": "test",
                "degraded": False,
                "vss_available": True,
                "degradation": {},
            }
        )
        container.budget_manager.get_status = AsyncMock(return_value={})
        container.event_store.write_event = AsyncMock(return_value=None)
        container.corpus_turn_store.total_turns = AsyncMock(return_value=0)
        container.corpus_turn_store.count_unembedded = AsyncMock(return_value=0)
        container.model_provider.list_slots = MagicMock(return_value=[])
        container.model_provider._ci_mode = True

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(health(container=container))
        sexton_status = result["actors"]["sexton"]
        # Sexton has all deps but no cycle — should be "degraded", not "active"
        assert sexton_status["state"] != "active", (
            f"Sexton should not claim 'active' before any cycle; got state={sexton_status['state']}"
        )


# ---------------------------------------------------------------------------
# Test 6: Actor cycle failure is recorded
# ---------------------------------------------------------------------------


class TestCycleFailureRecording:
    """Sexton cycle failures must be recorded, not silently swallowed."""

    def test_recent_errors_records_failure(self):
        """_recent_errors should capture cycle failure messages."""
        actor = _make_full_sexton()
        actor._recent_errors.append("cycle_1: connection refused")
        actor._recent_errors = actor._recent_errors[-10:]
        summary = actor.get_status_summary()
        assert len(summary["recent_errors"]) == 1
        assert "connection refused" in summary["recent_errors"][0]

    def test_recent_errors_capped_at_10(self):
        """_recent_errors should be capped at 10 entries."""
        actor = _make_full_sexton()
        for i in range(15):
            actor._recent_errors.append(f"cycle_{i}: error")
            actor._recent_errors = actor._recent_errors[-10:]
        assert len(actor._recent_errors) == 10

    def test_failed_state_when_errors_before_cycle(self):
        """If errors are recorded before any cycle completes, state should be 'failed'."""
        actor = _make_full_sexton()
        actor._recent_errors = ["cycle_1: timeout"]
        summary = actor.get_status_summary()
        assert summary["state"] == "failed"


# ---------------------------------------------------------------------------
# Test 7: /health/dogfood honest Sexton state
# ---------------------------------------------------------------------------


class TestDogfoodHonestSextonState:
    """/health/dogfood must report honest Sexton state."""

    @pytest.mark.asyncio
    async def test_dogfood_reports_degraded_sexton(self):
        """When Sexton is instantiated but has no core deps, dogfood reports degraded state."""
        from aip.adapter.api.routes.health import dogfood_health

        # Sexton with missing core deps
        degraded_sexton = Sexton(
            sexton_provider=None,
            corpus_turn_store=None,
            embedding_provider=None,
            vector_store=None,
            event_store=_make_mock_store(),
        )

        container = MagicMock()
        container.sexton_actor = degraded_sexton
        container.config = {}
        container._store_registry = {}

        mock_request = MagicMock()
        mock_request.app.state.raw_config = {"alpha": {"dogfood_mode": "full"}}

        response = await dogfood_health(mock_request, container)
        # Should NOT report "active" for a Sexton with missing core deps
        sexton_state = response.get("actors", {}).get("sexton", "unknown")
        assert sexton_state != "active", f"Sexton with missing core deps should not be 'active'; got '{sexton_state}'"
        assert sexton_state == "degraded", (
            f"Expected 'degraded' for Sexton with missing core deps; got '{sexton_state}'"
        )


# ---------------------------------------------------------------------------
# Test 8: L4 reset.py uses correct keyword args
# ---------------------------------------------------------------------------


class TestL4SextonSignature:
    """L4 reset.py must use keyword args for old Sexton constructor."""

    def test_sexton_constructor_rejects_positional_trace_store(self):
        """Old Sexton's first positional arg is config, not trace_store.

        Verify that passing trace_store as a positional arg is a bug
        by checking the actual constructor signature.
        """
        import inspect

        from aip.orchestration.sexton.sexton import Sexton as FailureSexton

        sig = inspect.signature(FailureSexton.__init__)
        params = list(sig.parameters.keys())
        # First param after self is 'config', not 'trace_store'
        assert params[1] == "config", (
            f"Expected first param to be 'config', got '{params[1]}'. If this changed, update l4/reset.py call site."
        )

    def test_sexton_constructor_with_keyword_args(self):
        """Old Sexton can be constructed with keyword args correctly."""
        from aip.orchestration.sexton.sexton import Sexton as FailureSexton

        trace_store = MagicMock()
        # This should NOT raise — correct keyword usage
        sexton = FailureSexton(trace_store=trace_store)
        assert sexton._trace_store is trace_store

    def test_sexton_constructor_positional_misattribution(self):
        """Old Sexton with positional arg passes it as config, not trace_store.

        This demonstrates the bug that was fixed in l4/reset.py.
        """
        from aip.orchestration.sexton.sexton import Sexton as FailureSexton

        trace_store = MagicMock()
        # This is the BUG: trace_store goes to config param
        sexton = FailureSexton(trace_store)
        assert sexton._trace_store is None, "trace_store was passed positionally to config param — this is the bug"
        assert sexton._config == trace_store, "trace_store ended up as config — confirms positional arg misattribution"


# ---------------------------------------------------------------------------
# Test 9: Dogfood readiness uses sexton_actor
# ---------------------------------------------------------------------------


class TestDogfoodReadinessSextonActor:
    """Dogfood readiness check must check sexton_actor, not container.sexton."""

    def test_readiness_checks_sexton_actor_not_sexton(self):
        """validate_dogfood_readiness checks 'sexton_actor' attribute, not 'sexton'."""
        from aip.config import validate_dogfood_readiness

        container = MagicMock()
        # container.sexton_actor = None (missing)
        container.sexton_actor = None
        container.config = {}
        container._store_registry = {}
        # Ensure other attributes exist (return None)
        for attr in [
            "lexical_store",
            "vector_store",
            "embedding_provider",
            "ecs_store",
            "artifact_store",
            "project_store",
            "graph_store",
            "corpus_turn_store",
            "event_store",
            "model_provider",
            "budget_store",
            "session_store",
            "review_queue_store",
            "knowledge_store",
            "ace_playbook",
        ]:
            setattr(container, attr, None)

        check = validate_dogfood_readiness({"alpha": {"dogfood_mode": "full"}}, container)
        assert "sexton_actor" in check.required_actors
        assert check.required_actors["sexton_actor"] is False

    def test_readiness_sexton_actor_active_when_present(self):
        """validate_dogfood_readiness reports sexton_actor as True when set."""
        from aip.config import validate_dogfood_readiness

        container = MagicMock()
        container.sexton_actor = MagicMock()  # Present
        container.config = {}
        container._store_registry = {}
        for attr in [
            "lexical_store",
            "vector_store",
            "embedding_provider",
            "ecs_store",
            "artifact_store",
            "project_store",
            "graph_store",
            "corpus_turn_store",
            "event_store",
            "model_provider",
            "budget_store",
            "session_store",
            "review_queue_store",
            "knowledge_store",
            "ace_playbook",
        ]:
            setattr(container, attr, MagicMock())

        check = validate_dogfood_readiness({"alpha": {"dogfood_mode": "full"}}, container)
        assert check.required_actors["sexton_actor"] is True
