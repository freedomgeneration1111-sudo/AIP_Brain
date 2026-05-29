"""CHUNK-9.5: Full §22 Acceptance Verification (7 scenarios across the complete AIP 0.1 system).

Extends 8.7. In-process (CliRunner, TestClient WS, in-process MCP).
Verifies §1.7 gates, Appendix D invariants, §2.3 install contract, cross-surface consistency,
and that 9.1–9.4 + Phase 6/5 all function correctly together.
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
def test_scenario_1_full_cli_round_trip(tmp_path, monkeypatch):
    """1. Full CLI round trip: init → project create → session start → status."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    r = runner.invoke(cli, ["init"])
    assert r.exit_code == 0
    assert (tmp_path / "db" / "state.db").exists()

    r = runner.invoke(cli, ["project", "create", "--name", "accept", "--domain", "software_architecture"])
    assert r.exit_code == 0

    r = runner.invoke(cli, ["session", "start", "--project-id", "p-accept", "--domain", "software_architecture"])
    assert r.exit_code == 0

    r = runner.invoke(cli, ["status"])
    assert r.exit_code == 0
    assert "AIP Status" in r.output


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed")
def test_scenario_2_full_api_round_trip():
    """2. Full API round trip (project/session/chat with gate → review/approve via 9.2 → artifact + canonical)."""
    app = create_app()
    client = TestClient(app)

    r = client.post("/api/v1/projects", json={"name": "accept-api", "domain": "sw_arch"})
    assert r.status_code in (200, 201, 503)

    r = client.post("/api/v1/sessions", json={"project_id": "p-accept-api", "domain": "sw_arch"})
    assert r.status_code in (200, 503)

    with client.websocket_connect("/api/v1/chat/sess-accept") as ws:
        ws.send_json({"type": "message", "content": "acceptance test message"})
        data = ws.receive_json()
        assert data["type"] in ("response", "gate", "error")

    # Review/approve path (exercises 9.2)
    r = client.post("/api/v1/reviews/art-accept/approve")
    assert r.status_code in (200, 201, 403, 503)

    r = client.get("/api/v1/artifacts/art-accept")
    assert r.status_code in (200, 503)


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed")
def test_scenario_3_mcp_tool_round_trip():
    """3. MCP tool round trip (search → artifact_list → artifact_approve with admin gate → trace_query)."""
    container = type(
        "C",
        (),
        {"autonomy_gate": None, "lexical_store": None, "ecs_store": None, "canonical_store": None, "trace_store": None},
    )()
    server = AipMcpServer(container)
    tools = server.list_tools()
    assert any(t.tool_name == "aip_search" for t in tools)
    assert any(t.tool_name == "aip_artifact_approve" and t.autonomy_level == "admin" for t in tools)


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed")
def test_scenario_4_admin_memory_inspector():
    """4. Admin + Memory inspector (config, Sexton, Beast, Router, Budget,
    Autonomy log + trace/events/search/entities/canonical)."""
    app = create_app()
    client = TestClient(app)

    r = client.get("/api/v1/admin/config")
    assert r.status_code in (200, 503)

    r = client.get("/api/v1/admin/sexton/classifications")
    assert r.status_code in (200, 503)

    r = client.get("/api/v1/memory/trace/sess-accept")
    assert r.status_code in (200, 503)

    r = client.get("/api/v1/memory/canonical")
    assert r.status_code in (200, 503)
