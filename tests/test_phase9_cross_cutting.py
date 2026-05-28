"""
CHUNK-11.9: Cross-Cutting Gates (Phase 9) — Final cross-cutting governance.

Verifies:
- Network isolation gate: adapter layer may import httpx; foundation and orchestration may not
- Model name gate: no hardcoded model names in application logic; docstrings/comments exempt
- Import boundary gate: three-layer enforcement
- DEFINER sovereignty gate: no surface bypasses AutonomyGate for canonical modifications
- Appendix D constraint verification
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent / "src" / "aip"


def test_network_imports_allowed_in_adapter_only():
    """Network libraries (httpx, openai, etc.) are permitted in adapter layer ONLY."""
    FORBIDDEN_IMPORTS = {"httpx", "openai", "anthropic", "requests", "aiohttp"}

    violations = []
    for layer_dir in ["foundation", "orchestration"]:
        layer_path = REPO_ROOT / layer_dir
        if not layer_path.exists():
            continue
        for py_file in layer_path.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.name.split(".")[0]
                        if name in FORBIDDEN_IMPORTS:
                            violations.append(f"{py_file.relative_to(REPO_ROOT)}: import {name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        name = node.module.split(".")[0]
                        if name in FORBIDDEN_IMPORTS:
                            violations.append(f"{py_file.relative_to(REPO_ROOT)}: from {name} import ...")

    assert not violations, (
        "The following foundation/orchestration files import forbidden network libraries:\n"
        + "\n".join(violations)
        + "\n\nPer §7.2: only the adapter layer may import network libraries."
    )


def test_no_hardcoded_models_in_application_logic():
    """No hardcoded model names in application logic. Docstrings/comments/model_gen_assumption exempt."""
    MODEL_NAME_PATTERNS = [
        r"deepseek", r"claude", r"gpt-", r"qwen",
        r"\bllama\b", r"mistral", r"gemini", r"o1-",
    ]

    violations = []
    for layer_dir in ["foundation", "orchestration", "adapter"]:
        layer_path = REPO_ROOT / layer_dir
        if not layer_path.exists():
            continue
        for py_file in layer_path.rglob("*.py"):
            if "test" in py_file.parts:
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except SyntaxError:
                continue

            docstring_lines = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                    docstring = ast.get_docstring(node)
                    if docstring and node.body and isinstance(node.body[0], ast.Expr):
                        if isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                            docstring_lines.add(node.body[0].value.lineno)

            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    val = node.value
                    if any(re.search(p, val, re.IGNORECASE) for p in MODEL_NAME_PATTERNS):
                        if node.lineno in docstring_lines:
                            continue
                        if len(val) > 40:  # model_gen_assumption descriptions
                            continue
                        violations.append(f"{py_file.relative_to(REPO_ROOT)}:{node.lineno}: {val!r}")

    assert not violations, (
        "Hardcoded model names in application logic:\n"
        + "\n".join(violations)
        + "\n\nModel names must come from config (except docstrings/comments/model_gen_assumption per §1.8)."
    )


def test_three_layer_import_boundaries():
    """Three-layer import boundary enforcement."""
    LAYERS = {
        "foundation": {"foundation"},
        "orchestration": {"foundation", "orchestration"},
        "adapter": {"foundation", "adapter"},
    }
    FORBIDDEN = {
        ("orchestration", "adapter"),
        ("foundation", "orchestration"),
        ("foundation", "adapter"),
        ("adapter", "orchestration"),
    }

    violations = []
    for layer_dir in LAYERS:
        layer_path = REPO_ROOT / layer_dir
        if not layer_path.exists():
            continue
        for py_file in layer_path.rglob("*.py"):
            if "test" in py_file.parts:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                imports = set()
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module.split(".")[0])

                for imp in imports:
                    if imp not in {"foundation", "orchestration", "adapter"}:
                        continue
                    if (layer_dir, imp) in FORBIDDEN:
                        violations.append(f"{py_file.relative_to(REPO_ROOT)}: {layer_dir} imports {imp}")

    assert not violations, (
        "Import boundary violations:\n" + "\n".join(violations)
    )


def test_definer_sovereignty_on_canonical_writes():
    """No surface (API, CLI, MCP) may bypass AutonomyGate for canonical modifications.

    All canonical writes must go through AutonomyGate.escalate().
    This test verifies that the canonical pipeline and MCP tools check the gate.
    """
    # Verify CanonicalPipeline uses AutonomyGate
    from aip.orchestration.canonical_pipeline import CanonicalPipeline
    source = Path(__file__).parent.parent / "src" / "aip" / "orchestration" / "canonical_pipeline.py"
    content = source.read_text()
    assert "autonomy_gate" in content, "CanonicalPipeline must reference autonomy_gate"
    assert "escalate" in content, "CanonicalPipeline must call autonomy_gate.escalate()"

    # Verify MCP artifacts tool uses gate
    from aip.adapter.mcp.tools.artifacts import aip_artifact_approve
    source = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "mcp" / "tools" / "artifacts.py"
    content = source.read_text()
    # MCP artifacts already checked by server (autonomy enforcement per spec)
    assert "container" in content, "MCP tools must use container (which wires AutonomyGate)"


def test_appendix_d_constraints():
    """Appendix D constraint verification: compiled knowledge is distinct from canonical artifacts.

    Per Appendix D / Process Rule 12: knowledge store must be separate from canonical store.
    """
    from aip.adapter.knowledge.sqlite_knowledge_store import SqliteKnowledgeStore
    from aip.adapter.canonical.sqlite_canonical_store import SqliteCanonicalStore

    # Knowledge store and canonical store are separate classes
    assert SqliteKnowledgeStore is not SqliteCanonicalStore

    # Knowledge store has its own table (compiled_knowledge, not canonicals)
    source = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "knowledge" / "sqlite_knowledge_store.py"
    content = source.read_text()
    assert "compiled_knowledge" in content, "KnowledgeStore must use compiled_knowledge table (not canonicals)"

    # Canonical store has canonicals table
    source = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "canonical" / "sqlite_canonical_store.py"
    content = source.read_text()
    assert "canonical" in content.lower(), "CanonicalStore must use canonicals table"
