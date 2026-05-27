"""Tests for the Synthesis Node Stub (CHUNK-1.3 per Rev 1.3)."""

import asyncio

import pytest

from aip.foundation.schemas import Chunk, RetrievalResult
from aip.orchestration.nodes.synthesis import synthesize, SynthesisOutput


def _make_retrieval_result(status="OK", hits=None, max_conf=0.75):
    if hits is None:
        hits = [
            Chunk(id="h1", content="Important context about the domain.", score=0.82, domain="test"),
            Chunk(id="h2", content="Additional supporting details.", score=0.71, domain="test"),
        ]
    return RetrievalResult(status=status, hits=hits, max_confidence=max_conf)


def test_synthesize_returns_correct_structure():
    retrieval = _make_retrieval_result()
    result = asyncio.run(synthesize("test query", "test", retrieval))

    assert isinstance(result, SynthesisOutput)
    assert isinstance(result.content, str)
    assert result.model_slot == "synthesis"
    assert isinstance(result.model_name, str)
    assert result.token_count_in > 0
    assert result.token_count_out > 0
    assert result.latency_ms >= 0


def test_synthesize_resolves_model_name_from_config():
    retrieval = _make_retrieval_result()
    config = {
        "models": {
            "synthesis": {"model": "stub-test-model"}
        }
    }
    result = asyncio.run(synthesize("q", "d", retrieval, model_slot="synthesis", config=config))
    assert result.model_name == "stub-test-model"


def test_synthesize_falls_back_when_no_models_in_config():
    retrieval = _make_retrieval_result()
    result = asyncio.run(synthesize("q", "d", retrieval, model_slot="evaluation"))
    assert "<evaluation-slot-unconfigured>" in result.model_name


def test_synthesize_output_passes_structural_validate():
    """Critical requirement from Rev 1.3: the stub output must pass CHUNK-1.2 validation."""
    from aip.foundation.validation import structural_validate

    retrieval = _make_retrieval_result()
    result = asyncio.run(synthesize("complex domain question", "test", retrieval))

    validation = structural_validate(result.content)
    assert validation.passed, f"Stub output failed structural validation: {validation.failure_detail}"


def test_synthesize_uses_retrieval_context():
    retrieval = _make_retrieval_result(hits=[
        Chunk(id="ctx1", content="The capital of France is Paris.", score=0.95, domain="geo")
    ])
    result = asyncio.run(synthesize("What is the capital of France?", "geo", retrieval))
    assert "Paris" in result.content or "capital" in result.content.lower()


@pytest.mark.asyncio
async def test_synthesize_is_async():
    retrieval = _make_retrieval_result()
    # Should not raise when awaited
    result = await synthesize("async test", "test", retrieval)
    assert isinstance(result, SynthesisOutput)
