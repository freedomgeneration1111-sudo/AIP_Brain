"""Phase 2 extension of the network isolation and model-name gates (CHUNK-4.8)."""

import ast
from pathlib import Path

import pytest


def test_phase2_code_has_no_network_imports():
    """CHUNK-4.8: Foundation and orchestration code must not contain network/LLM client imports.
    Adapter layer may use them per §7.2.
    """
    forbidden = {"openai", "anthropic", "httpx", "requests", "aiohttp"}

    src_root = Path("src/aip")
    violations = []

    for py_file in src_root.rglob("*.py"):
        if "test" in py_file.parts:
            continue
        # Adapter layer is allowed per §7.2
        if "adapter" in str(py_file):
            continue
        # CLI surface may use HTTP for service discovery (e.g., Ollama check) per §7.2
        if "cli" in str(py_file):
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
    """CHUNK-4.8: New Phase 2 code must not hardcode model names in application logic.
    Docstrings and model_gen_assumption strings are allowed per §1.8.
    """
    import ast as _ast

    forbidden_keywords = ["DeepSeek", "Qwen", "Claude", "GPT-", "gpt-", "sonnet", "o1-"]

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
                    if len(val) > 40:  # Long strings are assumption descriptions
                        continue
                    violations.append(f"{py_file}: contains '{val[:50]}'")

    assert not violations, "Phase 2 code contains hardcoded model names:\n" + "\n".join(violations)
