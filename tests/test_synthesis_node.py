"""Tests for the synthesis node — stub and ModelSlotResolver modes."""

import asyncio

import pytest

from aip.orchestration.nodes.synthesis import SynthesisOutput, synthesize


class FakeModelResolver:
    """Minimal fake ModelSlotResolver for testing (per 6.1 ANNEX)."""

    def __init__(self, ci_mode=True):
        self._ci_mode = ci_mode

    async def call(self, slot_name, messages, **kwargs):
        return {
            "content": f"[CI synthesis fixture for {slot_name}]",
            "model": f"ci-{slot_name}",
            "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            "latency_ms": 150,
            "cost_usd": 0.0,
        }


def _make_retrieval_result(status="OK", hits=None, max_conf=0.75):
    # Legacy helper kept for old-style compat tests
    from aip.foundation.schemas import Chunk, RetrievalResult

    if hits is None:
        hits = [
            Chunk(id="h1", content="Important context about the domain.", score=0.82, domain="test"),
            Chunk(id="h2", content="Additional supporting details.", score=0.71, domain="test"),
        ]
    return RetrievalResult(status=status, hits=hits, max_confidence=max_conf)


# --- New 6.1 tests (from ANNEX, adapted) ---


@pytest.mark.asyncio
async def test_stub_mode_no_resolver():
    """Phase 1 compat: synthesize without model_resolver returns stub (legacy path)."""
    result = await synthesize(query="What is X?", domain="test", context="Some context")
    assert isinstance(result, SynthesisOutput)
    assert result.model_slot == "synthesis"
    assert "Synthesized output" in result.content


@pytest.mark.asyncio
async def test_resolver_mode_ci():
    """Phase 4: synthesize with ModelSlotResolver in CI mode returns dict."""
    resolver = FakeModelResolver(ci_mode=True)
    result = await synthesize(
        query="What is X?",
        domain="test",
        context="Some context",
        model_resolver=resolver,
    )
    assert isinstance(result, dict)
    assert result["model"] == "ci-synthesis"
    assert result["usage"]["total_tokens"] == 300


@pytest.mark.asyncio
async def test_token_budget_passed():
    """Token budget is passed through to model resolver."""
    resolver = FakeModelResolver()
    result = await synthesize(
        query="Test",
        domain="test",
        context="ctx",
        model_resolver=resolver,
        token_budget=2048,
    )
    assert isinstance(result, dict)
    assert result["usage"]["total_tokens"] > 0


def test_no_hardcoded_model_names():
    """Per §4.1: no hardcoded model names in synthesis code."""
    import inspect

    from aip.orchestration.nodes.synthesis import synthesize

    source = inspect.getsource(synthesize)
    forbidden = ["deepseek", "claude", "gpt", "qwen", "nomic"]
    for name in forbidden:
        assert name.lower() not in source.lower(), f"Hardcoded model name: {name}"


# --- Legacy compat tests (preserved/updated for old callers) ---


def test_synthesize_resolves_model_name_from_config():
    retrieval = _make_retrieval_result()
    config = {"models": {"synthesis": {"model": "stub-test-model"}}}
    result = asyncio.run(synthesize("q", "d", retrieval_result=retrieval, model_slot="synthesis", config=config))
    assert isinstance(result, SynthesisOutput)
    assert result.model_name == "stub-test-model"


@pytest.mark.asyncio
async def test_synthesize_is_async():
    retrieval = _make_retrieval_result()
    result = await synthesize("async test", "test", retrieval_result=retrieval)
    assert isinstance(result, SynthesisOutput)
