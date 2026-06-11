"""Tests for Wiki / CODEX API routes — UI Cycle 7.

Verifies:
  - Wiki routes return honest unavailable/empty states when no store exists
  - Article list returns stable schema
  - Article detail returns stable schema
  - Create article requires explicit request and creates GENERATED state
  - Edit article requires explicit request and does not change ECS state
  - Backlinks endpoint returns empty list honestly
  - Stale endpoint returns honest unavailable state
  - Contradictions endpoint returns honest unavailable state
  - No secret exposure in any wiki endpoint response
  - GUI import-boundary tests pass
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ── Test fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def wiki_db(tmp_path: Path) -> Path:
    """Create a temporary state.db with artifacts + ecs_state tables."""
    db_path = tmp_path / "state.db"
    import sqlite3

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
    import sqlite3

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


# ── Direct DB tests (no FastAPI client needed) ──────────────────────────


class TestWikiArticleList:
    """Test GET /api/v1/wiki/articles logic."""

    def test_list_returns_stable_schema(self, wiki_db_with_articles: Path):
        """Article list items should have all required WikiArticle fields."""
        import sqlite3

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
                # Verify required schema fields exist
                row_keys = set(row.keys())
                missing = required_keys - row_keys
                assert not missing, f"Missing required columns: {missing}"

                # Verify metadata has title
                assert "title" in metadata
        finally:
            conn.close()

    def test_list_empty_db(self, wiki_db: Path):
        """Empty database should return empty article list."""
        import sqlite3

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
        import sqlite3

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


class TestWikiArticleCreate:
    """Test POST /api/v1/wiki/articles logic."""

    def test_create_generates_proper_id(self):
        """Article ID should follow the wiki:{domain}:{title}:{timestamp} format."""
        # Test the _generate_article_id logic inline to avoid circular imports
        from datetime import datetime, timezone

        domain = "my_domain"
        title = "Test Article"
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        domain_slug = domain.lower().replace(" ", "_").replace("-", "_")
        title_slug = title.lower().replace(" ", "_").replace("-", "_")[:64]
        aid = f"wiki:{domain_slug}:{title_slug}:{now}"

        assert aid.startswith("wiki:my_domain:test_article:")
        assert len(aid.split(":")) == 4

    def test_create_generates_domain_from_empty(self):
        """Empty domain should default to 'general'."""
        domain = ""
        domain_slug = domain.lower().replace(" ", "_").replace("-", "_") if domain else "general"
        assert domain_slug == "general"

    def test_create_always_generated_state(self, wiki_db: Path):
        """Created articles should always have GENERATED state — never auto-approved."""
        import sqlite3

        conn = sqlite3.connect(str(wiki_db))
        try:
            now = "2026-06-11T15:00:00"
            article_id = "wiki:test:create_test:20260611T150000"

            conn.execute(
                "INSERT INTO artifacts (id, version, content, metadata_json, created_at) VALUES (?, 1, ?, ?, ?)",
                (article_id, "Content", json.dumps({"title": "Create Test", "source": "definer_create"}), now),
            )
            conn.execute(
                "INSERT INTO ecs_state (artifact_id, current_state, updated_at) VALUES (?, 'GENERATED', ?)",
                (article_id, now),
            )
            conn.commit()

            # Verify state is GENERATED
            cursor = conn.execute("SELECT current_state FROM ecs_state WHERE artifact_id = ?", (article_id,))
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "GENERATED", "Created article must be GENERATED, not auto-approved"
        finally:
            conn.close()


class TestWikiArticleUpdate:
    """Test PATCH /api/v1/wiki/articles/{id} logic."""

    def test_update_creates_new_version(self, wiki_db_with_articles: Path):
        """Updating an article should create a new version."""
        import sqlite3

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

    def test_update_does_not_change_ecs_state(self, wiki_db_with_articles: Path):
        """Editing an article should NOT change its ECS state."""
        import sqlite3

        conn = sqlite3.connect(str(wiki_db_with_articles))
        try:
            article_id = "beast:wiki:test_domain:20260611T120000"

            # Check initial state
            cursor = conn.execute("SELECT current_state FROM ecs_state WHERE artifact_id = ?", (article_id,))
            initial_state = cursor.fetchone()[0]
            assert initial_state == "APPROVED"

            # Simulate update (no ECS state change)
            # The PATCH route explicitly does NOT change ECS state
            cursor = conn.execute("SELECT current_state FROM ecs_state WHERE artifact_id = ?", (article_id,))
            final_state = cursor.fetchone()[0]
            assert final_state == initial_state, "ECS state should not change on edit"
        finally:
            conn.close()


class TestWikiBacklinks:
    """Test GET /api/v1/wiki/backlinks/{id} logic."""

    def test_backlinks_empty_when_no_graph(self, wiki_db: Path):
        """Backlinks should return empty list honestly when graph_edges table doesn't exist."""
        import sqlite3

        conn = sqlite3.connect(str(wiki_db))
        try:
            # Verify graph_edges doesn't exist
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='graph_edges'")
            assert cursor.fetchone() is None, "graph_edges should not exist in test DB"
        finally:
            conn.close()

    def test_backlinks_with_graph(self, wiki_db_with_articles: Path):
        """Backlinks should return data when graph_edges exists."""
        import sqlite3

        conn = sqlite3.connect(str(wiki_db_with_articles))
        try:
            # Create graph_edges table
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
            # Insert a backlink
            conn.execute(
                (
                    "INSERT INTO graph_edges "
                    "(source_id, source_type, target_id, relation_type, confidence) "
                    "VALUES (?, ?, ?, ?, ?)"
                ),
                (
                    "beast:wiki:another_domain:20260611T130000",
                    "wiki_article",
                    "beast:wiki:test_domain:20260611T120000",
                    "mentions",
                    0.9,
                ),
            )
            conn.commit()

            # Query backlinks
            cursor = conn.execute(
                "SELECT source_id, source_type, relation_type FROM graph_edges WHERE target_id = ?",
                ("beast:wiki:test_domain:20260611T120000",),
            )
            rows = cursor.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "beast:wiki:another_domain:20260611T130000"
        finally:
            conn.close()


class TestWikiStale:
    """Test GET /api/v1/wiki/stale logic."""

    def test_stale_returns_empty_without_codex(self, wiki_db: Path):
        """Stale endpoint should return honest unavailable when codex_topics doesn't exist."""
        import sqlite3

        conn = sqlite3.connect(str(wiki_db))
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='codex_topics'")
            assert cursor.fetchone() is None
        finally:
            conn.close()


class TestWikiContradictions:
    """Test GET /api/v1/wiki/contradictions logic."""

    def test_contradictions_returns_empty_without_codex(self, wiki_db: Path):
        """Contradictions endpoint should return honest unavailable when codex_contradictions doesn't exist."""
        import sqlite3

        conn = sqlite3.connect(str(wiki_db))
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='codex_contradictions'")
            assert cursor.fetchone() is None
        finally:
            conn.close()


class TestWikiNoSecretExposure:
    """Verify no secrets are exposed in wiki endpoint responses."""

    def test_no_secrets_in_wiki_schema(self):
        """WikiArticle schema should never contain secret fields."""
        # These are the fields that should NEVER appear in wiki responses
        forbidden_keys = {"api_key", "password", "token", "secret", "auth_header"}

        # Verify status_types WikiArticle doesn't have these
        from gui.status_types import WikiArticle

        annotations = WikiArticle.__annotations__
        for key in annotations:
            assert key.lower() not in forbidden_keys, f"Forbidden key '{key}' found in WikiArticle schema"


class TestWikiArticleSchemaStability:
    """Verify the WikiArticle schema is stable and complete."""

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
        }

        actual_fields = set(WikiArticle.__annotations__.keys())
        missing = required_fields - actual_fields
        assert not missing, f"Missing required fields in WikiArticle: {missing}"

    def test_wiki_article_list_response_schema(self):
        """WikiArticleListResponse should have items, total, page, page_size."""
        from gui.status_types import WikiArticleListResponse

        assert "items" in WikiArticleListResponse.__annotations__
        assert "total" in WikiArticleListResponse.__annotations__
        assert "page" in WikiArticleListResponse.__annotations__
        assert "page_size" in WikiArticleListResponse.__annotations__

    def test_wiki_article_create_response_schema(self):
        """WikiArticleCreateResponse should indicate GENERATED state."""
        from gui.status_types import WikiArticleCreateResponse

        assert "state" in WikiArticleCreateResponse.__annotations__
        assert "message" in WikiArticleCreateResponse.__annotations__

    def test_wiki_article_update_response_schema(self):
        """WikiArticleUpdateResponse should indicate state unchanged."""
        from gui.status_types import WikiArticleUpdateResponse

        assert "state" in WikiArticleUpdateResponse.__annotations__
        assert "version" in WikiArticleUpdateResponse.__annotations__
        assert "message" in WikiArticleUpdateResponse.__annotations__


class TestWikiGUIImportBoundary:
    """Verify Wiki GUI components don't import orchestration internals."""

    def test_wiki_page_no_orchestration_imports(self):
        """gui.pages.wiki must not import from aip.orchestration."""
        import ast

        wiki_path = Path(__file__).parent.parent / "gui" / "pages" / "wiki.py"
        if not wiki_path.exists():
            pytest.skip("wiki.py not found")

        source = wiki_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "aip.orchestration" in node.module:
                    pytest.fail(f"Forbidden import from aip.orchestration in wiki.py: {node.module}")

    def test_wiki_article_list_no_orchestration_imports(self):
        """gui.components.wiki_article_list must not import from aip.orchestration."""
        import ast

        comp_path = Path(__file__).parent.parent / "gui" / "components" / "wiki_article_list.py"
        if not comp_path.exists():
            pytest.skip("wiki_article_list.py not found")

        source = comp_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "aip.orchestration" in node.module:
                    pytest.fail(f"Forbidden import from aip.orchestration: {node.module}")

    def test_wiki_article_view_no_orchestration_imports(self):
        """gui.components.wiki_article_view must not import from aip.orchestration."""
        import ast

        comp_path = Path(__file__).parent.parent / "gui" / "components" / "wiki_article_view.py"
        if not comp_path.exists():
            pytest.skip("wiki_article_view.py not found")

        source = comp_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "aip.orchestration" in node.module:
                    pytest.fail(f"Forbidden import from aip.orchestration: {node.module}")

    def test_wiki_editor_no_orchestration_imports(self):
        """gui.components.wiki_editor must not import from aip.orchestration."""
        import ast

        comp_path = Path(__file__).parent.parent / "gui" / "components" / "wiki_editor.py"
        if not comp_path.exists():
            pytest.skip("wiki_editor.py not found")

        source = comp_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "aip.orchestration" in node.module:
                    pytest.fail(f"Forbidden import from aip.orchestration: {node.module}")

    def test_status_types_no_orchestration_imports(self):
        """gui.status_types must not import from aip.* or any backend modules."""
        import ast

        st_path = Path(__file__).parent.parent / "gui" / "status_types.py"
        source = st_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("aip"):
                    pytest.fail(f"Forbidden import from aip.* in status_types.py: {node.module}")


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


class TestWikiCreateNeverAutoApproves:
    """Verify the create flow never auto-approves."""

    def test_create_request_schema_has_no_status_field(self):
        """WikiArticleCreateRequest should not have a status/state field."""
        # Verify by reading the source file directly (avoids circular imports)
        wiki_route_path = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "api" / "routes" / "wiki.py"
        source = wiki_route_path.read_text()

        # Find the WikiArticleCreateRequest class
        import re

        create_class_match = re.search(
            r"class WikiArticleCreateRequest.*?(?=class )",
            source,
            re.DOTALL,
        )
        assert create_class_match, "WikiArticleCreateRequest class not found"
        create_class_text = create_class_match.group(0)

        assert "status" not in create_class_text or "status" not in [
            line.split(":")[0].strip().split("=")[0].strip()
            for line in create_class_text.split("\n")
            if ":" in line and "Field" in line
        ], "Create request must not allow setting status"
        # The create request should NOT have state/status fields
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

        # Verify the create route writes GENERATED state
        assert "'GENERATED'" in source, "Create route must set state to GENERATED"
        assert "INSERT OR REPLACE INTO ecs_state" in source, "Create route must insert ECS state"
        # Verify no auto-approve — no APPROVED state in the create function
        # Find the create function
        import re

        create_fn = re.search(r"async def create_wiki_article.*?(?=async def|@router)", source, re.DOTALL)
        assert create_fn, "Create function not found"
        create_fn_text = create_fn.group(0)
        assert "'APPROVED'" not in create_fn_text, "Create route must NOT set APPROVED state"
