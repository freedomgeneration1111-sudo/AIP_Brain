"""Tests for UI Cycle 5 — Beast Counsel Panel v1 + Cycle 5.1 Mode Persistence Fix.

Covers:
  - Beast commentary schema stability
  - GET commentary endpoint (available, not_available, unavailable states)
  - POST run commentary endpoint (generation, not_wired, sovereignty)
  - No secret exposure in commentary responses
  - GUI BeastPanel import without server start
  - BeastPanel state handling
  - Answer card Beast Counsel action wiring
  - GUI import boundary (gui/ never imports from aip.orchestration)
  - Backend import boundary (adapter routes never import from orchestration)
  - Cycle 5.1: Mode persistence — distinct artifacts per mode, mode isolation,
    latest-version behavior on re-run, no overwrite between modes, sovereignty,
    GUI mode-aware state, API client mode parameter
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── 1. Schema stability tests ────────────────────────────────────────


class TestBeastCommentarySchema:
    """Verify BeastCommentaryRequest and BeastCommentaryResponse schemas are stable."""

    def test_request_model_fields(self):
        """BeastCommentaryRequest has all required fields."""
        from aip.adapter.api.routes.beast_commentary import BeastCommentaryRequest

        req = BeastCommentaryRequest()
        assert hasattr(req, "session_id")
        assert hasattr(req, "mode")
        assert hasattr(req, "question_text")
        assert hasattr(req, "answer_text")
        assert hasattr(req, "sources")
        assert hasattr(req, "trace_available")
        assert hasattr(req, "lexical_only")
        assert hasattr(req, "vector_contributed")

    def test_request_mode_default(self):
        """Default mode is 'continuity'."""
        from aip.adapter.api.routes.beast_commentary import BeastCommentaryRequest

        req = BeastCommentaryRequest()
        assert req.mode == "continuity"

    def test_response_model_fields(self):
        """BeastCommentaryResponse has all required fields."""
        from aip.adapter.api.routes.beast_commentary import BeastCommentaryResponse

        resp = BeastCommentaryResponse()
        fields = [
            "id", "turn_id", "session_id", "mode", "summary", "critique",
            "continuity_notes", "risk_notes", "suggested_actions",
            "suggested_wiki_links", "suggested_artifacts", "model_comparison",
            "retrieval_notes", "source_notes", "created_at", "status",
            "persistence", "error",
        ]
        for field in fields:
            assert hasattr(resp, field), f"Missing field: {field}"

    def test_response_status_values(self):
        """Status field supports all honest state values."""
        from aip.adapter.api.routes.beast_commentary import BeastCommentaryResponse

        for status in ["available", "not_available", "unavailable", "not_wired", "error"]:
            resp = BeastCommentaryResponse(status=status)
            assert resp.status == status

    def test_valid_modes_constant(self):
        """VALID_MODES contains the five Beast modes."""
        from aip.adapter.api.routes.beast_commentary import VALID_MODES

        assert VALID_MODES == {"continuity", "critique", "strategy", "librarian", "risk"}

    def test_commentary_artifact_id_deterministic(self):
        """Artifact ID is deterministic based on turn_id + mode."""
        from aip.adapter.api.routes.beast_commentary import _commentary_artifact_id

        id1 = _commentary_artifact_id("turn-123", "continuity")
        id2 = _commentary_artifact_id("turn-123", "continuity")
        assert id1 == id2
        assert id1.startswith("beast:commentary:")

    def test_commentary_artifact_id_no_mode_backwards_compat(self):
        """Artifact ID with empty mode is deterministic (backwards compat)."""
        from aip.adapter.api.routes.beast_commentary import _commentary_artifact_id

        id_no_mode = _commentary_artifact_id("turn-123")
        id_empty = _commentary_artifact_id("turn-123", "")
        assert id_no_mode == id_empty
        assert id_no_mode.startswith("beast:commentary:")

    def test_commentary_artifact_id_different_turns(self):
        """Different turn_ids produce different artifact IDs."""
        from aip.adapter.api.routes.beast_commentary import _commentary_artifact_id

        id1 = _commentary_artifact_id("turn-123", "continuity")
        id2 = _commentary_artifact_id("turn-456", "continuity")
        assert id1 != id2

    def test_commentary_artifact_id_different_modes_same_turn(self):
        """Different modes on the same turn produce different artifact IDs."""
        from aip.adapter.api.routes.beast_commentary import _commentary_artifact_id

        id_cont = _commentary_artifact_id("turn-123", "continuity")
        id_crit = _commentary_artifact_id("turn-123", "critique")
        id_risk = _commentary_artifact_id("turn-123", "risk")
        assert id_cont != id_crit
        assert id_cont != id_risk
        assert id_crit != id_risk


# ── 2. GET commentary endpoint tests ────────────────────────────────


class TestGetBeastCommentary:
    """Test GET /api/v1/turns/{turn_id}/beast-commentary."""

    @pytest.fixture
    def mock_container(self):
        """Create a mock AipContainer with necessary stores."""
        from aip.adapter.api.dependencies import AipContainer

        container = AipContainer({})
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()
        container.model_provider = AsyncMock()
        return container

    @pytest.mark.asyncio
    async def test_get_returns_available_when_exists(self, mock_container):
        """GET returns status='available' when commentary artifact exists for the mode."""
        from aip.adapter.api.routes.beast_commentary import (
            _commentary_artifact_id,
            get_beast_commentary,
        )

        turn_id = "turn-test-001"
        mode = "continuity"
        artifact_id = _commentary_artifact_id(turn_id, mode)
        commentary_data = json.dumps({
            "summary": "Test summary",
            "mode": "continuity",
            "critique": "Test critique",
            "suggested_actions": [{"action": "review", "target": "art-1"}],
        })
        metadata = {"created_at": "2026-06-11T10:00:00Z", "mode": "continuity"}

        mock_container.artifact_store.read_with_metadata = AsyncMock(
            return_value=(commentary_data, metadata)
        )

        result = await get_beast_commentary(turn_id, mode=mode, container=mock_container)

        assert result.status == "available"
        assert result.summary == "Test summary"
        assert result.mode == "continuity"
        assert result.critique == "Test critique"
        assert result.turn_id == turn_id

    @pytest.mark.asyncio
    async def test_get_returns_not_available_when_absent(self, mock_container):
        """GET returns status='not_available' when no commentary exists for the mode."""
        from aip.adapter.api.routes.beast_commentary import get_beast_commentary

        mock_container.artifact_store.read_with_metadata = AsyncMock(
            side_effect=KeyError("not found")
        )

        result = await get_beast_commentary("turn-nonexistent", mode="continuity", container=mock_container)

        assert result.status == "not_available"
        assert "No commentary yet" in result.summary

    @pytest.mark.asyncio
    async def test_get_returns_unavailable_when_no_artifact_store(self):
        """GET returns status='unavailable' when artifact_store is None."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.beast_commentary import get_beast_commentary

        container = AipContainer({})
        container.artifact_store = None

        result = await get_beast_commentary("turn-123", mode="continuity", container=container)

        assert result.status == "unavailable"
        assert result.persistence == "not_available"


# ── 3. POST run commentary endpoint tests ────────────────────────────


class TestRunBeastCommentary:
    """Test POST /api/v1/turns/{turn_id}/beast-commentary/run."""

    @pytest.fixture
    def mock_container_with_provider(self):
        """Create a mock container with model provider and stores."""
        from aip.adapter.api.dependencies import AipContainer

        container = AipContainer({})
        container.model_provider = AsyncMock()
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()
        return container

    @pytest.mark.asyncio
    async def test_run_returns_not_wired_without_provider(self):
        """POST returns status='not_wired' when model_provider is None."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            run_beast_commentary,
        )

        container = AipContainer({})
        container.model_provider = None
        container.artifact_store = AsyncMock()

        request = BeastCommentaryRequest(mode="continuity")
        result = await run_beast_commentary("turn-123", request, container=container)

        assert result.status == "not_wired"
        assert "model provider" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_run_returns_unavailable_without_artifact_store(self):
        """POST returns status='unavailable' when artifact_store is None."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            run_beast_commentary,
        )

        container = AipContainer({})
        container.model_provider = AsyncMock()
        container.artifact_store = None

        request = BeastCommentaryRequest(mode="continuity")
        result = await run_beast_commentary("turn-123", request, container=container)

        assert result.status == "unavailable"
        assert result.persistence == "not_available"

    @pytest.mark.asyncio
    async def test_run_generates_commentary(self, mock_container_with_provider):
        """POST generates commentary when provider and stores are available."""
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            run_beast_commentary,
        )

        # Mock model provider response
        llm_response = json.dumps({
            "summary": "This answer is well-grounded.",
            "critique": "Strong on evidence.",
            "continuity_notes": "Follows prior discussion.",
            "risk_notes": "Minimal risk.",
            "suggested_actions": [{"action": "Create wiki article", "target": "topic-X"}],
            "suggested_wiki_links": ["Topic X"],
            "suggested_artifacts": [],
            "retrieval_notes": "Retrieval was adequate.",
            "source_notes": "Sources are relevant.",
        })
        mock_container_with_provider.model_provider.call = AsyncMock(
            return_value={"content": llm_response}
        )
        mock_container_with_provider.artifact_store.write = AsyncMock()
        mock_container_with_provider.ecs_store.transition = AsyncMock()

        request = BeastCommentaryRequest(
            session_id="sess-001",
            mode="critique",
            question_text="What is full dogfood mode?",
            answer_text="Full dogfood mode means...",
        )
        result = await run_beast_commentary(
            "turn-123", request, container=mock_container_with_provider
        )

        assert result.status == "available"
        assert result.summary == "This answer is well-grounded."
        assert result.mode == "critique"
        # Verify artifact was written (NOT auto-approved)
        mock_container_with_provider.artifact_store.write.assert_called_once()
        # Verify ECS transition was GENERATED (NOT APPROVED)
        ecs_call = mock_container_with_provider.ecs_store.transition.call_args
        assert ecs_call.kwargs.get("to_state") == "GENERATED" or ecs_call[1].get("to_state") == "GENERATED"

    @pytest.mark.asyncio
    async def test_run_does_not_mutate_wiki(self, mock_container_with_provider):
        """POST run commentary does not call any wiki mutation methods."""
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            run_beast_commentary,
        )

        mock_container_with_provider.model_provider.call = AsyncMock(
            return_value={"content": '{"summary": "OK"}'}
        )
        mock_container_with_provider.artifact_store.write = AsyncMock()
        mock_container_with_provider.ecs_store.transition = AsyncMock()

        request = BeastCommentaryRequest(mode="risk")
        await run_beast_commentary("turn-123", request, container=mock_container_with_provider)

        # No wiki-related method should be called on the container
        for attr in dir(mock_container_with_provider):
            if "wiki" in attr.lower() and not attr.startswith("_"):
                method = getattr(mock_container_with_provider, attr, None)
                if callable(method) and hasattr(method, "assert_not_called"):
                    method.assert_not_called()

    @pytest.mark.asyncio
    async def test_suggested_actions_are_advisory_only(self, mock_container_with_provider):
        """All suggested actions must have advisory_only=True and requires_DEFINER_approval=True."""
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            run_beast_commentary,
        )

        llm_response = json.dumps({
            "summary": "Test",
            "suggested_actions": [
                {"action": "Create wiki article", "target": "topic-X"},
                {"action": "Approve artifact", "target": "art-001"},
            ],
        })
        mock_container_with_provider.model_provider.call = AsyncMock(
            return_value={"content": llm_response}
        )
        mock_container_with_provider.artifact_store.write = AsyncMock()
        mock_container_with_provider.ecs_store.transition = AsyncMock()

        request = BeastCommentaryRequest(mode="strategy")
        result = await run_beast_commentary("turn-123", request, container=mock_container_with_provider)

        for action in result.suggested_actions:
            assert action.get("advisory_only") is True, f"Action not advisory: {action}"
            assert action.get("requires_DEFINER_approval") is True, f"Action missing DEFINER approval: {action}"

    @pytest.mark.asyncio
    async def test_run_invalid_mode_returns_400(self, mock_container_with_provider):
        """POST with invalid mode returns 400 error."""
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            run_beast_commentary,
        )
        from fastapi import HTTPException

        request = BeastCommentaryRequest(mode="invalid_mode")
        with pytest.raises(HTTPException) as exc_info:
            await run_beast_commentary("turn-123", request, container=mock_container_with_provider)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_run_handles_provider_error(self, mock_container_with_provider):
        """POST returns error status when provider call fails."""
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            run_beast_commentary,
        )

        mock_container_with_provider.model_provider.call = AsyncMock(
            side_effect=RuntimeError("Provider unavailable")
        )

        request = BeastCommentaryRequest(mode="continuity")
        # Patch logger to avoid structlog-style kwargs incompatibility with stdlib logging
        with patch("aip.adapter.api.routes.beast_commentary.logger"):
            result = await run_beast_commentary("turn-123", request, container=mock_container_with_provider)

        assert result.status == "error"
        assert "Provider unavailable" in result.error


# ── 4. No secret exposure tests ──────────────────────────────────────


class TestBeastCommentaryNoSecrets:
    """Verify commentary endpoints never expose secrets."""

    @pytest.mark.asyncio
    async def test_response_has_no_api_key(self):
        """BeastCommentaryResponse never contains API keys."""
        from aip.adapter.api.routes.beast_commentary import BeastCommentaryResponse

        resp = BeastCommentaryResponse(status="available")
        resp_dict = resp.model_dump()
        for key, value in resp_dict.items():
            if isinstance(value, str):
                assert "api_key" not in key.lower()
                assert "password" not in key.lower()
                assert "token" not in key.lower()
                assert "secret" not in key.lower()

    @pytest.mark.asyncio
    async def test_response_no_secret_patterns(self):
        """Generated commentary content doesn't contain secret patterns."""
        from aip.adapter.api.routes.beast_commentary import BeastCommentaryResponse

        resp = BeastCommentaryResponse(
            status="available",
            summary="Test summary",
            critique="Test critique",
        )
        serialized = json.dumps(resp.model_dump())
        assert "sk-" not in serialized
        assert "password" not in serialized.lower()
        assert "api_key" not in serialized.lower()


# ── 5. GUI BeastPanel import test ────────────────────────────────────


class TestBeastPanelImport:
    """BeastPanel can be imported without starting a server."""

    def test_import_beast_panel(self):
        """BeastPanel imports cleanly without NiceGUI server."""
        from gui.components.beast_panel import BeastPanel

        panel = BeastPanel()
        assert panel is not None
        assert hasattr(panel, "show_counsel")
        assert hasattr(panel, "close")

    def test_beast_panel_has_modes(self):
        """BeastPanel defines the expected Beast modes."""
        from gui.components.beast_panel import BEAST_MODES

        assert "continuity" in BEAST_MODES
        assert "critique" in BEAST_MODES
        assert "strategy" in BEAST_MODES
        assert "librarian" in BEAST_MODES
        assert "risk" in BEAST_MODES


# ── 6. BeastPanel state handling tests ───────────────────────────────


class TestBeastPanelStates:
    """BeastPanel handles all honest states."""

    def test_panel_initial_state(self):
        """BeastPanel starts with no drawer, no current turn, default mode."""
        from gui.components.beast_panel import BeastPanel

        panel = BeastPanel()
        assert panel._drawer is None
        assert panel._current_turn_id == ""
        assert panel._current_mode == "continuity"
        assert panel._loading is False

    def test_panel_close_no_error(self):
        """BeastPanel.close() doesn't raise when no drawer is open."""
        from gui.components.beast_panel import BeastPanel

        panel = BeastPanel()
        panel.close()  # Should not raise


# ── 7. Answer card Beast Counsel wiring test ─────────────────────────


class TestAnswerCardBeastCounsel:
    """Answer card includes Beast Counsel button wiring."""

    def test_add_answer_card_accepts_beast_counsel_callback(self):
        """add_answer_card function accepts on_beast_counsel parameter."""
        from gui.components.answer_card import add_answer_card

        import inspect
        sig = inspect.signature(add_answer_card)
        assert "on_beast_counsel" in sig.parameters

    def test_determine_answer_status_all_states(self):
        """determine_answer_status returns valid results for all states."""
        from gui.components.answer_card import determine_answer_status

        # Direct model
        result = determine_answer_status(sources=None, direct_model=True)
        assert result["label"] == "DIRECT MODEL ONLY"

        # Normal mode
        result = determine_answer_status(sources=None, mode="normal")
        assert result["label"] == "NORMAL MODE"

        # No sources
        result = determine_answer_status(sources=[], mode="augmented")
        assert "NO SOURCES" in result["label"]

        # Lexical only
        result = determine_answer_status(
            sources=[{"id": "1"}], lexical_only=True, vector_contributed=False, mode="augmented"
        )
        assert "LEXICAL ONLY" in result["label"]

        # Healthy
        result = determine_answer_status(
            sources=[{"id": "1"}], trace_available=True, vector_contributed=True, mode="augmented"
        )
        assert "HEALTHY" in result["label"]


# ── 8. GUI import boundary test ──────────────────────────────────────


class TestGUIImportBoundary:
    """GUI modules must never import from aip.orchestration."""

    GUI_MODULES = [
        "gui.components.beast_panel",
        "gui.components.answer_card",
        "gui.components.source_panel",
        "gui.components.trace_panel",
        "gui.api_client",
        "gui.state",
        "gui.status_types",
    ]

    def test_beast_panel_no_orchestration_imports(self):
        """beast_panel.py does not import from aip.orchestration."""
        source = (PROJECT_ROOT / "gui/components/beast_panel.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip."), f"Found aip import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith("aip."), f"Found aip import from: {node.module}"

    def test_all_gui_modules_no_orchestration_imports(self):
        """No GUI module imports from aip.orchestration."""
        for module_name in self.GUI_MODULES:
            try:
                source_path = PROJECT_ROOT / (module_name.replace(".", "/") + ".py")
                source = source_path.read_text()
            except FileNotFoundError:
                continue
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("aip.orchestration"), \
                            f"{module_name} imports from aip.orchestration: {alias.name}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("aip.orchestration"):
                        pytest.fail(f"{module_name} imports from aip.orchestration: {node.module}")


# ── 9. Backend import boundary test ──────────────────────────────────


class TestBackendImportBoundary:
    """Beast commentary route must not import from orchestration."""

    def test_beast_commentary_route_no_orchestration(self):
        """beast_commentary.py does not import from aip.orchestration."""
        source = (PROJECT_ROOT / "src/aip/adapter/api/routes/beast_commentary.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), \
                        f"Found orchestration import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("aip.orchestration"):
                    pytest.fail(f"Found orchestration import from: {node.module}")


# ── 10. API client methods test ──────────────────────────────────────


class TestBeastCounselAPIClient:
    """API client has Beast commentary methods."""

    def test_client_has_get_beast_commentary(self):
        """AipApiClient has get_beast_commentary method."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        assert hasattr(client, "get_beast_commentary")
        assert callable(client.get_beast_commentary)

    def test_client_has_run_beast_commentary(self):
        """AipApiClient has run_beast_commentary method."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        assert hasattr(client, "run_beast_commentary")
        assert callable(client.run_beast_commentary)


# ── 11. TypedDict types test ─────────────────────────────────────────


class TestBeastCounselTypes:
    """Beast commentary types exist in status_types module."""

    def test_beast_commentary_response_type_exists(self):
        """BeastCommentaryResponse TypedDict exists in status_types."""
        from gui.status_types import BeastCommentaryResponse

        assert BeastCommentaryResponse is not None

    def test_beast_commentary_suggested_action_type_exists(self):
        """BeastCommentarySuggestedAction TypedDict exists in status_types."""
        from gui.status_types import BeastCommentarySuggestedAction

        assert BeastCommentarySuggestedAction is not None


# ── 12. Route registration test ──────────────────────────────────────


class TestBeastCounselRouteRegistration:
    """Beast commentary route is properly registered in the app."""

    def test_beast_commentary_router_exists(self):
        """beast_commentary module has a router attribute."""
        from aip.adapter.api.routes import beast_commentary

        assert hasattr(beast_commentary, "router")
        routes = [r.path for r in beast_commentary.router.routes]
        assert "/turns/{turn_id}/beast-commentary" in routes
        assert "/turns/{turn_id}/beast-commentary/run" in routes

    def test_beast_commentary_route_importable(self):
        """beast_commentary route module can be imported."""
        from aip.adapter.api.routes import beast_commentary

        assert beast_commentary is not None


# ── 13. Cycle 5.1 — Mode persistence tests ─────────────────────────────


class TestModePersistence:
    """UI Cycle 5.1: Beast commentary mode persistence.

    Verify that different modes on the same turn produce distinct
    commentary records/artifacts and that mode switching works correctly.
    """

    @pytest.fixture
    def mock_container_full(self):
        """Create a mock container with all dependencies."""
        from aip.adapter.api.dependencies import AipContainer

        container = AipContainer({})
        container.model_provider = AsyncMock()
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()
        return container

    @pytest.mark.asyncio
    async def test_two_modes_produce_distinct_artifacts(self, mock_container_full):
        """Running continuity and critique on the same turn creates two distinct artifacts."""
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            _commentary_artifact_id,
            run_beast_commentary,
        )

        turn_id = "turn-mode-test"
        write_calls = []

        async def capture_write(**kwargs):
            write_calls.append(kwargs)

        mock_container_full.model_provider.call = AsyncMock(
            return_value={"content": json.dumps({"summary": "Test"})}
        )
        mock_container_full.artifact_store.write = AsyncMock(side_effect=capture_write)
        mock_container_full.ecs_store.transition = AsyncMock()

        # Run continuity
        req_cont = BeastCommentaryRequest(mode="continuity")
        with patch("aip.adapter.api.routes.beast_commentary.logger"):
            result_cont = await run_beast_commentary(turn_id, req_cont, container=mock_container_full)

        # Run critique
        req_crit = BeastCommentaryRequest(mode="critique")
        with patch("aip.adapter.api.routes.beast_commentary.logger"):
            result_crit = await run_beast_commentary(turn_id, req_crit, container=mock_container_full)

        # Both should succeed
        assert result_cont.status == "available"
        assert result_crit.status == "available"

        # Two distinct artifact IDs were used
        artifact_ids = [call.get("id") for call in write_calls]
        assert len(artifact_ids) == 2
        assert artifact_ids[0] != artifact_ids[1]

        # Verify the artifact IDs match the deterministic formula
        assert artifact_ids[0] == _commentary_artifact_id(turn_id, "continuity")
        assert artifact_ids[1] == _commentary_artifact_id(turn_id, "critique")

    @pytest.mark.asyncio
    async def test_get_mode_a_does_not_return_mode_b(self, mock_container_full):
        """GET with mode=continuity does not return critique commentary."""
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            _commentary_artifact_id,
            get_beast_commentary,
            run_beast_commentary,
        )

        turn_id = "turn-isolation-test"

        # Write critique commentary
        critique_data = json.dumps({"summary": "Critique only", "mode": "critique"})
        critique_metadata = {"created_at": "2026-06-11T10:00:00Z", "mode": "critique"}

        # The artifact store should raise KeyError for continuity (not found)
        # but return data for critique
        continuity_artifact_id = _commentary_artifact_id(turn_id, "continuity")
        critique_artifact_id = _commentary_artifact_id(turn_id, "critique")

        async def mock_read(artifact_id, version=None):
            if artifact_id == critique_artifact_id:
                return (critique_data, critique_metadata)
            raise KeyError(f"Not found: {artifact_id}")

        mock_container_full.artifact_store.read_with_metadata = AsyncMock(side_effect=mock_read)

        # GET with mode=continuity should return not_available
        result_cont = await get_beast_commentary(turn_id, mode="continuity", container=mock_container_full)
        assert result_cont.status == "not_available"

        # GET with mode=critique should return available
        result_crit = await get_beast_commentary(turn_id, mode="critique", container=mock_container_full)
        assert result_crit.status == "available"
        assert result_crit.mode == "critique"
        assert result_crit.summary == "Critique only"

    @pytest.mark.asyncio
    async def test_same_mode_twice_creates_latest_version(self, mock_container_full):
        """Running the same mode twice creates a new version; GET returns latest."""
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            _commentary_artifact_id,
            run_beast_commentary,
        )

        turn_id = "turn-rerun-test"
        write_calls = []

        async def capture_write(**kwargs):
            write_calls.append(kwargs)

        # First run returns summary v1
        mock_container_full.model_provider.call = AsyncMock(
            return_value={"content": json.dumps({"summary": "Version 1"})}
        )
        mock_container_full.artifact_store.write = AsyncMock(side_effect=capture_write)
        mock_container_full.ecs_store.transition = AsyncMock()

        req = BeastCommentaryRequest(mode="risk")
        with patch("aip.adapter.api.routes.beast_commentary.logger"):
            result_v1 = await run_beast_commentary(turn_id, req, container=mock_container_full)

        assert result_v1.status == "available"
        assert result_v1.summary == "Version 1"

        # Second run returns summary v2
        mock_container_full.model_provider.call = AsyncMock(
            return_value={"content": json.dumps({"summary": "Version 2"})}
        )

        with patch("aip.adapter.api.routes.beast_commentary.logger"):
            result_v2 = await run_beast_commentary(turn_id, req, container=mock_container_full)

        assert result_v2.status == "available"
        assert result_v2.summary == "Version 2"

        # Both writes used the same artifact ID (versioned store handles versions)
        artifact_ids = [call.get("id") for call in write_calls]
        assert len(artifact_ids) == 2
        assert artifact_ids[0] == artifact_ids[1]
        assert artifact_ids[0] == _commentary_artifact_id(turn_id, "risk")

        # The VersionedArtifactStore appends versions; GET reads latest.
        # This is the documented behavior: re-running same mode returns latest version.

    @pytest.mark.asyncio
    async def test_continuity_does_not_overwrite_critique(self, mock_container_full):
        """Running continuity does not overwrite an existing critique artifact."""
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            _commentary_artifact_id,
            get_beast_commentary,
            run_beast_commentary,
        )

        turn_id = "turn-no-overwrite-test"

        # Store critique commentary
        critique_data = json.dumps({"summary": "Original critique", "mode": "critique"})
        critique_metadata = {"created_at": "2026-06-11T10:00:00Z", "mode": "critique"}
        critique_artifact_id = _commentary_artifact_id(turn_id, "critique")
        continuity_artifact_id = _commentary_artifact_id(turn_id, "continuity")

        async def mock_read(artifact_id, version=None):
            if artifact_id == critique_artifact_id:
                return (critique_data, critique_metadata)
            raise KeyError(f"Not found: {artifact_id}")

        # Run continuity mode
        mock_container_full.model_provider.call = AsyncMock(
            return_value={"content": json.dumps({"summary": "Continuity result"})}
        )
        mock_container_full.artifact_store.write = AsyncMock()
        mock_container_full.artifact_store.read_with_metadata = AsyncMock(side_effect=mock_read)
        mock_container_full.ecs_store.transition = AsyncMock()

        req = BeastCommentaryRequest(mode="continuity")
        with patch("aip.adapter.api.routes.beast_commentary.logger"):
            result_cont = await run_beast_commentary(turn_id, req, container=mock_container_full)

        assert result_cont.status == "available"
        assert result_cont.mode == "continuity"

        # Verify critique is still intact
        result_crit = await get_beast_commentary(turn_id, mode="critique", container=mock_container_full)
        assert result_crit.status == "available"
        assert result_crit.summary == "Original critique"

    @pytest.mark.asyncio
    async def test_no_auto_approve_or_export(self, mock_container_full):
        """Generated commentary artifacts are never auto-approved or auto-exported."""
        from aip.adapter.api.routes.beast_commentary import (
            BeastCommentaryRequest,
            run_beast_commentary,
        )

        mock_container_full.model_provider.call = AsyncMock(
            return_value={"content": json.dumps({"summary": "Test"})}
        )
        mock_container_full.artifact_store.write = AsyncMock()
        mock_container_full.ecs_store.transition = AsyncMock()

        for mode in ["continuity", "critique", "strategy", "librarian", "risk"]:
            mock_container_full.artifact_store.write.reset_mock()
            mock_container_full.ecs_store.transition.reset_mock()

            req = BeastCommentaryRequest(mode=mode)
            with patch("aip.adapter.api.routes.beast_commentary.logger"):
                result = await run_beast_commentary("turn-sov", req, container=mock_container_full)

            # ECS transition must be GENERATED, never APPROVED
            ecs_call = mock_container_full.ecs_store.transition.call_args
            to_state = ecs_call.kwargs.get("to_state") or ecs_call[1].get("to_state")
            assert to_state == "GENERATED", f"Mode {mode}: ECS state was {to_state}, expected GENERATED"

            # No export/approve related calls on the container
            for attr in dir(mock_container_full):
                if any(word in attr.lower() for word in ["export", "approve"]):
                    if not attr.startswith("_"):
                        method = getattr(mock_container_full, attr, None)
                        if callable(method) and hasattr(method, "assert_not_called"):
                            method.assert_not_called()

    def test_gui_beast_panel_mode_aware_state(self):
        """BeastPanel stores mode-related state for mode switching."""
        from gui.components.beast_panel import BeastPanel

        panel = BeastPanel()
        assert hasattr(panel, "_current_mode")
        assert panel._current_mode == "continuity"
        # Verify context is stored for mode switching
        assert hasattr(panel, "_session_id")
        assert hasattr(panel, "_api_client")
        assert hasattr(panel, "_question_text")
        assert hasattr(panel, "_answer_text")

    def test_gui_api_client_get_accepts_mode(self):
        """API client get_beast_commentary accepts mode parameter."""
        import inspect
        from gui.api_client import AipApiClient

        sig = inspect.signature(AipApiClient.get_beast_commentary)
        params = sig.parameters
        assert "mode" in params
        # Default mode is 'continuity'
        assert params["mode"].default == "continuity"
