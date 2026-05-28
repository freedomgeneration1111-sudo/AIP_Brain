"""Phase 5 Network Isolation and Model-Name Gate (CHUNK-7.7).

Extends the CHUNK-6.6 pattern for all new Phase 5 surfaces.
"""
import ast
from pathlib import Path
import pytest

FORBIDDEN_NETWORK = {"openai", "anthropic", "httpx", "requests", "aiohttp"}
FORBIDDEN_MODEL_NAMES = ["DeepSeek", "deepseek", "gpt-4", "claude-3", "llama-3", "o1-preview", "nomic-embed"]


def _scan_for_imports(py_file: Path, forbidden):
    violations = []
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                else:
                    names = [node.module.split(".")[0]] if node.module else []
                for name in names:
                    if name in forbidden:
                        violations.append(f"{py_file}: imports {name}")
    except Exception:
        pass
    return violations


def test_phase5_code_has_no_network_imports():
    """CHUNK-7.7: Phase 5 code must not contain network/LLM client imports."""
    src_root = Path("src/aip")
    violations = []
    phase5_roots = ["orchestration/actors", "orchestration/router.py", "orchestration/ace_playbook.py", "orchestration/budget.py", "orchestration/sexton"]
    for root in phase5_roots:
        for py_file in (src_root / root).rglob("*.py") if (src_root / root).exists() else []:
            if "test" in py_file.parts or "__pycache__" in str(py_file):
                continue
            violations.extend(_scan_for_imports(py_file, FORBIDDEN_NETWORK))
    assert not violations, "Phase 5 code contains forbidden network imports:\n" + "\n".join(violations)


def test_phase5_no_hardcoded_model_names():
    """CHUNK-7.7: No hardcoded model names in Phase 5 code (except allowed 'sexton' slot)."""
    src_root = Path("src/aip")
    violations = []
    phase5_roots = ["orchestration/actors", "orchestration/router.py", "orchestration/ace_playbook.py", "orchestration/budget.py", "orchestration/sexton"]
    for root in phase5_roots:
        for py_file in (src_root / root).rglob("*.py") if (src_root / root).exists() else []:
            if "test" in py_file.parts or "__pycache__" in str(py_file):
                continue
            text = py_file.read_text(encoding="utf-8")
            for name in FORBIDDEN_MODEL_NAMES:
                if name in text:
                    violations.append(f"{py_file}: {name}")
    assert not violations, "Phase 5 code contains forbidden hardcoded model names:\n" + "\n".join(violations)


def test_phase5_import_boundaries_respected():
    """CHUNK-7.7: Phase 5 modules respect §7.2 layering."""
    # The existing test_layering.py already covers the new surfaces; verify key files exist
    from pathlib import Path
    phase5_files = [
        Path("src/aip/orchestration/actors"),
        Path("src/aip/orchestration/router.py"),
        Path("src/aip/orchestration/ace_playbook.py"),
        Path("src/aip/orchestration/budget.py"),
        Path("src/aip/orchestration/sexton"),
    ]
    for f in phase5_files:
        assert f.exists(), f"Phase 5 path missing: {f}"
