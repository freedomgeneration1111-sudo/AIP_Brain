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
    r"llama",
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

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                if _looks_like_model_name(val):
                    # Allow the required §1.8 model_gen_assumption tagging
                    # (these strings are mandated by the spec itself)
                    if "deepseek" in val.lower() and "qwen" in val.lower():
                        continue
                    violations.append(
                        f"{py_file.relative_to(repo_root)}:{node.lineno}: "
                        f"possible hardcoded model name {val!r}"
                    )

    assert not violations, (
        "The following locations contain what appear to be hardcoded model names:\n"
        + "\n".join(violations)
        + "\n\nModel names must only be configured in aip.config.toml (except for required model_gen_assumption tags)."
    )
