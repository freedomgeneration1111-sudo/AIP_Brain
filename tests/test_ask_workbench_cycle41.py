"""UI Cycle 4.1 — Ask Workbench API Verification and Sovereignty Tests.

Focused tests for the new Ask Workbench backend/frontend integration:
  - POST /api/v1/turns/save-artifact sovereignty (GENERATED only, no auto-approve)
  - GET /api/v1/retrieval/traces/session/{session_id} honest states
  - Ask/chat metadata compatibility (trace_available, lexical_only, vector_contributed, direct_model)
  - Frontend answer_card component logic
  - Import boundary compliance for new components

Layer discipline: This test module imports from gui.* and aip.adapter.api.*
only — never from aip.orchestration in production-path assertions.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fake stores for testing (lightweight, no DB dependency)
# ---------------------------------------------------------------------------


class FakeArtifactStore:
    """Minimal fake VersionedArtifactStore for save-artifact tests."""

    def __init__(self):
        self.written: list[tuple[str, str, dict]] = []

    async def put(self, artifact_id: str, content: str, metadata: dict) -> None:
        self.written.append((artifact_id, content, metadata))

    async def read(self, artifact_id: str, version: int | None = None) -> str:
        for aid, content, _ in self.written:
            if aid == artifact_id:
                return content
        raise KeyError(artifact_id)

    async def list_versions(self, id: str) -> list[int]:
        return [1]


class FakeEcsStore:
    """Fake EcsStore that records transitions and tracks current state."""

    def __init__(self):
        self.transitions: list[dict] = []
        self._states: dict[str, str] = {}

    async def transition(self, artifact_id, new_state, actor, reason, **kwargs):
        self.transitions.append(
            {
                "artifact_id": artifact_id,
                "new_state": new_state,
                "actor": actor,
                "reason": reason,
            }
        )
        self._states[artifact_id] = new_state

    async def current_state(self, artifact_id):
        return self._states.get(artifact_id)


class FakeLexicalStore:
    """Fake lexical store for indexing tests."""

    def __init__(self):
        self.indexed: list[dict] = []

    async def index_content(self, artifact_id, content, metadata):
        self.indexed.append({"artifact_id": artifact_id, "content": content, "metadata": metadata})


class FakeEventStore:
    """Fake QueryableEventStore for retrieval trace tests."""

    def __init__(self, events=None):
        self._events = events or []

    async def query(self, event_type=None, limit=100):
        results = self._events
        if event_type:
            results = [e for e in results if getattr(e, "event_type", "") == event_type]
        return results[:limit]


class FakeEventObj:
    """Simulates an event store query result object."""

    def __init__(self, actor="", metadata=None, timestamp="", event_type="ask_query"):
        self.actor = actor
        self.metadata = metadata or {}
        self.timestamp = timestamp
        self.event_type = event_type


# ---------------------------------------------------------------------------
# 1. Save-as-Artifact Sovereignty Tests
# ---------------------------------------------------------------------------


class TestSaveArtifactSovereignty:
    """POST /api/v1/turns/save-artifact sovereignty tests.

    Verifies:
      - Creates artifact in GENERATED state only
      - Does not approve artifact
      - Does not export artifact
      - Rejects/handles missing turn content honestly
      - Returns stable response schema
      - Does not expose secrets
    """

    def _make_container(self, artifact_store=None, ecs_store=None, lexical_store=None):
        """Build a minimal AipContainer-like mock."""
        container = MagicMock()
        container.artifact_store = artifact_store or FakeArtifactStore()
        container.ecs_store = ecs_store or FakeEcsStore()
        container.lexical_store = lexical_store or FakeLexicalStore()
        return container

    @pytest.mark.asyncio
    async def test_save_artifact_creates_generated_state_only(self):
        """Artifact must be saved in GENERATED state — never APPROVED."""
        from aip.adapter.api.routes.turns import save_turn_artifact

        artifact_store = FakeArtifactStore()
        ecs_store = FakeEcsStore()
        container = self._make_container(artifact_store, ecs_store)

        result = await save_turn_artifact(
            payload={
                "session_id": "test-session-001",
                "content": "This is a test answer from the assistant.",
                "title": "Test Artifact",
                "domain": "chat",
            },
            container=container,
        )

        assert result["ecs_state"] == "GENERATED", (
            f"Expected ecs_state=GENERATED, got {result['ecs_state']}. Artifacts must NEVER be auto-approved."
        )

    @pytest.mark.asyncio
    async def test_save_artifact_does_not_approve(self):
        """ECS transitions must not contain APPROVED state."""
        from aip.adapter.api.routes.turns import save_turn_artifact

        artifact_store = FakeArtifactStore()
        ecs_store = FakeEcsStore()
        container = self._make_container(artifact_store, ecs_store)

        await save_turn_artifact(
            payload={
                "session_id": "test-session-002",
                "content": "Test content for approval check.",
            },
            container=container,
        )

        for transition in ecs_store.transitions:
            assert transition["new_state"] != "APPROVED", (
                f"ECS transition to APPROVED found: {transition}. Save-as-artifact must NEVER auto-approve."
            )

    @pytest.mark.asyncio
    async def test_save_artifact_does_not_export(self):
        """Save-as-artifact must not trigger any export action.

        We verify this by checking that no EXPORTED state appears
        in the ECS transitions and that the response does not
        mention export.
        """
        from aip.adapter.api.routes.turns import save_turn_artifact

        artifact_store = FakeArtifactStore()
        ecs_store = FakeEcsStore()
        container = self._make_container(artifact_store, ecs_store)

        result = await save_turn_artifact(
            payload={
                "session_id": "test-session-003",
                "content": "Test content for export check.",
            },
            container=container,
        )

        # No EXPORTED state in transitions
        for transition in ecs_store.transitions:
            assert transition["new_state"] != "EXPORTED", (
                "Save-as-artifact triggered an EXPORTED state — artifact should not be exported."
            )

        # Response should not mention export
        response_str = json.dumps(result).lower()
        assert "export" not in response_str, (
            "Save-as-artifact response mentions 'export' — artifact should not be exported."
        )

    @pytest.mark.asyncio
    async def test_save_artifact_rejects_missing_content(self):
        """Missing content should return 400, not silently fail."""
        from aip.adapter.api.routes.turns import save_turn_artifact
        from fastapi import HTTPException

        container = self._make_container()

        with pytest.raises(HTTPException) as exc_info:
            await save_turn_artifact(
                payload={"session_id": "test-session-004", "content": ""},
                container=container,
            )
        assert exc_info.value.status_code == 400
        assert "content" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_save_artifact_rejects_missing_session_id(self):
        """Missing session_id should return 400, not silently fail."""
        from aip.adapter.api.routes.turns import save_turn_artifact
        from fastapi import HTTPException

        container = self._make_container()

        with pytest.raises(HTTPException) as exc_info:
            await save_turn_artifact(
                payload={"session_id": "", "content": "Some content"},
                container=container,
            )
        assert exc_info.value.status_code == 400
        assert "session_id" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_save_artifact_returns_stable_schema(self):
        """Response must always include artifact_id, ecs_state, and message."""
        from aip.adapter.api.routes.turns import save_turn_artifact

        artifact_store = FakeArtifactStore()
        ecs_store = FakeEcsStore()
        container = self._make_container(artifact_store, ecs_store)

        result = await save_turn_artifact(
            payload={
                "session_id": "test-session-005",
                "content": "Schema stability test.",
            },
            container=container,
        )

        # Required fields in response
        assert "artifact_id" in result, "Response missing 'artifact_id'"
        assert "ecs_state" in result, "Response missing 'ecs_state'"
        assert "message" in result, "Response missing 'message'"

        # artifact_id should be a non-empty string
        assert isinstance(result["artifact_id"], str)
        assert len(result["artifact_id"]) > 0

        # ecs_state must be GENERATED
        assert result["ecs_state"] == "GENERATED"

        # message should mention DEFINER review
        assert "DEFINER" in result["message"] or "review" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_save_artifact_does_not_expose_secrets(self):
        """Response must not contain API keys, passwords, or other secrets."""
        from aip.adapter.api.routes.turns import save_turn_artifact

        artifact_store = FakeArtifactStore()
        ecs_store = FakeEcsStore()
        container = self._make_container(artifact_store, ecs_store)

        result = await save_turn_artifact(
            payload={
                "session_id": "test-session-006",
                "content": "Secret leak test.",
            },
            container=container,
        )

        response_str = json.dumps(result).lower()
        secret_patterns = [
            "api_key",
            "apikey",
            "secret",
            "password",
            "token",
            "authorization",
            "bearer",
            "sk-",
            "ghp_",
        ]
        for pattern in secret_patterns:
            assert pattern not in response_str, f"Save-artifact response may expose secret: found '{pattern}'"

    @pytest.mark.asyncio
    async def test_save_artifact_503_when_stores_unavailable(self):
        """Returns 503 when artifact_store or ecs_store is None."""
        from aip.adapter.api.routes.turns import save_turn_artifact
        from fastapi import HTTPException

        # artifact_store is None
        container = self._make_container()
        container.artifact_store = None

        with pytest.raises(HTTPException) as exc_info:
            await save_turn_artifact(
                payload={
                    "session_id": "test-session-007",
                    "content": "Store unavailable test.",
                },
                container=container,
            )
        assert exc_info.value.status_code == 503

        # ecs_store is None
        container2 = self._make_container()
        container2.ecs_store = None

        with pytest.raises(HTTPException) as exc_info:
            await save_turn_artifact(
                payload={
                    "session_id": "test-session-008",
                    "content": "ECS unavailable test.",
                },
                container=container2,
            )
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# 2. Retrieval Trace Endpoint Tests
# ---------------------------------------------------------------------------


class TestRetrievalTraceEndpoint:
    """GET /api/v1/retrieval/traces/session/{session_id} tests.

    Verifies:
      - Returns trace when available
      - Returns honest empty/unavailable state when no trace exists
      - Degraded channel details are preserved if present
      - Endpoint does not fake trace data
    """

    @pytest.mark.asyncio
    async def test_returns_trace_when_available(self):
        """When a matching event with trace metadata exists, returns trace."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_trace_by_session

        event = FakeEventObj(
            actor="session-abc-123",
            metadata={
                "session_id": "session-abc-123",
                "prompt": "What is AIP?",
                "retrieval_total_ms": 150,
                "retrieval_verdict": "OK",
                "retrieval_channels": '["fts", "vector"]',
                "retrieval_per_channel_ms": '{"fts": 50, "vector": 100}',
                "retrieval_hits_before_fusion": 8,
                "retrieval_hits_after_fusion": 5,
                "retrieval_hits_after_gate": 4,
                "retrieval_round": 1,
                "retrieval_channel_contributions": '{"fts": 3, "vector": 2}',
                "lexical_only": False,
                "vector_contributed": True,
            },
            timestamp="2025-01-15T10:00:00Z",
        )

        container = MagicMock()
        container.event_store = FakeEventStore(events=[event])

        result = await retrieval_trace_by_session("session-abc-123", container)

        assert result["status"] == "ok"
        assert result["trace"] is not None
        trace = result["trace"]
        assert trace["session_id"] == "session-abc-123"
        assert trace["total_elapsed_ms"] == 150
        assert trace["verdict"] == "OK"
        assert "fts" in trace["channels_queried"]
        assert "vector" in trace["channels_queried"]
        assert trace["lexical_only"] is False
        assert trace["vector_contributed"] is True

    @pytest.mark.asyncio
    async def test_returns_not_found_when_no_trace(self):
        """Returns honest not_found when no trace event exists for session."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_trace_by_session

        container = MagicMock()
        container.event_store = FakeEventStore(events=[])

        result = await retrieval_trace_by_session("nonexistent-session", container)

        assert result["status"] == "not_found"
        assert result["trace"] is None

    @pytest.mark.asyncio
    async def test_returns_not_found_when_event_store_none(self):
        """Returns honest not_found when event_store is None."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_trace_by_session

        container = MagicMock()
        container.event_store = None

        result = await retrieval_trace_by_session("any-session", container)

        assert result["status"] == "not_found"
        assert result["trace"] is None

    @pytest.mark.asyncio
    async def test_degraded_channel_details_preserved(self):
        """Degraded channel details (lexical_only, no vector) are preserved."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_trace_by_session

        event = FakeEventObj(
            actor="session-degraded",
            metadata={
                "session_id": "session-degraded",
                "prompt": "degraded query",
                "retrieval_total_ms": 50,
                "retrieval_verdict": "NEEDS_MORE_CONTEXT",
                "retrieval_channels": '["fts"]',
                "retrieval_per_channel_ms": '{"fts": 50}',
                "retrieval_hits_before_fusion": 2,
                "retrieval_hits_after_fusion": 2,
                "retrieval_hits_after_gate": 1,
                "retrieval_round": 1,
                "retrieval_channel_contributions": '{"fts": 2}',
                "lexical_only": True,
                "vector_contributed": False,
            },
            timestamp="2025-01-15T10:00:00Z",
        )

        container = MagicMock()
        container.event_store = FakeEventStore(events=[event])

        result = await retrieval_trace_by_session("session-degraded", container)

        assert result["status"] == "ok"
        trace = result["trace"]
        assert trace["lexical_only"] is True, "lexical_only should be True for degraded retrieval"
        assert trace["vector_contributed"] is False, "vector_contributed should be False when vector did not contribute"

    @pytest.mark.asyncio
    async def test_endpoint_does_not_fake_trace_data(self):
        """Endpoint must never fabricate trace data when none exists.

        If no matching event is found, the response must be
        {"status": "not_found", "trace": None} — never a synthetic
        trace object.
        """
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_trace_by_session

        # Event exists but for a DIFFERENT session
        event = FakeEventObj(
            actor="other-session",
            metadata={
                "session_id": "other-session",
                "prompt": "other query",
                "retrieval_total_ms": 100,
                "retrieval_verdict": "OK",
            },
            timestamp="2025-01-15T10:00:00Z",
        )

        container = MagicMock()
        container.event_store = FakeEventStore(events=[event])

        result = await retrieval_trace_by_session("target-session-no-trace", container)

        assert result["status"] == "not_found"
        assert result["trace"] is None, (
            "Endpoint returned a trace object when none should exist — must not fake trace data."
        )

    @pytest.mark.asyncio
    async def test_event_without_retrieval_metadata_skipped(self):
        """Events without retrieval trace metadata are skipped honestly."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_trace_by_session

        # Event for the right session but no retrieval metadata
        event = FakeEventObj(
            actor="session-no-retrieval",
            metadata={
                "session_id": "session-no-retrieval",
                "prompt": "normal chat message",
                # No retrieval_total_ms or retrieval_verdict
            },
            timestamp="2025-01-15T10:00:00Z",
        )

        container = MagicMock()
        container.event_store = FakeEventStore(events=[event])

        result = await retrieval_trace_by_session("session-no-retrieval", container)

        # Should be not_found because event lacks trace metadata
        assert result["status"] == "not_found"
        assert result["trace"] is None


# ---------------------------------------------------------------------------
# 3. Ask/Chat Metadata Compatibility Tests
# ---------------------------------------------------------------------------


class TestAskChatMetadataCompatibility:
    """Tests for ask/chat response metadata fields.

    Verifies:
      - Ask response includes metadata fields without breaking existing clients
      - Retrieve response includes metadata fields if applicable
      - WebSocket response includes metadata fields or omits them safely
      - Direct model response marks direct_model=true
    """

    def test_ask_route_includes_trace_available(self):
        """POST /api/v1/ask response dict must include trace_available."""
        # Verify the ask route builds the response with metadata fields
        # by inspecting the route code
        import ast
        from pathlib import Path

        ask_file = Path(__file__).resolve().parent.parent / "src/aip/adapter/api/routes/ask.py"
        source = ask_file.read_text(encoding="utf-8")

        assert "trace_available" in source, "ask.py must include trace_available in response dict"
        assert "lexical_only" in source, "ask.py must include lexical_only in response dict"
        assert "vector_contributed" in source, "ask.py must include vector_contributed in response dict"

    def test_ask_retrieve_includes_metadata(self):
        """POST /api/v1/ask/retrieve response must include metadata fields."""
        import ast
        from pathlib import Path

        ask_file = Path(__file__).resolve().parent.parent / "src/aip/adapter/api/routes/ask.py"
        source = ask_file.read_text(encoding="utf-8")

        # The retrieve endpoint also returns trace_available, lexical_only, vector_contributed
        assert "trace_available" in source, "ask.py retrieve endpoint must include trace_available"

    def test_websocket_chat_includes_direct_model_flag(self):
        """WebSocket chat response must include direct_model field."""
        from pathlib import Path

        chat_file = Path(__file__).resolve().parent.parent / "src/aip/adapter/api/routes/chat.py"
        source = chat_file.read_text(encoding="utf-8")

        assert '"direct_model"' in source or "'direct_model'" in source, (
            "chat.py must include direct_model in WebSocket response payload"
        )

        # The degraded path should set direct_model=True
        assert '"direct_model": True' in source or "'direct_model': True" in source, (
            "chat.py degraded path must set direct_model=True"
        )

    def test_websocket_normal_path_direct_model_false(self):
        """Normal backend response should have direct_model=False."""
        from pathlib import Path

        chat_file = Path(__file__).resolve().parent.parent / "src/aip/adapter/api/routes/chat.py"
        source = chat_file.read_text(encoding="utf-8")

        assert '"direct_model": False' in source or "'direct_model': False" in source, (
            "chat.py normal response path must set direct_model=False"
        )

    def test_websocket_includes_trace_metadata(self):
        """WebSocket response must include trace_available, lexical_only, vector_contributed."""
        from pathlib import Path

        chat_file = Path(__file__).resolve().parent.parent / "src/aip/adapter/api/routes/chat.py"
        source = chat_file.read_text(encoding="utf-8")

        assert "trace_available" in source, "chat.py must include trace_available in response payload"
        assert "lexical_only" in source, "chat.py must include lexical_only in response payload"
        assert "vector_contributed" in source, "chat.py must include vector_contributed in response payload"


# ---------------------------------------------------------------------------
# 4. Frontend Answer Card Component Tests
# ---------------------------------------------------------------------------


class TestAnswerCardComponent:
    """Tests for gui/components/answer_card.py logic.

    Since NiceGUI components can't be rendered in headless tests,
    we test the determine_answer_status logic directly.
    """

    def test_direct_model_warning(self):
        """Direct model response must produce DIRECT MODEL ONLY status."""
        from gui.components.answer_card import determine_answer_status

        status = determine_answer_status(
            sources=[],
            trace_available=False,
            lexical_only=False,
            vector_contributed=False,
            direct_model=True,
            mode="normal",
        )

        assert status["label"] == "DIRECT MODEL ONLY"
        assert status["level"] == "error"
        assert "No retrieval" in status["detail"]
        assert "No corpus" in status["detail"]

    def test_trace_unavailable_state(self):
        """When trace is unavailable but sources exist, shows appropriate state."""
        from gui.components.answer_card import determine_answer_status

        # Sources exist but trace is not available
        status = determine_answer_status(
            sources=[{"source_id": "test", "title": "Test Source"}],
            trace_available=False,
            lexical_only=False,
            vector_contributed=False,
            direct_model=False,
            mode="augmented",
        )

        assert status["label"] == "RETRIEVAL HEALTHY"
        assert "Trace data not available" in status["detail"]

    def test_lexical_only_degraded_state(self):
        """Lexical-only retrieval shows LEXICAL ONLY status."""
        from gui.components.answer_card import determine_answer_status

        status = determine_answer_status(
            sources=[{"source_id": "test", "title": "Test Source"}],
            trace_available=True,
            lexical_only=True,
            vector_contributed=False,
            direct_model=False,
            mode="augmented",
        )

        assert status["label"] == "LEXICAL ONLY"
        assert status["level"] == "degraded"

    def test_healthy_retrieval_with_vector(self):
        """Healthy hybrid retrieval (vector + lexical) shows RETRIEVAL HEALTHY."""
        from gui.components.answer_card import determine_answer_status

        status = determine_answer_status(
            sources=[{"source_id": "test", "title": "Test Source"}],
            trace_available=True,
            lexical_only=False,
            vector_contributed=True,
            direct_model=False,
            mode="augmented",
        )

        assert status["label"] == "RETRIEVAL HEALTHY"
        assert status["level"] == "ok"
        assert "Hybrid" in status["detail"] or "vector" in status["detail"].lower()

    def test_no_sources_warning(self):
        """No sources shows NO SOURCES status."""
        from gui.components.answer_card import determine_answer_status

        status = determine_answer_status(
            sources=[],
            trace_available=True,
            lexical_only=False,
            vector_contributed=False,
            direct_model=False,
            mode="augmented",
        )

        assert status["label"] == "NO SOURCES"
        assert status["level"] == "warning"

    def test_normal_mode_status(self):
        """Normal mode (no retrieval) shows NORMAL MODE status."""
        from gui.components.answer_card import determine_answer_status

        status = determine_answer_status(
            sources=[],
            trace_available=False,
            lexical_only=False,
            vector_contributed=False,
            direct_model=False,
            mode="normal",
        )

        assert status["label"] == "NORMAL MODE"
        assert status["level"] == "ok"

    def test_disabled_actions_in_code(self):
        """Verify Link Wiki and Model Council buttons are disabled in answer_card code."""
        from pathlib import Path

        answer_card_file = Path(__file__).resolve().parent.parent / "gui/components/answer_card.py"
        source = answer_card_file.read_text(encoding="utf-8")

        # Link Wiki should be disabled
        assert "Link Wiki" in source
        assert "disable" in source, "Link Wiki button must be disabled"

        # Model Council should be disabled
        assert "Model Council" in source
        # There should be tooltip indicating not implemented
        assert "not yet implemented" in source.lower() or "not wired" in source.lower(), (
            "Disabled actions must have explanatory tooltips"
        )

    def test_save_artifact_action_does_not_imply_approval(self):
        """Save Artifact action callback must not auto-approve the artifact."""
        from pathlib import Path

        # Check ask.py for the save artifact handler
        ask_file = Path(__file__).resolve().parent.parent / "gui/pages/ask.py"
        source = ask_file.read_text(encoding="utf-8")

        # The save handler should mention DEFINER review in the notification
        assert "DEFINER review" in source or "requires DEFINER" in source, (
            "Save artifact action must notify user that DEFINER review is required"
        )

        # Must not contain auto-approve patterns
        source_lower = source.lower()
        assert "auto_approve" not in source_lower.replace("auto_approve_ci", ""), (
            "ask.py must not contain auto_approve logic for save artifact"
        )


# ---------------------------------------------------------------------------
# 5. Import Boundary Tests for New Components
# ---------------------------------------------------------------------------


class TestNewComponentsImportBoundary:
    """Verify new Cycle 4 components obey import boundaries."""

    def test_answer_card_no_orchestration_import(self):
        """gui/components/answer_card.py must not import aip.orchestration."""
        import ast
        from pathlib import Path

        filepath = Path(__file__).resolve().parent.parent / "gui/components/answer_card.py"
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), (
                        f"answer_card.py imports orchestration: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    assert not node.module.startswith("aip.orchestration"), (
                        f"answer_card.py imports from orchestration: {node.module}"
                    )

    def test_source_panel_no_orchestration_import(self):
        """gui/components/source_panel.py must not import aip.orchestration."""
        import ast
        from pathlib import Path

        filepath = Path(__file__).resolve().parent.parent / "gui/components/source_panel.py"
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), (
                        f"source_panel.py imports orchestration: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    assert not node.module.startswith("aip.orchestration"), (
                        f"source_panel.py imports from orchestration: {node.module}"
                    )

    def test_trace_panel_no_orchestration_import(self):
        """gui/components/trace_panel.py must not import aip.orchestration."""
        import ast
        from pathlib import Path

        filepath = Path(__file__).resolve().parent.parent / "gui/components/trace_panel.py"
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), (
                        f"trace_panel.py imports orchestration: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    assert not node.module.startswith("aip.orchestration"), (
                        f"trace_panel.py imports from orchestration: {node.module}"
                    )

    def test_turns_route_no_orchestration_import(self):
        """src/aip/adapter/api/routes/turns.py must not import aip.orchestration."""
        import ast
        from pathlib import Path

        filepath = Path(__file__).resolve().parent.parent / "src/aip/adapter/api/routes/turns.py"
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), (
                        f"turns.py imports orchestration: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    assert not node.module.startswith("aip.orchestration"), (
                        f"turns.py imports from orchestration: {node.module}"
                    )

    def test_ask_route_no_orchestration_import(self):
        """src/aip/adapter/api/routes/ask.py must not import aip.orchestration."""
        import ast
        from pathlib import Path

        filepath = Path(__file__).resolve().parent.parent / "src/aip/adapter/api/routes/ask.py"
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), f"ask.py imports orchestration: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    assert not node.module.startswith("aip.orchestration"), (
                        f"ask.py imports from orchestration: {node.module}"
                    )


# ---------------------------------------------------------------------------
# 6. API Client Method Tests
# ---------------------------------------------------------------------------


class TestApiClientNewMethods:
    """Test that gui/api_client.py has the new UI Cycle 4 methods."""

    def test_has_get_retrieval_trace_by_session(self):
        """api_client must have get_retrieval_trace_by_session method."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        assert hasattr(client, "get_retrieval_trace_by_session"), (
            "AipApiClient missing get_retrieval_trace_by_session method"
        )
        assert callable(getattr(client, "get_retrieval_trace_by_session")), (
            "get_retrieval_trace_by_session must be callable"
        )

    def test_has_save_turn_as_artifact(self):
        """api_client must have save_turn_as_artifact method."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        assert hasattr(client, "save_turn_as_artifact"), "AipApiClient missing save_turn_as_artifact method"
        assert callable(getattr(client, "save_turn_as_artifact")), "save_turn_as_artifact must be callable"

    def test_save_turn_artifact_does_not_auto_approve(self):
        """api_client.save_turn_as_artifact docstring must mention no auto-approve."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        docstring = client.save_turn_as_artifact.__doc__ or ""
        assert "NOT APPROVED" in docstring or "not approve" in docstring.lower() or "GENERATED" in docstring, (
            "save_turn_as_artifact docstring must mention GENERATED state / no auto-approve"
        )


# ---------------------------------------------------------------------------
# 7. Turns Route Registration Test
# ---------------------------------------------------------------------------


class TestTurnsRouteRegistration:
    """Verify the turns route is properly mounted in the FastAPI app."""

    def test_turns_router_importable(self):
        """The turns route module must be importable."""
        from aip.adapter.api.routes.turns import router

        assert router is not None

    def test_turns_route_registered_in_app(self):
        """The turns route must be included in the app's routes."""
        from pathlib import Path

        app_file = Path(__file__).resolve().parent.parent / "src/aip/adapter/api/app.py"
        source = app_file.read_text(encoding="utf-8")

        assert "turns" in source, "app.py must import turns route module"

    def test_save_artifact_endpoint_path(self):
        """The endpoint must be at POST /api/v1/turns/save-artifact."""
        from aip.adapter.api.routes.turns import router

        # Check the route is registered
        routes = [r.path for r in router.routes]
        assert "/turns/save-artifact" in routes, f"Expected /turns/save-artifact in routes, got: {routes}"


# ---------------------------------------------------------------------------
# 8. Direct Model Fallback Tests
# ---------------------------------------------------------------------------


class TestDirectModelFallback:
    """Tests for direct model fallback behavior in the chat route."""

    def test_degraded_path_sets_direct_model_true(self):
        """When no model provider is configured, direct_model must be True."""
        from pathlib import Path

        chat_file = Path(__file__).resolve().parent.parent / "src/aip/adapter/api/routes/chat.py"
        source = chat_file.read_text(encoding="utf-8")

        # Find the degraded path response
        assert '"direct_model": True' in source or "'direct_model': True" in source, (
            "Degraded WebSocket response must set direct_model=True"
        )

    def test_normal_path_sets_direct_model_false(self):
        """Normal backend response must set direct_model=False."""
        from pathlib import Path

        chat_file = Path(__file__).resolve().parent.parent / "src/aip/adapter/api/routes/chat.py"
        source = chat_file.read_text(encoding="utf-8")

        assert '"direct_model": False' in source or "'direct_model': False" in source, (
            "Normal WebSocket response must set direct_model=False"
        )

    def test_frontend_displays_direct_model_banner(self):
        """The ask page must show DIRECT MODEL ONLY banner when backend unreachable."""
        from pathlib import Path

        ask_file = Path(__file__).resolve().parent.parent / "gui/pages/ask.py"
        source = ask_file.read_text(encoding="utf-8")

        assert "DIRECT MODEL ONLY" in source, "ask.py must display DIRECT MODEL ONLY warning banner"
        assert "NOT DOGFOOD" in source, "ask.py must display NOT DOGFOOD in direct model banner"

    def test_answer_card_direct_model_status(self):
        """Answer card must show error-level DIRECT MODEL ONLY status."""
        from gui.components.answer_card import determine_answer_status

        status = determine_answer_status(
            sources=[],
            trace_available=False,
            lexical_only=False,
            vector_contributed=False,
            direct_model=True,
            mode="normal",
        )

        assert status["level"] == "error", "Direct model status must be error-level, not ok or warning"
        assert "DIRECT" in status["label"], "Direct model status label must contain 'DIRECT'"
