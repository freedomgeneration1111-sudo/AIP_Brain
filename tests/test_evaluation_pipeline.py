"""Tests for the evaluation pipeline — adversarial eval, L3a Stage 2/3."""

import asyncio

import pytest

from aip.foundation.schemas import Chunk, EvaluationScore
from aip.orchestration.nodes.adversarial_eval import adversarial_eval, EvalResult
from aip.orchestration.nodes.faithfulness import evaluate_faithfulness
from aip.orchestration.nodes.domain_coherence import evaluate_domain_coherence
from aip.foundation.validation import structural_validate, full_l3a_evaluation


class FakeModelResolver:
    """Minimal fake ModelSlotResolver for testing."""
    def __init__(self, ci_mode=True):
        self._ci_mode = ci_mode

    async def call(self, slot_name, messages, **kwargs):
        return {
            "content": f"[CI fixture for {slot_name}]",
            "model": f"ci-{slot_name}",
            "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
            "latency_ms": 100,
            "cost_usd": 0.0,
        }


@pytest.fixture
def resolver():
    return FakeModelResolver()


@pytest.mark.asyncio
async def test_adversarial_eval_ci_mode(resolver):
    """Adversarial eval with ModelSlotResolver returns structured result."""
    result = await adversarial_eval(
        artifact_content="Test content",
        context="Test context",
        model_resolver=resolver,
    )
    assert "scores" in result or "overall" in result


def test_old_adversarial_eval_backward_compat():
    """Old Phase 1 signature still works."""
    from aip.orchestration.nodes.synthesis import SynthesisOutput
    from aip.foundation.validation import ValidationResult

    synth = SynthesisOutput(
        content="Test synthesis output that is long enough to pass basic checks.",
        model_slot="synthesis",
        model_name="test",
        token_count_in=100,
        token_count_out=50,
        latency_ms=10,
    )
    val = ValidationResult(passed=True, failure_type=None, failure_detail=None, checks_run=3, checks_failed=[])

    result = asyncio.run(adversarial_eval(synth, val))  # type: ignore
    assert isinstance(result, EvalResult)
    assert isinstance(result.passed, bool)


@pytest.mark.asyncio
async def test_faithfulness_returns_result(resolver):
    """Faithfulness evaluation returns FaithfulnessResult."""
    chunks = [
        Chunk(id="c1", content="Context text", score=0.9, metadata={}, domain="test"),
    ]
    result = await evaluate_faithfulness(
        artifact_id="a1",
        artifact_content="Test artifact content",
        retrieved_context=chunks,
        model_resolver=resolver,
    )
    assert result.faithfulness_score > 0.0
    assert result.artifact_id == "a1"
    assert len(result.evaluation_scores) > 0


@pytest.mark.asyncio
async def test_faithfulness_carries_model_gen_assumption(resolver):
    """Per §1.8: faithfulness evaluation must carry model_gen_assumption."""
    chunks = [Chunk(id="c1", content="ctx", score=0.9, metadata={}, domain="test")]
    result = await evaluate_faithfulness(
        artifact_id="a1",
        artifact_content="content",
        retrieved_context=chunks,
        model_resolver=resolver,
    )
    for score in result.evaluation_scores:
        assert score.model_gen_assumption is not None


@pytest.mark.asyncio
async def test_domain_coherence_returns_result(resolver):
    """Domain coherence evaluation returns DomainCoherenceResult."""
    result = await evaluate_domain_coherence(
        artifact_id="a1",
        artifact_content="Test content",
        domain="software_architecture",
        model_resolver=resolver,
    )
    assert result.coherence_score > 0.0
    assert result.domain == "software_architecture"


@pytest.mark.asyncio
async def test_domain_coherence_carries_model_gen_assumption(resolver):
    """Per §1.8: domain coherence evaluation must carry model_gen_assumption."""
    result = await evaluate_domain_coherence(
        artifact_id="a1",
        artifact_content="content",
        domain="test",
        model_resolver=resolver,
    )
    for score in result.evaluation_scores:
        assert score.model_gen_assumption is not None


@pytest.mark.asyncio
async def test_full_l3a_evaluation_orchestration(resolver):
    """full_l3a_evaluation runs Stage 1 + 2 + 3 when Stage 1 passes."""
    result = await full_l3a_evaluation(
        artifact_id="a1",
        artifact_content="A reasonably long piece of synthesized content that should pass the minimum length and marker checks for structural validation.",
        domain="test",
        retrieved_context=[Chunk(id="c1", content="Some context", score=0.8, metadata={}, domain="test")],
        model_resolver=resolver,
    )
    assert "stage1" in result
    assert "stage2" in result
    assert "stage3" in result
