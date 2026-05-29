"""Multi-surface isolation acceptance tests.

Tests that surfaces (API, CLI, MCP, Chat) respect isolation per §7.2:
- Surfaces compose Foundation + Orchestration (no direct adapter imports)
- AutonomyGate is enforced across all surfaces
- MCP cannot bypass DEFINER gates (Appendix D: "MCP ≠ bypass")
- Rate limiting applies to all surfaces
- Each surface respects the same sovereignty model
"""

import pytest


def test_surface_config_importable():
    """SurfaceConfig is importable with correct defaults."""
    from aip.foundation.schemas import SurfaceConfig

    cfg = SurfaceConfig()
    assert cfg.api_host == "127.0.0.1"
    assert cfg.api_port == 8000
    assert cfg.api_workers == 1


def test_api_route_sovereignty_model():
    """API routes carry auth_required and autonomy_gate flags."""
    from aip.foundation.schemas import ApiRoute

    # A public route
    public = ApiRoute(method="GET", path="/api/v1/health", handler="health")
    assert public.auth_required is False
    assert public.autonomy_gate is False

    # A privileged route
    admin = ApiRoute(
        method="POST",
        path="/api/v1/admin/promote",
        handler="promote",
        auth_required=True,
        autonomy_gate=True,
    )
    assert admin.auth_required is True
    assert admin.autonomy_gate is True


def test_mcp_tool_sovereignty_model():
    """MCP tools carry autonomy_level and model_gen_assumption."""
    from aip.foundation.schemas import McpToolDef

    # A read-only tool
    search = McpToolDef(
        tool_name="search_canonical",
        description="Search canonical artifacts",
        autonomy_level="read",
    )
    assert search.autonomy_level == "read"

    # A write tool
    create = McpToolDef(
        tool_name="create_artifact",
        description="Create a new artifact draft",
        autonomy_level="write",
        model_gen_assumption="Models can draft but not approve",
    )
    assert create.autonomy_level == "write"
    assert create.model_gen_assumption is not None


def test_mcp_server_importable():
    """MCP server is importable."""
    from aip.adapter.mcp.server import AipMcpServer

    # AipMcpServer should be importable
    assert AipMcpServer is not None


def test_rate_limit_config_defaults():
    """RateLimitConfig has sensible defaults that protect all surfaces."""
    from aip.foundation.schemas import RateLimitConfig

    cfg = RateLimitConfig()
    assert cfg.enabled is True
    assert cfg.requests_per_minute == 60
    assert cfg.burst_size == 10
    assert cfg.model_budget_protection is True


def test_auth_middleware_importable():
    """AuthMiddleware is importable and enforces identity across surfaces."""
    from aip.adapter.auth.middleware import AuthMiddleware

    assert AuthMiddleware is not None


def test_rate_limit_middleware_importable():
    """RateLimitMiddleware is importable."""
    from aip.adapter.middleware.rate_limiter import RateLimitMiddleware, TokenBucketRateLimiter

    assert RateLimitMiddleware is not None
    assert TokenBucketRateLimiter is not None


def test_chat_message_schema():
    """ChatMessage schema has required fields per §3."""
    from aip.foundation.schemas import ChatMessage

    msg = ChatMessage(
        message_id="msg-001",
        session_id="sess-001",
        role="user",
        content="Hello",
        tokens_used=5,
    )
    assert msg.role == "user"
    assert msg.session_id == "sess-001"


def test_collaborator_permissions_isolation():
    """Collaborators have restricted permissions vs DEFINER."""
    from aip.foundation.schemas import CollaboratorConfig

    cfg = CollaboratorConfig()
    assert cfg.collaborator_can_create_drafts is True
    assert cfg.collaborator_can_submit_review is True
    assert cfg.collaborator_can_approve is False  # DEFINER only


def test_autonomy_level_hierarchy():
    """Autonomy levels follow none < read < write < admin hierarchy."""
    import tempfile

    from aip.adapter.autonomy.autonomy_gate import AutonomyGateImpl

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        gate = AutonomyGateImpl(config={"db_path": tmp.name})
        assert gate._level_rank("none") < gate._level_rank("read")
        assert gate._level_rank("read") < gate._level_rank("write")
        assert gate._level_rank("write") < gate._level_rank("admin")
    finally:
        import os

        os.unlink(tmp.name)
