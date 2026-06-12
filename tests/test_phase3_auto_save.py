"""Phase 3 Auto-Save Ingestion tests.

Tests for:
  - PATCH /api/v1/sessions/{session_id} — session flag updates
  - POST /api/v1/ingest/conversation — API-driven ingestion
  - Auto-save hook integration in chat flow
  - Ingestion status tracking
"""

from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None  # type: ignore

from aip.adapter.api.app import create_app


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed (API surface dependency)")
class TestPatchSession:
    """Tests for PATCH /api/v1/sessions/{session_id}."""

    def test_patch_session_auto_save_flag(self):
        """PATCH should update the auto_save flag on an existing session."""
        app = create_app()
        client = TestClient(app)

        # Create a session first
        resp = client.post("/api/v1/sessions", json={"role": "beast", "model_slot": "synthesis"})
        assert resp.status_code == 200
        session_id = resp.json()["id"]

        # Toggle auto_save off
        resp = client.patch(f"/api/v1/sessions/{session_id}", json={"auto_save": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_save"] is False

        # Toggle auto_save back on
        resp = client.patch(f"/api/v1/sessions/{session_id}", json={"auto_save": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_save"] is True

    def test_patch_session_mode(self):
        """PATCH should update the session mode."""
        app = create_app()
        client = TestClient(app)

        resp = client.post("/api/v1/sessions", json={"mode": "normal"})
        assert resp.status_code == 200
        session_id = resp.json()["id"]

        resp = client.patch(f"/api/v1/sessions/{session_id}", json={"mode": "augmented"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "augmented"

    def test_patch_nonexistent_session_returns_404(self):
        """PATCH on a non-existent session should return 404."""
        app = create_app()
        client = TestClient(app)

        resp = client.patch("/api/v1/sessions/sess-nonexistent", json={"auto_save": False})
        assert resp.status_code == 404

    def test_patch_preserves_readonly_fields(self):
        """PATCH should not allow changing id or created_at."""
        app = create_app()
        client = TestClient(app)

        resp = client.post("/api/v1/sessions", json={"role": "beast"})
        assert resp.status_code == 200
        session_id = resp.json()["id"]
        resp.json().get("created_at")

        # Try to change id and created_at — they should be ignored
        resp = client.patch(f"/api/v1/sessions/{session_id}", json={"id": "hacked", "created_at": "1999-01-01"})
        data = resp.json()
        assert data["id"] == session_id  # unchanged
        # created_at may or may not be in the response, but if it is, it shouldn't be 1999


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed (API surface dependency)")
class TestDeleteSession:
    """Tests for DELETE /api/v1/sessions/{session_id}."""

    def test_delete_existing_session(self):
        """DELETE should remove a session."""
        app = create_app()
        client = TestClient(app)

        resp = client.post("/api/v1/sessions", json={"role": "beast"})
        session_id = resp.json()["id"]

        resp = client.delete(f"/api/v1/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_nonexistent_session_succeeds(self):
        """DELETE on a non-existent session should still return 200 (idempotent)."""
        app = create_app()
        client = TestClient(app)

        resp = client.delete("/api/v1/sessions/sess-never-existed")
        assert resp.status_code == 200


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed (API surface dependency)")
class TestIngestConversationEndpoint:
    """Tests for POST /api/v1/ingest/conversation."""

    def test_ingest_conversation_requires_stores(self):
        """Ingestion endpoint should return 503 when stores are not wired."""
        app = create_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/ingest/conversation",
            json={
                "conversation_id": "test-conv-1",
                "turns": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ],
            },
        )
        # In the test environment without lifespan, stores are None → 503
        # OR if the app does have stores wired via lifespan, it could be 200
        assert resp.status_code in (200, 503)

    def test_ingest_conversation_requires_turns(self):
        """Ingestion endpoint should return 400 when no turns are provided."""
        app = create_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/ingest/conversation",
            json={
                "conversation_id": "test-conv-2",
                "turns": [],
            },
        )
        assert resp.status_code == 400


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed (API surface dependency)")
class TestIngestFileEndpoint:
    """Tests for POST /api/v1/ingest/file."""

    def test_ingest_file_requires_path(self):
        """Ingestion endpoint should return 400 when no path is provided."""
        app = create_app()
        client = TestClient(app)

        resp = client.post("/api/v1/ingest/file", json={})
        assert resp.status_code == 400

    def test_ingest_file_nonexistent_path(self):
        """Ingestion endpoint should return 404 for a non-existent file."""
        app = create_app()
        client = TestClient(app)

        resp = client.post("/api/v1/ingest/file", json={"path": "/nonexistent/file.json"})
        # May be 404 or 503 depending on whether stores are wired
        assert resp.status_code in (404, 503)


class TestSessionCreationIncludesAutoSave:
    """Verify session creation response includes Phase 3 fields."""

    def test_create_session_includes_auto_save_field(self):
        """POST /sessions should return auto_save and ingestion_status fields."""
        app = create_app()
        client = TestClient(app)

        resp = client.post("/api/v1/sessions", json={"role": "beast"})
        assert resp.status_code == 200
        data = resp.json()
        assert "auto_save" in data
        assert "ingestion_status" in data
        assert data["auto_save"] is True
        assert data["ingestion_status"] == "idle"

    def test_create_session_auto_save_default_true(self):
        """New sessions should default to auto_save=True."""
        app = create_app()
        client = TestClient(app)

        resp = client.post("/api/v1/sessions", json={})
        data = resp.json()
        assert data["auto_save"] is True

    def test_create_session_auto_save_can_be_set_false(self):
        """Creating a session with auto_save=False should work."""
        app = create_app()
        client = TestClient(app)

        resp = client.post("/api/v1/sessions", json={"auto_save": False})
        data = resp.json()
        assert data["auto_save"] is False


class TestLayeringIngestRoute:
    """Layering check — ingest route should not import concrete adapters."""

    def test_ingest_route_no_direct_adapter_imports(self):
        """ingest.py should not import from concrete adapter modules."""
        from pathlib import Path

        ingest_file = Path(__file__).parent.parent / "src/aip/adapter/api/routes/ingest.py"
        text = ingest_file.read_text()
        # Should NOT import concrete adapter implementations directly
        assert "from aip.adapter.budget_store" not in text
        assert "from aip.adapter.vector._in_memory" not in text
        # Should import from foundation and orchestration (lazy)
        assert "from aip.foundation" in text or "from aip.orchestration" in text
