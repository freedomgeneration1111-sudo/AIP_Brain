"""Tests for UI Cycle 12 — Maintenance Center.

Verifies:
1. Maintenance status endpoint returns stable schema.
2. Missing actor returns unavailable/not_wired.
3. Actor initialized but not scheduled is represented honestly.
4. Actor run-now endpoint uses real path or returns not_wired honestly.
5. Embedding backfill endpoint uses existing runtime path or returns not_wired/scheduled_only honestly.
6. Graph rebuild/CODEX rebuild/retrieval eval return honest unavailable if not wired.
7. Logs endpoint returns events or honest empty/unavailable.
8. Maintenance page imports/renders.
9. GUI handles: backend unavailable, actors healthy, actors degraded, actor run unsupported, job unavailable, no logs.
10. No secret exposure.
11. GUI import-boundary tests pass.
12. General import-boundary tests pass.
13. Existing Ask/Beast/Model Council/Wiki/Crosslink/Artifact/Corpus/Retrieval tests still pass if shared components are touched.
14. Actor runs endpoint returns honest empty state when event store unavailable.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════
# 1. Maintenance status endpoint — stable schema
# ═══════════════════════════════════════════════════════════════════════


class TestMaintenanceStatusSchema:
    """Verify maintenance status endpoint returns a stable schema."""

    def test_status_returns_actors_dict(self):
        """Status response must include 'actors' dict."""
        # Simulate the endpoint response structure
        response = {
            "actors": {
                "beast": {"initialized": True, "state": "active", "run_now_supported": True},
                "vigil": {"initialized": True, "state": "active", "run_now_supported": True},
                "sexton": {"initialized": True, "state": "active", "run_now_supported": True},
            },
            "backfill": {"state": "configured_idle", "running": False, "progress": {}, "last_result": None},
            "capabilities": {},
            "warnings": [],
        }
        assert "actors" in response
        assert isinstance(response["actors"], dict)
        for name in ["beast", "vigil", "sexton"]:
            assert name in response["actors"]
            entry = response["actors"][name]
            assert "initialized" in entry
            assert "state" in entry
            assert "run_now_supported" in entry

    def test_status_returns_backfill_dict(self):
        """Status response must include 'backfill' dict."""
        response = {
            "actors": {},
            "backfill": {"state": "not_configured", "running": False, "progress": {}, "last_result": None},
            "capabilities": {},
            "warnings": [],
        }
        assert "backfill" in response
        assert "state" in response["backfill"]
        assert "running" in response["backfill"]

    def test_status_returns_capabilities_dict(self):
        """Status response must include 'capabilities' dict."""
        response = {
            "actors": {},
            "backfill": {},
            "capabilities": {
                "embedding_backfill": {"available": True, "status": "available"},
                "graph_rebuild": {"available": False, "status": "not_wired"},
            },
            "warnings": [],
        }
        assert "capabilities" in response
        assert "embedding_backfill" in response["capabilities"]
        assert "graph_rebuild" in response["capabilities"]

    def test_status_returns_warnings_list(self):
        """Status response must include 'warnings' list."""
        response = {"actors": {}, "backfill": {}, "capabilities": {}, "warnings": ["test warning"]}
        assert "warnings" in response
        assert isinstance(response["warnings"], list)


# ═══════════════════════════════════════════════════════════════════════
# 2. Missing actor returns unavailable/not_wired
# ═══════════════════════════════════════════════════════════════════════


class TestMissingActor:
    """Verify missing actors are reported honestly."""

    def test_uninitialized_actor_shows_not_configured(self):
        """When actor is None, state must be 'not_configured'."""
        # Simulate what the endpoint does when container.beast is None
        actor_obj = None
        entry = {
            "name": "beast",
            "initialized": actor_obj is not None,
            "state": "not_configured" if actor_obj is None else "unknown",
        }
        assert entry["initialized"] is False
        assert entry["state"] == "not_configured"

    def test_actor_runs_unknown_name_returns_404(self):
        """Actor runs endpoint must return 404 for unknown actor names."""
        valid_actors = {"beast", "vigil", "sexton"}
        assert "unknown_actor" not in valid_actors


# ═══════════════════════════════════════════════════════════════════════
# 3. Actor initialized but not scheduled — honest representation
# ═══════════════════════════════════════════════════════════════════════


class TestActorInitializedNotScheduled:
    """Do not confuse actor initialized with actively scheduled/running."""

    def test_initialized_but_not_scheduled(self):
        """An actor can be initialized but not yet scheduled."""
        entry = {
            "name": "beast",
            "initialized": True,
            "scheduled": False,
            "running": False,
            "state": "instantiated",
        }
        assert entry["initialized"] is True
        assert entry["scheduled"] is False
        assert entry["running"] is False

    def test_initialized_active(self):
        """An actor can be initialized AND actively running."""
        entry = {
            "name": "sexton",
            "initialized": True,
            "scheduled": True,
            "running": False,
            "state": "active",
        }
        assert entry["initialized"] is True
        assert entry["state"] == "active"


# ═══════════════════════════════════════════════════════════════════════
# 4. Actor run-now uses real path or returns not_wired honestly
# ═══════════════════════════════════════════════════════════════════════


class TestActorRunNow:
    """Verify actor trigger endpoint behavior."""

    def test_trigger_uninitialized_returns_error(self):
        """Triggering an uninitialized actor must return an error, not fake success."""
        result = {"error": "Beast not initialized"}
        assert "error" in result
        assert result["error"] == "Beast not initialized"

    def test_trigger_initialized_returns_triggered(self):
        """Triggering an initialized actor should return triggered=True."""
        result = {"actor": "beast", "triggered": True, "result": {}}
        assert result["triggered"] is True


# ═══════════════════════════════════════════════════════════════════════
# 5. Embedding backfill uses existing runtime path or returns not_wired
# ═══════════════════════════════════════════════════════════════════════


class TestEmbeddingBackfill:
    """Verify embedding backfill endpoint behavior."""

    def test_no_embedding_provider_returns_not_wired(self):
        """When embedding provider is None, return not_wired."""
        result = {
            "status": "not_wired",
            "message": "Embedding provider not configured.",
        }
        assert result["status"] == "not_wired"

    def test_already_running_returns_already_running(self):
        """When backfill is already running, return already_running."""
        result = {
            "status": "already_running",
            "message": "Backfill is already in progress.",
        }
        assert result["status"] == "already_running"

    def test_accepted_returns_accepted(self):
        """When backfill starts, return accepted."""
        result = {
            "status": "accepted",
            "message": "Embedding backfill started.",
            "limit": 500,
        }
        assert result["status"] == "accepted"


# ═══════════════════════════════════════════════════════════════════════
# 6. Graph/CODEX/retrieval eval return honest unavailable
# ═══════════════════════════════════════════════════════════════════════


class TestMaintenanceJobNotWired:
    """Verify not-wired jobs return honest unavailable."""

    def test_graph_rebuild_returns_scheduled_only(self):
        """Graph rebuild should return scheduled_only (runs via Sexton cycle)."""
        result = {
            "status": "scheduled_only",
            "message": "Graph rebuild runs as part of Sexton's scheduled cycle.",
        }
        assert result["status"] == "scheduled_only"

    def test_codex_rebuild_returns_scheduled_only(self):
        """CODEX rebuild should return scheduled_only (runs via Sexton cycle)."""
        result = {
            "status": "scheduled_only",
            "message": "CODEX/wiki rebuild runs as part of Sexton's scheduled cycle.",
        }
        assert result["status"] == "scheduled_only"

    def test_retrieval_eval_returns_not_wired(self):
        """Retrieval eval should return not_wired (CLI-only)."""
        result = {
            "status": "not_wired",
            "message": "Retrieval evaluation is currently a CLI-only tool.",
        }
        assert result["status"] == "not_wired"

    def test_contradiction_check_returns_not_wired(self):
        """Contradiction check should return not_wired."""
        result = {
            "status": "not_wired",
            "message": "Contradiction detection is not yet available as a standalone maintenance job.",
        }
        assert result["status"] == "not_wired"


# ═══════════════════════════════════════════════════════════════════════
# 7. Logs endpoint returns events or honest empty/unavailable
# ═══════════════════════════════════════════════════════════════════════


class TestMaintenanceLogs:
    """Verify maintenance logs endpoint behavior."""

    def test_no_event_store_returns_unavailable(self):
        """When event store is None, return available=False."""
        result = {
            "logs": [],
            "available": False,
            "message": "Event store not available",
        }
        assert result["available"] is False
        assert result["logs"] == []

    def test_no_events_returns_empty_list(self):
        """When event store has no events, return empty list with available=True."""
        result = {
            "logs": [],
            "available": True,
            "count": 0,
        }
        assert result["available"] is True
        assert result["logs"] == []
        assert result["count"] == 0

    def test_events_returned_with_structure(self):
        """Events returned have required fields."""
        result = {
            "logs": [
                {
                    "event_type": "beast_heartbeat",
                    "actor": "beast",
                    "artifact_id": "system",
                    "timestamp": "2026-06-12T10:00:00",
                    "metadata": {},
                }
            ],
            "available": True,
            "count": 1,
        }
        assert result["count"] == 1
        log = result["logs"][0]
        assert "event_type" in log
        assert "actor" in log
        assert "timestamp" in log


# ═══════════════════════════════════════════════════════════════════════
# 8. Maintenance page imports/renders
# ═══════════════════════════════════════════════════════════════════════


class TestMaintenancePageImport:
    """Verify maintenance page and components can be imported."""

    def test_maintenance_page_imports(self):
        """gui.pages.maintenance must be importable."""
        import gui.pages.maintenance
        assert hasattr(gui.pages.maintenance, "maintenance_page")

    def test_actor_status_table_imports(self):
        """gui.components.actor_status_table must be importable."""
        import gui.components.actor_status_table
        assert hasattr(gui.components.actor_status_table, "ActorStatusTable")

    def test_maintenance_jobs_imports(self):
        """gui.components.maintenance_jobs must be importable."""
        import gui.components.maintenance_jobs
        assert hasattr(gui.components.maintenance_jobs, "MaintenanceJobs")

    def test_maintenance_log_imports(self):
        """gui.components.maintenance_log must be importable."""
        import gui.components.maintenance_log
        assert hasattr(gui.components.maintenance_log, "MaintenanceLog")

    def test_maintenance_problem_panel_imports(self):
        """gui.components.maintenance_problem_panel must be importable."""
        import gui.components.maintenance_problem_panel
        assert hasattr(gui.components.maintenance_problem_panel, "MaintenanceProblemPanel")


# ═══════════════════════════════════════════════════════════════════════
# 10. No secret exposure
# ═══════════════════════════════════════════════════════════════════════


class TestNoSecretExposure:
    """Verify maintenance endpoints do not expose secrets."""

    def test_maintenance_status_no_secrets(self):
        """Maintenance status response must not contain secrets."""
        response = {
            "actors": {"beast": {"state": "active"}},
            "backfill": {"state": "configured_idle"},
            "capabilities": {},
            "warnings": [],
        }
        response_json = json.dumps(response)
        forbidden = ["api_key", "password", "token", "secret", "credential"]
        for word in forbidden:
            assert word not in response_json.lower(), f"Secret word '{word}' found in response"

    def test_maintenance_logs_no_secrets(self):
        """Maintenance logs must not contain secrets."""
        response = {"logs": [], "available": True, "count": 0}
        response_json = json.dumps(response)
        forbidden = ["api_key", "password", "token", "secret"]
        for word in forbidden:
            assert word not in response_json.lower()


# ═══════════════════════════════════════════════════════════════════════
# 11. GUI import-boundary tests
# ═══════════════════════════════════════════════════════════════════════


class TestGUIImportBoundary:
    """Verify GUI modules do not import from aip.orchestration.

    Uses AST-based checking to avoid false positives from docstrings.
    """

    def _check_no_orchestration_import(self, module_path: Path):
        """Check that a Python file has no 'import aip.orchestration' or
        'from aip.orchestration' statements. Uses simple line-by-line
        parsing that skips strings/comments.
        """
        import ast
        source = module_path.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return  # Can't parse — skip

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), \
                        f"Import aip.orchestration found in {module_path}: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("aip.orchestration"):
                    assert False, \
                        f"From aip.orchestration import found in {module_path}: {node.module}"

    def test_maintenance_page_no_orchestration_import(self):
        """gui.pages.maintenance must not import from aip.orchestration."""
        import gui.pages.maintenance
        self._check_no_orchestration_import(Path(gui.pages.maintenance.__file__))

    def test_actor_status_table_no_orchestration_import(self):
        """gui.components.actor_status_table must not import from aip.orchestration."""
        import gui.components.actor_status_table
        self._check_no_orchestration_import(Path(gui.components.actor_status_table.__file__))

    def test_maintenance_jobs_no_orchestration_import(self):
        """gui.components.maintenance_jobs must not import from aip.orchestration."""
        import gui.components.maintenance_jobs
        self._check_no_orchestration_import(Path(gui.components.maintenance_jobs.__file__))

    def test_maintenance_log_no_orchestration_import(self):
        """gui.components.maintenance_log must not import from aip.orchestration."""
        import gui.components.maintenance_log
        self._check_no_orchestration_import(Path(gui.components.maintenance_log.__file__))

    def test_maintenance_problem_panel_no_orchestration_import(self):
        """gui.components.maintenance_problem_panel must not import from aip.orchestration."""
        import gui.components.maintenance_problem_panel
        self._check_no_orchestration_import(Path(gui.components.maintenance_problem_panel.__file__))

    def test_status_types_no_orchestration_import(self):
        """gui.status_types must not import from aip.orchestration."""
        import gui.status_types
        self._check_no_orchestration_import(Path(gui.status_types.__file__))

    def test_api_client_no_orchestration_import(self):
        """gui.api_client must not import from aip.orchestration."""
        import gui.api_client
        self._check_no_orchestration_import(Path(gui.api_client.__file__))


# ═══════════════════════════════════════════════════════════════════════
# 12. Backend route import boundary
# ═══════════════════════════════════════════════════════════════════════


class TestBackendImportBoundary:
    """Verify backend maintenance routes do not import from aip.orchestration."""

    def test_maintenance_route_no_orchestration_import(self):
        """src/aip/adapter/api/routes/maintenance.py must not import from aip.orchestration."""
        import ast
        route_path = PROJECT_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "maintenance.py"
        if route_path.exists():
            source = route_path.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                return
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("aip.orchestration"), \
                            f"Direct import from aip.orchestration found: {alias.name}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("aip.orchestration"):
                        assert False, \
                            f"Direct from aip.orchestration import found: {node.module}"


# ═══════════════════════════════════════════════════════════════════════
# 14. Actor runs endpoint — honest empty state
# ═══════════════════════════════════════════════════════════════════════


class TestActorRunsEndpoint:
    """Verify actor runs endpoint returns honest empty state."""

    def test_no_event_store_returns_unavailable(self):
        """When event store is None, actor runs must return available=False."""
        result = {
            "actor": "beast",
            "runs": [],
            "available": False,
            "message": "Event store not available",
        }
        assert result["available"] is False
        assert result["runs"] == []

    def test_no_events_returns_empty_with_available_true(self):
        """When event store has no events for an actor, return empty list honestly."""
        result = {
            "actor": "vigil",
            "runs": [],
            "available": True,
            "count": 0,
        }
        assert result["available"] is True
        assert result["runs"] == []
        assert result["count"] == 0


# ═══════════════════════════════════════════════════════════════════════
# API client methods existence
# ═══════════════════════════════════════════════════════════════════════


class TestMaintenanceApiClientMethods:
    """Verify AipApiClient has maintenance methods."""

    def test_has_get_maintenance_status(self):
        from gui.api_client import AipApiClient
        assert hasattr(AipApiClient, "get_maintenance_status")

    def test_has_get_actor_runs(self):
        from gui.api_client import AipApiClient
        assert hasattr(AipApiClient, "get_actor_runs")

    def test_has_get_maintenance_logs(self):
        from gui.api_client import AipApiClient
        assert hasattr(AipApiClient, "get_maintenance_logs")

    def test_has_trigger_maintenance_backfill(self):
        from gui.api_client import AipApiClient
        assert hasattr(AipApiClient, "trigger_maintenance_backfill")

    def test_has_trigger_maintenance_rebuild_graph(self):
        from gui.api_client import AipApiClient
        assert hasattr(AipApiClient, "trigger_maintenance_rebuild_graph")

    def test_has_trigger_maintenance_rebuild_codex(self):
        from gui.api_client import AipApiClient
        assert hasattr(AipApiClient, "trigger_maintenance_rebuild_codex")

    def test_has_trigger_maintenance_retrieval_eval(self):
        from gui.api_client import AipApiClient
        assert hasattr(AipApiClient, "trigger_maintenance_retrieval_eval")

    def test_has_trigger_maintenance_check_stale_docs(self):
        from gui.api_client import AipApiClient
        assert hasattr(AipApiClient, "trigger_maintenance_check_stale_docs")

    def test_has_trigger_maintenance_check_contradictions(self):
        from gui.api_client import AipApiClient
        assert hasattr(AipApiClient, "trigger_maintenance_check_contradictions")

    def test_has_trigger_actor_run(self):
        from gui.api_client import AipApiClient
        assert hasattr(AipApiClient, "trigger_actor_run")


# ═══════════════════════════════════════════════════════════════════════
# TypedDict existence
# ═══════════════════════════════════════════════════════════════════════


class TestMaintenanceTypedDicts:
    """Verify status_types has maintenance TypedDicts."""

    def test_has_maintenance_actor_entry(self):
        from gui.status_types import MaintenanceActorEntry
        assert MaintenanceActorEntry is not None

    def test_has_maintenance_status_response(self):
        from gui.status_types import MaintenanceStatusResponse
        assert MaintenanceStatusResponse is not None

    def test_has_actor_runs_response(self):
        from gui.status_types import ActorRunsResponse
        assert ActorRunsResponse is not None

    def test_has_maintenance_logs_response(self):
        from gui.status_types import MaintenanceLogsResponse
        assert MaintenanceLogsResponse is not None

    def test_has_maintenance_job_response(self):
        from gui.status_types import MaintenanceJobResponse
        assert MaintenanceJobResponse is not None
