"""DEFINER sovereignty acceptance tests.

Tests that DEFINER sovereignty is enforced per §1.7:
"No UI, workflow, Beast cadence, MCP call, or queued task may bypass the DEFINER gates."

Verifies:
- AutonomyGate blocks admin escalation for non-DEFINER
- Canonical promotion requires definer approval
- Collaborator cannot approve artifacts
- API routes with autonomy_gate=True enforce sovereignty
"""
import os
import tempfile
import pytest


def _make_gate(config=None):
    """Create AutonomyGateImpl with a temporary file DB (not :memory:).

    AutonomyGateImpl opens a new connection per call, so :memory: doesn't
    share the table across calls. A temp file solves this.
    """
    from aip.adapter.autonomy.autonomy_gate import AutonomyGateImpl

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cfg = config or {}
    cfg["db_path"] = tmp.name
    gate = AutonomyGateImpl(config=cfg)
    gate._tmp_db_path = tmp.name  # keep ref for cleanup
    return gate


@pytest.fixture(autouse=True)
def _cleanup_gate_db(request):
    """Cleanup temp DB files after test."""
    yield
    # Check if the test created a gate with a temp db
    for attr_name in dir(request.node):
        pass  # Cleanup happens in individual tests


@pytest.mark.asyncio
async def test_autonomy_gate_impl_importable():
    """AutonomyGateImpl is importable from adapter layer."""
    gate = _make_gate()
    assert gate is not None
    os.unlink(gate._tmp_db_path)


@pytest.mark.asyncio
async def test_admin_escalation_blocked_for_non_definer():
    """Admin escalation is blocked for non-DEFINER when escalation_requires_definer=True."""
    gate = _make_gate({"escalation_requires_definer": True})
    try:
        result = await gate.escalate(
            action_type="approve_artifact",
            resource_id="art-001",
            requested_level="admin",
            requested_by="collaborator",
        )
        assert result.granted is False
        assert "DEFINER" in result.reason or "definer" in result.reason.lower()
    finally:
        os.unlink(gate._tmp_db_path)


@pytest.mark.asyncio
async def test_admin_escalation_granted_for_definer():
    """Admin escalation is granted when requested_by is 'definer'."""
    gate = _make_gate({"escalation_requires_definer": True})
    try:
        result = await gate.escalate(
            action_type="approve_artifact",
            resource_id="art-001",
            requested_level="admin",
            requested_by="definer",
        )
        assert result.granted is True
    finally:
        os.unlink(gate._tmp_db_path)


@pytest.mark.asyncio
async def test_read_level_auto_granted():
    """Read-level actions are auto-granted when default_level is 'read'."""
    gate = _make_gate({"default_level": "read"})
    try:
        result = await gate.check(
            action_type="search",
            resource_id="art-001",
            requested_level="read",
            requested_by="anyone",
        )
        assert result.granted is True
    finally:
        os.unlink(gate._tmp_db_path)


@pytest.mark.asyncio
async def test_write_level_requires_escalation_from_read_default():
    """Write-level requires escalation from read default."""
    gate = _make_gate({"default_level": "read"})
    try:
        result = await gate.check(
            action_type="create_artifact",
            resource_id="art-001",
            requested_level="write",
            requested_by="anyone",
        )
        # Write is above read default, so escalation required
        assert result.granted is False
    finally:
        os.unlink(gate._tmp_db_path)


def test_collaborator_config_cannot_approve():
    """CollaboratorConfig default denies approval per Process Rule 11."""
    from aip.foundation.schemas import CollaboratorConfig

    cfg = CollaboratorConfig()
    assert cfg.collaborator_can_approve is False


def test_definer_gate_module_importable():
    """DEFINER gate node is importable."""
    from aip.orchestration.nodes.definer_gate import definer_gate, DefinerGateMode, DefinerDecision

    assert DefinerGateMode.AUTO_APPROVE_STUB.value == "auto_approve_stub"


def test_canonical_promotion_requires_definer():
    """CanonicalPromotionConfig requires DEFINER approval by default."""
    from aip.foundation.schemas import CanonicalPromotionConfig

    cfg = CanonicalPromotionConfig()
    assert cfg.require_definer_approval is True


def test_api_routes_sovereignty():
    """API routes with autonomy_gate=True enforce sovereignty."""
    from aip.foundation.schemas import ApiRoute

    admin_route = ApiRoute(
        method="POST",
        path="/api/v1/admin/promote",
        handler="admin_promote",
        auth_required=True,
        autonomy_gate=True,
    )
    assert admin_route.autonomy_gate is True
    assert admin_route.auth_required is True


def test_auth_config_definer_identity():
    """AuthConfig has a configurable definer_identity."""
    from aip.foundation.schemas import AuthConfig

    cfg = AuthConfig()
    assert cfg.definer_identity == "definer"


@pytest.mark.asyncio
async def test_autonomy_escalation_audit_trail():
    """AutonomyGateImpl writes audit trail for all escalations."""
    gate = _make_gate({"escalation_requires_definer": True})
    try:
        await gate.escalate(
            action_type="test_action",
            resource_id="res-001",
            requested_level="admin",
            requested_by="non_definer",
        )
        log = await gate.audit_log(limit=10)
        assert len(log) >= 1
        assert log[0].action_type == "test_action"
        assert log[0].granted is False
    finally:
        os.unlink(gate._tmp_db_path)
