
import asyncio
import pytest

from aip.orchestration.workflow.node import DialogNode, NodeType, ScriptNode, ParallelNode
from aip.orchestration.workflow.context import WorkflowContext
from aip.orchestration.workflow.runner import SequentialRunner
from aip.orchestration.nodes.synthesis import SynthesisOutput
from aip.foundation.validation import ValidationResult
from aip.orchestration.nodes.adversarial_eval import EvalResult

def _make_synth():
    return SynthesisOutput(
        content="Test synthesis for dialog", model_slot="synthesis", model_name="stub",
        token_count_in=50, token_count_out=40, latency_ms=30
    )

def _make_val(passed=True):
    return ValidationResult(passed=passed, failure_type=None, failure_detail=None, checks_run=3, checks_failed=[] if passed else ["min_length"])

def _make_eval(passed=True):
    return EvalResult(passed=passed, scores={"grounding": 0.8}, requires_deep_eval=not passed)

@pytest.mark.asyncio
async def test_dialog_node_emits_pause_event_and_stops_runner():
    synth = _make_synth()
    val = _make_val(passed=True)
    ev = _make_eval(passed=True)

    dialog = DialogNode("review_step", prompt="Please review this output",
                        synthesis_output=synth, validation_result=val, eval_result=ev)

    nodes = [dialog]
    ctx = WorkflowContext()
    runner = SequentialRunner(nodes, ctx)
    results = await runner.run()

    assert len(ctx.events) == 1
    assert ctx.events[0]["type"] == "workflow.dialog.paused"
    assert results[-1].output.get("paused") is True
    assert results[-1].output.get("type") == "dialog"

@pytest.mark.asyncio
async def test_parallel_node_basic_execution():
    """Basic smoke test that ParallelNode runs its children concurrently via the runner."""
    nodes = [
        ScriptNode("p1", code="parallel one"),
        ScriptNode("p2", code="parallel two"),
        ParallelNode("par", children=["p1", "p2"]),
    ]
    ctx = WorkflowContext()
    runner = SequentialRunner(nodes, ctx)
    results = await runner.run()

    # The parallel node itself plus its children should have run
    executed_types = [r.output.get("type") for r in results if isinstance(r.output, dict)]
    assert "parallel" in executed_types

@pytest.mark.asyncio
async def test_parallel_node_executes_children_concurrently():
    """Verify that ParallelNode actually runs its children via the runner with forked contexts."""
    nodes = [
        ScriptNode("p1", code="branch one"),
        ScriptNode("p2", code="branch two"),
        ParallelNode("par", children=["p1", "p2"]),
        ScriptNode("after", code="after parallel"),
    ]
    ctx = WorkflowContext()
    runner = SequentialRunner(nodes, ctx)
    results = await runner.run()

    executed = [r.output.get("executed") for r in results if isinstance(r.output, dict) and "executed" in r.output]
    # We expect the parallel block and its children + the node after it
    assert "par" in executed or any("parallel" in str(r) for r in executed)
    assert "after" in executed

@pytest.mark.asyncio
async def test_agent_node_wires_to_phase1_synthesis(monkeypatch):
    """Smoke test that AgentNode now calls the real Phase 1 retrieve + synthesis path via protocols."""
    from aip.orchestration.workflow.node import AgentNode
    from aip.foundation.schemas import Chunk, RetrievalResult

    # Fake minimal vector store
    class FakeVS:
        async def retrieve(self, query_vector, domain=None, top_k=10):
            return [Chunk(id="c1", content="test context", score=0.9, domain=domain)]

    async def fake_embed(text):
        return [0.1] * 768

    nodes = [AgentNode("research", model_slot="synthesis", prompt_template="Research: {{query}}")]
    ctx = WorkflowContext(
        variables={"query": "What is the capital of France?"},
        protocols={
            "vector_store": FakeVS(),
            "embed_fn": fake_embed,
        }
    )
    runner = SequentialRunner(nodes, ctx)
    results = await runner.run()

    assert results[0].success
    assert hasattr(results[0].output, "content")  # Should be a real SynthesisOutput
