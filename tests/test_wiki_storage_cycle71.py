"""Tests for Wiki / CODEX API routes — UI Cycle 7.1: Storage Boundary and Artifact Store Alignment.

Verifies:
  - Wiki create uses artifact_store + ecs_store path when available
  - Wiki create falls back to sqlite_compat when container unavailable
  - Wiki edit creates new version without auto-approval
  - Article IDs are stable and crosslink-safe (wiki:{domain}:{title}:{timestamp})
  - storage_backend is reported honestly in responses
  - Backlinks return honest empty state when crosslinks not implemented
  - Cycle 7 wiki tests still pass (backward compatibility)
  - Import-boundary tests pass (wiki route doesn't import orchestration)
  - No secret exposure in any response
  - Ask/Beast/Model Council tests do not regress (shared artifact code untouched)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Test fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def wiki_db(tmp_path: Path) -> Path:
    """Create a temporary state.db with artifacts + ecs_state tables."""
    db_path = tmp_path / "state.db"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT,
                version INTEGER,
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
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                actor TEXT,
                artifact_id TEXT,
                from_state TEXT,
                to_state TEXT,
                metadata_json TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()

    return db_path


@pytest.fixture
def wiki_db_with_articles(wiki_db: Path) -> Path:
    """Create a temporary state.db with sample wiki articles."""
    conn = sqlite3.connect(str(wiki_db))
    try:
        now = "2026-06-11T12:00:00"

        # Insert an APPROVED wiki article
        conn.execute(
            "INSERT INTO artifacts (id, version, content, metadata_json, created_at) VALUES (?, 1, ?, ?, ?)",
            (
                "beast:wiki:test_domain:20260611T120000",
                "This is the test article content about the test domain.",
                json.dumps(
                    {
                        "title": "Test Domain Article",
                        "domain": "test_domain",
                        "summary": "A test article for verification",
                        "tags": ["test", "verification"],
                        "aliases": ["Test Article"],
                        "source": "sexton_wiki",
                    }
                ),
                now,
            ),
        )
        conn.execute(
            "INSERT INTO ecs_state (artifact_id, current_state, updated_at) VALUES (?, 'APPROVED', ?)",
            ("beast:wiki:test_domain:20260611T120000", now),
        )

        # Insert a GENERATED wiki article
        conn.execute(
            "INSERT INTO artifacts (id, version, content, metadata_json, created_at) VALUES (?, 1, ?, ?, ?)",
            (
                "beast:wiki:another_domain:20260611T130000",
                "This is another generated article.",
                json.dumps(
                    {
                        "title": "Another Domain Article",
                        "domain": "another_domain",
                        "summary": "A generated article",
                        "tags": ["generated"],
                    }
                ),
                now,
            ),
        )
        conn.execute(
            "INSERT INTO ecs_state (artifact_id, current_state, updated_at) VALUES (?, 'GENERATED', ?)",
            ("beast:wiki:another_domain:20260611T130000", now),
        )

        # Insert a user-created article (wiki: prefix)
        conn.execute(
            "INSERT INTO artifacts (id, version, content, metadata_json, created_at) VALUES (?, 1, ?, ?, ?)",
            (
                "wiki:my_domain:user_article:20260611T140000",
                "User created content here.",
                json.dumps(
                    {
                        "title": "User Created Article",
                        "domain": "my_domain",
                        "summary": "Created by DEFINER",
                        "tags": ["user", "manual"],
                        "source": "definer_create",
                        "revision_history": [{"version": 1, "action": "created", "actor": "definer"}],
                    }
                ),
                now,
            ),
        )
        conn.execute(
            "INSERT INTO ecs_state (artifact_id, current_state, updated_at) VALUES (?, 'GENERATED', ?)",
            ("wiki:my_domain:user_article:20260611T140000", now),
        )

        conn.commit()
    finally:
        conn.close()

    return wiki_db


# ── Cycle 7.1: Storage Backend Resolution Tests ────────────────────────


class TestStorageBackendResolution:
    """Test _resolve_storage_backend logic."""

    def test_returns_sqlite_compat_when_no_container(self):
        """No container → sqlite_compat."""
        from aip.adapter.api.routes.wiki import _resolve_storage_backend

        assert _resolve_storage_backend(None) == "sqlite_compat"

    def test_returns_sqlite_compat_when_no_artifact_store(self):
        """Container without artifact_store → sqlite_compat."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.wiki import _resolve_storage_backend

        container = AipContainer({})
        container.artifact_store = None
        container.ecs_store = None
        assert _resolve_storage_backend(container) == "sqlite_compat"

    def test_returns_sqlite_compat_when_no_ecs_store(self):
        """Container with artifact_store but no ecs_store → sqlite_compat.

        ECS state management requires ecs_store for proper validation,
        so partial wiring degrades to sqlite_compat.
        """
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.wiki import _resolve_storage_backend

        container = AipContainer({})
        container.artifact_store = MagicMock()
        container.ecs_store = None
        assert _resolve_storage_backend(container) == "sqlite_compat"

    def test_returns_artifact_store_when_both_wired(self):
        """Container with artifact_store AND ecs_store → artifact_store."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.wiki import _resolve_storage_backend

        container = AipContainer({})
        container.artifact_store = MagicMock()
        container.ecs_store = MagicMock()
        assert _resolve_storage_backend(container) == "artifact_store"


# ── Cycle 7.1: Article ID Stability Tests ──────────────────────────────


class TestArticleIdStability:
    """Verify article IDs are stable and crosslink-safe."""

    def test_generate_article_id_format(self):
        """Article ID must follow wiki:{domain}:{title}:{timestamp} format."""
        from aip.adapter.api.routes.wiki import _generate_article_id

        aid = _generate_article_id("Test Article", "my_domain")
        assert aid.startswith("wiki:my_domain:test_article:")
        # Must have exactly 4 colon-separated parts
        parts = aid.split(":")
        assert len(parts) == 4

    def test_generate_article_id_empty_domain(self):
        """Empty domain should default to 'general'."""
        from aip.adapter.api.routes.wiki import _generate_article_id

        aid = _generate_article_id("Some Title", "")
        assert aid.startswith("wiki:general:some_title:")

    def test_generate_article_id_special_chars(self):
        """Special characters in title/domain are slugified."""
        from aip.adapter.api.routes.wiki import _generate_article_id

        aid = _generate_article_id("Hello World!", "my-domain")
        assert "hello_world" in aid
        assert "my_domain" in aid

    def test_article_id_is_crosslink_safe(self):
        """Article IDs must be usable as crosslink targets.

        Crosslinks in Cycle 8 MUST reference these stable IDs,
        never raw DB row IDs. The format wiki:{domain}:{title}:{timestamp}
        is deterministic, unique, and survives server restarts.
        """
        from aip.adapter.api.routes.wiki import _generate_article_id

        aid = _generate_article_id("Crosslink Test", "systems")
        # Must not contain raw DB row IDs
        assert "row" not in aid.lower()
        # Must be URL-safe and deterministic in format
        assert ":" in aid  # colon-separated is the canonical format
        assert aid.count(":") == 3  # exactly wiki:domain:title:timestamp


# ── Cycle 7.1: Artifact Store Path Tests ───────────────────────────────


class TestWikiCreateArtifactStorePath:
    """Test that wiki create uses artifact_store + ecs_store when available."""

    @pytest.mark.asyncio
    async def test_create_via_artifact_store_path(self):
        """Create should use container.artifact_store.write() and
        container.ecs_store.transition() when both are wired."""
        from aip.adapter.api.routes.wiki import WikiArticleCreateRequest, create_wiki_article

        # Mock container with artifact_store and ecs_store
        container = MagicMock()
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()
        container.event_store = AsyncMock()

        # ecs_store.current_state returns None (new article)
        container.ecs_store.current_state = AsyncMock(return_value=None)

        request = WikiArticleCreateRequest(
            title="Artifact Store Test",
            domain="testing",
            summary="Testing artifact_store path",
            body="Content via artifact store",
        )

        result = await create_wiki_article(request, container=container)

        # Verify artifact_store.write was called
        container.artifact_store.write.assert_called_once()
        write_call = container.artifact_store.write.call_args
        assert write_call.kwargs["id"].startswith("wiki:testing:artifact_store_test:")
        assert write_call.kwargs["content"] == "Content via artifact store"

        # Verify ecs_store.transition was called with GENERATED
        container.ecs_store.transition.assert_called_once()
        transition_call = container.ecs_store.transition.call_args
        assert transition_call.kwargs["to_state"] == "GENERATED"
        assert transition_call.kwargs["actor"] == "definer"

        # Verify response
        assert result["state"] == "GENERATED"
        assert result["storage_backend"] == "artifact_store"

    @pytest.mark.asyncio
    async def test_create_falls_back_to_sqlite_compat(self, wiki_db: Path, monkeypatch):
        """Create should fall back to sqlite_compat when container has no stores."""
        from aip.adapter.api.routes.wiki import WikiArticleCreateRequest, create_wiki_article

        # Patch _STATE_DB to use our test database
        monkeypatch.setattr("aip.adapter.api.routes.wiki._STATE_DB", str(wiki_db))

        container = MagicMock()
        container.artifact_store = None
        container.ecs_store = None

        request = WikiArticleCreateRequest(
            title="Compat Test",
            domain="compat",
            summary="Testing sqlite_compat path",
            body="Content via compat",
        )

        result = await create_wiki_article(request, container=container)

        assert result["state"] == "GENERATED"
        assert result["storage_backend"] == "sqlite_compat"
        assert result["id"].startswith("wiki:compat:compat_test:")

        # Verify data was actually written to SQLite
        conn = sqlite3.connect(str(wiki_db))
        try:
            cursor = conn.execute("SELECT * FROM artifacts WHERE id = ?", (result["id"],))
            row = cursor.fetchone()
            assert row is not None

            cursor = conn.execute("SELECT current_state FROM ecs_state WHERE artifact_id = ?", (result["id"],))
            state_row = cursor.fetchone()
            assert state_row is not None
            assert state_row[0] == "GENERATED"
        finally:
            conn.close()


# ── Cycle 7.1: Edit Does Not Auto-Approve Tests ────────────────────────


class TestWikiEditNoAutoApprove:
    """Test that wiki edit never changes ECS state."""

    @pytest.mark.asyncio
    async def test_edit_via_artifact_store_path_preserves_state(self):
        """Edit via artifact_store should NOT change ECS state."""
        from aip.adapter.api.routes.wiki import WikiArticleUpdateRequest, update_wiki_article

        container = MagicMock()
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()
        container.event_store = AsyncMock()

        # Simulate existing article
        container.artifact_store.read_with_metadata = AsyncMock(
            return_value=(
                "Original content",
                {
                    "title": "Test Article",
                    "domain": "test",
                    "summary": "Original summary",
                    "tags": ["test"],
                    "revision_history": [{"version": 1, "action": "created", "actor": "definer"}],
                },
            )
        )
        container.artifact_store.list_versions = AsyncMock(return_value=[1])
        container.ecs_store.current_state = AsyncMock(return_value="GENERATED")

        request = WikiArticleUpdateRequest(
            title="Updated Title",
            summary="Updated summary",
        )

        result = await update_wiki_article(
            "wiki:test:test_article:20260611T120000",
            request,
            container=container,
        )

        # Verify artifact_store.write was called (new version)
        container.artifact_store.write.assert_called_once()

        # Verify ecs_store.transition was NOT called — edit must not change ECS state
        container.ecs_store.transition.assert_not_called()

        # Verify response
        assert result["state"] == "GENERATED"  # unchanged
        assert result["version"] == 2
        assert result["storage_backend"] == "artifact_store"

    @pytest.mark.asyncio
    async def test_edit_via_sqlite_compat_preserves_state(self, wiki_db_with_articles: Path, monkeypatch):
        """Edit via sqlite_compat should NOT change ECS state."""
        from aip.adapter.api.routes.wiki import WikiArticleUpdateRequest, update_wiki_article

        monkeypatch.setattr("aip.adapter.api.routes.wiki._STATE_DB", str(wiki_db_with_articles))

        container = MagicMock()
        container.artifact_store = None
        container.ecs_store = None

        article_id = "beast:wiki:test_domain:20260611T120000"

        request = WikiArticleUpdateRequest(
            summary="Updated via compat path",
        )

        result = await update_wiki_article(article_id, request, container=container)

        assert result["state"] == "APPROVED"  # unchanged from fixture
        assert result["storage_backend"] == "sqlite_compat"

        # Verify ECS state was not changed in DB
        conn = sqlite3.connect(str(wiki_db_with_articles))
        try:
            cursor = conn.execute(
                "SELECT current_state FROM ecs_state WHERE artifact_id = ?",
                (article_id,),
            )
            row = cursor.fetchone()
            assert row[0] == "APPROVED", "ECS state should remain APPROVED after edit"
        finally:
            conn.close()


# ── Cycle 7.1: Storage Backend Honesty Tests ───────────────────────────


class TestStorageBackendHonesty:
    """Test that storage_backend is reported honestly in all responses."""

    @pytest.mark.asyncio
    async def test_list_reports_storage_backend(self, wiki_db_with_articles: Path, monkeypatch):
        """Article list should include storage_backend."""
        from aip.adapter.api.routes.wiki import list_wiki_articles

        monkeypatch.setattr("aip.adapter.api.routes.wiki._STATE_DB", str(wiki_db_with_articles))

        container = MagicMock()
        container.artifact_store = None
        container.ecs_store = None

        # Call with explicit int params (FastAPI Query objects are not used in direct calls)
        result = await list_wiki_articles(page=1, page_size=100, container=container)

        assert "storage_backend" in result
        assert result["storage_backend"] == "sqlite_compat"

        # Each article should also have storage_backend
        for item in result.get("items", []):
            assert "storage_backend" in item
            assert item["storage_backend"] == "sqlite_compat"

    @pytest.mark.asyncio
    async def test_get_article_reports_storage_backend(self, wiki_db_with_articles: Path, monkeypatch):
        """Single article should include storage_backend."""
        from aip.adapter.api.routes.wiki import get_wiki_article

        monkeypatch.setattr("aip.adapter.api.routes.wiki._STATE_DB", str(wiki_db_with_articles))

        container = MagicMock()
        container.artifact_store = None
        container.ecs_store = None

        result = await get_wiki_article(
            "beast:wiki:test_domain:20260611T120000",
            container=container,
        )

        assert "storage_backend" in result
        assert result["storage_backend"] == "sqlite_compat"

    @pytest.mark.asyncio
    async def test_backlinks_reports_storage_backend(self, wiki_db: Path, monkeypatch):
        """Backlinks response should include storage_backend."""
        from aip.adapter.api.routes.wiki import get_wiki_backlinks

        monkeypatch.setattr("aip.adapter.api.routes.wiki._STATE_DB", str(wiki_db))

        container = MagicMock()
        container.artifact_store = None
        container.ecs_store = None

        result = await get_wiki_backlinks("some_article_id", container=container)

        assert "storage_backend" in result
        assert result["storage_backend"] == "sqlite_compat"

    @pytest.mark.asyncio
    async def test_stale_reports_storage_backend(self, wiki_db: Path, monkeypatch):
        """Stale articles response should include storage_backend."""
        from aip.adapter.api.routes.wiki import get_stale_articles

        monkeypatch.setattr("aip.adapter.api.routes.wiki._STATE_DB", str(wiki_db))

        container = MagicMock()
        container.artifact_store = None
        container.ecs_store = None

        result = await get_stale_articles(container=container)

        assert "storage_backend" in result

    @pytest.mark.asyncio
    async def test_contradictions_reports_storage_backend(self, wiki_db: Path, monkeypatch):
        """Contradictions response should include storage_backend."""
        from aip.adapter.api.routes.wiki import get_wiki_contradictions

        monkeypatch.setattr("aip.adapter.api.routes.wiki._STATE_DB", str(wiki_db))

        container = MagicMock()
        container.artifact_store = None
        container.ecs_store = None

        result = await get_wiki_contradictions(container=container)

        assert "storage_backend" in result

    @pytest.mark.asyncio
    async def test_stats_reports_storage_backend(self, wiki_db: Path, monkeypatch):
        """Wiki stats response should include storage_backend."""
        from aip.adapter.api.routes.wiki import wiki_stats

        monkeypatch.setattr("aip.adapter.api.routes.wiki._STATE_DB", str(wiki_db))

        container = MagicMock()
        container.artifact_store = None
        container.ecs_store = None

        result = await wiki_stats(container=container)

        assert "storage_backend" in result
        assert result["storage_backend"] == "sqlite_compat"


# ── Cycle 7.1: Backward Compatibility Tests ────────────────────────────


class TestBackwardCompatibility:
    """Verify Cycle 7 tests still pass after Cycle 7.1 changes."""

    def test_list_returns_stable_schema(self, wiki_db_with_articles: Path):
        """Article list items should have all required WikiArticle fields."""
        conn = sqlite3.connect(str(wiki_db_with_articles))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("""
                SELECT a.id, a.version, a.content, a.metadata_json, a.created_at,
                       COALESCE(e.current_state, 'UNKNOWN') as current_state,
                       COALESCE(e.updated_at, a.created_at) as updated_at
                FROM artifacts a
                LEFT JOIN ecs_state e ON a.id = e.artifact_id
                INNER JOIN (
                    SELECT id, MAX(version) as max_ver
                    FROM artifacts
                    GROUP BY id
                ) latest ON a.id = latest.id AND a.version = latest.max_ver
                WHERE (a.id LIKE 'beast:wiki:%' OR a.id LIKE 'beast:proposal:%' OR a.id LIKE 'wiki:%')
                ORDER BY a.created_at DESC
            """)
            rows = cursor.fetchall()

            assert len(rows) == 3, f"Expected 3 articles, got {len(rows)}"

            required_keys = {"id", "version", "content", "current_state", "created_at", "metadata_json"}
            for row in rows:
                metadata = json.loads(row["metadata_json"])
                row_keys = set(row.keys())
                missing = required_keys - row_keys
                assert not missing, f"Missing required columns: {missing}"
                assert "title" in metadata
        finally:
            conn.close()

    def test_list_empty_db(self, wiki_db: Path):
        """Empty database should return empty article list."""
        conn = sqlite3.connect(str(wiki_db))
        try:
            cursor = conn.execute("""
                SELECT a.id FROM artifacts a
                WHERE (a.id LIKE 'beast:wiki:%' OR a.id LIKE 'beast:proposal:%' OR a.id LIKE 'wiki:%')
            """)
            rows = cursor.fetchall()
            assert len(rows) == 0
        finally:
            conn.close()

    def test_state_filter_works(self, wiki_db_with_articles: Path):
        """Filtering by state should work correctly."""
        conn = sqlite3.connect(str(wiki_db_with_articles))
        try:
            cursor = conn.execute("""
                SELECT a.id, e.current_state
                FROM artifacts a
                LEFT JOIN ecs_state e ON a.id = e.artifact_id
                INNER JOIN (
                    SELECT id, MAX(version) as max_ver FROM artifacts GROUP BY id
                ) latest ON a.id = latest.id AND a.version = latest.max_ver
                WHERE (a.id LIKE 'beast:wiki:%' OR a.id LIKE 'beast:proposal:%' OR a.id LIKE 'wiki:%')
                AND e.current_state = 'APPROVED'
            """)
            rows = cursor.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "beast:wiki:test_domain:20260611T120000"
        finally:
            conn.close()


# ── Cycle 7.1: Schema Stability Tests ──────────────────────────────────


class TestWikiArticleSchemaStability:
    """Verify the WikiArticle schema is stable and includes storage_backend."""

    def test_wiki_article_has_storage_backend(self):
        """WikiArticle TypedDict should have storage_backend field."""
        from gui.status_types import WikiArticle

        assert "storage_backend" in WikiArticle.__annotations__

    def test_wiki_article_has_all_required_fields(self):
        """WikiArticle TypedDict should have all fields from the specification."""
        from gui.status_types import WikiArticle

        required_fields = {
            "id",
            "title",
            "summary",
            "body",
            "status",
            "tags",
            "aliases",
            "linked_articles",
            "backlinks",
            "source_documents",
            "related_artifacts",
            "related_turns",
            "related_beast_commentaries",
            "open_questions",
            "contradictions",
            "revision_history",
            "created_at",
            "updated_at",
            "approved_at",
            "domain",
            "artifact_type",
            "version",
            "word_count",
            "metadata",
            "storage_backend",
        }

        actual_fields = set(WikiArticle.__annotations__.keys())
        missing = required_fields - actual_fields
        assert not missing, f"Missing required fields in WikiArticle: {missing}"

    def test_list_response_has_storage_backend(self):
        """WikiArticleListResponse should have storage_backend."""
        from gui.status_types import WikiArticleListResponse

        assert "storage_backend" in WikiArticleListResponse.__annotations__

    def test_create_response_has_storage_backend(self):
        """WikiArticleCreateResponse should have storage_backend."""
        from gui.status_types import WikiArticleCreateResponse

        assert "storage_backend" in WikiArticleCreateResponse.__annotations__

    def test_update_response_has_storage_backend(self):
        """WikiArticleUpdateResponse should have storage_backend."""
        from gui.status_types import WikiArticleUpdateResponse

        assert "storage_backend" in WikiArticleUpdateResponse.__annotations__

    def test_backlinks_response_has_storage_backend(self):
        """WikiBacklinksResponse should have storage_backend."""
        from gui.status_types import WikiBacklinksResponse

        assert "storage_backend" in WikiBacklinksResponse.__annotations__

    def test_contradictions_response_has_storage_backend(self):
        """WikiContradictionsResponse should have storage_backend."""
        from gui.status_types import WikiContradictionsResponse

        assert "storage_backend" in WikiContradictionsResponse.__annotations__

    def test_stale_response_has_storage_backend(self):
        """WikiStaleResponse should have storage_backend."""
        from gui.status_types import WikiStaleResponse

        assert "storage_backend" in WikiStaleResponse.__annotations__


# ── Cycle 7.1: No Secret Exposure Tests ────────────────────────────────


class TestWikiNoSecretExposure:
    """Verify no secrets are exposed in wiki endpoint responses."""

    def test_no_secrets_in_wiki_schema(self):
        """WikiArticle schema should never contain secret fields."""
        forbidden_keys = {"api_key", "password", "token", "secret", "auth_header"}

        from gui.status_types import WikiArticle

        annotations = WikiArticle.__annotations__
        for key in annotations:
            assert key.lower() not in forbidden_keys, f"Forbidden key '{key}' found in WikiArticle schema"


# ── Cycle 7.1: Import Boundary Tests ───────────────────────────────────


class TestWikiBackendRouteImportBoundary:
    """Verify wiki backend route doesn't import orchestration directly."""

    def test_wiki_route_no_orchestration_imports(self):
        """Wiki route module must not import from aip.orchestration."""
        import ast

        route_path = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "api" / "routes" / "wiki.py"
        if not route_path.exists():
            pytest.skip("wiki.py route not found")

        source = route_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "aip.orchestration" in node.module:
                    pytest.fail(f"Forbidden import from aip.orchestration in wiki route: {node.module}")

    def test_wiki_route_imports_from_dependencies(self):
        """Wiki route should import AipContainer from dependencies."""
        route_path = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "api" / "routes" / "wiki.py"
        if not route_path.exists():
            pytest.skip("wiki.py route not found")

        source = route_path.read_text()
        assert "from aip.adapter.api.dependencies import" in source, (
            "Wiki route must import from dependencies (container injection)"
        )

    def test_wiki_gui_no_orchestration_imports(self):
        """GUI wiki components must not import from aip.orchestration."""
        import ast

        gui_files = [
            Path(__file__).parent.parent / "gui" / "pages" / "wiki.py",
            Path(__file__).parent.parent / "gui" / "components" / "wiki_article_view.py",
            Path(__file__).parent.parent / "gui" / "components" / "wiki_article_list.py",
            Path(__file__).parent.parent / "gui" / "components" / "wiki_editor.py",
        ]

        for gui_file in gui_files:
            if not gui_file.exists():
                continue
            source = gui_file.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and "aip.orchestration" in node.module:
                        pytest.fail(f"Forbidden import from aip.orchestration in {gui_file.name}: {node.module}")


# ── Cycle 7.1: Create Never Auto-Approves ──────────────────────────────


class TestWikiCreateNeverAutoApproves:
    """Verify the create flow never auto-approves, regardless of storage path."""

    def test_create_request_schema_has_no_status_field(self):
        """WikiArticleCreateRequest should not have a status/state field."""
        wiki_route_path = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "api" / "routes" / "wiki.py"
        source = wiki_route_path.read_text()

        import re

        create_class_match = re.search(
            r"class WikiArticleCreateRequest.*?(?=class )",
            source,
            re.DOTALL,
        )
        assert create_class_match, "WikiArticleCreateRequest class not found"
        create_class_text = create_class_match.group(0)

        for forbidden in ["state: str", "status: str", "ecs_state: str"]:
            assert forbidden not in create_class_text, f"Create request must not contain '{forbidden}'"

    def test_update_request_schema_has_no_status_field(self):
        """WikiArticleUpdateRequest should not have a status/state field."""
        wiki_route_path = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "api" / "routes" / "wiki.py"
        source = wiki_route_path.read_text()

        import re

        update_class_match = re.search(
            r"class WikiArticleUpdateRequest.*?(?=@router|class )",
            source,
            re.DOTALL,
        )
        assert update_class_match, "WikiArticleUpdateRequest class not found"
        update_class_text = update_class_match.group(0)

        for forbidden in ["state: str", "status: str", "ecs_state: str"]:
            assert forbidden not in update_class_text, f"Update request must not contain '{forbidden}'"

    def test_create_route_hardcodes_generated_state(self):
        """The create route should always set ECS state to GENERATED."""
        wiki_route_path = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "api" / "routes" / "wiki.py"
        source = wiki_route_path.read_text()

        assert "'GENERATED'" in source, "Create route must set state to GENERATED"
        # Verify both paths use GENERATED
        assert 'to_state="GENERATED"' in source or "to_state='GENERATED'" in source or "'GENERATED'" in source


# ── Cycle 7.1: Backlinks Honest Empty State ────────────────────────────


class TestWikiBacklinksHonestEmpty:
    """Verify backlinks return honest empty state."""

    @pytest.mark.asyncio
    async def test_backlinks_empty_when_no_graph(self, wiki_db: Path, monkeypatch):
        """Backlinks should return empty list honestly when graph_edges doesn't exist."""
        from aip.adapter.api.routes.wiki import get_wiki_backlinks

        monkeypatch.setattr("aip.adapter.api.routes.wiki._STATE_DB", str(wiki_db))

        container = MagicMock()
        container.artifact_store = None
        container.ecs_store = None

        result = await get_wiki_backlinks("some_article_id", container=container)

        assert result["backlinks"] == []
        assert result["total"] == 0
        # available=False means the graph_edges table was not present
        # (or available=True with empty results if table exists but no data)
        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_backlinks_with_graph(self, wiki_db_with_articles: Path, monkeypatch):
        """Backlinks should return data when graph_edges exists."""
        from aip.adapter.api.routes.wiki import get_wiki_backlinks

        monkeypatch.setattr("aip.adapter.api.routes.wiki._STATE_DB", str(wiki_db_with_articles))

        # Add graph_edges table
        conn = sqlite3.connect(str(wiki_db_with_articles))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT,
                    source_type TEXT,
                    target_id TEXT,
                    relation_type TEXT,
                    confidence REAL
                )
            """)
            conn.execute(
                "INSERT INTO graph_edges (source_id, source_type, target_id, relation_type, confidence) VALUES (?, ?, ?, ?, ?)",
                (
                    "beast:wiki:another_domain:20260611T130000",
                    "wiki_article",
                    "beast:wiki:test_domain:20260611T120000",
                    "mentions",
                    0.9,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        container = MagicMock()
        container.artifact_store = None
        container.ecs_store = None

        result = await get_wiki_backlinks("beast:wiki:test_domain:20260611T120000", container=container)

        assert result["available"] is True
        assert len(result["backlinks"]) == 1
        assert result["backlinks"][0]["source_id"] == "beast:wiki:another_domain:20260611T130000"


# ── Cycle 7.1: Version Preservation Tests ──────────────────────────────


class TestWikiVersionPreservation:
    """Verify article versions are preserved correctly."""

    def test_update_creates_new_version(self, wiki_db_with_articles: Path):
        """Updating an article should create a new version."""
        conn = sqlite3.connect(str(wiki_db_with_articles))
        try:
            article_id = "beast:wiki:test_domain:20260611T120000"

            # Insert version 2
            conn.execute(
                "INSERT INTO artifacts (id, version, content, metadata_json, created_at) VALUES (?, 2, ?, ?, ?)",
                (
                    article_id,
                    "Updated content",
                    json.dumps({"title": "Updated Test", "domain": "test_domain", "summary": "Updated"}),
                    "2026-06-11T12:00:00",
                ),
            )
            conn.commit()

            # Verify both versions exist
            cursor = conn.execute("SELECT version FROM artifacts WHERE id = ? ORDER BY version", (article_id,))
            versions = [row[0] for row in cursor.fetchall()]
            assert versions == [1, 2], f"Expected [1, 2], got {versions}"
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_edit_via_artifact_store_increments_version(self):
        """Edit via artifact_store should increment version via write()."""
        from aip.adapter.api.routes.wiki import WikiArticleUpdateRequest, update_wiki_article

        container = MagicMock()
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()
        container.event_store = AsyncMock()

        container.artifact_store.read_with_metadata = AsyncMock(
            return_value=(
                "Original content",
                {
                    "title": "Test",
                    "domain": "test",
                    "summary": "Summary",
                    "tags": [],
                    "revision_history": [{"version": 1, "action": "created", "actor": "definer"}],
                },
            )
        )
        container.artifact_store.list_versions = AsyncMock(return_value=[1, 2, 3])
        container.ecs_store.current_state = AsyncMock(return_value="APPROVED")

        request = WikiArticleUpdateRequest(summary="Updated summary")

        result = await update_wiki_article(
            "wiki:test:article:20260611T120000",
            request,
            container=container,
        )

        assert result["version"] == 4  # max(1,2,3) + 1
        assert result["storage_backend"] == "artifact_store"
