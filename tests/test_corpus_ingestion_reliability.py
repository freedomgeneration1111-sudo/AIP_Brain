"""Sprint 9 — Corpus Ingestion and Memory Reliability tests.

Covers:
  - Document parser (markdown, text sections → CorpusTurns)
  - Unified corpus ingest pipeline (dedup, re-ingest, provenance)
  - Content hash and dedup detection
  - Embedding failure tracking and backfill queue
  - Corpus audit and status commands
  - CorpusTurnStore new methods (content_hash, source_path, etc.)
  - CLI commands (corpus status/audit/backfill/list)
  - Dogfood seed corpus configuration
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.foundation.schemas.corpus_turn import (
    CorpusTurn,
    compute_content_hash,
    make_document_conversation_id,
    make_turn_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn(
    turn_id="test_turn_1",
    conversation_id="conv:abc",
    conversation_name="Test Conversation",
    turn_index=0,
    source_model="claude",
    source_account="test",
    export_date="2026-06-10",
    user_text="What is AIP?",
    assistant_text="AI Poiesis, a knowledge engine.",
    **kwargs,
) -> CorpusTurn:
    """Create a CorpusTurn with sensible defaults."""
    return CorpusTurn(
        turn_id=turn_id,
        conversation_id=conversation_id,
        conversation_name=conversation_name,
        turn_index=turn_index,
        source_model=source_model,
        source_account=source_account,
        export_date=export_date,
        user_text=user_text,
        assistant_text=assistant_text,
        turn_timestamp="",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Content Hash and Identity Tests
# ---------------------------------------------------------------------------


class TestContentHash:
    """Tests for content hash computation and dedup."""

    def test_compute_content_hash_deterministic(self):
        """Same text produces same hash."""
        h1 = compute_content_hash("Hello world")
        h2 = compute_content_hash("Hello world")
        assert h1 == h2

    def test_compute_content_hash_different_text(self):
        """Different text produces different hash."""
        h1 = compute_content_hash("Hello world")
        h2 = compute_content_hash("Goodbye world")
        assert h1 != h2

    def test_compute_content_hash_length(self):
        """Hash is 32 hex chars (128 bits)."""
        h = compute_content_hash("test")
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)

    def test_corpus_turn_auto_computes_hash(self):
        """CorpusTurn auto-computes content_hash from searchable_text."""
        turn = _make_turn(user_text="Hello", assistant_text="World")
        assert turn.content_hash != ""
        assert turn.content_hash == compute_content_hash(turn.searchable_text)

    def test_corpus_turn_respects_explicit_hash(self):
        """If content_hash is explicitly provided, it's preserved."""
        turn = _make_turn(user_text="Hello", assistant_text="World", content_hash="custom_hash")
        assert turn.content_hash == "custom_hash"


class TestDocumentConversationId:
    """Tests for stable document conversation IDs."""

    def test_stable_id_from_path(self):
        """Same path produces same conversation_id."""
        id1 = make_document_conversation_id("docs/ARCHITECTURE.md")
        id2 = make_document_conversation_id("docs/ARCHITECTURE.md")
        assert id1 == id2

    def test_different_paths_different_ids(self):
        """Different paths produce different conversation_ids."""
        id1 = make_document_conversation_id("docs/ARCHITECTURE.md")
        id2 = make_document_conversation_id("docs/CONFIGURATION.md")
        assert id1 != id2

    def test_id_starts_with_doc_prefix(self):
        """Document conversation IDs have doc: prefix."""
        conv_id = make_document_conversation_id("docs/test.md")
        assert conv_id.startswith("doc:")

    def test_long_path_stability(self):
        """Long absolute paths produce stable IDs using last 3 components."""
        id1 = make_document_conversation_id("/home/user/project/docs/ARCHITECTURE.md")
        id2 = make_document_conversation_id("/different/root/project/docs/ARCHITECTURE.md")
        # Last 3 components: project/docs/ARCHITECTURE.md → same stable key
        assert id1 == id2


# ---------------------------------------------------------------------------
# Document Parser Tests
# ---------------------------------------------------------------------------


class TestMarkdownDocumentParser:
    """Tests for markdown document parsing into CorpusTurns."""

    def test_parse_sections_at_headings(self):
        """Markdown with headings produces one turn per section."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_markdown_document

        text = """# Architecture

Three-layer architecture design.

# Configuration

Config is stored in TOML files."""

        turns = parse_markdown_document(text, "docs/ARCHITECTURE.md")
        assert len(turns) >= 2
        assert turns[0].user_text == "Architecture"
        assert "Three-layer" in turns[0].assistant_text
        assert turns[1].user_text == "Configuration"
        assert "TOML" in turns[1].assistant_text

    def test_parse_sets_source_model_document(self):
        """Document turns have source_model='document'."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_markdown_document

        text = "# Test Section\n\nContent here."
        turns = parse_markdown_document(text, "test.md")
        assert all(t.source_model == "document" for t in turns)

    def test_parse_sets_source_path(self):
        """Document turns have source_path set."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_markdown_document

        text = "# Test Section\n\nContent here."
        turns = parse_markdown_document(text, "docs/ARCHITECTURE.md")
        assert all(t.source_path == "docs/ARCHITECTURE.md" for t in turns)

    def test_parse_populates_content_hash(self):
        """Each turn has a non-empty content_hash."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_markdown_document

        text = "# Test Section\n\nContent here."
        turns = parse_markdown_document(text, "test.md")
        assert all(t.content_hash != "" for t in turns)

    def test_parse_metadata_has_provenance(self):
        """Document turns have provenance metadata (section_heading, offset, ingest_timestamp)."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_markdown_document

        text = "# Test Section\n\nContent here."
        turns = parse_markdown_document(text, "test.md")
        for turn in turns:
            meta = json.loads(turn.metadata_json)
            assert "section_heading" in meta
            assert "offset" in meta
            assert "ingest_timestamp" in meta
            assert meta["source_type"] == "markdown_document"

    def test_parse_no_headings_single_turn(self):
        """Markdown without headings becomes a single turn."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_markdown_document

        text = "This is a document without any headings.\n\nIt has multiple paragraphs."
        turns = parse_markdown_document(text, "test.md")
        assert len(turns) == 1

    def test_parse_stable_conversation_id(self):
        """Same path produces same conversation_id across parses."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_markdown_document

        text = "# Test\n\nContent."
        turns1 = parse_markdown_document(text, "docs/test.md")
        turns2 = parse_markdown_document(text, "docs/test.md")
        assert turns1[0].conversation_id == turns2[0].conversation_id

    def test_parse_empty_text_no_turns(self):
        """Empty text produces no turns."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_markdown_document

        turns = parse_markdown_document("", "empty.md")
        assert len(turns) == 0

    def test_preamble_before_first_heading(self):
        """Content before the first heading becomes an 'Introduction' section."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_markdown_document

        text = """This is the preamble.

# First Section

Section content."""

        turns = parse_markdown_document(text, "test.md")
        assert len(turns) == 2
        assert turns[0].user_text == "Introduction"
        assert turns[1].user_text == "First Section"


class TestTextDocumentParser:
    """Tests for plain text document parsing."""

    def test_parse_creates_turns(self):
        """Plain text produces at least one turn."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_text_document

        text = "This is a plain text document.\n\nIt has two paragraphs."
        turns = parse_text_document(text, "test.txt")
        assert len(turns) >= 1
        assert all(t.source_model == "document" for t in turns)

    def test_parse_long_text_splits(self):
        """Long text is split into multiple chunks."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_text_document

        # Create text exceeding default max_chars
        paragraphs = [f"Paragraph {i} with enough content to matter." for i in range(200)]
        text = "\n\n".join(paragraphs)
        turns = parse_text_document(text, "long.txt", max_chars=500, overlap_chars=50)
        assert len(turns) > 1

    def test_parse_sets_source_path(self):
        """Plain text turns have source_path."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_text_document

        text = "Simple text."
        turns = parse_text_document(text, "notes.txt")
        assert all(t.source_path == "notes.txt" for t in turns)


class TestDocumentFileParser:
    """Tests for document file parsing (auto-detect format)."""

    def test_parse_markdown_file(self):
        """Parse a .md file using document parser."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_document_file

        with tempfile.TemporaryDirectory() as tmp:
            filepath = os.path.join(tmp, "test.md")
            with open(filepath, "w") as f:
                f.write("# Architecture\n\nThree-layer design.\n\n# Config\n\nTOML files.")

            turns = parse_document_file(filepath)
            assert len(turns) >= 2
            assert turns[0].source_model == "document"

    def test_parse_text_file(self):
        """Parse a .txt file using document parser."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_document_file

        with tempfile.TemporaryDirectory() as tmp:
            filepath = os.path.join(tmp, "test.txt")
            with open(filepath, "w") as f:
                f.write("This is a plain text file.\n\nWith paragraphs.")

            turns = parse_document_file(filepath)
            assert len(turns) >= 1
            assert turns[0].source_model == "document"

    def test_parse_file_not_found(self):
        """FileNotFoundError for non-existent files."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_document_file

        with pytest.raises(FileNotFoundError):
            parse_document_file("/nonexistent/file.txt")

    def test_pdf_graceful_skip(self):
        """PDF files are gracefully skipped when no PDF library is available."""
        from aip.orchestration.ingestion.parsers.document_parser import parse_document_file

        with tempfile.TemporaryDirectory() as tmp:
            filepath = os.path.join(tmp, "test.pdf")
            # Write a minimal fake PDF (won't parse but tests the graceful handling)
            with open(filepath, "wb") as f:
                f.write(b"%PDF-1.4 fake")

            turns = parse_document_file(filepath)
            # Should not crash — either returns turns or empty list
            assert isinstance(turns, list)


# ---------------------------------------------------------------------------
# CorpusTurnStore Sprint 9 Methods
# ---------------------------------------------------------------------------


class TestCorpusTurnStoreSprint9:
    """Tests for new CorpusTurnStore methods."""

    @pytest.fixture
    async def store(self, tmp_path):
        """Create a fresh CorpusTurnStore for testing."""
        db_path = str(tmp_path / "test.db")
        s = CorpusTurnStore(db_path=db_path)
        await s.initialize()
        yield s
        await s.close()

    async def test_write_turn_with_content_hash(self, store):
        """Write a turn with content_hash and retrieve it."""
        turn = _make_turn(turn_id="hash_test_1", user_text="Hello", assistant_text="World")
        assert turn.content_hash != ""

        await store.write_turn(turn)
        retrieved = await store.get_turn("hash_test_1")
        assert retrieved is not None
        assert retrieved.content_hash == turn.content_hash

    async def test_write_turn_with_source_path(self, store):
        """Write a turn with source_path and retrieve it."""
        turn = _make_turn(turn_id="path_test_1", source_path="docs/ARCHITECTURE.md")
        await store.write_turn(turn)
        retrieved = await store.get_turn("path_test_1")
        assert retrieved is not None
        assert retrieved.source_path == "docs/ARCHITECTURE.md"

    async def test_write_turn_with_doc_version(self, store):
        """Write a turn with doc_version and retrieve it."""
        turn = _make_turn(turn_id="ver_test_1", doc_version=2)
        await store.write_turn(turn)
        retrieved = await store.get_turn("ver_test_1")
        assert retrieved is not None
        assert retrieved.doc_version == 2

    async def test_check_content_hash(self, store):
        """Find a turn by content_hash."""
        turn = _make_turn(turn_id="hash_find_1", user_text="Findable content", assistant_text="Response")
        await store.write_turn(turn)

        found = await store.check_content_hash(turn.content_hash)
        assert found is not None
        assert found.turn_id == "hash_find_1"

    async def test_check_content_hash_not_found(self, store):
        """check_content_hash returns None for unknown hash."""
        found = await store.check_content_hash("nonexistent_hash_1234")
        assert found is None

    async def test_find_by_source_path(self, store):
        """Find turns by source_path."""
        for i in range(3):
            turn = _make_turn(
                turn_id=f"path_find_{i}",
                turn_index=i,
                source_path="docs/ARCHITECTURE.md",
            )
            await store.write_turn(turn)

        turns = await store.find_by_source_path("docs/ARCHITECTURE.md")
        assert len(turns) == 3
        assert all(t.source_path == "docs/ARCHITECTURE.md" for t in turns)

    async def test_find_by_source_path_empty(self, store):
        """find_by_source_path returns empty list for unknown path."""
        turns = await store.find_by_source_path("nonexistent/path.md")
        assert turns == []

    async def test_increment_doc_version(self, store):
        """increment_doc_version bumps version for all turns in a conversation."""
        conv_id = "conv:version_test"
        for i in range(3):
            turn = _make_turn(
                turn_id=f"ver_{i}",
                conversation_id=conv_id,
                turn_index=i,
            )
            await store.write_turn(turn)

        max_ver = await store.increment_doc_version(conv_id)
        assert max_ver == 1

        # Verify all turns now have doc_version=1
        for i in range(3):
            t = await store.get_turn(f"ver_{i}")
            assert t.doc_version == 1

    async def test_record_embed_failure(self, store):
        """record_embed_failure increments fail count and stores error."""
        turn = _make_turn(turn_id="fail_test_1")
        await store.write_turn(turn)

        await store.record_embed_failure("fail_test_1", "Connection timeout")
        retrieved = await store.get_turn("fail_test_1")
        assert retrieved.embed_fail_count == 1
        assert "Connection timeout" in retrieved.last_embed_error

        # Second failure increments
        await store.record_embed_failure("fail_test_1", "Second failure")
        retrieved = await store.get_turn("fail_test_1")
        assert retrieved.embed_fail_count == 2

    async def test_clear_embed_failure(self, store):
        """clear_embed_failure resets failure state."""
        turn = _make_turn(turn_id="clear_fail_1")
        await store.write_turn(turn)

        await store.record_embed_failure("clear_fail_1", "Error")
        await store.clear_embed_failure("clear_fail_1")

        retrieved = await store.get_turn("clear_fail_1")
        assert retrieved.embed_fail_count == 0
        assert retrieved.last_embed_error == ""

    async def test_get_backfill_queue(self, store):
        """Backfill queue returns unembedded turns, failures first."""
        # Create turns: one with failure, one normal unembedded
        turn_fail = _make_turn(turn_id="bf_fail_1", user_text="Failed turn", assistant_text="Content")
        turn_normal = _make_turn(turn_id="bf_normal_1", user_text="Normal turn", assistant_text="Content")
        turn_embedded = _make_turn(turn_id="bf_embedded_1", embedded=1, user_text="Embedded", assistant_text="Content")

        await store.write_turn(turn_fail)
        await store.write_turn(turn_normal)
        await store.write_turn(turn_embedded)

        # Record failure on first turn
        await store.record_embed_failure("bf_fail_1", "Test error")

        queue = await store.get_backfill_queue(limit=10)
        # Failed turn should come first (higher priority)
        assert len(queue) >= 2
        assert queue[0].turn_id == "bf_fail_1"
        assert queue[0].embed_fail_count > 0

    async def test_count_embed_failures(self, store):
        """count_embed_failures returns correct count."""
        turn1 = _make_turn(turn_id="cnt_fail_1")
        turn2 = _make_turn(turn_id="cnt_fail_2")
        await store.write_turn(turn1)
        await store.write_turn(turn2)

        await store.record_embed_failure("cnt_fail_1", "Error 1")
        await store.record_embed_failure("cnt_fail_2", "Error 2")

        count = await store.count_embed_failures()
        assert count == 2

    async def test_get_corpus_audit(self, store):
        """get_corpus_audit returns comprehensive audit dict."""
        turn1 = _make_turn(turn_id="audit_1", source_model="claude", user_text="Test", assistant_text="Content")
        turn2 = _make_turn(turn_id="audit_2", source_model="document", source_path="docs/test.md")
        await store.write_turn(turn1)
        await store.write_turn(turn2)

        audit = await store.get_corpus_audit()
        assert audit["total_turns"] == 2
        assert "by_source_model" in audit
        assert "by_domain" in audit
        assert "issues" in audit
        assert "healthy" in audit
        assert isinstance(audit["issues"], list)

    async def test_get_corpus_status(self, store):
        """get_corpus_status returns quick status dict."""
        turn1 = _make_turn(turn_id="status_1")
        await store.write_turn(turn1)

        status = await store.get_corpus_status()
        assert status["total_turns"] == 1
        assert "embed_coverage" in status
        assert "tag_coverage" in status
        assert "conversations" in status
        assert "documents" in status

    async def test_embed_fail_count_and_error_roundtrip(self, store):
        """embed_fail_count and last_embed_error survive write/read cycle."""
        turn = _make_turn(turn_id="roundtrip_1", embed_fail_count=3, last_embed_error="Test error message")
        await store.write_turn(turn)
        retrieved = await store.get_turn("roundtrip_1")
        assert retrieved.embed_fail_count == 3
        assert retrieved.last_embed_error == "Test error message"


# ---------------------------------------------------------------------------
# Unified Corpus Ingest Pipeline Tests
# ---------------------------------------------------------------------------


class TestCorpusIngestPipeline:
    """Tests for the unified corpus ingest pipeline."""

    @pytest.fixture
    async def store(self, tmp_path):
        """Create a fresh CorpusTurnStore for testing."""
        db_path = str(tmp_path / "test.db")
        s = CorpusTurnStore(db_path=db_path)
        await s.initialize()
        yield s
        await s.close()

    async def test_ingest_markdown_document(self, store, tmp_path):
        """Ingest a markdown file through the unified pipeline."""
        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_file_to_corpus,
        )

        filepath = str(tmp_path / "test.md")
        with open(filepath, "w") as f:
            f.write("# Architecture\n\nThree-layer design.\n\n# Config\n\nTOML files.")

        config = CorpusIngestConfig(db_path=str(tmp_path / "test.db"))
        result = await ingest_file_to_corpus(filepath, store, config)

        assert result.source_type == "document"
        assert result.turns_ingested >= 2
        assert result.turns_failed == 0

    async def test_ingest_text_document(self, store, tmp_path):
        """Ingest a plain text file through the unified pipeline."""
        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_file_to_corpus,
        )

        filepath = str(tmp_path / "test.txt")
        with open(filepath, "w") as f:
            f.write("This is a text document.\n\nWith two paragraphs.")

        config = CorpusIngestConfig(db_path=str(tmp_path / "test.db"))
        result = await ingest_file_to_corpus(filepath, store, config)

        assert result.source_type == "document"
        assert result.turns_ingested >= 1

    async def test_ingest_dedup_skips_unchanged(self, store, tmp_path):
        """Re-ingesting the same file skips unchanged turns."""
        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_file_to_corpus,
        )

        filepath = str(tmp_path / "dedup_test.md")
        with open(filepath, "w") as f:
            f.write("# Test\n\nContent that doesn't change.")

        config = CorpusIngestConfig(db_path=str(tmp_path / "test.db"))

        # First ingest
        result1 = await ingest_file_to_corpus(filepath, store, config)
        assert result1.turns_ingested >= 1

        # Second ingest (same content)
        result2 = await ingest_file_to_corpus(filepath, store, config)
        assert result2.turns_skipped >= 1
        assert result2.turns_ingested == 0

    async def test_ingest_detects_content_change(self, store, tmp_path):
        """Re-ingesting with changed content updates the turn."""
        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_file_to_corpus,
        )

        filepath = str(tmp_path / "change_test.md")

        # First ingest
        with open(filepath, "w") as f:
            f.write("# Test\n\nOriginal content.")
        config = CorpusIngestConfig(db_path=str(tmp_path / "test.db"))
        result1 = await ingest_file_to_corpus(filepath, store, config)
        assert result1.turns_ingested >= 1

        # Modify and re-ingest
        with open(filepath, "w") as f:
            f.write("# Test\n\nUpdated content here.")
        result2 = await ingest_file_to_corpus(filepath, store, config)
        assert result2.turns_updated >= 1

        # Verify doc_version was incremented
        turns = await store.find_by_source_path(filepath)
        assert any(t.doc_version > 0 for t in turns)

    async def test_ingest_file_not_found(self, store, tmp_path):
        """ingest_file_to_corpus handles missing files gracefully."""
        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_file_to_corpus,
        )

        config = CorpusIngestConfig(db_path=str(tmp_path / "test.db"))
        result = await ingest_file_to_corpus("/nonexistent/file.md", store, config)
        assert len(result.errors) > 0

    async def test_ingest_directory(self, store, tmp_path):
        """ingest_directory_to_corpus scans and ingests multiple files."""
        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_directory_to_corpus,
        )

        # Create directory with files
        docs_dir = str(tmp_path / "docs")
        os.makedirs(docs_dir)
        with open(os.path.join(docs_dir, "arch.md"), "w") as f:
            f.write("# Architecture\n\nDesign info.")
        with open(os.path.join(docs_dir, "config.txt"), "w") as f:
            f.write("Configuration documentation.")

        config = CorpusIngestConfig(db_path=str(tmp_path / "test.db"))
        results = await ingest_directory_to_corpus(docs_dir, store, config)

        total_ingested = sum(r.turns_ingested for r in results)
        assert total_ingested >= 2  # At least one turn per file

    async def test_ingest_provenance_tracked(self, store, tmp_path):
        """Every turn from ingestion has provenance metadata."""
        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_file_to_corpus,
        )

        filepath = str(tmp_path / "provenance_test.md")
        with open(filepath, "w") as f:
            f.write("# Test\n\nContent with provenance.")

        config = CorpusIngestConfig(db_path=str(tmp_path / "test.db"))
        result = await ingest_file_to_corpus(filepath, store, config)
        assert result.turns_ingested >= 1

        # Check that turns have provenance
        turns = await store.find_by_source_path(filepath)
        assert len(turns) > 0
        for turn in turns:
            assert turn.source_path == filepath
            assert turn.content_hash != ""
            meta = json.loads(turn.metadata_json)
            assert "ingest_timestamp" in meta


# ---------------------------------------------------------------------------
# CLI Command Tests
# ---------------------------------------------------------------------------


class TestCorpusCLICommands:
    """Tests for Sprint 9 CLI commands."""

    def test_corpus_status_command_exists(self):
        """aip corpus status command is registered."""
        from click.testing import CliRunner
        from aip.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["corpus", "status", "--help"])
        assert result.exit_code == 0
        assert "corpus status" in result.output.lower() or "status" in result.output.lower()

    def test_corpus_audit_command_exists(self):
        """aip corpus audit command is registered."""
        from click.testing import CliRunner
        from aip.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["corpus", "audit", "--help"])
        assert result.exit_code == 0

    def test_corpus_backfill_command_exists(self):
        """aip corpus backfill command is registered."""
        from click.testing import CliRunner
        from aip.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["corpus", "backfill", "--help"])
        assert result.exit_code == 0

    def test_corpus_list_command_exists(self):
        """aip corpus list command is registered."""
        from click.testing import CliRunner
        from aip.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["corpus", "list", "--help"])
        assert result.exit_code == 0
        assert "--unembedded" in result.output

    def test_corpus_ingest_accepts_document_model(self):
        """aip corpus ingest accepts --source-model document."""
        from click.testing import CliRunner
        from aip.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["corpus", "ingest", "--help"])
        assert result.exit_code == 0
        assert "document" in result.output

    def test_corpus_ingest_supports_recursive(self):
        """aip corpus ingest supports --recursive flag."""
        from click.testing import CliRunner
        from aip.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["corpus", "ingest", "--help"])
        assert result.exit_code == 0
        assert "recursive" in result.output.lower()

    def test_corpus_list_failed_flag(self):
        """aip corpus list supports --failed flag."""
        from click.testing import CliRunner
        from aip.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["corpus", "list", "--help"])
        assert "--failed" in result.output


# ---------------------------------------------------------------------------
# Integration: End-to-end Ingest → Audit → Verify
# ---------------------------------------------------------------------------


class TestCorpusReliabilityIntegration:
    """End-to-end tests for corpus reliability (the gate)."""

    @pytest.fixture
    async def store(self, tmp_path):
        """Create a fresh CorpusTurnStore for testing."""
        db_path = str(tmp_path / "test.db")
        s = CorpusTurnStore(db_path=db_path)
        await s.initialize()
        yield s
        await s.close()

    async def test_ingest_then_audit(self, store, tmp_path):
        """Ingest documents then audit finds no critical issues."""
        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_file_to_corpus,
        )

        filepath = str(tmp_path / "audit_test.md")
        with open(filepath, "w") as f:
            f.write("# Architecture\n\nThree-layer design.\n\n# Config\n\nTOML files.")

        config = CorpusIngestConfig(db_path=str(tmp_path / "test.db"))
        await ingest_file_to_corpus(filepath, store, config)

        # Run audit
        audit = await store.get_corpus_audit()
        assert audit["total_turns"] >= 2
        # All turns should have content_hash
        assert audit["missing_content_hash"] == 0

    async def test_ingest_provenance_chain(self, store, tmp_path):
        """Every turn in the corpus has full provenance chain."""
        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_file_to_corpus,
        )

        filepath = str(tmp_path / "provenance_chain.md")
        with open(filepath, "w") as f:
            f.write("# Section One\n\nContent one.\n\n# Section Two\n\nContent two.")

        config = CorpusIngestConfig(db_path=str(tmp_path / "test.db"))
        result = await ingest_file_to_corpus(filepath, store, config)
        assert result.turns_ingested >= 2

        # Verify each turn has provenance
        turns = await store.find_by_source_path(filepath)
        for turn in turns:
            # Source provenance
            assert turn.source_model == "document"
            assert turn.source_path == filepath
            assert turn.content_hash != ""

            # Metadata provenance
            meta = json.loads(turn.metadata_json)
            assert "section_heading" in meta
            assert "ingest_timestamp" in meta

    async def test_embed_failure_then_backfill(self, store, tmp_path):
        """Embed failure is tracked, then cleared on successful backfill."""
        turn = _make_turn(turn_id="backfill_test_1", user_text="Test", assistant_text="Content")
        await store.write_turn(turn)

        # Simulate embedding failure
        await store.record_embed_failure("backfill_test_1", "Provider unavailable")

        # Verify failure is tracked
        count = await store.count_embed_failures()
        assert count == 1

        # Verify failure appears in backfill queue
        queue = await store.get_backfill_queue(limit=10)
        assert any(t.turn_id == "backfill_test_1" for t in queue)

        # Simulate successful embed
        await store.mark_embedded("backfill_test_1", embedding_model="test-model")
        await store.clear_embed_failure("backfill_test_1")

        # Verify failure is cleared
        retrieved = await store.get_turn("backfill_test_1")
        assert retrieved.embed_fail_count == 0
        assert retrieved.embedded == 1

    async def test_reingest_version_tracking(self, store, tmp_path):
        """Re-ingesting changed content tracks versions correctly."""
        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_file_to_corpus,
        )

        filepath = str(tmp_path / "version_test.md")

        # Version 1
        with open(filepath, "w") as f:
            f.write("# Section\n\nOriginal content.")
        config = CorpusIngestConfig(db_path=str(tmp_path / "test.db"))
        await ingest_file_to_corpus(filepath, store, config)

        # Version 2 (content changed)
        with open(filepath, "w") as f:
            f.write("# Section\n\nUpdated content.")
        await ingest_file_to_corpus(filepath, store, config)

        # Version 3 (content changed again)
        with open(filepath, "w") as f:
            f.write("# Section\n\nThird version of content.")
        await ingest_file_to_corpus(filepath, store, config)

        turns = await store.find_by_source_path(filepath)
        # At least one turn should have doc_version > 0
        assert any(t.doc_version > 0 for t in turns)

    async def test_corpus_status_covers_all_metrics(self, store):
        """get_corpus_status returns all expected metrics."""
        turn = _make_turn(turn_id="metrics_test_1", source_path="docs/test.md")
        await store.write_turn(turn)

        status = await store.get_corpus_status()
        expected_keys = [
            "total_turns", "embedded", "tagged", "embed_failures",
            "needs_reembed", "documents", "conversations",
            "embed_coverage", "tag_coverage",
        ]
        for key in expected_keys:
            assert key in status, f"Missing key: {key}"

    async def test_no_silent_unembedded(self, store):
        """No document remains silently unembedded — failure tracking works."""
        turn = _make_turn(turn_id="silent_test_1")
        await store.write_turn(turn)

        # Turn should appear as unembedded
        unembedded = await store.count_unembedded()
        assert unembedded >= 1

        # Turn should appear in backfill queue
        queue = await store.get_backfill_queue(limit=100)
        assert any(t.turn_id == "silent_test_1" for t in queue)
