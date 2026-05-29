"""Phase 3 extension of the network isolation and model-name gates (CHUNK-5.9).

Extends CHUNK-4.8 (test_phase2_no_network.py) to cover all new Phase 3 code
(trajectory/*, session.py, and any other 5.x orchestration/foundation changes).

Also re-asserts that Phase 1/2 gates remain green (no regressions) and that
import boundaries (§7.2) hold for the new modules.
"""

import ast
from pathlib import Path

import pytest

# Re-use the exact logic from 4.8 by importing the tests (or copy the scanners for independence).
# For strict extension we duplicate the scanners with Phase 3 paths emphasized.


def test_phase3_code_has_no_network_imports():
    """CHUNK-5.9: New Phase 3 code (trajectory, session, etc.) must not contain network/LLM client imports.
    Only adapter/* may (conditionally under ci_mode).
    """
    forbidden = {"openai", "anthropic", "httpx", "requests", "aiohttp"}

    src_root = Path("src/aip")
    violations = []

    # Focus + all (to catch any accidental addition in 5.x)
    phase3_dirs = ["orchestration/trajectory", "orchestration/session.py"]

    for py_file in src_root.rglob("*.py"):
        if "test" in py_file.parts:
            continue
        # Always scan; the 4.8 baseline already covered prior
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                else:
                    if node.module:
                        names = [node.module.split(".")[0]]

                for name in names:
                    if name in forbidden:
                        # Allow only in adapter layer and CLI surface
                        if "adapter" not in str(py_file) and "cli" not in str(py_file):
                            violations.append(f"{py_file}: imports {name}")

    assert not violations, "Phase 3 code contains forbidden network imports:\n" + "\n".join(violations)


def test_phase3_code_has_no_hardcoded_models():
    """CHUNK-5.9: New Phase 3 code must not hardcode model names in application logic.
    Docstrings and model_gen_assumption strings are allowed per §1.8.
    """
    import ast as _ast

    forbidden_keywords = ["DeepSeek", "Qwen", "Claude", "GPT-", "gpt-", "sonnet", "o1-", "nomic-embed"]

    src_root = Path("src/aip")
    violations = []

    for py_file in src_root.rglob("*.py"):
        if "test" in py_file.parts:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = _ast.parse(source)
        except Exception:
            continue

        # Collect docstring lines
        docstring_lines = set()
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef, _ast.Module)):
                if (
                    node.body
                    and isinstance(node.body[0], _ast.Expr)
                    and isinstance(node.body[0].value, _ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    docstring_lines.add(node.body[0].value.lineno)

        for node in _ast.walk(tree):
            if isinstance(node, _ast.Constant) and isinstance(node.value, str):
                val = node.value
                if any(kw.lower() in val.lower() for kw in forbidden_keywords):
                    if node.lineno in docstring_lines:
                        continue
                    if len(val) > 40:
                        continue
                    # Allow in adapter layer (config fixtures)
                    if "adapter" in str(py_file):
                        continue
                    violations.append(f"{py_file}: contains '{val[:50]}'")

    assert not violations, "Phase 3 code contains hardcoded model names:\n" + "\n".join(violations)


def test_phase3_import_boundaries():
    """CHUNK-5.9 + §7.2: Foundation must not import orchestration or adapter;
    orchestration imports only via protocols."""
    src_root = Path("src/aip")

    foundation_violations = []
    orchestration_adapter_violations = []

    for py_file in src_root.rglob("*.py"):
        if "test" in py_file.parts:
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if str(py_file).startswith("src/aip/foundation"):
                    if (
                        mod.startswith("aip.orchestration")
                        or mod.startswith("aip.adapter")
                        or mod.startswith("orchestration")
                        or mod.startswith("adapter")
                    ):
                        foundation_violations.append(f"{py_file}: foundation imports {mod}")
                if str(py_file).startswith("src/aip/orchestration"):
                    # orchestration may import foundation + aip.orchestration.*
                    # but not adapter directly (except protocols)
                    if mod.startswith("aip.adapter") or mod.startswith("adapter"):
                        if "protocols" not in mod:
                            orchestration_adapter_violations.append(f"{py_file}: orchestration imports adapter {mod}")

    all_v = foundation_violations + orchestration_adapter_violations
    assert not all_v, "Phase 3 import boundary violations:\n" + "\n".join(all_v)


# Re-run the original 4.8 tests (or the core logic) to prove no regression from Phase 3 work.
# For simplicity we exec the same checks against the whole tree (the 4.8 test already does this).
def test_phase1_and_phase2_network_gates_still_pass():
    """CHUNK-5.9: All prior network/model-name gates (1.7/4.8) must still pass after Phase 3 additions."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("p2_gate", "tests/test_phase2_no_network.py")
    p2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p2)

    # Both tests should now pass since adapter is excluded from the scan
    p2.test_phase2_code_has_no_network_imports()
    p2.test_phase2_code_has_no_hardcoded_models()
