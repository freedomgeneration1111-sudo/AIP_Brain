"""
CHUNK-11.0b: Layer Violation Remediation — Phase 9 gate tests.

Verifies that the three-layer import discipline is enforced:
- foundation has zero imports from orchestration or adapter
- orchestration imports from foundation only (via proxy for model provider)
- adapter imports from foundation only
- full_l3a_evaluation lives in orchestration, not foundation
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent / "src" / "aip"


def _get_imports(file_path: Path) -> set[str]:
    """Extract all aip-internal imports from a Python file."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name.startswith("aip."):
                    # Extract the layer: aip.foundation.xxx -> foundation
                    parts = name.split(".")
                    if len(parts) >= 2:
                        imports.add(parts[1])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("aip."):
                parts = node.module.split(".")
                if len(parts) >= 2:
                    imports.add(parts[1])
    return imports


def test_foundation_no_orchestration_imports():
    """foundation/ must not import from orchestration/ or adapter/."""
    foundation_dir = REPO_ROOT / "foundation"
    if not foundation_dir.exists():
        pytest.skip("No foundation directory")

    violations = []
    for py_file in foundation_dir.rglob("*.py"):
        if "test" in py_file.parts:
            continue
        imports = _get_imports(py_file)
        for imp in imports:
            if imp in ("orchestration", "adapter"):
                violations.append(
                    f"{py_file.relative_to(REPO_ROOT)} imports from '{imp}' (foundation must stay isolated)",
                )

    assert not violations, "foundation/ has upward imports (must import only stdlib + itself):\n" + "\n".join(
        violations,
    )


def test_orchestration_no_adapter_imports():
    """orchestration/ must not import from adapter/ directly.

    Orchestration uses model_provider_proxy to access ModelSlotResolver
    via a Protocol, avoiding direct adapter dependency.
    """
    orchestration_dir = REPO_ROOT / "orchestration"
    if not orchestration_dir.exists():
        pytest.skip("No orchestration directory")

    violations = []
    for py_file in orchestration_dir.rglob("*.py"):
        if "test" in py_file.parts:
            continue
        imports = _get_imports(py_file)
        for imp in imports:
            if imp == "adapter":
                # Allow model_provider_proxy (lazy import is acceptable)
                # but flag any top-level adapter import
                violations.append(
                    f"{py_file.relative_to(REPO_ROOT)} imports from 'adapter' "
                    f"(use orchestration.model_provider_proxy instead)",
                )

    assert not violations, "orchestration/ has direct adapter imports (use model_provider_proxy):\n" + "\n".join(
        violations,
    )


def test_adapter_may_import_foundation_and_orchestration():
    """adapter/ may import from foundation/ but should not import orchestration/."""
    adapter_dir = REPO_ROOT / "adapter"
    if not adapter_dir.exists():
        pytest.skip("No adapter directory")

    violations = []
    for py_file in adapter_dir.rglob("*.py"):
        if "test" in py_file.parts:
            continue
        imports = _get_imports(py_file)
        for imp in imports:
            if imp == "orchestration":
                violations.append(
                    f"{py_file.relative_to(REPO_ROOT)} imports from 'orchestration' "
                    f"(adapter should not depend on orchestration)",
                )

    assert not violations, "adapter/ has orchestration imports (should not depend on orchestration):\n" + "\n".join(
        violations,
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
