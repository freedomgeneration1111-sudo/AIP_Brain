
import asyncio
import pytest

from aip.orchestration.workflow.node import DialogNode, NodeType
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
