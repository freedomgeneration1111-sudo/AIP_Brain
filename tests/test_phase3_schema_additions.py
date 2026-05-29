"""Tests for CHUNK-5.0a: Phase 3 Schema + Protocol Additions."""

from aip.foundation.protocols import (
    EmbeddingProvider,
    ModelProvider,
    TraceStore,
)
from aip.foundation.schemas import (
    ModelSlotConfig,
    SessionContext,
    TrajectorySignal,
    TrajectorySignalType,
)


def test_trajectory_signal_has_model_gen_assumption():
    sig = TrajectorySignal(
        signal_type="loop",
        session_id="s1",
        failure_type="D",
        model_gen_assumption="current models have limited long-context coherence",
    )
    assert sig.model_gen_assumption is not None


def test_session_context_defaults():
    ctx = SessionContext(session_id="s1", project_id="p1")
    assert ctx.turn_count == 0
    assert ctx.artifacts_produced == []


def test_model_slot_config_structure():
    cfg = ModelSlotConfig(
        slot_name="synthesis",
        provider="ollama",
        model="qwen2.5:32b",
    )
    assert cfg.slot_name == "synthesis"


def test_trace_store_has_query_events():
    assert hasattr(TraceStore, "query_events")


def test_model_provider_protocol():
    assert hasattr(ModelProvider, "call")


def test_embedding_provider_protocol():
    assert hasattr(EmbeddingProvider, "embed")
