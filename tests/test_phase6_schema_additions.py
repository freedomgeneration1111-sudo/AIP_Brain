"""Verify Phase 6 schema additions do not break Phase 0, 1, 2, 3, 4, or 5."""

from aip.foundation.protocols import (
    AutonomyGate,
    CanonicalStore,
    EntityStore,
    LexicalStore,
)
from aip.foundation.schemas import (
    ApiRoute,
    AutonomyEscalation,
    AutonomyLevel,
    ChatMessage,
    Chunk,
    EcsState,
    FailureClassification,
    FailureType,
    McpAutonomyLevel,
    McpToolDef,
    ReviewQueueEntry,
    ReviewVerdict,
    SurfaceConfig,
    TrajectorySignal,
)


def test_surface_config_dataclass():
    sc = SurfaceConfig(
        api_host="127.0.0.1",
        api_port=8000,
        chat_max_history_turns=50,
    )
    assert sc.api_port == 8000
    assert sc.chat_max_history_turns == 50


def test_api_route_dataclass():
    ar = ApiRoute(
        method="POST",
        path="/api/v1/artifacts/{artifact_id}/approve",
        handler="approve_artifact",
        auth_required=True,
        autonomy_gate=True,
    )
    assert ar.autonomy_gate is True
    assert ar.auth_required is True


def test_mcp_tool_def_carries_model_gen_assumption():
    """Per §1.8: every MCP tool definition must carry model_gen_assumption."""
    td = McpToolDef(
        tool_name="aip_search",
        description="Search AIP memory for relevant context",
        autonomy_level="read",
        model_gen_assumption="Models may hallucinate without retrieved context",
    )
    assert td.model_gen_assumption is not None
    assert td.autonomy_level == "read"


def test_autonomy_escalation_carries_model_gen_assumption():
    """Per §1.8: every autonomy escalation must carry model_gen_assumption."""
    ae = AutonomyEscalation(
        escalation_id="esc-001",
        action_type="approve_artifact",
        requested_by="mcp",
        resource_id="artifact-42",
        current_level="read",
        requested_level="admin",
        granted=False,
        reason="DEFINER approval required for canonical promotion",
        model_gen_assumption="Models should not autonomously approve artifacts",
        created_at="2026-05-28T10:00:00Z",
    )
    assert ae.model_gen_assumption is not None
    assert ae.granted is False


def test_chat_message_dataclass():
    cm = ChatMessage(
        message_id="msg-001",
        session_id="sess-001",
        role="user",
        content="Generate a design document for the API layer",
    )
    assert cm.role == "user"
    assert cm.artifacts_referenced == []


def test_review_queue_entry_dataclass():
    rqe = ReviewQueueEntry(
        artifact_id="art-42",
        ecs_state="REVIEWED",
        domain="software_architecture",
        project_id="proj-1",
        review_type="definer",
    )
    assert rqe.ecs_state == "REVIEWED"
    assert rqe.review_type == "definer"


def test_autonomy_level_type_alias():
    """AutonomyLevel must accept the defined levels."""
    _al_none: AutonomyLevel = "none"
    _al_read: AutonomyLevel = "read"
    _al_write: AutonomyLevel = "write"
    al_admin: AutonomyLevel = "admin"
    assert al_admin == "admin"


def test_mcp_autonomy_level_type_alias():
    """McpAutonomyLevel must accept the defined levels."""
    _ml_read: McpAutonomyLevel = "read"
    _ml_write: McpAutonomyLevel = "write"
    ml_admin: McpAutonomyLevel = "admin"
    assert ml_admin == "admin"


def test_phase0_through_phase5_enums_still_work():
    """Phase 0/1/2/3/4/5 enums must not be broken by Phase 6 additions."""
    assert EcsState.GENERATED is not None
    from typing import get_args

    assert "C" in get_args(FailureType)


def test_phase0_through_phase5_dataclasses_still_work():
    """Phase 0/1/2/3/4/5 dataclasses must not be broken by Phase 6 additions."""
    c = Chunk(id="x", content="hello", score=0.9, metadata={"k": "v"}, domain="test")
    assert c.id == "x"
    v = ReviewVerdict(artifact_id="a1", verdict="APPROVED", reviewer="definer")
    assert v.verdict == "APPROVED"
    s = TrajectorySignal(
        signal_type="loop",
        session_id="s1",
        failure_type="D",
        confidence=0.85,
        detail="Repeated pattern",
        detected_at="2026-05-28T10:00:00Z",
    )
    assert s.signal_type == "loop"
    fc = FailureClassification(
        trace_event_id=42,
        failure_type="A",
        confidence=0.92,
        model_gen_assumption="Test",
        classified_at="2026-05-28T10:00:00Z",
    )
    assert fc.failure_type == "A"


def test_autonomy_gate_protocol_methods():
    """Phase 6: AutonomyGate must have check, escalate, audit_log methods."""
    assert hasattr(AutonomyGate, "check"), "AutonomyGate missing check method"
    assert hasattr(AutonomyGate, "escalate"), "AutonomyGate missing escalate method"
    assert hasattr(AutonomyGate, "audit_log"), "AutonomyGate missing audit_log method"


def test_lexical_store_protocol_methods():
    """Phase 6: LexicalStore must have search, index_document, delete_document methods."""
    assert hasattr(LexicalStore, "search"), "LexicalStore missing search method"
    assert hasattr(LexicalStore, "index_document"), "LexicalStore missing index_document method"
    assert hasattr(LexicalStore, "delete_document"), "LexicalStore missing delete_document method"


def test_canonical_store_new_methods():
    """Phase 6: CanonicalStore must have read_canonical, write_canonical, list_canonical."""
    assert hasattr(CanonicalStore, "read_canonical"), "CanonicalStore missing read_canonical"
    assert hasattr(CanonicalStore, "write_canonical"), "CanonicalStore missing write_canonical"
    assert hasattr(CanonicalStore, "list_canonical"), "CanonicalStore missing list_canonical"


def test_entity_store_new_methods():
    """Phase 6: EntityStore must have get_entity, list_entities, update_entity."""
    assert hasattr(EntityStore, "get_entity"), "EntityStore missing get_entity"
    assert hasattr(EntityStore, "list_entities"), "EntityStore missing list_entities"
    assert hasattr(EntityStore, "update_entity"), "EntityStore missing update_entity"
