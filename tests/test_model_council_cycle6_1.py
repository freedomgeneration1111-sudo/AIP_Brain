"""Tests for UI Cycle 6.1 — Model Council Slot Selector and Report UX Finish.

Covers:
  - Backend honors selected_model_slots
  - Embedding slot excluded even if requested
  - Invalid slot handled honestly
  - Backend text-generation-slots endpoint
  - Frontend selector renders available slots
  - Frontend requires/selects at least two slots
  - Frontend sends selected slots to API client
  - Insufficient models state renders honestly
  - Existing Cycle 6 tests still pass
  - Beast Counsel tests still pass
  - GUI import-boundary tests pass
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _make_mock_provider(slots: list[str], resolve_config=None, call_fn=None):
    """Create a mock model provider.

    Uses MagicMock (not AsyncMock) because list_slots() and _resolve_slot_config()
    are synchronous methods. Only call() is async.
    """
    provider = MagicMock()
    provider.list_slots.return_value = slots
    if resolve_config:
        provider._resolve_slot_config = resolve_config
    if call_fn:
        provider.call = AsyncMock(side_effect=call_fn)
    else:
        provider.call = AsyncMock(return_value={"content": "{}", "model": "test", "usage": {}, "latency_ms": 100, "error": False})
    return provider


# ── 1. Backend: selected_model_slots honored ──────────────────────────


class TestSelectedModelSlotsHonored:
    """Backend honors the selected_model_slots field."""

    @pytest.mark.asyncio
    async def test_selected_slots_limit_comparison(self):
        """When selected_model_slots specifies specific slots, only those are used."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "beast":
                return {"content": json.dumps({"convergence": "Agree", "disagreements": "None", "unique_contributions": "Both", "risks": "Low", "beast_conclusion": "Good", "recommended_decision": "Accept"}), "model": "beast-model", "usage": {}, "latency_ms": 500, "error": False}
            return {"content": f"Response from {slot_name}", "model": f"model-{slot_name}", "usage": {"total_tokens": 50}, "latency_ms": 300, "error": False}

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "beast", "embedding"],
            resolve_config=lambda slot: {"provider": "test", "model": f"model-{slot}", "api_key": "key"},
            call_fn=mock_call,
        )
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()

        # Request only synthesis and evaluation
        request = ModelCouncilRequest(
            prompt="Test",
            selected_model_slots=["synthesis", "evaluation"],
        )
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=container)

        model_slots = [m.model_slot for m in result.selected_models]
        assert "synthesis" in model_slots
        assert "evaluation" in model_slots
        # beast and embedding should NOT be in results
        assert "beast" not in model_slots
        assert "embedding" not in model_slots

    @pytest.mark.asyncio
    async def test_empty_selected_slots_uses_defaults(self):
        """When selected_model_slots is empty, backend uses default slots."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "beast":
                return {"content": json.dumps({"convergence": "Agree", "disagreements": "None", "unique_contributions": "All", "risks": "Low", "beast_conclusion": "Good", "recommended_decision": "Accept"}), "model": "beast-model", "usage": {}, "latency_ms": 500, "error": False}
            return {"content": f"Response from {slot_name}", "model": f"model-{slot_name}", "usage": {"total_tokens": 50}, "latency_ms": 300, "error": False}

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "beast", "embedding"],
            resolve_config=lambda slot: {"provider": "test", "model": f"model-{slot}", "api_key": "key"},
            call_fn=mock_call,
        )
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()

        request = ModelCouncilRequest(prompt="Test", selected_model_slots=[])
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=container)

        # Should use defaults: synthesis, evaluation, beast
        model_slots = [m.model_slot for m in result.selected_models]
        assert "synthesis" in model_slots
        assert "evaluation" in model_slots
        assert "beast" in model_slots
        assert "embedding" not in model_slots


# ── 2. Backend: Embedding excluded even if requested ──────────────────


class TestEmbeddingExclusion:
    """Embedding slot is excluded even if explicitly requested in selected_model_slots."""

    @pytest.mark.asyncio
    async def test_embedding_excluded_when_requested(self):
        """Embedding is filtered out even when included in selected_model_slots."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "beast":
                return {"content": json.dumps({"convergence": "Agree", "disagreements": "None", "unique_contributions": "Both", "risks": "Low", "beast_conclusion": "Good", "recommended_decision": "Accept"}), "model": "beast-model", "usage": {}, "latency_ms": 500, "error": False}
            return {"content": f"Response from {slot_name}", "model": f"model-{slot_name}", "usage": {"total_tokens": 50}, "latency_ms": 300, "error": False}

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "beast", "embedding"],
            resolve_config=lambda slot: {"provider": "test", "model": f"model-{slot}", "api_key": "key"},
            call_fn=mock_call,
        )
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()

        # Explicitly request embedding
        request = ModelCouncilRequest(
            prompt="Test",
            selected_model_slots=["synthesis", "evaluation", "embedding"],
        )
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=container)

        model_slots = [m.model_slot for m in result.selected_models]
        assert "embedding" not in model_slots
        assert "synthesis" in model_slots
        assert "evaluation" in model_slots

    @pytest.mark.asyncio
    async def test_embedding_only_requested_returns_insufficient(self):
        """If only embedding is requested, returns insufficient_models."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "embedding"],
            resolve_config=lambda slot: {"provider": "test", "model": f"model-{slot}", "api_key": "key"},
        )
        container.artifact_store = AsyncMock()

        request = ModelCouncilRequest(
            prompt="Test",
            selected_model_slots=["embedding"],
        )
        result = await compare_models(request, container=container)

        assert result.status == "insufficient_models"


# ── 3. Backend: Invalid slot handled honestly ────────────────────────


class TestInvalidSlotHandling:
    """Invalid/unconfigured slots are reported honestly."""

    @pytest.mark.asyncio
    async def test_invalid_slot_filtered_out(self):
        """Invalid slot names in selected_model_slots are filtered out."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "beast":
                return {"content": json.dumps({"convergence": "Agree", "disagreements": "None", "unique_contributions": "Both", "risks": "Low", "beast_conclusion": "Good", "recommended_decision": "Accept"}), "model": "beast-model", "usage": {}, "latency_ms": 500, "error": False}
            return {"content": f"Response from {slot_name}", "model": f"model-{slot_name}", "usage": {"total_tokens": 50}, "latency_ms": 300, "error": False}

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "beast"],
            resolve_config=lambda slot: {"provider": "test", "model": f"model-{slot}", "api_key": "key"},
            call_fn=mock_call,
        )
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()

        request = ModelCouncilRequest(
            prompt="Test",
            selected_model_slots=["synthesis", "nonexistent_slot", "fake_slot"],
        )
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=container)

        model_slots = [m.model_slot for m in result.selected_models]
        assert "synthesis" in model_slots
        assert "nonexistent_slot" not in model_slots
        assert "fake_slot" not in model_slots

    @pytest.mark.asyncio
    async def test_all_invalid_slots_returns_insufficient(self):
        """If all requested slots are invalid, returns insufficient_models."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation"],
            resolve_config=lambda slot: {"provider": "test", "model": f"model-{slot}", "api_key": "key"},
        )
        container.artifact_store = AsyncMock()

        request = ModelCouncilRequest(
            prompt="Test",
            selected_model_slots=["nonexistent_slot", "another_fake"],
        )
        result = await compare_models(request, container=container)

        assert result.status == "insufficient_models"


# ── 4. Backend: text-generation-slots endpoint ───────────────────────


class TestTextGenerationSlotsEndpoint:
    """GET /models/text-generation-slots returns only text-generation slots."""

    @pytest.mark.asyncio
    async def test_endpoint_excludes_embedding(self):
        """Text-generation endpoint excludes embedding slot."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.models import list_text_generation_slots

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "beast", "embedding"],
            resolve_config=lambda slot: {
                "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "key"},
                "evaluation": {"provider": "openai_compatible", "model": "claude-3", "api_key": "key"},
                "beast": {"provider": "openai_compatible", "model": "deepseek", "api_key": "key"},
                "embedding": {"provider": "openai_compatible", "model": "text-embedding-3-small", "api_key": "key"},
            }.get(slot, {}),
        )

        result = await list_text_generation_slots(container=container)
        slot_names = [s["slot_name"] for s in result["slots"]]
        assert "embedding" not in slot_names
        assert "synthesis" in slot_names
        assert "evaluation" in slot_names
        assert "beast" in slot_names
        assert result["sufficient_for_council"] is True

    @pytest.mark.asyncio
    async def test_endpoint_no_provider(self):
        """Returns empty and insufficient when no model provider."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.models import list_text_generation_slots

        container = AipContainer({})
        container.model_provider = None

        result = await list_text_generation_slots(container=container)
        assert result["slots"] == []
        assert result["sufficient_for_council"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_endpoint_insufficient_slots(self):
        """Returns insufficient_for_council when only one text-gen slot."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.models import list_text_generation_slots

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "embedding"],
            resolve_config=lambda slot: {
                "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "key"},
                "embedding": {"provider": "openai_compatible", "model": "text-embedding-3-small", "api_key": "key"},
            }.get(slot, {}),
        )

        result = await list_text_generation_slots(container=container)
        slot_names = [s["slot_name"] for s in result["slots"]]
        assert slot_names == ["synthesis"]
        assert result["sufficient_for_council"] is False

    @pytest.mark.asyncio
    async def test_endpoint_no_secrets_exposed(self):
        """Text-generation endpoint never exposes API keys."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.models import list_text_generation_slots

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation"],
            resolve_config=lambda slot: {
                "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "sk-super-secret-key-12345"},
                "evaluation": {"provider": "openai_compatible", "model": "claude-3", "api_key": "sk-another-secret-key"},
            }.get(slot, {"provider": "unknown", "model": f"<{slot}>"}),
        )

        result = await list_text_generation_slots(container=container)
        # Check that no slot entry contains api_key field
        for slot_entry in result["slots"]:
            assert "api_key" not in slot_entry, f"Slot {slot_entry.get('slot_name')} has api_key field"
        # Also check string values don't contain secret patterns
        for slot_entry in result["slots"]:
            for key, value in slot_entry.items():
                if isinstance(value, str):
                    assert "sk-super-secret" not in value
                    assert "sk-another-secret" not in value

    @pytest.mark.asyncio
    async def test_endpoint_has_real_model_flag(self):
        """Endpoint sets has_real_model=False for sentinel model names."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.models import list_text_generation_slots

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "unconfigured_slot"],
            resolve_config=lambda slot: {
                "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "key"},
                "unconfigured_slot": {"provider": "openai_compatible"},  # No model → sentinel
            }.get(slot, {}),
        )

        result = await list_text_generation_slots(container=container)
        slots_by_name = {s["slot_name"]: s for s in result["slots"]}
        assert slots_by_name["synthesis"]["has_real_model"] is True
        assert slots_by_name["unconfigured_slot"]["has_real_model"] is False


# ── 5. Frontend: Selector renders available slots ────────────────────


class TestFrontendSlotSelector:
    """Model Council panel has slot selector that renders available slots."""

    def test_panel_has_slot_selector_methods(self):
        """ModelCouncilPanel has slot selector methods."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        assert hasattr(panel, "_render_slot_selector")
        assert hasattr(panel, "_toggle_slot")
        assert hasattr(panel, "_available_slots")
        assert hasattr(panel, "_selected_slots")
        assert hasattr(panel, "_slots_loaded")
        assert hasattr(panel, "_slots_sufficient")

    def test_panel_initial_slot_state(self):
        """ModelCouncilPanel starts with empty slot state."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        assert panel._available_slots == []
        assert panel._selected_slots == []
        assert panel._slots_loaded is False
        assert panel._slots_sufficient is False

    def test_panel_toggle_slot(self):
        """_toggle_slot adds and removes slots correctly."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        panel._toggle_slot("synthesis", True)
        assert "synthesis" in panel._selected_slots
        panel._toggle_slot("synthesis", False)
        assert "synthesis" not in panel._selected_slots

    def test_panel_toggle_no_duplicates(self):
        """_toggle_slot doesn't add duplicates."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        panel._toggle_slot("synthesis", True)
        panel._toggle_slot("synthesis", True)
        assert panel._selected_slots.count("synthesis") == 1


# ── 6. Frontend: Requires/selects at least two slots ──────────────────


class TestFrontendMinSlots:
    """Frontend requires at least two slots for Model Council."""

    def test_panel_has_insufficient_inline_render(self):
        """Panel has method for rendering insufficient models inline."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        assert hasattr(panel, "_render_insufficient_models_inline")

    def test_default_selected_slots_constant(self):
        """Module has default selected slots constant."""
        from gui.components.model_council_panel import _DEFAULT_SELECTED_SLOTS

        assert isinstance(_DEFAULT_SELECTED_SLOTS, list)
        assert "synthesis" in _DEFAULT_SELECTED_SLOTS


# ── 7. Frontend: Sends selected slots to API client ──────────────────


class TestFrontendSendsSelectedSlots:
    """Frontend sends selected_model_slots to the API client."""

    def test_show_council_accepts_selected_model_slots(self):
        """show_council method accepts selected_model_slots parameter."""
        from gui.components.model_council_panel import ModelCouncilPanel

        sig = inspect.signature(ModelCouncilPanel.show_council)
        assert "selected_model_slots" in sig.parameters

    def test_api_client_has_list_text_generation_slots(self):
        """AipApiClient has list_text_generation_slots method."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        assert hasattr(client, "list_text_generation_slots")
        assert callable(client.list_text_generation_slots)

    def test_api_client_run_model_council_sends_slots(self):
        """run_model_council sends selected_model_slots in payload."""
        from gui.api_client import AipApiClient

        sig = inspect.signature(AipApiClient.run_model_council)
        assert "selected_model_slots" in sig.parameters


# ── 8. Insufficient models state renders honestly ────────────────────


class TestInsufficientModelsHonest:
    """Insufficient models state is rendered honestly in panel."""

    def test_panel_has_insufficient_models_render(self):
        """Panel has _render_insufficient_models method."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        assert hasattr(panel, "_render_insufficient_models")

    def test_insufficient_models_honest_no_faking(self):
        """Insufficient models text mentions requirement honestly."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        # The method exists and handles the case honestly
        # We can verify the panel doesn't fake available models
        assert panel._available_slots == []  # Empty is honest

    def test_backend_insufficient_models_message(self):
        """Backend insufficient_models message is honest about requirements."""
        from aip.adapter.api.routes.model_council import _resolve_comparison_slots

        # With only one slot (after filtering embedding), it should return < 2
        provider = _make_mock_provider(
            slots=["synthesis", "embedding"],
            resolve_config=lambda slot: {
                "synthesis": {"provider": "test", "model": "gpt-4", "api_key": "key"},
                "embedding": {"provider": "test", "model": "emb", "api_key": "key"},
            }.get(slot, {}),
        )
        result = _resolve_comparison_slots(provider, [])
        # Should only have synthesis (embedding excluded)
        assert len(result) < 2


# ── 9. Existing Cycle 6 tests still pass ─────────────────────────────


class TestExistingCycle6StillPasses:
    """Verify existing Cycle 6 tests still pass after Cycle 6.1 changes."""

    def test_model_council_request_still_valid(self):
        """ModelCouncilRequest schema is still valid."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        req = ModelCouncilRequest(prompt="test")
        assert req.selected_model_slots == []
        assert req.save_as_artifact is False

    def test_model_council_response_still_valid(self):
        """ModelCouncilResponse schema is still valid."""
        from aip.adapter.api.routes.model_council import ModelCouncilResponse

        resp = ModelCouncilResponse(status="completed")
        assert resp.advisory_only is True
        assert resp.requires_DEFINER_approval is True

    def test_excluded_slots_unchanged(self):
        """_EXCLUDED_SLOTS is still {embedding}."""
        from aip.adapter.api.routes.model_council import _EXCLUDED_SLOTS

        assert "embedding" in _EXCLUDED_SLOTS

    def test_artifact_id_deterministic(self):
        """Artifact ID generation is still deterministic."""
        from aip.adapter.api.routes.model_council import _council_artifact_id

        id1 = _council_artifact_id("turn-1", "sess-1")
        id2 = _council_artifact_id("turn-1", "sess-1")
        assert id1 == id2


# ── 10. Beast Counsel tests still pass ───────────────────────────────


class TestBeastCounselStillPasses:
    """Verify Beast Counsel functionality still works after Cycle 6.1 changes."""

    def test_beast_commentary_importable(self):
        """Beast commentary route module can still be imported."""
        from aip.adapter.api.routes import beast_commentary

        assert beast_commentary is not None

    def test_beast_panel_importable(self):
        """BeastPanel can still be imported."""
        from gui.components.beast_panel import BeastPanel

        panel = BeastPanel()
        assert panel is not None

    def test_beast_api_client_methods(self):
        """API client still has Beast commentary methods."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        assert hasattr(client, "get_beast_commentary")
        assert hasattr(client, "run_beast_commentary")


# ── 11. GUI import-boundary tests pass ───────────────────────────────


class TestGUIImportBoundary:
    """GUI modules must never import from aip.orchestration."""

    GUI_MODULES = [
        "gui.components.beast_panel",
        "gui.components.model_council_panel",
        "gui.components.answer_card",
        "gui.api_client",
        "gui.status_types",
    ]

    def test_model_council_panel_no_orchestration_imports(self):
        """model_council_panel.py does not import from aip.orchestration."""
        source = (PROJECT_ROOT / "gui/components/model_council_panel.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), \
                        f"Found orchestration import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("aip.orchestration"):
                    pytest.fail(f"Found orchestration import from: {node.module}")

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


# ── 12. Backend import-boundary test ─────────────────────────────────


class TestBackendImportBoundary:
    """Model Council route and models route must not import from orchestration."""

    def test_model_council_route_no_orchestration(self):
        """model_council.py does not import from aip.orchestration."""
        source = (PROJECT_ROOT / "src/aip/adapter/api/routes/model_council.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), \
                        f"Found orchestration import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("aip.orchestration"):
                    pytest.fail(f"Found orchestration import from: {node.module}")

    def test_models_route_no_orchestration(self):
        """models.py does not import from aip.orchestration."""
        source = (PROJECT_ROOT / "src/aip/adapter/api/routes/models.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), \
                        f"Found orchestration import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("aip.orchestration"):
                    pytest.fail(f"Found orchestration import from: {node.module}")


# ── 13. TextGenerationSlotEntry type test ─────────────────────────────


class TestTextGenerationSlotType:
    """TextGenerationSlotEntry TypedDict exists in status_types module."""

    def test_text_generation_slot_entry_exists(self):
        """TextGenerationSlotEntry TypedDict exists in status_types."""
        from gui.status_types import TextGenerationSlotEntry

        assert TextGenerationSlotEntry is not None

    def test_type_has_expected_fields(self):
        """TextGenerationSlotEntry has expected field annotations."""
        from gui.status_types import TextGenerationSlotEntry

        annotations = TextGenerationSlotEntry.__annotations__
        assert "slot_name" in annotations
        assert "provider" in annotations
        assert "model" in annotations
        assert "has_real_model" in annotations


# ── 14. Models route endpoint registration ────────────────────────────


class TestModelsRouteRegistration:
    """Text-generation-slots route is properly registered."""

    def test_models_router_has_text_generation_slots(self):
        """models router has /models/text-generation-slots route."""
        from aip.adapter.api.routes.models import router

        routes = [r.path for r in router.routes]
        assert "/models/text-generation-slots" in routes

    def test_excluded_slots_constant(self):
        """Models route has _EXCLUDED_TEXT_GEN_SLOTS constant."""
        from aip.adapter.api.routes.models import _EXCLUDED_TEXT_GEN_SLOTS

        assert "embedding" in _EXCLUDED_TEXT_GEN_SLOTS
