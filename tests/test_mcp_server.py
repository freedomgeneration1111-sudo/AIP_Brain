"""MCP Server tests — real dispatch, structured responses, autonomy enforcement.

Tests verify:
- Tool listing with proper schemas and autonomy levels
- Autonomy gate enforcement for write/admin tools
- Real search dispatch (empty result is valid, BACKEND_UNAVAILABLE when no stores)
- Real artifact approval dispatch (NOT_FOUND, PROMOTION_BLOCKED, success)
- Config/admin/write tools (NOT_IMPLEMENTED for unsafe, BACKEND_UNAVAILABLE when no store)
- Unknown tool returns NOT_FOUND
- No fake success paths remain
"""

from __future__ import annotations

import pytest

from aip.adapter.mcp.server import AipMcpServer, _error, _ok

# ---- Helper: Mock container ----


class _MockEscalation:
    def __init__(self, granted: bool, reason: str = ""):
        self.granted = granted
        self.reason = reason


class _MockAutonomyGate:
    def __init__(self, granted: bool = True, reason: str = ""):
        self._granted = granted
        self._reason = reason

    async def escalate(self, **kwargs):
        return _MockEscalation(self._granted, self._reason)


class _MockSearchResult:
    def __init__(self, id: str, content: str, score: float):
        self.id = id
        self.content = content
        self.score = score


class _MockLexicalStore:
    def __init__(self, results: list | None = None):
        self._results = results or []

    async def search(self, query: str, domain=None):
        return self._results


class _MockVectorStore:
    def __init__(self, results: list | None = None):
        self._results = results or []

    async def retrieve(self, query_vector, domain=None):
        return self._results


class _MockEcsStore:
    def __init__(self, states: dict | None = None):
        self._states = states or {}

    async def current_state(self, artifact_id: str):
        return self._states.get(artifact_id)

    async def transition(self, artifact_id: str, from_state: str, to_state: str, actor: str = "", reason: str = ""):
        self._states[artifact_id] = to_state


class _MockCanonicalStore:
    def __init__(self):
        self.canonicals = {}

    async def write_canonical(self, artifact_id: str, content: dict, approved_by: str = ""):
        self.canonicals[artifact_id] = content


class _MockArtifactStore:
    def __init__(self, artifacts: dict | None = None):
        self._artifacts = artifacts or {}

    async def read(self, artifact_id: str):
        if artifact_id in self._artifacts:
            return self._artifacts[artifact_id]
        raise KeyError(f"Artifact not found: {artifact_id}")


class _MockProjectStore:
    def __init__(self, projects: list | None = None):
        self._projects = projects or []

    async def list_projects(self, limit=100):
        return self._projects[:limit]

    async def create_project(self, name: str, description: str = ""):
        return f"proj_{name}"


class _MockEventStore:
    def __init__(self, events: list | None = None):
        self._events = events or []

    async def query_events(self, artifact_id=None, event_type=None, limit=100):
        return self._events[:limit]


class _MockEmbeddingProvider:
    async def embed(self, text: str):
        return [0.1] * 384


def _make_container(**overrides):
    """Create a mock container with sensible defaults."""
    container = type("Container", (), {})()
    container.config = {"embedding": {"provider": "fake"}, "auth": {"auth_enabled": False}}
    container.lexical_store = None
    container.vector_store = None
    container.ecs_store = None
    container.canonical_store = None
    container.artifact_store = None
    container.autonomy_gate = None
    container.project_store = None
    container.event_store = None
    container.embedding_provider = None
    container.knowledge_store = None
    for k, v in overrides.items():
        setattr(container, k, v)
    return container


# ---- Tool listing tests ----


def test_mcp_server_lists_tools_with_autonomy_and_model_gen():
    server = AipMcpServer(container=_make_container())
    tools = server.list_tools()
    assert len(tools) >= 8
    approve = next(t for t in tools if t.tool_name == "aip_artifact_approve")
    assert approve.autonomy_level == "admin"
    assert approve.model_gen_assumption is not None
    assert "approve" in approve.model_gen_assumption.lower()


def test_mcp_tools_have_input_schemas():
    server = AipMcpServer(container=_make_container())
    tools = server.list_tools()
    for tool in tools:
        assert tool.input_schema != {}, f"Tool {tool.tool_name} has empty input_schema"
        assert "properties" in tool.input_schema, f"Tool {tool.tool_name} schema missing properties"


# ---- Structured response helpers ----


def test_ok_response_format():
    result = _ok({"items": [1, 2, 3]})
    assert result["ok"] is True
    assert result["result"]["items"] == [1, 2, 3]
    assert "error" not in result


def test_error_response_format():
    result = _error("NOT_FOUND", "Item missing", {"id": "abc"})
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"
    assert result["error"]["message"] == "Item missing"
    assert result["error"]["details"]["id"] == "abc"


# ---- Unknown tool ----


@pytest.mark.asyncio
async def test_unknown_tool_returns_not_found():
    server = AipMcpServer(container=_make_container())
    result = await server.call_tool("nonexistent_tool", {})
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"
    assert "nonexistent_tool" in result["error"]["message"]


# ---- aip_search dispatch ----


@pytest.mark.asyncio
async def test_search_no_backends_returns_backend_unavailable():
    server = AipMcpServer(container=_make_container(lexical_store=None, vector_store=None))
    result = await server.call_tool("aip_search", {"query": "test"})
    assert result["ok"] is False
    assert result["error"]["code"] == "BACKEND_UNAVAILABLE"


@pytest.mark.asyncio
async def test_search_empty_result_is_valid_success():
    """Real empty result should return ok=true with empty results, not an error."""
    container = _make_container(
        lexical_store=_MockLexicalStore(results=[]),
        vector_store=None,
    )
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_search", {"query": "nothing matches"})
    assert result["ok"] is True
    assert result["result"]["results"] == []
    assert result["result"]["count"] == 0


@pytest.mark.asyncio
async def test_search_with_results_returns_real_data():
    """Real search results should flow through."""
    mock_results = [_MockSearchResult("chunk1", "hello world", 0.95)]
    container = _make_container(
        lexical_store=_MockLexicalStore(results=mock_results),
        vector_store=None,
    )
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_search", {"query": "hello"})
    assert result["ok"] is True
    assert result["result"]["count"] == 1
    assert result["result"]["results"][0]["id"] == "chunk1"
    assert result["result"]["results"][0]["source"] == "lexical"


@pytest.mark.asyncio
async def test_search_missing_query_returns_validation_error():
    server = AipMcpServer(container=_make_container(lexical_store=_MockLexicalStore()))
    result = await server.call_tool("aip_search", {"query": ""})
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_search_vector_store_with_embedding_provider():
    """Vector store + embedding provider should produce vector results."""
    mock_results = [_MockSearchResult("vec1", "semantic content", 0.88)]
    container = _make_container(
        lexical_store=None,
        vector_store=_MockVectorStore(results=mock_results),
        embedding_provider=_MockEmbeddingProvider(),
    )
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_search", {"query": "semantic"})
    assert result["ok"] is True
    assert result["result"]["count"] == 1
    assert result["result"]["results"][0]["source"] == "vector"


# ---- aip_artifact_approve dispatch ----


@pytest.mark.asyncio
async def test_artifact_approve_missing_id_returns_validation_error():
    container = _make_container(
        ecs_store=_MockEcsStore(),
        canonical_store=_MockCanonicalStore(),
        autonomy_gate=_MockAutonomyGate(),  # grant gate so dispatch is reached
    )
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_artifact_approve", {"artifact_id": ""})
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_artifact_approve_no_ecs_store_returns_backend_unavailable():
    container = _make_container(ecs_store=None, canonical_store=_MockCanonicalStore(), autonomy_gate=_MockAutonomyGate())
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_artifact_approve", {"artifact_id": "art1"})
    assert result["ok"] is False
    assert result["error"]["code"] == "BACKEND_UNAVAILABLE"


@pytest.mark.asyncio
async def test_artifact_approve_artifact_not_found():
    container = _make_container(
        ecs_store=_MockEcsStore(states={}),  # no state for art_missing
        canonical_store=_MockCanonicalStore(),
        autonomy_gate=_MockAutonomyGate(),  # grant gate so dispatch is reached
    )
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_artifact_approve", {"artifact_id": "art_missing"})
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_artifact_approve_wrong_state_returns_promotion_blocked():
    """Artifact in GENERATED state cannot be approved."""
    container = _make_container(
        ecs_store=_MockEcsStore(states={"art1": "GENERATED"}),
        canonical_store=_MockCanonicalStore(),
        autonomy_gate=_MockAutonomyGate(),  # grant gate so dispatch is reached
    )
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_artifact_approve", {"artifact_id": "art1"})
    assert result["ok"] is False
    assert result["error"]["code"] == "PROMOTION_BLOCKED"
    assert "GENERATED" in result["error"]["message"]


@pytest.mark.asyncio
async def test_artifact_approve_reviewed_artifact_succeeds():
    """Artifact in REVIEWED state can be approved."""
    ecs = _MockEcsStore(states={"art1": "REVIEWED"})
    canonical = _MockCanonicalStore()
    container = _make_container(
        ecs_store=ecs,
        canonical_store=canonical,
        artifact_store=_MockArtifactStore(artifacts={"art1": "some content"}),
        autonomy_gate=_MockAutonomyGate(),  # grant gate so dispatch is reached
    )
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_artifact_approve", {"artifact_id": "art1"})
    assert result["ok"] is True
    assert result["result"]["approved"] is True
    assert result["result"]["artifact_id"] == "art1"
    # Verify ECS state changed to APPROVED
    assert ecs._states["art1"] == "APPROVED"
    # Verify canonical was written
    assert "art1" in canonical.canonicals


# ---- Autonomy gate enforcement ----


@pytest.mark.asyncio
async def test_admin_tool_blocked_by_autonomy_gate():
    """Admin tools must be blocked when gate denies escalation."""
    gate = _MockAutonomyGate(granted=False, reason="Not authorized for admin operations")
    container = _make_container(autonomy_gate=gate)
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_artifact_approve", {"artifact_id": "art1"})
    assert result["ok"] is False
    assert result["error"]["code"] == "FORBIDDEN"
    assert "Not authorized" in result["error"]["message"]


@pytest.mark.asyncio
async def test_write_tool_blocked_by_autonomy_gate():
    """Write tools must be blocked when gate denies escalation."""
    gate = _MockAutonomyGate(granted=False, reason="Write access denied")
    container = _make_container(autonomy_gate=gate)
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_config_write", {"section": "test", "values": {"key": "val"}})
    assert result["ok"] is False
    assert result["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_read_tool_not_blocked_by_autonomy_gate():
    """Read tools should not go through autonomy gate."""
    container = _make_container(lexical_store=_MockLexicalStore(results=[]))
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_search", {"query": "test"})
    # Should succeed (empty results is valid), not be blocked by gate
    assert result["ok"] is True


# ---- Config tools ----


@pytest.mark.asyncio
async def test_config_read_returns_config():
    container = _make_container()
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_config_read", {})
    assert result["ok"] is True
    assert "config" in result["result"]


@pytest.mark.asyncio
async def test_config_read_section_returns_section():
    container = _make_container(config={"embedding": {"provider": "ollama"}, "auth": {"enabled": True}})
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_config_read", {"section": "embedding"})
    assert result["ok"] is True
    assert result["result"]["section"] == "embedding"
    assert result["result"]["values"]["provider"] == "ollama"


@pytest.mark.asyncio
async def test_config_read_missing_section_returns_not_found():
    container = _make_container(config={})
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_config_read", {"section": "nonexistent"})
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_config_write_returns_not_implemented():
    """Config write through MCP is not implemented — must not fake success."""
    container = _make_container(autonomy_gate=_MockAutonomyGate())
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_config_write", {"section": "test", "values": {"key": "val"}})
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_config_write_blocked_by_gate_even_though_not_implemented():
    """Config write should be gate-checked BEFORE returning NOT_IMPLEMENTED."""
    gate = _MockAutonomyGate(granted=False, reason="Admin required")
    container = _make_container(autonomy_gate=gate)
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_config_write", {"section": "test", "values": {"key": "val"}})
    # Gate blocks first, so FORBIDDEN not NOT_IMPLEMENTED
    assert result["ok"] is False
    assert result["error"]["code"] == "FORBIDDEN"


# ---- Project tools ----


@pytest.mark.asyncio
async def test_project_list_no_store_returns_backend_unavailable():
    container = _make_container(project_store=None)
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_project_list", {})
    assert result["ok"] is False
    assert result["error"]["code"] == "BACKEND_UNAVAILABLE"


@pytest.mark.asyncio
async def test_project_list_with_store_returns_projects():
    container = _make_container(project_store=_MockProjectStore(projects=[{"id": "p1", "name": "test"}]))
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_project_list", {})
    assert result["ok"] is True
    assert result["result"]["count"] == 1


@pytest.mark.asyncio
async def test_project_create_no_store_returns_backend_unavailable():
    container = _make_container(project_store=None, autonomy_gate=_MockAutonomyGate())
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_project_create", {"name": "test"})
    assert result["ok"] is False
    assert result["error"]["code"] == "BACKEND_UNAVAILABLE"


@pytest.mark.asyncio
async def test_project_create_missing_name_returns_validation_error():
    container = _make_container(project_store=_MockProjectStore(), autonomy_gate=_MockAutonomyGate())
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_project_create", {"name": ""})
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


# ---- Trace query ----


@pytest.mark.asyncio
async def test_trace_query_no_store_returns_backend_unavailable():
    container = _make_container(event_store=None)
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_trace_query", {})
    assert result["ok"] is False
    assert result["error"]["code"] == "BACKEND_UNAVAILABLE"


@pytest.mark.asyncio
async def test_trace_query_with_store_returns_events():
    container = _make_container(event_store=_MockEventStore(events=[{"type": "transition", "id": "e1"}]))
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_trace_query", {})
    assert result["ok"] is True
    assert result["result"]["count"] == 1


# ---- Artifact list ----


@pytest.mark.asyncio
async def test_artifact_list_no_store_returns_backend_unavailable():
    container = _make_container(artifact_store=None)
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_artifact_list", {})
    assert result["ok"] is False
    assert result["error"]["code"] == "BACKEND_UNAVAILABLE"


# ---- Layering / Appendix D compliance ----


def test_mcp_uses_protocols_not_direct_storage():
    """Appendix D: MCP ≠ vector_store.retrieve() directly, MCP ≠ bypass."""
    from pathlib import Path

    server_file = Path(__file__).parent.parent / "src/aip/adapter/mcp/server.py"
    text = server_file.read_text()
    assert "import sqlite3" not in text
    assert "from aip.adapter." not in text.replace("from aip.adapter.mcp.tools", "")  # tools/ is allowed
    assert "vector_store.retrieve" not in text.lower() or "container." in text


def test_layering_and_no_orchestration_impl_imports():
    """Layering (enforced in combined gate)."""
    from pathlib import Path

    for f in ["server.py"]:
        p = Path(__file__).parent.parent / "src/aip/adapter/mcp" / f
        if p.exists():
            text = p.read_text()
            assert "from aip.orchestration.nodes" not in text


# ---- No fake success verification ----


@pytest.mark.asyncio
async def test_no_hardcoded_success_in_search():
    """Search must not return hardcoded {"results": []} without consulting backends."""
    # When no backends, should return BACKEND_UNAVAILABLE, not ok=true with empty
    server = AipMcpServer(container=_make_container(lexical_store=None, vector_store=None))
    result = await server.call_tool("aip_search", {"query": "test"})
    assert result["ok"] is False, "Search with no backends must not return ok=true"


@pytest.mark.asyncio
async def test_no_hardcoded_approval():
    """Approval must not return hardcoded {"approved": True} without real work."""
    # When artifact doesn't exist, should return NOT_FOUND, not approved=True
    server = AipMcpServer(
        container=_make_container(ecs_store=_MockEcsStore(states={}), canonical_store=_MockCanonicalStore(), autonomy_gate=_MockAutonomyGate())
    )
    result = await server.call_tool("aip_artifact_approve", {"artifact_id": "nonexistent"})
    assert result["ok"] is False, "Approval of nonexistent artifact must not return ok=true"


@pytest.mark.asyncio
async def test_no_generic_ok_true_for_unsupported_tools():
    """Unsupported tools must not return generic {"ok": True}."""
    # aip_config_write returns NOT_IMPLEMENTED, not ok=True
    server = AipMcpServer(container=_make_container(autonomy_gate=_MockAutonomyGate()))
    result = await server.call_tool("aip_config_write", {"section": "x", "values": {}})
    assert result["ok"] is False, "Config write must not return generic ok=true"


# ---- Fail-closed when autonomy_gate is None ----


@pytest.mark.asyncio
async def test_admin_tool_rejected_when_gate_is_none():
    """Admin tools must be rejected when autonomy_gate is None (fail-closed)."""
    container = _make_container(autonomy_gate=None)
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_artifact_approve", {"artifact_id": "art1"})
    assert result["ok"] is False
    assert result["error"]["code"] == "FORBIDDEN"
    assert "unavailable" in result["error"]["message"].lower() or "gate" in result["error"]["message"].lower()


@pytest.mark.asyncio
async def test_write_tool_rejected_when_gate_is_none():
    """Write tools must be rejected when autonomy_gate is None (fail-closed)."""
    container = _make_container(autonomy_gate=None)
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_project_create", {"name": "test"})
    assert result["ok"] is False
    assert result["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_read_tool_not_blocked_when_gate_is_none():
    """Read tools should NOT be blocked when autonomy_gate is None."""
    container = _make_container(lexical_store=_MockLexicalStore(results=[]))
    server = AipMcpServer(container=container)
    result = await server.call_tool("aip_search", {"query": "test"})
    # Should succeed (empty results is valid), not be blocked by missing gate
    assert result["ok"] is True


# ---- Server lifecycle ----


@pytest.mark.asyncio
async def test_server_start_stop():
    server = AipMcpServer(container=_make_container())
    assert server._running is False
    await server.start()
    assert server._running is True
    await server.stop()
    assert server._running is False
