"""Tests for CHUNK-5.0b Model Slot Resolver."""
import pytest

from aip.adapter.model_slot_resolver import ModelSlotResolver


@pytest.fixture
def ci_config():
    return {
        "models": {
            "ci_mode": True,
            "synthesis": {"provider": "ollama", "model": "qwen2.5:32b"},
            "embedding": {"provider": "ollama", "model": "nomic-embed-text", "dimensions": 768},
        }
    }


def test_resolve_slot(ci_config):
    resolver = ModelSlotResolver(ci_config)
    cfg = resolver.resolve("synthesis")
    assert cfg.slot_name == "synthesis"
    assert cfg.provider == "ollama"


def test_ci_mode_returns_fixture(ci_config):
    import asyncio

    resolver = ModelSlotResolver(ci_config)
    result = asyncio.run(resolver.call("synthesis", [{"role": "user", "content": "hello"}]))
    assert "CI-FIXTURE" in result["content"]
    assert result["cost_usd"] == 0.0


def test_list_slots(ci_config):
    resolver = ModelSlotResolver(ci_config)
    slots = resolver.list_slots()
    assert "synthesis" in slots
    assert "embedding" in slots
