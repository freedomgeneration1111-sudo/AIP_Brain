"""Phase 4 network isolation and model-name gate.

Extends CHUNK-4.8 and CHUNK-5.9 gates for Phase 4 code.
Verifies: deterministic CI, import boundaries, no hardcoded model names.
"""

import ast
import importlib
import inspect

import pytest

# Phase 4 modules to check
# (module names use the actual importable package paths under "aip" for this repo layout)
PHASE4_ADAPTER_MODULES = [
    "aip.adapter.vector.pgvector_store",
    "aip.adapter.vector.factory",
    "aip.adapter.vector.migrate",
    "aip.adapter.health",
]

PHASE4_ORCHESTRATION_MODULES = [
    "aip.orchestration.nodes.synthesis",
    "aip.orchestration.nodes.adversarial_eval",
    "aip.orchestration.nodes.faithfulness",
    "aip.orchestration.nodes.domain_coherence",
]

PHASE4_FOUNDATION_MODULES = [
    "aip.foundation.schemas",
    "aip.foundation.protocols",
]

FORBIDDEN_NETWORK_IMPORTS = ["openai", "anthropic", "httpx", "requests"]
FORBIDDEN_MODEL_NAMES = ["deepseek-chat", "claude-sonnet", "gpt-4", "qwen3-coder"]


class TestNetworkIsolation:
    """Verify Phase 4 code does not import network libraries in wrong layers.

    Reconciliation note (per pre-6.6 CC + Rule #10):
    Uses AST-based detection (matching CHUNK-4.8 / CHUNK-5.9 style) so that
    explanatory docstrings/comments referencing forbidden names (e.g. the
    architectural rule comment in foundation/protocols.py) do not trigger
    false positives. Only real import statements are flagged.
    """

    @pytest.mark.parametrize("module_name", PHASE4_FOUNDATION_MODULES)
    def test_foundation_no_network_imports(self, module_name):
        """Foundation modules must not import network libraries."""
        try:
            mod = importlib.import_module(module_name)
            source = inspect.getsource(mod)
            # Use AST to detect only actual import statements (not docstrings/comments)
            try:
                tree = ast.parse(source)
            except Exception:
                pytest.skip(f"Could not parse {module_name}")
                return

            violations = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [alias.name.split(".")[0] for alias in node.names]
                    else:
                        if node.module:
                            names = [node.module.split(".")[0]]
                    for name in names:
                        if name in FORBIDDEN_NETWORK_IMPORTS:
                            violations.append(name)

            assert not violations, f"{module_name} imports network libraries: {violations}"
        except ImportError:
            pytest.skip(f"Module {module_name} not yet built")


class TestImportBoundaries:
    """Verify Phase 4 code respects import boundaries (§7.2)."""

    @pytest.mark.parametrize("module_name", PHASE4_FOUNDATION_MODULES)
    def test_foundation_no_orchestration_import(self, module_name):
        """Foundation must not import orchestration."""
        try:
            mod = importlib.import_module(module_name)
            source = inspect.getsource(mod)
            # AST-based: only real imports, not mentions in docstrings/comments
            try:
                tree = ast.parse(source)
            except Exception:
                pytest.skip(f"Could not parse {module_name}")
                return

            violations = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    else:
                        names = [node.module] if node.module else []
                    for n in names:
                        if n and n.split(".")[0] == "orchestration":
                            violations.append(n)
            assert not violations, f"{module_name} imports orchestration (violates §7.2): {violations}"
        except ImportError:
            pytest.skip(f"Module {module_name} not yet built")

    @pytest.mark.parametrize("module_name", PHASE4_ADAPTER_MODULES)
    def test_adapter_no_orchestration_import(self, module_name):
        """Adapter must not import orchestration."""
        try:
            mod = importlib.import_module(module_name)
            source = inspect.getsource(mod)
            # Match ANNEX intent ("from orchestration") but via AST for precision
            try:
                tree = ast.parse(source)
            except Exception:
                pytest.skip(f"Could not parse {module_name}")
                return

            violations = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split(".")[0] == "orchestration":
                        violations.append(node.module)
            assert not violations, f"{module_name} imports orchestration (violates §7.2): {violations}"
        except ImportError:
            pytest.skip(f"Module {module_name} not yet built")


class TestNoHardcodedModelNames:
    """Verify Phase 4 code does not hardcode model names (§4.1)."""

    @pytest.mark.parametrize("module_name", PHASE4_ADAPTER_MODULES + PHASE4_ORCHESTRATION_MODULES)
    def test_no_hardcoded_models(self, module_name):
        """Per §4.1: no hardcoded model names in application code."""
        try:
            mod = importlib.import_module(module_name)
            source = inspect.getsource(mod)
            for name in FORBIDDEN_MODEL_NAMES:
                assert name.lower() not in source.lower(), f"{module_name} contains hardcoded model name: {name}"
        except ImportError:
            pytest.skip(f"Module {module_name} not yet built")
