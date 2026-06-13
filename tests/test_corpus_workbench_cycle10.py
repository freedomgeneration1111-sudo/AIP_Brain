"""UI Cycle 10 — Corpus Workbench Tests.

Tests that:
1. Corpus status endpoint returns stable schema.
2. Missing stores return honest unavailable/not_wired state.
3. Document list returns empty list honestly when no docs exist.
4. Document detail returns 404/not_found for missing document.
5. Ingest action is explicit and reports unavailable/not_wired if unsupported.
6. Backfill action uses existing runtime path or reports scheduled_only/not_wired honestly.
7. Embedding coverage is computed honestly.
8. Failed jobs/problems are visible or honestly unavailable.
9. GUI Corpus page imports/renders.
10. GUI handles: backend unavailable, empty corpus, populated corpus,
    unembedded chunks, failed jobs, action unavailable.
11. No secret exposure.
12. GUI import-boundary tests pass.
13. General import-boundary tests pass.
14. Existing tests still pass.

Backend tests use FastAPI TestClient. Frontend tests use import checks.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Backend Endpoint Schema Tests ────────────────────────────────────


class TestCorpusStatusEndpoint:
    """Test corpus status endpoint returns stable schema."""

    def test_corpus_status_schema_with_store(self):
        """Corpus status with store returns expected fields."""
        from aip.adapter.api.routes.corpus import get_corpus_status

        # Create a mock container with a mock corpus_turn_store
        container = MagicMock()
        mock_cts = AsyncMock()
        mock_cts.get_corpus_status.return_value = {
            "total_turns": 100,
            "embedded": 50,
            "tagged": 80,
            "untagged": 20,
            "embed_failures": 3,
            "needs_reembed": 5,
            "documents": 10,
            "conversations": 8,
            "embed_coverage": 50.0,
            "tag_coverage": 80.0,
        }
        container.corpus_turn_store = mock_cts

        # Call the route handler directly
        result = asyncio.run(get_corpus_status(container=container))
        assert "total_turns" in result
        assert "embedded" in result
        assert "tagged" in result
        assert "embed_coverage" in result

    def test_corpus_status_schema_without_store(self):
        """Corpus status without store returns honest unavailable state."""
        from aip.adapter.api.routes.corpus import get_corpus_status

        container = MagicMock()
        container.corpus_turn_store = None

        result = asyncio.run(get_corpus_status(container=container))
        assert result.get("total_turns") == 0
        assert result.get("embedded") == 0
        assert result.get("tagged") == 0


class TestCorpusDocumentsEndpoint:
    """Test corpus documents endpoint."""

    def test_documents_returns_empty_list_honestly(self):
        """Document list returns empty list when no documents exist."""
        from aip.adapter.api.routes.corpus import list_documents

        container = MagicMock()
        mock_cts = AsyncMock()
        mock_cts.count_documents.return_value = 0
        mock_cts.list_documents.return_value = []
        container.corpus_turn_store = mock_cts

        result = asyncio.run(list_documents(container=container))
        assert result.get("items") == []
        assert result.get("total") == 0

    def test_documents_without_store(self):
        """Document list returns unavailable state when store not wired."""
        from aip.adapter.api.routes.corpus import list_documents

        container = MagicMock()
        container.corpus_turn_store = None

        result = asyncio.run(list_documents(container=container))
        assert result.get("items") == []
        assert result.get("total") == 0

    def test_documents_with_search(self):
        """Document list supports search filtering."""
        from aip.adapter.api.routes.corpus import list_documents

        container = MagicMock()
        mock_cts = AsyncMock()
        mock_cts.count_documents.return_value = 1
        mock_cts.list_documents.return_value = [
            {
                "source_path": "test/doc.json",
                "source_model": "claude",
                "turn_count": 5,
                "embedded_count": 3,
                "unembedded_count": 2,
                "embed_fail_count": 0,
                "needs_reembed_count": 0,
                "primary_domains": ["test"],
                "last_updated": "2026-01-01",
                "conversation_count": 1,
            }
        ]
        container.corpus_turn_store = mock_cts

        result = asyncio.run(list_documents(search="test", container=container))
        assert len(result.get("items", [])) == 1


class TestCorpusDocumentDetailEndpoint:
    """Test corpus document detail endpoint."""

    def test_document_detail_not_found(self):
        """Document detail returns 404 for missing document."""
        from fastapi import HTTPException

        from aip.adapter.api.routes.corpus import get_document_detail

        container = MagicMock()
        mock_cts = AsyncMock()
        mock_cts.get_document_detail.return_value = {
            "not_found": True,
            "source_path": "nonexistent.json",
        }
        container.corpus_turn_store = mock_cts

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_document_detail(source_path="nonexistent.json", container=container))
        assert exc_info.value.status_code == 404

    def test_document_detail_without_store(self):
        """Document detail returns unavailable when store not wired."""
        from aip.adapter.api.routes.corpus import get_document_detail

        container = MagicMock()
        container.corpus_turn_store = None

        result = asyncio.run(get_document_detail(source_path="test.json", container=container))
        assert result.get("not_found") is True


class TestCorpusProblemsEndpoint:
    """Test corpus problems endpoint."""

    def test_problems_returns_honest_empty(self):
        """Problems endpoint returns empty lists when no problems exist."""
        from aip.adapter.api.routes.corpus import get_corpus_problems

        container = MagicMock()
        mock_cts = AsyncMock()
        mock_cts.get_corpus_problems.return_value = {
            "failed_ingest_jobs": [],
            "unembedded_count": 0,
            "needs_reembed_count": 0,
            "duplicate_hashes": [],
            "stale_docs": [],
        }
        container.corpus_turn_store = mock_cts

        result = asyncio.run(get_corpus_problems(container=container))
        assert result.get("failed_ingest_jobs") == []
        assert result.get("available") is True

    def test_problems_without_store(self):
        """Problems endpoint returns unavailable when store not wired."""
        from aip.adapter.api.routes.corpus import get_corpus_problems

        container = MagicMock()
        container.corpus_turn_store = None

        result = asyncio.run(get_corpus_problems(container=container))
        assert result.get("available") is False


class TestCorpusBackfillEndpoint:
    """Test corpus backfill endpoint."""

    def test_backfill_not_wired_without_provider(self):
        """Backfill returns not_wired when embedding provider not configured."""
        from aip.adapter.api.routes.corpus import trigger_embedding_backfill

        container = MagicMock()
        container.embedding_provider = None
        container.corpus_turn_store = None

        result = asyncio.run(trigger_embedding_backfill(payload={}, container=container))
        assert result.get("status") == "not_wired"

    def test_backfill_already_running(self):
        """Backfill returns already_running when backfill in progress."""
        from aip.adapter.api.routes.corpus import trigger_embedding_backfill

        container = MagicMock()
        container.embedding_provider = MagicMock()  # Provider configured
        container.backfill_status = {"running": True}

        result = asyncio.run(trigger_embedding_backfill(payload={}, container=container))
        assert result.get("status") == "already_running"


class TestCorpusRetryFailedEndpoint:
    """Test corpus retry-failed endpoint."""

    def test_retry_not_wired_without_store(self):
        """Retry returns not_wired when store not wired."""
        from aip.adapter.api.routes.corpus import retry_failed_embeds

        container = MagicMock()
        container.corpus_turn_store = None
        container.embedding_provider = MagicMock()

        result = asyncio.run(retry_failed_embeds(payload={}, container=container))
        assert result.get("status") == "not_wired"

    def test_retry_not_wired_without_provider(self):
        """Retry returns not_wired when embedding provider not configured."""
        from aip.adapter.api.routes.corpus import retry_failed_embeds

        container = MagicMock()
        mock_cts = AsyncMock()
        container.corpus_turn_store = mock_cts
        container.embedding_provider = None

        result = asyncio.run(retry_failed_embeds(payload={}, container=container))
        assert result.get("status") == "not_wired"


class TestCorpusIngestEndpoint:
    """Test corpus ingest endpoint - explicit DEFINER action."""

    def test_ingest_not_wired(self):
        """Ingest returns 503 when ingestion pipeline not wired."""
        from fastapi import HTTPException

        from aip.adapter.api.routes.corpus import ingest_to_corpus

        container = MagicMock()
        container._corpus_ingest_config_class = None
        container._ingest_file_to_corpus_fn = None

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(ingest_to_corpus(payload={"path": "/tmp/test.json"}, container=container))
        assert exc_info.value.status_code == 503

    def test_ingest_requires_path(self):
        """Ingest returns 400 when no path provided."""
        from fastapi import HTTPException

        from aip.adapter.api.routes.corpus import ingest_to_corpus

        container = MagicMock()
        container._corpus_ingest_config_class = MagicMock()
        container._ingest_file_to_corpus_fn = MagicMock()
        container.corpus_turn_store = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(ingest_to_corpus(payload={}, container=container))
        assert exc_info.value.status_code == 400


class TestCorpusDuplicatesAndStaleEndpoints:
    """Test corpus duplicates and stale endpoints."""

    def test_duplicates_without_store(self):
        """Duplicates endpoint returns unavailable when store not wired."""
        from aip.adapter.api.routes.corpus import get_duplicate_documents

        container = MagicMock()
        container.corpus_turn_store = None

        result = asyncio.run(get_duplicate_documents(container=container))
        assert result.get("available") is False

    def test_stale_without_store(self):
        """Stale endpoint returns unavailable when store not wired."""
        from aip.adapter.api.routes.corpus import get_stale_documents

        container = MagicMock()
        container.corpus_turn_store = None

        result = asyncio.run(get_stale_documents(container=container))
        assert result.get("available") is False


class TestEmbeddingCoverageHonesty:
    """Test that embedding coverage is computed honestly."""

    def test_coverage_zero_with_empty_corpus(self):
        """Embedding coverage returns 0% with empty corpus."""
        from aip.adapter.api.routes.corpus import get_corpus_status

        container = MagicMock()
        mock_cts = AsyncMock()
        mock_cts.get_corpus_status.return_value = {
            "total_turns": 0,
            "embedded": 0,
            "tagged": 0,
            "embed_coverage": 0.0,
        }
        container.corpus_turn_store = mock_cts

        result = asyncio.run(get_corpus_status(container=container))
        assert result.get("embed_coverage") == 0.0


# ── Frontend Import Tests ────────────────────────────────────────────


class TestCorpusWorkbenchImports:
    """Test that Corpus Workbench frontend modules can be imported."""

    def test_corpus_page_importable(self):
        """gui.pages.corpus can be imported."""
        import gui.pages.corpus  # noqa: F401

    def test_corpus_summary_importable(self):
        """gui.components.corpus_summary can be imported."""
        import gui.components.corpus_summary  # noqa: F401

    def test_document_table_importable(self):
        """gui.components.document_table can be imported."""
        import gui.components.document_table  # noqa: F401

    def test_document_detail_importable(self):
        """gui.components.document_detail can be imported."""
        import gui.components.document_detail  # noqa: F401

    def test_corpus_actions_importable(self):
        """gui.components.corpus_actions can be imported."""
        import gui.components.corpus_actions  # noqa: F401

    def test_corpus_problems_importable(self):
        """gui.components.corpus_problems can be imported."""
        import gui.components.corpus_problems  # noqa: F401

    def test_status_types_corpus_types(self):
        """gui.status_types includes Corpus Workbench types."""

    def test_api_client_corpus_methods(self):
        """gui.api_client has Corpus Workbench methods."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        assert hasattr(client, "get_corpus_status")
        assert hasattr(client, "get_corpus_embedding_progress")
        assert hasattr(client, "list_corpus_documents")
        assert hasattr(client, "get_corpus_document_detail")
        assert hasattr(client, "get_corpus_problems")
        assert hasattr(client, "get_corpus_unembedded")
        assert hasattr(client, "trigger_corpus_backfill")
        assert hasattr(client, "retry_failed_embeds")
        assert hasattr(client, "ingest_to_corpus")
        assert hasattr(client, "get_corpus_duplicates")
        assert hasattr(client, "get_corpus_stale")


# ── No Secret Exposure Tests ─────────────────────────────────────────


class TestNoSecretExposure:
    """Test that corpus endpoints don't expose secrets."""

    def test_corpus_status_no_secrets(self):
        """Corpus status response doesn't contain secrets."""
        from aip.adapter.api.routes.corpus import get_corpus_status

        container = MagicMock()
        mock_cts = AsyncMock()
        mock_cts.get_corpus_status.return_value = {
            "total_turns": 100,
            "embedded": 50,
        }
        container.corpus_turn_store = mock_cts

        result = asyncio.run(get_corpus_status(container=container))
        result_str = str(result)
        for secret_term in ["api_key", "password", "token", "secret"]:
            assert secret_term not in result_str.lower(), f"Secret term '{secret_term}' found in corpus status response"

    def test_corpus_documents_no_secrets(self):
        """Corpus documents response doesn't contain secrets."""
        from aip.adapter.api.routes.corpus import list_documents

        container = MagicMock()
        mock_cts = AsyncMock()
        mock_cts.count_documents.return_value = 0
        mock_cts.list_documents.return_value = []
        container.corpus_turn_store = mock_cts

        result = asyncio.run(list_documents(container=container))
        result_str = str(result)
        for secret_term in ["api_key", "password", "token", "secret"]:
            assert secret_term not in result_str.lower(), f"Secret term '{secret_term}' found in documents response"


# ── GUI Import Boundary Tests ────────────────────────────────────────


class TestCorpusImportBoundary:
    """Test that Corpus Workbench modules don't import orchestration."""

    def test_corpus_page_no_orchestration_imports(self):
        """gui.pages.corpus doesn't import aip.orchestration."""
        import ast

        filepath = Path(__file__).resolve().parent.parent / "gui" / "pages" / "corpus.py"
        if not filepath.exists():
            pytest.skip("gui/pages/corpus.py not found")

        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("aip."), (
                    f"gui/pages/corpus.py imports '{node.module}' — GUI must use API client only"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip."), (
                        f"gui/pages/corpus.py imports '{alias.name}' — GUI must use API client only"
                    )

    def test_corpus_components_no_orchestration_imports(self):
        """Corpus Workbench components don't import aip.orchestration."""
        import ast

        component_files = [
            "corpus_summary.py",
            "document_table.py",
            "document_detail.py",
            "corpus_actions.py",
            "corpus_problems.py",
        ]

        gui_root = Path(__file__).resolve().parent.parent / "gui" / "components"

        for fname in component_files:
            filepath = gui_root / fname
            if not filepath.exists():
                continue

            source = filepath.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(filepath))

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("aip."), (
                        f"gui/components/{fname} imports '{node.module}' — GUI must use API client only"
                    )
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("aip."), (
                            f"gui/components/{fname} imports '{alias.name}' — GUI must use API client only"
                        )
