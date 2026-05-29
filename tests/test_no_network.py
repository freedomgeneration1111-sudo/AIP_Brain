"""
Cross-cutting governance test: No network libraries in production code.

Part of the no-network governance test suite.
Scans foundation/, orchestration/, and adapter/ for imports of
httpx, openai, anthropic (and similar network/LLM client libraries).
"""

import ast
from pathlib import Path

FORBIDDEN_IMPORTS = {
    "httpx",
    "openai",
    "anthropic",
    "requests",
    "aiohttp",
}

PRODUCTION_DIRS = ["foundation", "orchestration", "adapter"]


def _get_python_files(base: Path) -> list[Path]:
    files = []
    for d in PRODUCTION_DIRS:
        dir_path = base / d
        if dir_path.exists():
            files.extend(dir_path.rglob("*.py"))
    return files


def test_no_network_imports_in_production_code():
    """
    Foundation and orchestration code must not import network/LLM client libraries directly.
    Adapter code may import them (per §7.2: adapter is the correct place for HTTP calls).
    All such access must go through the configured model abstraction layer.
    """
    repo_root = Path(__file__).parent.parent / "src" / "aip"
    py_files = _get_python_files(repo_root)

    violations = []

    for py_file in py_files:
        # Adapter layer is allowed to use network libraries per §7.2
        if "adapter" in str(py_file):
            continue

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.split(".")[0]
                    if name in FORBIDDEN_IMPORTS:
                        violations.append(f"{py_file.relative_to(repo_root)}: import {name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    name = node.module.split(".")[0]
                    if name in FORBIDDEN_IMPORTS:
                        violations.append(f"{py_file.relative_to(repo_root)}: from {name} import ...")

    assert not violations, (
        "The following foundation/orchestration files import forbidden network libraries:\n" + "\n".join(violations)
    )
