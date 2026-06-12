"""Sprint 8 — Full Dogfood Wiring Smoke Test.

Validates that:
  1. DogfoodMode enum and config resolution works (minimal/full/diagnostic)
  2. [alpha] dogfood_mode config key is read correctly
  3. AIP_DOGFOOD_MODE env var override works
  4. validate_dogfood_readiness checks all 14 components
  5. validate_dogfood_readiness checks all 3 actors
  6. validate_dogfood_readiness checks all 6 retrieval channels
  7. validate_dogfood_readiness checks DB paths
  8. validate_dogfood_readiness detects embedding provider type
  9. FULL mode readiness gate: is_ready=False when components missing
  10. FULL mode readiness gate: is_ready=True when all components present
  11. DIAGNOSTIC mode logs summary without blocking
  12. MINIMAL mode does not require all components
  13. DogfoodReadinessCheck.summary is human-readable
  14. degraded_components lists missing items
  15. /health/dogfood endpoint returns all fields
  16. /health endpoint includes dogfood_mode field
  17. Beast actor wiring: receives all required stores
  18. Vigil actor wiring: receives all required stores
  19. Sexton actor wiring: receives alert_manager
  20. End-to-end smoke: stores → actors → channels → readiness
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aip.config import (
    DogfoodMode,
    get_dogfood_mode,
    validate_dogfood_readiness,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeContainer:
    """Minimal container-like object for testing dogfood readiness checks.

    Allows setting attributes dynamically to simulate initialized/missing
    components.
    """

    def __init__(self, **kwargs):
        self.config = kwargs.get("config", {})
        self._store_registry = kwargs.get("_store_registry", {})

        # Set all component attributes from kwargs
        for attr, val in kwargs.items():
            if attr not in ("config", "_store_registry"):
                setattr(self, attr, val)


def _make_full_container() -> FakeContainer:
    """Create a container with all components initialized (FULL mode ready)."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "state.db")
    lexical_db = os.path.join(tmp_dir, "lexical.db")
    vectors_db = os.path.join(tmp_dir, "vectors.db")
    # Create all DB files so DB path validation passes
    Path(db_path).touch()
    Path(lexical_db).touch()
    Path(vectors_db).touch()

    return FakeContainer(
        config={"alpha": {"dogfood_mode": "full"}},
        _store_registry={
            "entity_store": db_path,
            "canonical_store": db_path,
            "event_store": db_path,
            "artifact_store": db_path,
            "ecs_store": db_path,
            "project_store": db_path,
            "lexical_store": lexical_db,
            "vector_store": vectors_db,
            "corpus_turn_store": db_path,
            "graph_store": db_path,
            "session_store": db_path,
            "budget_store": db_path,
            "review_queue_store": db_path,
            "knowledge_store": db_path,
        },
        # Components
        lexical_store=MagicMock(),
        vector_store=MagicMock(),
        embedding_provider=MagicMock(__class__=type("OpenAICompatibleProvider", (), {})),
        ecs_store=MagicMock(),
        artifact_store=MagicMock(),
        project_store=MagicMock(),
        graph_store=MagicMock(),
        corpus_turn_store=MagicMock(),
        event_store=MagicMock(),
        model_provider=MagicMock(),
        budget_store=MagicMock(),
        session_store=MagicMock(),
        review_queue_store=MagicMock(),
        knowledge_store=MagicMock(),
        budget_manager=MagicMock(),
        # Actors
        beast=MagicMock(),
        vigil=MagicMock(
            config=MagicMock(retrieval_quality_sampling_enabled=True),
        ),
        sexton_actor=MagicMock(),
        # Additional
        ace_playbook=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Test 1-3: DogfoodMode enum and config resolution
# ---------------------------------------------------------------------------


class TestDogfoodModeResolution:
    """Test DogfoodMode enum values and config resolution."""

    def test_enum_values(self):
        """DogfoodMode has the correct enum values."""
        assert DogfoodMode.MINIMAL.value == "minimal"
        assert DogfoodMode.FULL.value == "full"
        assert DogfoodMode.DIAGNOSTIC.value == "diagnostic"

    def test_config_alpha_section(self):
        """Config [alpha] dogfood_mode is read correctly."""
        config = {"alpha": {"dogfood_mode": "full"}}
        assert get_dogfood_mode(config) == DogfoodMode.FULL

        config = {"alpha": {"dogfood_mode": "diagnostic"}}
        assert get_dogfood_mode(config) == DogfoodMode.DIAGNOSTIC

        config = {"alpha": {"dogfood_mode": "minimal"}}
        assert get_dogfood_mode(config) == DogfoodMode.MINIMAL

    def test_env_var_override(self, monkeypatch):
        """AIP_DOGFOOD_MODE env var overrides when config has no alpha section."""
        monkeypatch.setenv("AIP_DOGFOOD_MODE", "full")
        config = {}  # No alpha section — env var should win
        assert get_dogfood_mode(config) == DogfoodMode.FULL

    def test_env_var_overrides_empty_alpha(self, monkeypatch):
        """AIP_DOGFOOD_MODE env var overrides empty alpha.dogfood_mode."""
        monkeypatch.setenv("AIP_DOGFOOD_MODE", "full")
        config = {"alpha": {}}  # alpha section exists but no dogfood_mode
        assert get_dogfood_mode(config) == DogfoodMode.FULL

    def test_default_is_minimal(self):
        """Without config or env, default is MINIMAL."""
        config = {}
        assert get_dogfood_mode(config) == DogfoodMode.MINIMAL

    def test_invalid_value_falls_back_to_minimal(self):
        """Invalid dogfood_mode value falls back to MINIMAL with warning."""
        config = {"alpha": {"dogfood_mode": "invalid_mode"}}
        assert get_dogfood_mode(config) == DogfoodMode.MINIMAL

    def test_case_insensitive(self):
        """Dogfood mode values are matched case-insensitively."""
        config = {"alpha": {"dogfood_mode": "FULL"}}
        assert get_dogfood_mode(config) == DogfoodMode.FULL

        config = {"alpha": {"dogfood_mode": "Diagnostic"}}
        assert get_dogfood_mode(config) == DogfoodMode.DIAGNOSTIC


# ---------------------------------------------------------------------------
# Test 4-8: validate_dogfood_readiness checks
# ---------------------------------------------------------------------------


class TestDogfoodReadinessChecks:
    """Test validate_dogfood_readiness checks all required components/actors/channels."""

    def test_checks_all_14_components(self):
        """validate_dogfood_readiness checks 14 required components."""
        container = FakeContainer()
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)
        assert len(check.required_components) == 14

    def test_checks_all_3_actors(self):
        """validate_dogfood_readiness checks 3 required actors."""
        container = FakeContainer()
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)
        assert len(check.required_actors) == 3
        assert "beast" in check.required_actors
        assert "vigil" in check.required_actors
        assert "sexton_actor" in check.required_actors

    def test_checks_all_6_retrieval_channels(self):
        """validate_dogfood_readiness checks 6 retrieval channels."""
        container = FakeContainer()
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)
        assert len(check.retrieval_channels) == 6
        expected_channels = {"lexical", "vector", "corpus", "graph", "wiki", "procedural"}
        assert set(check.retrieval_channels.keys()) == expected_channels

    def test_db_paths_validation(self):
        """validate_dogfood_readiness validates DB paths from _store_registry."""
        tmp_dir = tempfile.mkdtemp()
        db_path = os.path.join(tmp_dir, "state.db")
        Path(db_path).touch()  # Create the file so it "exists"

        container = FakeContainer(
            _store_registry={"entity_store": db_path},
        )
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)
        assert check.db_paths_valid is True
        assert "entity_store" in check.db_path_details
        assert check.db_path_details["entity_store"]["exists"] is True

    def test_db_paths_invalid_when_missing(self):
        """validate_dogfood_readiness detects missing DB files."""
        container = FakeContainer(
            _store_registry={"entity_store": "/nonexistent/path/state.db"},
        )
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)
        assert check.db_paths_valid is False
        assert check.db_path_details["entity_store"]["exists"] is False

    def test_embedding_provider_type_detection(self):
        """validate_dogfood_readiness detects embedding provider type."""
        container = FakeContainer(
            embedding_provider=MagicMock(__class__=type("OpenAICompatibleProvider", (), {})),
        )
        config = {}
        check = validate_dogfood_readiness(config, container)
        # Should detect something from the class name
        assert check.embedding_provider_type != "unknown"

    def test_embedding_provider_type_from_config(self):
        """validate_dogfood_readiness falls back to config for provider type."""
        container = FakeContainer()
        config = {"embedding": {"provider": "openai_compatible"}}
        check = validate_dogfood_readiness(config, container)
        assert check.embedding_provider_type == "openai_compatible"


# ---------------------------------------------------------------------------
# Test 9-12: Readiness gate
# ---------------------------------------------------------------------------


class TestDogfoodReadinessGate:
    """Test the dogfood readiness gate logic."""

    def test_full_mode_not_ready_when_components_missing(self):
        """FULL mode is_ready=False when components are missing."""
        container = FakeContainer()  # No components set
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)
        assert check.is_ready is False

    def test_full_mode_ready_when_all_present(self):
        """FULL mode is_ready=True when all components/actors/channels are present."""
        container = _make_full_container()
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)
        assert check.is_ready is True, f"Expected ready, but degraded: {check.degraded_components}"

    def test_minimal_mode_not_blocked(self):
        """MINIMAL mode is_ready is always False (only FULL can be ready)."""
        container = _make_full_container()
        config = {"alpha": {"dogfood_mode": "minimal"}}
        check = validate_dogfood_readiness(config, container)
        # MINIMAL mode never reports is_ready=True (that's a FULL-only gate)
        assert check.is_ready is False

    def test_diagnostic_mode_not_blocked(self):
        """DIAGNOSTIC mode does not block startup (is_ready=False by design)."""
        container = FakeContainer()
        config = {"alpha": {"dogfood_mode": "diagnostic"}}
        check = validate_dogfood_readiness(config, container)
        assert check.is_ready is False
        # But it should still have populated all checks
        assert len(check.required_components) == 14
        assert len(check.required_actors) == 3


# ---------------------------------------------------------------------------
# Test 13-14: Summary and degraded_components
# ---------------------------------------------------------------------------


class TestDogfoodReadinessSummary:
    """Test DogfoodReadinessCheck summary and degraded_components."""

    def test_summary_is_human_readable(self):
        """summary returns a multi-line human-readable report."""
        container = _make_full_container()
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)
        summary = check.summary
        assert "DogfoodMode: full" in summary
        assert "Ready: True" in summary
        assert "Components:" in summary
        assert "Actors:" in summary
        assert "Embedding provider:" in summary
        assert "Retrieval channels:" in summary
        assert "DB paths valid:" in summary

    def test_degraded_components_lists_missing(self):
        """degraded_components lists names of missing items."""
        container = FakeContainer(
            lexical_store=MagicMock(),  # Only one component set
        )
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)
        degraded = check.degraded_components
        assert len(degraded) > 0
        # lexical_store is present, so it should NOT be in degraded
        assert "lexical_store" not in degraded
        # But most other components should be missing
        assert "vector_store" in degraded
        assert "beast" in degraded

    def test_degraded_includes_db_paths(self):
        """degraded_components includes 'db_paths' when DB paths are invalid."""
        container = FakeContainer(
            _store_registry={"entity_store": "/nonexistent/path.db"},
        )
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)
        assert "db_paths" in check.degraded_components


# ---------------------------------------------------------------------------
# Test 15-16: API endpoints
# ---------------------------------------------------------------------------


class TestDogfoodHealthEndpoint:
    """Test /health/dogfood endpoint returns all required fields."""

    @pytest.mark.asyncio
    async def test_health_dogfood_endpoint_fields(self):
        """/health/dogfood returns all required fields."""
        from aip.adapter.api.routes.health import dogfood_health

        container = _make_full_container()

        # Create a mock request with app.state
        mock_request = MagicMock()
        mock_request.app.state.raw_config = {"alpha": {"dogfood_mode": "full"}}

        # Call the endpoint directly
        response = await dogfood_health(mock_request, container)

        # Verify all required fields
        required_fields = [
            "dogfood_mode",
            "is_ready",
            "required_components",
            "required_actors",
            "embedding_provider_active",
            "embedding_provider_type",
            "retrieval_channels",
            "degraded_components",
            "db_paths_valid",
            "db_path_details",
            "summary",
        ]
        for field in required_fields:
            assert field in response, f"Missing field: {field}"

        # Verify FULL mode specific fields
        assert "actors" in response
        assert "review_gates" in response
        assert "channel_summary" in response

    @pytest.mark.asyncio
    async def test_health_endpoint_includes_dogfood_mode(self):
        """/health endpoint includes dogfood_mode field."""
        from aip.adapter.api.routes.health import health

        # Build a comprehensive mock container for the full /health check
        container = MagicMock()
        container.config = {"alpha": {"dogfood_mode": "full"}}
        container._store_registry = {}
        # Core stores
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
        container.beast = None  # Simplify
        container.vigil = None
        container.sexton_actor = None
        container._app_start_time = 0
        container.definer_profile = None
        container.session_manager = None
        container.performance_profiler = None
        container._alert_manager = None
        container._config_watcher = None
        container._read_pool_auto_sizer = None
        container._vigil_quality_store = None
        container.auth_session_store = None
        # Mock async methods
        container.vector_store.count = MagicMock(return_value=0)
        container.vector_store.health_check = MagicMock(
            return_value={
                "backend_status": "available",
                "backend_name": "test",
                "degraded": False,
                "vss_available": True,
                "degradation": {},
            }
        )
        container.budget_manager.get_status = MagicMock(return_value={})
        container.event_store.write_event = MagicMock(return_value=None)
        container.corpus_turn_store.total_turns = MagicMock(return_value=0)
        container.corpus_turn_store.count_unembedded = MagicMock(return_value=0)
        container.model_provider.list_slots = MagicMock(return_value=[])
        container.model_provider._ci_mode = True
        # Mock connection_health on all stores
        for store_name in [
            "entity_store",
            "event_store",
            "artifact_store",
            "ecs_store",
            "canonical_store",
            "budget_store",
            "project_store",
            "session_store",
            "review_queue_store",
            "vigil_store",
            "corpus_turn_store",
            "lexical_store",
            "graph_store",
            "vector_store",
            "knowledge_store",
            "autonomy_gate",
        ]:
            store = getattr(container, store_name, None)
            if store is not None:
                store.connection_health = MagicMock(
                    return_value={
                        "read_pool": {
                            "checkout_count": 0,
                            "fallback_count": 0,
                            "exhaustion_count": 0,
                            "exhaustion_rate": 0.0,
                            "pool_size": 3,
                        }
                    }
                )

        response = await health(container=container)
        assert "dogfood_mode" in response
        assert response["dogfood_mode"] in ("minimal", "full", "diagnostic")


# ---------------------------------------------------------------------------
# Test 17-19: Actor wiring
# ---------------------------------------------------------------------------


class TestActorWiring:
    """Test that actors receive all required stores in lifespan wiring."""

    def test_beast_receives_all_stores(self):
        """Beast actor constructor accepts all stores wired in lifespan."""
        # Verify the constructor accepts all the parameters we pass
        import inspect

        from aip.orchestration.actors.beast import Beast

        sig = inspect.signature(Beast.__init__)
        params = list(sig.parameters.keys())

        required_params = [
            "config",
            "vector_store",
            "embedding_provider",
            "project_store",
            "event_store",
            "entity_store",
            "canonical_store",
            "beast_provider",
            "artifact_store",
            "ecs_store",
            "lexical_store",
            "corpus_turn_store",
        ]
        for param in required_params:
            assert param in params, f"Beast missing parameter: {param}"

    def test_vigil_receives_all_stores(self):
        """Vigil actor constructor accepts all stores wired in lifespan."""
        import inspect

        from aip.orchestration.actors.vigil import Vigil

        sig = inspect.signature(Vigil.__init__)
        params = list(sig.parameters.keys())

        required_params = [
            "config",
            "vigil_store",
            "canonical_store",
            "entity_store",
            "model_provider",
            "trace_store",
            "artifact_store",
            "ecs_store",
            "event_store",
            "corpus_turn_store",
            "alert_manager",
            "quality_store",
        ]
        for param in required_params:
            assert param in params, f"Vigil missing parameter: {param}"

    def test_sexton_receives_alert_manager(self):
        """Sexton actor constructor accepts alert_manager parameter."""
        import inspect

        from aip.orchestration.actors.sexton import Sexton

        sig = inspect.signature(Sexton.__init__)
        params = list(sig.parameters.keys())

        assert "alert_manager" in params, "Sexton missing parameter: alert_manager"


# ---------------------------------------------------------------------------
# Test 20: End-to-end smoke
# ---------------------------------------------------------------------------


class TestEndToEndSmoke:
    """End-to-end smoke test: stores → actors → channels → readiness."""

    def test_full_dogfood_mode_all_components_ready(self):
        """With all components present, FULL mode is ready."""
        container = _make_full_container()
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)

        # All components must be True
        for name, ok in check.required_components.items():
            assert ok, f"Component {name} is not initialized"

        # All actors must be active
        for name, ok in check.required_actors.items():
            assert ok, f"Actor {name} is not active"

        # All channels must be available
        for name, ok in check.retrieval_channels.items():
            assert ok, f"Retrieval channel {name} is not available"

        # DB paths must be valid
        assert check.db_paths_valid, "DB paths are not valid"

        # Embedding provider must be active
        assert check.embedding_provider_active, "Embedding provider is not active"

        # Overall readiness
        assert check.is_ready is True

    def test_minimal_mode_with_empty_container(self):
        """MINIMAL mode with empty container — just checks, no blocking."""
        container = FakeContainer()
        config = {"alpha": {"dogfood_mode": "minimal"}}
        check = validate_dogfood_readiness(config, container)

        # Should not be ready (it's minimal mode)
        assert check.is_ready is False

        # But should have populated all the checks for operator visibility
        assert len(check.required_components) > 0
        assert len(check.required_actors) > 0
        assert len(check.retrieval_channels) > 0

    def test_full_mode_missing_actor_degrades_loudly(self):
        """FULL mode with missing actor: is_ready=False, degraded list includes actor."""
        container = _make_full_container()
        # Remove an actor
        container.vigil = None
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)

        assert check.is_ready is False
        assert "vigil" in check.degraded_components

    def test_full_mode_missing_retrieval_channel_degrades(self):
        """FULL mode with missing retrieval channel: is_ready=False."""
        container = _make_full_container()
        # Remove graph_store → affects graph, wiki, and procedural channels
        container.graph_store = None
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)

        assert check.is_ready is False
        assert "graph" in check.degraded_components

    def test_full_mode_missing_embedding_provider_degrades(self):
        """FULL mode without embedding provider: is_ready=False."""
        container = _make_full_container()
        container.embedding_provider = None
        config = {"alpha": {"dogfood_mode": "full"}}
        check = validate_dogfood_readiness(config, container)

        assert check.is_ready is False
        assert "embedding_provider" in check.degraded_components

    def test_config_toml_has_alpha_section(self):
        """The main config file has [alpha] dogfood_mode section."""
        config_path = Path(__file__).parent.parent / "config" / "aip.config.toml"
        if config_path.exists():
            content = config_path.read_text()
            assert "[alpha]" in content, "Missing [alpha] section in config"
            assert "dogfood_mode" in content, "Missing dogfood_mode in [alpha] section"
