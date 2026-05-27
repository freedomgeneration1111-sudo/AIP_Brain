"""Phase 2 extension of the network isolation and model-name gates (CHUNK-4.8)."""
import ast
from pathlib import Path

import pytest


def test_phase2_code_has_no_network_imports():
    """CHUNK-4.8: New Phase 2 code must not contain network/LLM client imports."""
    forbidden = {"openai", "anthropic", "httpx", "requests", "aiohttp"}

    src_root = Path("src/aip")
    violations = []

    for py_file in src_root.rglob("*.py"):
        if "test" in py_file.parts:
            continue
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
                        violations.append(f"{py_file}: imports {name}")

    assert not violations, "Phase 2 code contains forbidden network imports:\n" + "\n".join(violations)


def test_phase2_code_has_no_hardcoded_models():
    """CHUNK-4.8: New Phase 2 code must not hardcode model names."""
    forbidden_keywords = ["DeepSeek", "Qwen", "Claude", "GPT-", "gpt-", "sonnet", "o1-"]

    src_root = Path("src/aip")
    violations = []

    for py_file in src_root.rglob("*.py"):
        if "test" in py_file.parts:
            continue
        text = py_file.read_text(encoding="utf-8")
        for kw in forbidden_keywords:
            if kw in text:
                violations.append(f"{py_file}: contains '{kw}'")

    assert not violations, "Phase 2 code contains hardcoded model names:\n" + "\n".join(violations)
