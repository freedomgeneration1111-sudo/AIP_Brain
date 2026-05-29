"""Tests for the evaluation pipeline — adversarial eval, L3a Stage 2/3."""

import asyncio

import pytest

from aip.foundation.schemas import Chunk
from aip.foundation.validation import full_l3a_evaluation
from aip.orchestration.nodes.adversarial_eval import EvalResult, adversarial_eval
from aip.orchestration.nodes.domain_coherence import evaluate_domain_coherence
from aip.orchestration.nodes.faithfulness import evaluate_faithfulness


class FakeModelResolver:
    """Minimal fake ModelSlotResolver for testing — returns CI fixture responses."""

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


class FakeModelResolverReal:
    """Fake ModelSlotResolver that returns real-looking JSON responses."""

    def __init__(self):
        pass

    async def call(self, slot_name, messages, **kwargs):
        if slot_name == "evaluation":
            return {
                "content": (
                    '{"scores": {"framework_integrity": 0.85, '
                    '"logic": 0.80, "honesty": 0.88, '
                    '"completeness": 0.82}, "overall": 0.84, '
                    '"critique": "Artifact is well-grounded and coherent."}'
                ),
                "model": "test-model-v1",
                "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
                "latency_ms": 200,
                "cost_usd": 0.001,
            }
        # Default: faithfulness/domain_coherence response
        return {
            "content": (
                '{"faithfulness_score": 0.88, '
                '"context_coverage": 0.85, '
                '"hallucination_flags": [], '
                '"rationale": "Well-grounded"}'
            ),
            "model": "test-model-v1",
            "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
            "latency_ms": 150,
            "cost_usd": 0.001,
        }


@pytest.fixture
def resolver():
    return FakeModelResolver()


@pytest.fixture
def real_resolver():
    return FakeModelResolverReal()


@pytest.mark.asyncio
async def test_adversarial_eval_ci_mode(resolver):
    """Adversarial eval with CI fixture ModelSlotResolver returns ci_fixture=True with 0.0 scores."""
    result = await adversarial_eval(
        artifact_content="Test content",
        context="Test context",
        model_resolver=resolver,
    )
    assert "scores" in result or "overall" in result
    assert result.get("ci_fixture") is True
    assert result.get("passed") is False
    assert result.get("overall") == 0.0


@pytest.mark.asyncio
async def test_adversarial_eval_real_mode(real_resolver):
    """Adversarial eval with real model response returns parsed scores and ci_fixture=False."""
    result = await adversarial_eval(
        artifact_content="Test content",
        context="Test context",
        model_resolver=real_resolver,
    )
    assert result.get("ci_fixture") is False
    assert result.get("overall", 0.0) > 0.0
    assert result.get("passed") is True
    assert "framework_integrity" in result.get("scores", {})


def test_old_adversarial_eval_backward_compat():
    """Old Phase 1 signature still works and returns ci_fixture=True."""
    from aip.foundation.validation import ValidationResult
    from aip.orchestration.nodes.synthesis import SynthesisOutput

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
    assert result.ci_fixture is True
    assert result.passed is False  # Stub mode cannot pass


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
        artifact_content=(
            "A reasonably long piece of synthesized content that "
            "should pass the minimum length and marker checks for structural validation."
        ),
        domain="test",
        retrieved_context=[Chunk(id="c1", content="Some context", score=0.8, metadata={}, domain="test")],
        model_resolver=resolver,
    )
    assert "stage1" in result
    assert "stage2" in result
    assert "stage3" in result
