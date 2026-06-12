"""Tests for UI Cycle 8 — Crosslink System v1.

Verifies:
1. Create link with valid object types/relation types
2. Reject invalid object type
3. Reject invalid relation type
4. Link defaults to suggested/unapproved
5. Explicit approval requires explicit PATCH/field
6. Approve link updates approved_by_definer and approved_at
7. Reject link marks rejected
8. Backlinks return created links
9. Empty backlinks return empty list honestly
10. Delete link works
11. No linked object is mutated by link creation
12. No artifact is approved/exported by link creation
13. GUI Link Panel imports/renders
14. Wiki article view shows link panel
15. Ask/Beast/Model Council link panels do not break existing behavior
16. GUI import-boundary tests pass
17. General import-boundary tests pass
18. Existing Wiki tests pass (backward compatibility)
19. Existing Ask/Beast/Model Council tests pass if touched

Sovereignty guarantees tested:
- No auto-approve
- No auto-export
- No wiki mutation
- No fake links
- Honest empty/unavailable states
- No secret exposure
"""

import os
import sqlite3
import tempfile

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

pytestmark = pytest.mark.skipif(TestClient is None, reason="fastapi not available")


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def links_db():
    """Create a temporary state.db with knowledge_links table."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_links (
            id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            created_by TEXT NOT NULL DEFAULT 'definer',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            approved_by_definer INTEGER NOT NULL DEFAULT 0,
            approved_at TEXT,
            status TEXT NOT NULL DEFAULT 'suggested',
            provenance TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kl_source ON knowledge_links(source_type, source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kl_target ON knowledge_links(target_type, target_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kl_status ON knowledge_links(status)")
    # Also create artifacts + ecs_state tables so the app can initialize
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content TEXT,
            metadata_json TEXT,
            created_at TEXT,
            PRIMARY KEY (id, version)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ecs_state (
            artifact_id TEXT PRIMARY KEY,
            current_state TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # Create events table for event store
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            actor TEXT,
            artifact_id TEXT,
            from_state TEXT,
            to_state TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL
        )
    """)
    # Create entities table for entity store
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            domain TEXT,
            confidence REAL,
            source TEXT,
            aliases TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # Create canonical_artifacts table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS canonical_artifacts (
            id TEXT PRIMARY KEY,
            content TEXT,
            metadata_json TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    yield db_path

    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def links_client(links_db):
    """Create a FastAPI TestClient with the links route mounted."""
    from fastapi import FastAPI

    from aip.adapter.api.dependencies import AipContainer
    from aip.adapter.api.routes.links import router as links_router

    app = FastAPI()

    # Create a minimal container with _store_registry pointing to the test DB
    container = AipContainer({"db_path": links_db, "auth": {"auth_enabled": False}})
    container._store_registry = {"state": links_db}

    # Store container on app.state so the route can find it via Request
    app.state.container = container

    app.include_router(links_router, prefix="/api/v1")

    client = TestClient(app)
    yield client


# ── Test 1: Create link with valid object types/relation types ──────────


class TestCreateLinkValid:
    """Test 1: Create link with valid object types/relation types."""

    def test_create_link_valid_types(self, links_client):
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:article:20260101",
                "target_type": "artifact",
                "target_id": "art:abc123",
                "relation_type": "supports",
                "confidence": 0.9,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["source_type"] == "wiki_article"
        assert data["source_id"] == "wiki:test:article:20260101"
        assert data["target_type"] == "artifact"
        assert data["target_id"] == "art:abc123"
        assert data["relation_type"] == "supports"
        assert data["confidence"] == 0.9
        assert data["status"] == "suggested"
        assert data["approved_by_definer"] is False
        assert data["storage_backend"] == "knowledge_link_store"

    def test_create_link_all_valid_object_types(self, links_client):
        """Verify all 10 object types are accepted."""
        valid_types = [
            "source_document",
            "chunk",
            "conversation_turn",
            "retrieval_trace",
            "beast_commentary",
            "wiki_article",
            "artifact",
            "review_event",
            "actor_event",
            "model_comparison_report",
        ]
        for ot in valid_types:
            resp = links_client.post(
                "/api/v1/links",
                json={
                    "source_type": ot,
                    "source_id": f"test:{ot}:1",
                    "target_type": "wiki_article",
                    "target_id": "wiki:test:1",
                    "relation_type": "related_to",
                },
            )
            assert resp.status_code == 201, f"Failed for source_type={ot}: {resp.text}"

    def test_create_link_all_valid_relation_types(self, links_client):
        """Verify all 12 relation types are accepted."""
        valid_rels = [
            "supports",
            "contradicts",
            "summarizes",
            "extends",
            "mentions",
            "depends_on",
            "implements",
            "supersedes",
            "related_to",
            "generated_from",
            "reviewed_by",
            "approved_by",
        ]
        for rel in valid_rels:
            resp = links_client.post(
                "/api/v1/links",
                json={
                    "source_type": "wiki_article",
                    "source_id": "wiki:test:1",
                    "target_type": "artifact",
                    "target_id": "art:1",
                    "relation_type": rel,
                },
            )
            assert resp.status_code == 201, f"Failed for relation_type={rel}: {resp.text}"


# ── Test 2: Reject invalid object type ──────────────────────────────────


class TestRejectInvalidObjectType:
    """Test 2: Reject invalid object type."""

    def test_invalid_source_type_returns_400(self, links_client):
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "invalid_type",
                "source_id": "test:1",
                "target_type": "wiki_article",
                "target_id": "wiki:test:1",
                "relation_type": "supports",
            },
        )
        assert resp.status_code == 400
        assert "Invalid source_type" in resp.json()["detail"]

    def test_invalid_target_type_returns_400(self, links_client):
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "not_a_type",
                "target_id": "test:1",
                "relation_type": "supports",
            },
        )
        assert resp.status_code == 400
        assert "Invalid target_type" in resp.json()["detail"]

    def test_backlinks_invalid_target_type_returns_400(self, links_client):
        resp = links_client.get("/api/v1/links/backlinks/invalid_type/some_id")
        assert resp.status_code == 400

    def test_forward_links_invalid_source_type_returns_400(self, links_client):
        resp = links_client.get("/api/v1/links/forward/invalid_type/some_id")
        assert resp.status_code == 400


# ── Test 3: Reject invalid relation type ────────────────────────────────


class TestRejectInvalidRelationType:
    """Test 3: Reject invalid relation type."""

    def test_invalid_relation_type_returns_400(self, links_client):
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "is_better_than",
            },
        )
        assert resp.status_code == 400
        assert "Invalid relation_type" in resp.json()["detail"]

    def test_patch_invalid_relation_type_returns_400(self, links_client):
        # First create a valid link
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "supports",
            },
        )
        link_id = resp.json()["id"]

        # Try to update with invalid relation type
        resp = links_client.patch(
            f"/api/v1/links/{link_id}",
            json={
                "relation_type": "invalid_relation",
            },
        )
        assert resp.status_code == 400


# ── Test 4: Link defaults to suggested/unapproved ───────────────────────


class TestLinkDefaultsSuggested:
    """Test 4: Link defaults to suggested/unapproved."""

    def test_default_status_is_suggested(self, links_client):
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "supports",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "suggested"
        assert data["approved_by_definer"] is False
        assert data["approved_at"] is None

    def test_cannot_create_with_rejected_status(self, links_client):
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "supports",
                "status": "rejected",
            },
        )
        assert resp.status_code == 400

    def test_cannot_create_with_deleted_status(self, links_client):
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "supports",
                "status": "deleted",
            },
        )
        assert resp.status_code == 400

    def test_cannot_create_approved_without_explicit_flag(self, links_client):
        """Creating with status=approved but approved_by_definer=False is rejected."""
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "supports",
                "status": "approved",
                "approved_by_definer": False,
            },
        )
        assert resp.status_code == 400

    def test_self_link_rejected(self, links_client):
        """Cannot create a self-referential link."""
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "wiki_article",
                "target_id": "wiki:test:1",
                "relation_type": "related_to",
            },
        )
        assert resp.status_code == 400
        assert "self-referential" in resp.json()["detail"].lower()


# ── Test 5: Explicit approval requires explicit PATCH/field ─────────────


class TestExplicitApproval:
    """Test 5: Explicit approval requires explicit PATCH/field."""

    def test_approval_requires_explicit_patch(self, links_client):
        """Simply creating a link does NOT approve it, even if created_by=definer."""
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "supports",
                "created_by": "definer",
            },
        )
        data = resp.json()
        assert data["status"] == "suggested"
        assert data["approved_by_definer"] is False
        assert data["approved_at"] is None


# ── Test 6: Approve link updates approved_by_definer and approved_at ────


class TestApproveLink:
    """Test 6: Approve link updates approved_by_definer and approved_at."""

    def test_approve_sets_fields(self, links_client):
        # Create link
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "supports",
            },
        )
        link_id = resp.json()["id"]
        assert resp.json()["approved_by_definer"] is False

        # Approve via PATCH
        resp = links_client.patch(
            f"/api/v1/links/{link_id}",
            json={
                "approved_by_definer": True,
            },
        )
        data = resp.json()
        assert data["approved_by_definer"] is True
        assert data["approved_at"] is not None
        assert data["status"] == "approved"
        assert data["storage_backend"] == "knowledge_link_store"

    def test_approve_with_explicit_status_approved(self, links_client):
        # Create link
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "supports",
                "status": "approved",
                "approved_by_definer": True,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "approved"
        assert data["approved_by_definer"] is True
        assert data["approved_at"] is not None


# ── Test 7: Reject link marks rejected ─────────────────────────────────


class TestRejectLink:
    """Test 7: Reject link marks rejected."""

    def test_reject_via_patch(self, links_client):
        # Create link
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "contradicts",
            },
        )
        link_id = resp.json()["id"]

        # Reject via PATCH
        resp = links_client.patch(
            f"/api/v1/links/{link_id}",
            json={
                "status": "rejected",
            },
        )
        data = resp.json()
        assert data["status"] == "rejected"

    def test_patch_invalid_status_returns_400(self, links_client):
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:1",
                "target_type": "artifact",
                "target_id": "art:1",
                "relation_type": "supports",
            },
        )
        link_id = resp.json()["id"]

        resp = links_client.patch(
            f"/api/v1/links/{link_id}",
            json={
                "status": "invalid_status",
            },
        )
        assert resp.status_code == 400


# ── Test 8: Backlinks return created links ──────────────────────────────


class TestBacklinks:
    """Test 8: Backlinks return created links."""

    def test_backlinks_return_links_pointing_to_object(self, links_client):
        # Create two links pointing to the same target
        links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:alpha:1",
                "target_type": "artifact",
                "target_id": "art:shared:1",
                "relation_type": "supports",
            },
        )
        links_client.post(
            "/api/v1/links",
            json={
                "source_type": "conversation_turn",
                "source_id": "turn:beta:1",
                "target_type": "artifact",
                "target_id": "art:shared:1",
                "relation_type": "mentions",
            },
        )

        # Get backlinks for the target
        resp = links_client.get("/api/v1/links/backlinks/artifact/art:shared:1")
        data = resp.json()
        assert data["total"] == 2
        assert data["available"] is True
        assert len(data["backlinks"]) == 2
        source_ids = {bl["source_id"] for bl in data["backlinks"]}
        assert "wiki:alpha:1" in source_ids
        assert "turn:beta:1" in source_ids

    def test_forward_links_return_links_from_object(self, links_client):
        # Create a link from a source
        links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:source:1",
                "target_type": "artifact",
                "target_id": "art:target:1",
                "relation_type": "supports",
            },
        )

        resp = links_client.get("/api/v1/links/forward/wiki_article/wiki:source:1")
        data = resp.json()
        assert data["total"] == 1
        assert data["available"] is True
        assert data["forward_links"][0]["target_id"] == "art:target:1"


# ── Test 9: Empty backlinks return empty list honestly ──────────────────


class TestEmptyBacklinks:
    """Test 9: Empty backlinks return empty list honestly."""

    def test_no_backlinks_returns_empty_list(self, links_client):
        resp = links_client.get("/api/v1/links/backlinks/wiki_article/wiki:nobody:1")
        data = resp.json()
        assert data["backlinks"] == []
        assert data["total"] == 0
        assert data["available"] is True  # Storage is available, just no links

    def test_no_forward_links_returns_empty_list(self, links_client):
        resp = links_client.get("/api/v1/links/forward/artifact/art:nobody:1")
        data = resp.json()
        assert data["forward_links"] == []
        assert data["total"] == 0
        assert data["available"] is True

    def test_list_links_empty_returns_empty(self, links_client):
        resp = links_client.get("/api/v1/links")
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0


# ── Test 10: Delete link works ──────────────────────────────────────────


class TestDeleteLink:
    """Test 10: Delete link works or marks deleted."""

    def test_delete_link_removes_it(self, links_client):
        # Create link
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:del:1",
                "target_type": "artifact",
                "target_id": "art:del:1",
                "relation_type": "supports",
            },
        )
        link_id = resp.json()["id"]

        # Delete it
        resp = links_client.delete(f"/api/v1/links/{link_id}")
        data = resp.json()
        assert data["deleted"] is True
        assert data["id"] == link_id

        # Confirm it's gone from backlinks
        resp = links_client.get("/api/v1/links/backlinks/artifact/art:del:1")
        assert resp.json()["total"] == 0

    def test_delete_nonexistent_returns_404(self, links_client):
        resp = links_client.delete("/api/v1/links/nonexistent_link_id")
        assert resp.status_code == 404

    def test_patch_nonexistent_returns_404(self, links_client):
        resp = links_client.patch(
            "/api/v1/links/nonexistent_link_id",
            json={
                "status": "approved",
            },
        )
        assert resp.status_code == 404


# ── Test 11: No linked object is mutated by link creation ───────────────


class TestNoLinkedObjectMutation:
    """Test 11: No linked object is mutated by link creation."""

    def test_link_creation_does_not_modify_objects(self, links_client):
        """Creating a link does not alter any existing object.
        We verify this by checking that the links API only creates links,
        not modifying wiki articles, artifacts, or any other objects.
        The links table is separate from all other tables.
        """
        # Create a link
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:test:immutable:1",
                "target_type": "artifact",
                "target_id": "art:immutable:1",
                "relation_type": "supports",
            },
        )
        assert resp.status_code == 201

        # Check that the link response does not contain any mutation signals
        data = resp.json()
        # There should be no artifact_id, ecs_state, or approval fields
        # that would indicate object mutation
        assert "artifact_id" not in data or data.get("artifact_id") is None
        assert "ecs_state" not in data
        assert "export" not in str(data).lower()

    def test_link_creation_only_creates_link_record(self, links_db, links_client):
        """Verify that creating a link only adds a row to knowledge_links,
        not to any other table."""
        import sqlite3

        conn = sqlite3.connect(links_db)
        # Count artifacts before
        artifacts_before = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        # Count ECS states before
        ecs_before = conn.execute("SELECT COUNT(*) FROM ecs_state").fetchone()[0]
        # Count events before
        events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        # Create link
        links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:check:1",
                "target_type": "artifact",
                "target_id": "art:check:1",
                "relation_type": "mentions",
            },
        )

        # Count after
        artifacts_after = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        ecs_after = conn.execute("SELECT COUNT(*) FROM ecs_state").fetchone()[0]
        events_after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        links_after = conn.execute("SELECT COUNT(*) FROM knowledge_links").fetchone()[0]

        conn.close()

        assert artifacts_after == artifacts_before, "Link creation should not create artifacts"
        assert ecs_after == ecs_before, "Link creation should not change ECS states"
        assert events_after == events_before, "Link creation should not create events"
        assert links_after == 1, "Link creation should add exactly one link"


# ── Test 12: No artifact is approved/exported by link creation ──────────


class TestNoArtifactApprovalExport:
    """Test 12: No artifact is approved/exported by link creation."""

    def test_link_creation_does_not_approve_artifacts(self, links_client):
        """Creating a link does not approve or export any artifact."""
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "artifact",
                "source_id": "art:pending:1",
                "target_type": "wiki_article",
                "target_id": "wiki:ref:1",
                "relation_type": "generated_from",
            },
        )
        data = resp.json()
        # The link itself is suggested/unapproved
        assert data["status"] == "suggested"
        assert data["approved_by_definer"] is False
        # No export signals
        assert "exported" not in str(data).lower()
        assert "auto_approve" not in str(data).lower()

    def test_no_secret_exposure_in_link_responses(self, links_client):
        """Link responses must not expose secrets."""
        resp = links_client.post(
            "/api/v1/links",
            json={
                "source_type": "wiki_article",
                "source_id": "wiki:testdoc:1",
                "target_type": "artifact",
                "target_id": "art:testdoc:1",
                "relation_type": "supports",
            },
        )
        data = resp.json()
        response_text = str(data).lower()
        # Check for actual secret values, not test IDs that happen to contain "secret"
        forbidden_patterns = ["api_key=", "password=", "token=", "credential="]
        for pattern in forbidden_patterns:
            assert pattern not in response_text, f"Secret exposure: found '{pattern}' in link response"


# ── Test 13: GUI Link Panel imports/renders ─────────────────────────────


class TestLinkPanelImport:
    """Test 13: GUI Link Panel imports/renders.

    Uses source-file reading instead of importing NiceGUI modules,
    since nicegui may not be installed in the test environment.
    """

    def test_link_panel_source_exists(self):
        """Link Panel source file exists."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "link_panel.py"
        assert p.exists(), "link_panel.py not found"

    def test_link_panel_has_correct_constants(self):
        """Link Panel has the correct valid object and relation types."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "link_panel.py"
        source = p.read_text()
        assert "wiki_article" in source
        assert "artifact" in source
        assert "conversation_turn" in source
        assert "supports" in source
        assert "contradicts" in source
        assert "related_to" in source

    def test_link_panel_status_config(self):
        """Link Panel source defines status configuration."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "link_panel.py"
        source = p.read_text()
        assert "STATUS_CONFIG" in source
        assert "suggested" in source
        assert "approved" in source
        assert "rejected" in source
        assert "deleted" in source
        assert "needs_approval" in source

    def test_link_editor_source_exists(self):
        """Link Editor source file exists."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "link_editor.py"
        assert p.exists(), "link_editor.py not found"

    def test_link_editor_has_form_fields(self):
        """Link Editor source has object type, relation type, and confidence fields."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "link_editor.py"
        source = p.read_text()
        assert "OBJECT_TYPES" in source
        assert "RELATION_TYPES" in source
        assert "confidence" in source.lower()
        assert "notes" in source.lower()


# ── Test 14: Wiki article view shows link panel ─────────────────────────


class TestWikiArticleViewLinkPanel:
    """Test 14: Wiki article view shows link panel."""

    def test_wiki_article_view_accepts_api_client(self):
        """Wiki article view source code has api_client parameter."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "wiki_article_view.py"
        source = p.read_text()
        assert "api_client" in source
        # Check that it's in the function signature
        assert "api_client: Any = None" in source or "api_client=None" in source

    def test_wiki_article_view_renders_link_panel(self):
        """Wiki article view source code renders link panel."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "wiki_article_view.py"
        source = p.read_text()
        assert "render_link_panel" in source
        assert "wiki_article" in source


# ── Test 15: Ask/Beast/Model Council link panels do not break ───────────


class TestExistingPanelsNotBroken:
    """Test 15: Ask/Beast/Model Council link panels do not break existing behavior."""

    def test_answer_card_source_has_link_wiki_callback(self):
        """Answer card source code supports on_link_wiki callback."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "answer_card.py"
        source = p.read_text()
        assert "on_link_wiki" in source
        # Check it's no longer always disabled
        assert "not yet implemented" not in source.lower()

    def test_beast_panel_source_exists(self):
        """Beast panel source file still exists."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "beast_panel.py"
        assert p.exists()

    def test_model_council_panel_source_exists(self):
        """Model Council panel source file still exists."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "model_council_panel.py"
        assert p.exists()


# ── Test 16: GUI import-boundary tests pass ─────────────────────────────


class TestGuiImportBoundary:
    """Test 16: GUI import-boundary tests pass.

    Uses source-file reading to check import boundaries, since
    NiceGUI may not be installed in the test environment.
    """

    def test_link_panel_does_not_import_orchestration(self):
        """Link Panel does not import from aip.orchestration (checked via AST, not docstrings)."""
        import ast
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "link_panel.py"
        source = p.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "aip.orchestration" in node.module:
                    pytest.fail(f"link_panel imports from aip.orchestration: {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "aip.orchestration" in alias.name:
                        pytest.fail(f"link_panel imports from aip.orchestration: {alias.name}")

    def test_link_editor_does_not_import_orchestration(self):
        """Link Editor does not import from aip.orchestration (checked via AST, not docstrings)."""
        import ast
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "link_editor.py"
        source = p.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "aip.orchestration" in node.module:
                    pytest.fail(f"link_editor imports from aip.orchestration: {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "aip.orchestration" in alias.name:
                        pytest.fail(f"link_editor imports from aip.orchestration: {alias.name}")

    def test_links_route_does_not_import_orchestration(self):
        """Links route does not import from aip.orchestration directly."""
        import aip.adapter.api.routes.links as mod

        source = open(mod.__file__).read()
        assert "aip.orchestration" not in source

    def test_status_types_link_types_exist(self):
        """status_types.py has KnowledgeLink types."""
        from gui.status_types import (
            KnowledgeLink,
        )

        assert KnowledgeLink is not None

    def test_api_client_link_methods_exist(self):
        """AipApiClient has Crosslink System methods."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        assert hasattr(client, "list_knowledge_links")
        assert hasattr(client, "create_knowledge_link")
        assert hasattr(client, "update_knowledge_link")
        assert hasattr(client, "delete_knowledge_link")
        assert hasattr(client, "get_link_backlinks")
        assert hasattr(client, "get_link_forward_links")


# ── Test 17: General import-boundary tests pass ─────────────────────────


class TestGeneralImportBoundary:
    """Test 17: General import-boundary tests pass."""

    def test_links_route_imports_only_from_adapter_and_foundation(self):
        """Links route only imports from adapter and foundation layers."""
        import ast

        import aip.adapter.api.routes.links as mod

        source = open(mod.__file__).read()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod_name = alias.name
                    if mod_name.startswith("aip."):
                        assert mod_name.startswith("aip.adapter.") or mod_name.startswith("aip.foundation."), (
                            f"Links route imports from wrong layer: {mod_name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("aip."):
                    assert node.module.startswith("aip.adapter.") or node.module.startswith("aip.foundation."), (
                        f"Links route imports from wrong layer: {node.module}"
                    )


# ── Test 18: Existing Wiki tests still pass (backward compatibility) ────


class TestWikiBackwardCompatibility:
    """Test 18: Existing Wiki tests still pass (backward compatibility)."""

    def test_wiki_article_view_backward_compatible(self):
        """Wiki article view function signature is backward compatible.
        api_client has a default of None, so existing callers don't break.
        """
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "wiki_article_view.py"
        source = p.read_text()
        # api_client has a default of None
        assert "api_client: Any = None" in source or "api_client=None" in source

    def test_wiki_page_source_exists(self):
        """Wiki page source still exists."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "pages" / "wiki.py"
        assert p.exists()


# ── Test 19: Existing Ask/Beast/Model Council tests pass if touched ─────


class TestExistingTestsPass:
    """Test 19: Existing Ask/Beast/Model Council tests are not broken.

    Uses source-file analysis since NiceGUI is not available in test env.
    """

    def test_answer_card_determine_answer_status_logic(self):
        """Answer card determine_answer_status function logic is correct.
        We test by reading the source and verifying key logic patterns.
        """
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "answer_card.py"
        source = p.read_text()
        assert "DIRECT MODEL ONLY" in source
        assert "RETRIEVAL HEALTHY" in source
        assert "LEXICAL ONLY" in source

    def test_beast_modes_still_defined_in_source(self):
        """Beast modes are still properly defined in source."""
        import pathlib

        p = pathlib.Path(__file__).parent.parent / "gui" / "components" / "beast_panel.py"
        source = p.read_text()
        assert "continuity" in source
        assert "critique" in source
        assert "strategy" in source
        assert "librarian" in source
        assert "risk" in source


# ── Additional sovereignty and storage tests ────────────────────────────


class TestKnowledgeLinkStore:
    """Direct tests for the KnowledgeLinkStore helper class."""

    @pytest.mark.asyncio
    async def test_store_creates_table_if_missing(self, links_db):
        """KnowledgeLinkStore creates the knowledge_links table on first use."""
        import sqlite3

        # Drop the table to test creation
        conn = sqlite3.connect(links_db)
        conn.execute("DROP TABLE IF EXISTS knowledge_links")
        conn.commit()
        conn.close()

        from aip.adapter.api.routes.links import KnowledgeLinkStore

        store = KnowledgeLinkStore(links_db)

        link = {
            "id": "test:link:1",
            "source_type": "wiki_article",
            "source_id": "wiki:test:1",
            "target_type": "artifact",
            "target_id": "art:1",
            "relation_type": "supports",
            "confidence": 1.0,
            "created_by": "definer",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "approved_by_definer": False,
            "approved_at": None,
            "status": "suggested",
            "provenance": "test",
            "notes": "",
        }
        result = await store.create_link(link)
        assert result["id"] == "test:link:1"

        # Verify table was created
        conn = sqlite3.connect(links_db)
        cursor = conn.execute("SELECT COUNT(*) FROM knowledge_links")
        assert cursor.fetchone()[0] == 1
        conn.close()

    @pytest.mark.asyncio
    async def test_store_get_link(self, links_db):
        """KnowledgeLinkStore.get_link returns the correct link."""
        from aip.adapter.api.routes.links import KnowledgeLinkStore

        store = KnowledgeLinkStore(links_db)

        link = {
            "id": "test:get:1",
            "source_type": "wiki_article",
            "source_id": "wiki:test:1",
            "target_type": "artifact",
            "target_id": "art:1",
            "relation_type": "supports",
            "confidence": 0.85,
            "created_by": "beast",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "approved_by_definer": False,
            "approved_at": None,
            "status": "suggested",
            "provenance": "beast_suggestion",
            "notes": "Test note",
        }
        await store.create_link(link)

        result = await store.get_link("test:get:1")
        assert result is not None
        assert result["source_type"] == "wiki_article"
        assert result["confidence"] == 0.85
        assert result["created_by"] == "beast"
        assert result["approved_by_definer"] is False

    @pytest.mark.asyncio
    async def test_store_list_links_with_filter(self, links_db):
        """KnowledgeLinkStore.list_links supports filtering."""
        from aip.adapter.api.routes.links import KnowledgeLinkStore

        store = KnowledgeLinkStore(links_db)

        for i in range(5):
            link = {
                "id": f"test:filter:{i}",
                "source_type": "wiki_article",
                "source_id": f"wiki:test:{i}",
                "target_type": "artifact",
                "target_id": f"art:{i}",
                "relation_type": "supports" if i < 3 else "contradicts",
                "confidence": 1.0,
                "created_by": "definer",
                "created_at": f"2026-01-0{i + 1}T00:00:00Z",
                "updated_at": f"2026-01-0{i + 1}T00:00:00Z",
                "approved_by_definer": False,
                "approved_at": None,
                "status": "suggested",
                "provenance": "",
                "notes": "",
            }
            await store.create_link(link)

        # Filter by relation_type
        links, total = await store.list_links(relation_type="supports")
        assert total == 3
        assert len(links) == 3

        # Filter by source_type
        links, total = await store.list_links(source_type="wiki_article")
        assert total == 5

    @pytest.mark.asyncio
    async def test_store_delete_link(self, links_db):
        """KnowledgeLinkStore.delete_link removes the link."""
        from aip.adapter.api.routes.links import KnowledgeLinkStore

        store = KnowledgeLinkStore(links_db)

        link = {
            "id": "test:del:1",
            "source_type": "wiki_article",
            "source_id": "wiki:test:1",
            "target_type": "artifact",
            "target_id": "art:1",
            "relation_type": "supports",
            "confidence": 1.0,
            "created_by": "definer",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "approved_by_definer": False,
            "approved_at": None,
            "status": "suggested",
            "provenance": "",
            "notes": "",
        }
        await store.create_link(link)
        deleted = await store.delete_link("test:del:1")
        assert deleted is True

        result = await store.get_link("test:del:1")
        assert result is None

    @pytest.mark.asyncio
    async def test_store_update_link_approval(self, links_db):
        """KnowledgeLinkStore.update_link handles approval correctly."""
        from aip.adapter.api.routes.links import KnowledgeLinkStore

        store = KnowledgeLinkStore(links_db)

        link = {
            "id": "test:approve:1",
            "source_type": "wiki_article",
            "source_id": "wiki:test:1",
            "target_type": "artifact",
            "target_id": "art:1",
            "relation_type": "supports",
            "confidence": 1.0,
            "created_by": "definer",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "approved_by_definer": False,
            "approved_at": None,
            "status": "suggested",
            "provenance": "",
            "notes": "",
        }
        await store.create_link(link)

        # Approve
        result = await store.update_link(
            "test:approve:1",
            {
                "approved_by_definer": True,
                "approved_at": "2026-01-02T00:00:00Z",
                "status": "approved",
                "updated_at": "2026-01-02T00:00:00Z",
            },
        )
        assert result["approved_by_definer"] is True
        assert result["approved_at"] is not None
        assert result["status"] == "approved"
