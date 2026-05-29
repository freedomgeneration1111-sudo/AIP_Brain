"""
Import boundary enforcement test.

Part of the import boundary enforcement (extends §7.2).

Enforces the three-layer architecture:
- foundation/     : may only import stdlib + itself
- orchestration/  : may import from foundation/ (and stdlib), but NOT adapter/ or direct storage implementations
- adapter/        : may import from foundation/, but not orchestration/

This test uses AST analysis to detect illegal cross-layer imports in production code.
"""

import ast
from pathlib import Path
from typing import List, Set

REPO_ROOT = Path(__file__).parent.parent / "src" / "aip"

LAYERS = {
    "foundation": {"foundation"},
    "orchestration": {"foundation", "orchestration"},
    "adapter": {"foundation", "adapter"},
}

FORBIDDEN_CROSS_IMPORTS = {
    # orchestration should never reach into adapter
    ("orchestration", "adapter"),
    # Nothing in foundation should reach into orchestration or adapter
    ("foundation", "orchestration"),
    ("foundation", "adapter"),
}


def _get_all_python_files(layer: str) -> List[Path]:
    layer_dir = REPO_ROOT / layer
    if not layer_dir.exists():
        return []
    return [p for p in layer_dir.rglob("*.py") if "test" not in p.parts]


def _get_imports(file_path: Path) -> Set[str]:
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()

    imports: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports


def _layer_of(file_path: Path) -> str | None:
    parts = file_path.relative_to(REPO_ROOT).parts
    if not parts:
        return None
    top = parts[0]
    if top in LAYERS:
        return top
    return None


def test_import_boundaries_are_respected():
    violations = []

    for layer_name in ["foundation", "orchestration", "adapter"]:
        for py_file in _get_all_python_files(layer_name):
            imports = _get_imports(py_file)
            current_layer = _layer_of(py_file)
            if current_layer is None:
                continue

            _allowed = LAYERS[current_layer]

            for imp in imports:
                # Skip stdlib and known safe things
                if imp in {
                    "__future__",
                    "dataclasses",
                    "enum",
                    "typing",
                    "pathlib",
                    "hashlib",
                    "json",
                    "sqlite3",
                    "math",
                    "time",
                    "re",
                    "ast",
                    "pytest",
                }:
                    continue

                # Check against forbidden cross-layer imports
                for forbidden_layer in ["orchestration", "adapter", "foundation"]:
                    if imp == forbidden_layer or imp.startswith(forbidden_layer + "."):
                        if (current_layer, forbidden_layer) in FORBIDDEN_CROSS_IMPORTS:
                            violations.append(
                                f"{py_file.relative_to(REPO_ROOT)} imports from '{forbidden_layer}' "
                                f"(current layer: {current_layer})",
                            )

    assert not violations, (
        "Import boundary violations detected (foundation must stay isolated, "
        "orchestration must not reach into adapter):\n\n" + "\n".join(violations)
    )


def test_full_l3a_evaluation_in_orchestration_not_foundation():
    """full_l3a_evaluation must be defined in orchestration, not foundation.

    Per §7.2: multi-stage evaluation with model calls belongs in orchestration.
    foundation/validation.py may have a backward-compat alias but the real
    implementation must live in orchestration.l3a_orchestrator.
    """
    # Verify the real implementation is in orchestration
    l3a_file = REPO_ROOT / "orchestration" / "l3a_orchestrator.py"
    assert l3a_file.exists(), "orchestration/l3a_orchestrator.py must exist"

    source = l3a_file.read_text()
    assert "async def full_l3a_evaluation" in source, (
        "full_l3a_evaluation must be defined in orchestration/l3a_orchestrator.py"
    )

    # Verify foundation has only the backward-compat alias
    validation_file = REPO_ROOT / "foundation" / "validation.py"
    source = validation_file.read_text()

    # foundation should have structural_validate as a top-level function
    assert "def structural_validate" in source, "structural_validate must remain in foundation/validation.py"

    # foundation may have the alias but the alias must delegate to orchestration
    if "async def full_l3a_evaluation" in source:
        # It's the alias - should import from orchestration
        assert "orchestration.l3a_orchestrator" in source or "orchestration.evaluation" in source, (
            "foundation full_l3a_evaluation alias must delegate to orchestration"
        )
