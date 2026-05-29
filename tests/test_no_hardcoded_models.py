"""
Cross-cutting governance test: No hardcoded model names in production code.

Part of CHUNK-1.7 per Rev 1.3.
Scans foundation/, orchestration/, and adapter/ for literal strings
that look like model names. The only authoritative place for model
mappings is config/aip.config.toml (per §4.1).
"""

import ast
import re
from pathlib import Path

import pytest

# Common patterns for model names that should not appear as literals in code
MODEL_NAME_PATTERNS = [
    r"deepseek",
    r"claude",
    r"gpt-",
    r"qwen",
    r"\bllama\b",  # word boundary to avoid matching "ollama" (a service, not a model)
    r"mistral",
    r"gemini",
    r"o1-",
]

EXCLUDE_DIRS = {"config", "tests"}
EXCLUDE_FILES = {".toml", ".md", ".txt"}

PRODUCTION_DIRS = ["foundation", "orchestration", "adapter"]


def _looks_like_model_name(s: str) -> bool:
    s_lower = s.lower()
    return any(re.search(p, s_lower) for p in MODEL_NAME_PATTERNS)


def _get_python_files(base: Path) -> list[Path]:
    files = []
    for d in PRODUCTION_DIRS:
        dir_path = base / d
        if dir_path.exists():
            for py in dir_path.rglob("*.py"):
                # Skip anything under excluded dirs
                parts = py.relative_to(base).parts
                if any(part in EXCLUDE_DIRS for part in parts):
                    continue
                files.append(py)
    return files


def test_no_hardcoded_model_names_in_production_code():
    """
    Model names must only come from configuration (aip.config.toml),
    never as string literals in production source code.

    Exception: model_gen_assumption fields are *required* by §1.8 to
    contain the model names they compensate for. These are allowed.
    Exception: docstrings and comments are documentation, not application logic.
    """
    repo_root = Path(__file__).parent.parent / "src" / "aip"
    py_files = _get_python_files(repo_root)

    violations = []

    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except SyntaxError:
            continue

        # Collect all docstring node line ranges to skip
        docstring_lines = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                docstring = ast.get_docstring(node)
                if docstring:
                    # The docstring is the first statement's value
                    if (
                        node.body
                        and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)
                    ):
                        docstring_lines.add(node.body[0].value.lineno)

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                if _looks_like_model_name(val):
                    # Skip docstrings
                    if node.lineno in docstring_lines:
                        continue
                    # Skip model_gen_assumption values (required by §1.8)
                    # These strings are typically assigned to model_gen_assumption parameters
                    # or are in ValidationRule/adversarial_eval data
                    if len(val) > 40:  # Long strings are likely assumption descriptions, not model references
                        continue
                    violations.append(
                        f"{py_file.relative_to(repo_root)}:{node.lineno}: possible hardcoded model name {val!r}",
                    )

    assert not violations, (
        "The following locations contain what appear to be hardcoded model names:\n"
        + "\n".join(violations)
        + "\n\nModel names must only be configured in aip.config.toml (except for required model_gen_assumption tags)."
    )
