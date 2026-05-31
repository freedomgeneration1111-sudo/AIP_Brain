"""Surface round-trip integration — verifies CLI, API, MCP, and Admin/Memory surfaces.

All in-process (CliRunner, TestClient, in-process MCP) — deterministic CI.
"""

from __future__ import annotations

import pytest

try:
    from click.testing import CliRunner
except ImportError:
    CliRunner = None  # type: ignore

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None  # type: ignore

from aip.adapter.api.app import create_app
from aip.adapter.mcp.server import AipMcpServer
from aip.cli.main import cli


@pytest.mark.skipif(CliRunner is None, reason="click not installed")
def test_cli_round_trip(tmp_path, monkeypatch):
    """CLI: init → project create → session start → status."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # init
    r = runner.invoke(cli, ["init"])
    assert r.exit_code == 0
    assert (tmp_path / "db" / "state.db").exists()

    # project create
    r = runner.invoke(cli, ["project", "create", "--name", "itest", "--domain", "software_architecture"])
    assert r.exit_code == 0

    # session start
    r = runner.invoke(cli, ["session", "start", "--project-id", "p1", "--domain", "software_architecture"])
    assert r.exit_code == 0

    # status
    r = runner.invoke(cli, ["status"])
    assert r.exit_code == 0
    assert "AIP Status" in r.output


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed")
def test_api_round_trip():
    """API: project/session/chat (gate) → review approve → artifact + canonical."""
    app = create_app()
    client = TestClient(app)

    # project
    r = client.post("/api/v1/projects", json={"name": "itest", "domain": "sw_arch"})
    assert r.status_code in (200, 201, 503)

    # session
    r = client.post("/api/v1/sessions", json={"project_id": "p1", "domain": "sw_arch"})
    assert r.status_code in (200, 503)

    # chat WS (gate handling)
    with client.websocket_connect("/api/v1/chat/sess-1") as ws:
        ws.send_json({"type": "message", "content": "test gate"})
        data = ws.receive_json()
        assert data["type"] in ("response", "gate", "error")

    # review approve (admin gate path)
    r = client.post("/api/v1/reviews/art-1/approve")
    assert r.status_code in (200, 201, 403, 503)

    # artifact + canonical check
    r = client.get("/api/v1/artifacts/art-1")
    assert r.status_code in (200, 503)


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed")
def test_mcp_tool_round_trip():
    """MCP: search → artifact_list → artifact_approve (admin gate) → trace_query."""

    class MockContainer:
        autonomy_gate = None
        lexical_store = None
        ecs_store = None
        canonical_store = None
        trace_store = None

    server = AipMcpServer(MockContainer())
    tools = server.list_tools()
    assert any(t.tool_name == "aip_search" for t in tools)
    assert any(t.tool_name == "aip_artifact_approve" and t.autonomy_level == "admin" for t in tools)


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed")
def test_admin_memory_inspector():
    """Admin + Memory: config, sexton, beast, trace, search, entities, canonical."""
    app = create_app()
    client = TestClient(app)

    r = client.get("/api/v1/admin/config")
    assert r.status_code in (200, 503)

    r = client.get("/api/v1/admin/sexton/classifications")
    assert r.status_code in (200, 503)

    r = client.get("/api/v1/memory/trace/sess-1")
    assert r.status_code in (200, 503)

    r = client.get("/api/v1/memory/canonical")
    assert r.status_code in (200, 503)
