"""Tests for PluginLoader + YamlPluginProvider (CHUNK-10.0b).

Verifies:
- Implements contracts
- Sandbox mode catches errors
- Adapter layer does not import orchestration (enforced by test_layering + static check)
- CI deterministic fixture path
"""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from aip.foundation.protocols import PluginProvider
from aip.adapter.plugins.plugin_loader import PluginLoader
from aip.adapter.plugins.yaml_plugin_provider import YamlPluginProvider
from aip.foundation.schemas import PluginConfig


def test_yaml_plugin_provider_implements_protocol():
    # Minimal valid config (no real key needed for fixture mode)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "test.yaml"
        cfg.write_text(
            "slot_name: test\nprovider_name: fixture\nbase_url: http://example\nmodel: test\n"
        )
        p = YamlPluginProvider(str(cfg))
        assert isinstance(p, PluginProvider)
        assert p.get_slot_name() == "test"


def test_plugin_loader_discover_and_sandbox():
    with tempfile.TemporaryDirectory() as tmp:
        plugins_dir = Path(tmp)
        bad = plugins_dir / "bad.yaml"
        bad.write_text("slot_name: bad\n  this: is: not: valid: yaml: [")  # syntactically broken

        cfg = PluginConfig(plugins_dir=str(plugins_dir), enabled=True, auto_discover=True, sandbox_mode=True)
        loader = PluginLoader(cfg)

        # discover should not crash even with bad yaml when sandbox=True
        metas = loader.discover_plugins()
        assert isinstance(metas, list)

        # load bad file returns None instead of raising (sandbox mode)
        result = loader.load_plugin(str(bad))
        assert result is None


def test_ci_deterministic_mode_for_provider():
    os.environ["AIP_CI_MODE"] = "1"
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "ci.yaml"
        cfg.write_text(
            "slot_name: ci\nprovider_name: ci-fixture\nbase_url: http://example\nmodel: test\n"
        )
        p = YamlPluginProvider(str(cfg))
        resp = asyncio.get_event_loop().run_until_complete(p.call_model("hello world", {}))
        assert "[CI-FIXTURE]" in resp
    del os.environ["AIP_CI_MODE"]


def test_adapter_layer_does_not_import_orchestration():
    """Static check: these adapter modules must not contain 'from aip.orchestration' imports."""
    import aip.adapter.knowledge.sqlite_knowledge_store as ks
    import aip.adapter.plugins.plugin_loader as pl
    import aip.adapter.plugins.yaml_plugin_provider as yp

    for mod in (ks, pl, yp):
        src = mod.__file__
        with open(src, "r", encoding="utf-8") as f:
            content = f.read()
        assert "from aip.orchestration" not in content, f"Orchestration import found in {src}"
        assert "import aip.orchestration" not in content, f"Orchestration import found in {src}"
