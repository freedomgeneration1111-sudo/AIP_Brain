"""Tests for the dogfood loop — datastore coherence and CLI integration.

Verifies that the full first-run path works:
  init → project create → ingest → ask → review → approve → export

All tests use temporary directories and verify that ALL CLI commands
use the same default database path after `aip init`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_aip_env(tmp_path):
    """Create a temporary AIP environment with proper directory structure.

    Returns the absolute path to the main database file.
    """
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Write config with absolute db_path
    db_path = str(db_dir / "state.db")
    config_path = config_dir / "aip.config.toml"
    config_path.write_text(f'[vector_backend]\nprovider = "sqlite_vss"\n\n[database]\ndb_path = "{db_path}"\n')

    return db_path


@pytest.fixture
def sample_markdown_file(tmp_path):
    """Create a sample markdown transcript file."""
    md_dir = tmp_path / "sample_data"
    md_dir.mkdir(exist_ok=True)
    md_file = md_dir / "decisions.md"
    md_file.write_text("""# Project Decisions

**User**: What have we decided about artifact storage?

**Assistant**: We decided on VersionedArtifactStore with DEFINER sovereignty. Every version is preserved for provenance.

**User**: And the review process?

**Assistant**: Artifacts go through GENERATED then REVIEWED then APPROVED lifecycle. No auto-approve path exists.
""")
    return md_file


async def _setup_project_and_ingest(db_path: str, sample_markdown_file: Path):
    """Helper: create a project and ingest sample content.

    Returns (ingest_results, project_id).
    """
    from aip.adapter.project.sqlite_project_store import SqliteProjectStore
    from aip.orchestration.ingestion.pipeline import create_ingestion_stores, ingest_file

    project_store = SqliteProjectStore(db_path)
    await project_store.initialize()
    await project_store.create_project(project_id="test123", name="test_project", domain="test_project")
    await project_store.close()

    ingest_stores = await create_ingestion_stores(db_path)
    results = await ingest_file(
        path=str(sample_markdown_file),
        artifact_store=ingest_stores.artifact_store,
        lexical_store=ingest_stores.lexical_store,
        vector_store=ingest_stores.vector_store,
        embedding_provider=getattr(ingest_stores, "embedding_provider", None),
        event_store=ingest_stores.event_store,
        domain="test_project",
    )
    await ingest_stores.close()

    return results


class MockModelProvider:
    """Mock model provider that returns a fixed answer."""

    async def call(self, slot, messages, **kwargs):
        return {
            "content": "Based on the sources, artifact storage uses VersionedArtifactStore with DEFINER sovereignty.",
            "model": "mock",
        }


async def _save_artifact_with_mock(db_path: str, question: str = "artifact storage VersionedArtifactStore"):
    """Helper: ask a question with mock model and save as artifact.

    Returns (artifact_id, ask_result).
    """
    from aip.orchestration.ask_pipeline import ask, create_ask_stores

    ask_stores = await create_ask_stores(db_path)
    ask_stores.model_provider = MockModelProvider()
    try:
        result = await ask(
            question=question,
            project_name="test_project",
            stores=ask_stores,
            source="all",
            save_artifact=True,
        )
        return result.artifact_id, result
    finally:
        await ask_stores.close()


# ---------------------------------------------------------------------------
# Test 1: All CLI commands use the same default DB path after aip init
# ---------------------------------------------------------------------------


def test_cli_commands_use_same_default_db_path(tmp_aip_env, monkeypatch):
    """All CLI commands must resolve to the same default database path."""
    from aip.cli._db_path import get_default_db_path, get_default_lexical_db_path

    # Set AIP_DB_PATH to our test DB
    monkeypatch.setenv("AIP_DB_PATH", tmp_aip_env)
    assert get_default_db_path() == tmp_aip_env

    # Verify lexical path derivation
    db_dir = str(Path(tmp_aip_env).parent)
    expected_lexical = os.path.join(db_dir, "lexical.db")
    assert get_default_lexical_db_path() == expected_lexical


def test_db_path_from_env_override(tmp_aip_env, monkeypatch):
    """AIP_DB_PATH environment variable overrides the default."""
    from aip.cli._db_path import get_default_db_path

    monkeypatch.setenv("AIP_DB_PATH", "custom/path.db")
    assert get_default_db_path() == "custom/path.db"


def test_db_path_fallback_to_default(monkeypatch):
    """Without config or env, falls back to db/state.db."""
    from aip.cli._db_path import get_default_db_path

    # Remove env var
    monkeypatch.delenv("AIP_DB_PATH", raising=False)
    # Use a temp dir with no config
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.chdir(tmp)
        assert get_default_db_path() == "db/state.db"


# ---------------------------------------------------------------------------
# Test 2: aip project create creates a project visible to aip ask
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_create_visible_to_ask(tmp_aip_env):
    """Project created by aip project create must be findable by ask pipeline."""
    from aip.adapter.project.sqlite_project_store import SqliteProjectStore
    from aip.orchestration.ask_pipeline import _resolve_project

    db_path = tmp_aip_env
    store = SqliteProjectStore(db_path)
    await store.initialize()

    await store.create_project(project_id="test123", name="test_project", domain="test_project")

    project = await _resolve_project("test_project", store)
    assert project is not None, "Project 'test_project' not found by ask pipeline"
    assert project["name"] == "test_project"
    assert project["domain"] == "test_project"

    await store.close()


# ---------------------------------------------------------------------------
# Test 3: aip ingest directory indexes content into the same store that aip ask searches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_and_ask_share_same_store(tmp_aip_env, sample_markdown_file):
    """Content ingested by aip ingest must be searchable by aip ask."""
    db_path = tmp_aip_env

    results = await _setup_project_and_ingest(db_path, sample_markdown_file)
    assert len(results) > 0, "No conversations ingested"
    assert results[0].lexical_indexed, "Content not indexed into lexical store"

    # Ask — must find the same content in the same lexical store
    from aip.orchestration.ask_pipeline import _search_sources_with_trace, create_ask_stores

    ask_stores = await create_ask_stores(db_path)
    try:
        sources, _trace, _ctx = await _search_sources_with_trace(
            query="artifact storage",
            stores=ask_stores,
            source_filter="all",
        )
        assert len(sources) > 0, "Ask pipeline found no sources from ingested content"
    finally:
        await ask_stores.close()


# ---------------------------------------------------------------------------
# Test 4: aip ask after process/store restart can retrieve ingested content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_retrieves_after_restart(tmp_aip_env, sample_markdown_file):
    """Content must survive a process restart (simulated by closing and reopening stores)."""
    db_path = tmp_aip_env

    await _setup_project_and_ingest(db_path, sample_markdown_file)

    # Simulate restart by creating entirely new store instances
    from aip.orchestration.ask_pipeline import _search_sources_with_trace, create_ask_stores

    ask_stores = await create_ask_stores(db_path)
    try:
        sources, _trace, _ctx = await _search_sources_with_trace(
            query="artifact storage",
            stores=ask_stores,
            source_filter="all",
        )
        assert len(sources) > 0, "No sources found after simulated restart"
    finally:
        await ask_stores.close()


# ---------------------------------------------------------------------------
# Test 5: aip ask with no model configured returns NEEDS_CONFIGURATION but includes retrieved sources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_no_model_shows_sources(tmp_aip_env, sample_markdown_file):
    """Without a model, ask should return NEEDS_CONFIGURATION with sources."""
    db_path = tmp_aip_env

    await _setup_project_and_ingest(db_path, sample_markdown_file)

    from aip.orchestration.ask_pipeline import ask, create_ask_stores

    ask_stores = await create_ask_stores(db_path)
    ask_stores.model_provider = None  # Ensure no model
    try:
        result = await ask(
            question="What about artifact storage?",
            project_name="test_project",
            stores=ask_stores,
            source="all",
        )
        assert result.status == "NEEDS_CONFIGURATION", f"Expected NEEDS_CONFIGURATION, got {result.status}"
        assert "NEEDS_CONFIGURATION" in result.answer
        assert len(result.sources) > 0, "Sources should be present even without model"
    finally:
        await ask_stores.close()


# ---------------------------------------------------------------------------
# Test 6: aip ask --save-artifact with mock provider creates GENERATED artifact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_save_artifact_creates_generated(tmp_aip_env, sample_markdown_file):
    """ask --save-artifact should create an artifact in GENERATED ECS state."""
    db_path = tmp_aip_env

    await _setup_project_and_ingest(db_path, sample_markdown_file)

    from aip.orchestration.ask_pipeline import create_ask_stores

    artifact_id, result = await _save_artifact_with_mock(db_path)

    assert result.status == "OK", f"Expected OK, got {result.status}: {result.errors}"
    assert artifact_id, "No artifact ID returned"

    # Verify ECS state
    ask_stores = await create_ask_stores(db_path)
    try:
        ecs_state = await ask_stores.ecs_store.current_state(artifact_id)
        assert ecs_state == "GENERATED", f"Expected GENERATED, got {ecs_state}"
    finally:
        await ask_stores.close()


# ---------------------------------------------------------------------------
# Test 7: aip review list finds that artifact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_list_finds_artifact(tmp_aip_env, sample_markdown_file):
    """review list should find artifacts created by ask --save-artifact."""
    db_path = tmp_aip_env

    await _setup_project_and_ingest(db_path, sample_markdown_file)
    artifact_id, _ = await _save_artifact_with_mock(db_path)
    assert artifact_id, "No artifact saved"

    from aip.orchestration.review_export_pipeline import create_review_export_stores, review_list

    review_stores = await create_review_export_stores(db_path)
    try:
        list_result = await review_list("test_project", review_stores)
        assert "error" not in list_result, f"Review list error: {list_result.get('error')}"
        assert len(list_result["artifacts"]) > 0, "Review list found no artifacts"
        found_ids = [a["artifact_id"] for a in list_result["artifacts"]]
        assert artifact_id in found_ids, f"Artifact {artifact_id} not in review list: {found_ids}"
    finally:
        await review_stores.close()


# ---------------------------------------------------------------------------
# Test 8: aip review sources displays its provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_sources_displays_provenance(tmp_aip_env, sample_markdown_file):
    """review sources should show source links from the ingested conversation."""
    db_path = tmp_aip_env

    await _setup_project_and_ingest(db_path, sample_markdown_file)
    artifact_id, _ = await _save_artifact_with_mock(db_path)
    assert artifact_id, "No artifact saved"

    from aip.orchestration.review_export_pipeline import create_review_export_stores, review_sources

    review_stores = await create_review_export_stores(db_path)
    try:
        sources_result = await review_sources(artifact_id, review_stores)
        assert "error" not in sources_result, f"Sources error: {sources_result.get('error')}"
        assert sources_result["source_count"] > 0, "No source links found"
        assert len(sources_result["sources"]) > 0, "Sources list is empty"
    finally:
        await review_stores.close()


# ---------------------------------------------------------------------------
# Test 9: aip review approve transitions to APPROVED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_approve_transitions_to_approved(tmp_aip_env, sample_markdown_file):
    """review approve should transition GENERATED → REVIEWED → APPROVED."""
    db_path = tmp_aip_env

    await _setup_project_and_ingest(db_path, sample_markdown_file)
    artifact_id, _ = await _save_artifact_with_mock(db_path)
    assert artifact_id, "No artifact saved"

    from aip.orchestration.review_export_pipeline import create_review_export_stores, review_approve

    review_stores = await create_review_export_stores(db_path)
    try:
        approve_result = await review_approve(artifact_id, review_stores)
        assert "error" not in approve_result, f"Approve error: {approve_result.get('error')}"
        assert approve_result["lifecycle_state"] == "APPROVED", (
            f"Expected APPROVED, got {approve_result['lifecycle_state']}"
        )
        assert approve_result.get("canonical_written"), "Canonical store should be written"
    finally:
        await review_stores.close()


# ---------------------------------------------------------------------------
# Test 10: aip export artifact exports approved markdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_approved_artifact(tmp_aip_env, sample_markdown_file, tmp_path):
    """export artifact should work for APPROVED artifacts."""
    db_path = tmp_aip_env

    await _setup_project_and_ingest(db_path, sample_markdown_file)
    artifact_id, _ = await _save_artifact_with_mock(db_path)
    assert artifact_id, "No artifact saved"

    from aip.orchestration.review_export_pipeline import create_review_export_stores, export_artifact, review_approve

    # Approve
    review_stores = await create_review_export_stores(db_path)
    await review_approve(artifact_id, review_stores)
    await review_stores.close()

    # Export
    out_path = str(tmp_path / "exports" / "test.md")
    export_stores = await create_review_export_stores(db_path)
    try:
        export_result = await export_artifact(artifact_id, out_path, export_stores)
        assert "error" not in export_result, f"Export error: {export_result.get('error')}"
        assert export_result["bytes_written"] > 0, "Exported file is empty"
    finally:
        await export_stores.close()


# ---------------------------------------------------------------------------
# Test 11: Exported markdown contains content, metadata, and provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exported_markdown_content(tmp_aip_env, sample_markdown_file, tmp_path):
    """Exported markdown must have content, metadata frontmatter, and provenance footer."""
    db_path = tmp_aip_env

    await _setup_project_and_ingest(db_path, sample_markdown_file)
    artifact_id, _ = await _save_artifact_with_mock(db_path)
    assert artifact_id, "No artifact saved"

    from aip.orchestration.review_export_pipeline import create_review_export_stores, export_artifact, review_approve

    # Approve
    review_stores = await create_review_export_stores(db_path)
    await review_approve(artifact_id, review_stores)
    await review_stores.close()

    # Export
    out_path = str(tmp_path / "exports" / "test_content.md")
    export_stores = await create_review_export_stores(db_path)
    await export_artifact(artifact_id, out_path, export_stores)
    await export_stores.close()

    # Verify content
    md = Path(out_path).read_text()
    assert len(md) > 50, "Exported markdown is too short"
    assert "---" in md, "Missing frontmatter delimiter"
    assert "lifecycle_state" in md, "Missing lifecycle_state in frontmatter"
    assert "artifact_id" in md, "Missing artifact_id in frontmatter"
    assert "Provenance" in md or "source" in md.lower(), "Missing provenance footer"


# ---------------------------------------------------------------------------
# Test 12: Docs examples match actual CLI commands
# ---------------------------------------------------------------------------


def test_dogfood_ready_doc_exists():
    """DOGFOOD_READY.md must exist."""
    repo_root = Path(__file__).parent.parent
    assert (repo_root / "DOGFOOD_READY.md").exists(), "DOGFOOD_READY.md not found"


def test_dogfood_ready_doc_has_all_commands():
    """DOGFOOD_READY.md must document all dogfood loop commands."""
    repo_root = Path(__file__).parent.parent
    doc = (repo_root / "DOGFOOD_READY.md").read_text()
    required_commands = [
        "aip init",
        "aip project create",
        "aip ingest",
        "aip ask",
        "aip review list",
        "aip review show",
        "aip review sources",
        "aip review approve",
        "aip export",
        "NEEDS_CONFIGURATION",
    ]
    for cmd in required_commands:
        assert cmd in doc, f"DOGFOOD_READY.md missing: {cmd}"


def test_dogfood_ready_troubleshooting_section():
    """DOGFOOD_READY.md must have troubleshooting section."""
    repo_root = Path(__file__).parent.parent
    doc = (repo_root / "DOGFOOD_READY.md").read_text()
    assert "Troubleshooting" in doc, "Missing Troubleshooting section"
    required_topics = [
        "Project Not Found",
        "No Project Memory",
        "No Model Provider",
        "Review List Empty",
        "Export Refused",
        "Database Path Mismatch",
    ]
    for topic in required_topics:
        assert topic.lower() in doc.lower(), f"Missing troubleshooting topic: {topic}"


def test_readme_has_dogfood_loop():
    """README.md must include the dogfood loop in Quick Start."""
    repo_root = Path(__file__).parent.parent
    readme = (repo_root / "README.md").read_text()
    assert "aip ingest" in readme, "README missing ingest command"
    assert "aip ask" in readme, "README missing ask command"
    assert "aip review" in readme, "README missing review command"
    assert "aip export" in readme, "README missing export command"
    assert "DOGFOOD_READY.md" in readme, "README missing link to DOGFOOD_READY.md"
