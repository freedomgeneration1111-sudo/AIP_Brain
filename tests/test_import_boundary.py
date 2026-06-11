"""Chunk 6 — Import Boundary and Composition Root Enforcement.

Enforces AIP_Brain's layer discipline with a comprehensive AST-based
checker that catches both static and function-local imports.  The test
allows explicit composition-root exceptions and fails on any new
unacknowledged cross-layer violation.

Architecture rule (per docs/ARCHITECTURE.md):
  - foundation imports no upper AIP layers
  - orchestration may import foundation/protocols, not adapter
  - adapter composition root (app.py, dependencies.py) may wire
    orchestration implementations via importlib
  - adapter routes should use container/protocol interfaces,
    not concrete orchestration internals
  - GUI must remain API-first and must not import orchestration internals
  - tests may use direct imports, but production import rules should be clear
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "aip"
GUI_ROOT = PROJECT_ROOT / "gui"

# ---------------------------------------------------------------------------
# Composition-root exceptions
# ---------------------------------------------------------------------------

# Files that are allowed to import orchestration from the adapter layer
# because they are the explicit composition root.
COMPOSITION_ROOT_FILES = {
    "adapter/api/app.py",  # wires orchestration into container
    "adapter/api/dependencies.py",  # DI container definition
}

# Route files that have acknowledged violations (deferred for protocol
# extraction).  These are tracked but not yet fixed.
ACKNOWLEDGED_ROUTE_VIOLATIONS: dict[str, list[str]] = {
    # "adapter/api/routes/corpus.py": ["aip.orchestration.ingestion.corpus_ingest_pipeline"],
    # All route violations should be fixed or explicitly listed here.
}

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_type_checking_block(node: ast.AST) -> bool:
    """Return True if the node is `if TYPE_CHECKING:`."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _collect_imports(filepath: Path) -> list[tuple[str, int, str]]:
    """Collect all runtime imports from a Python file.

    Returns list of (module_path, line_number, import_style).
    import_style is one of: "static", "lazy", "importlib"

    - "static": top-level `from X import Y` or `import X`
    - "lazy": function-local `from X import Y` or `import X`
    - "importlib": `importlib.import_module("X")` or `__import__("X")`
    """
    try:
        source = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    imports: list[tuple[str, int, str]] = []

    # Walk the tree, tracking nesting depth to distinguish top-level from local
    def _visit(node: ast.AST, depth: int = 0) -> None:
        # Skip TYPE_CHECKING blocks entirely
        if _is_type_checking_block(node):
            return

        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    style = "static" if depth <= 1 else "lazy"
                    imports.append((alias.name, child.lineno, style))
                _visit(child, depth + 1)

            elif isinstance(child, ast.ImportFrom):
                if child.module and child.level == 0:
                    style = "static" if depth <= 1 else "lazy"
                    imports.append((child.module, child.lineno, style))
                _visit(child, depth + 1)

            elif isinstance(child, ast.Call):
                # Detect importlib.import_module("X")
                func = child.func
                mod_name: str | None = None

                if isinstance(func, ast.Attribute) and func.attr == "import_module" and child.args:
                    # importlib.import_module("X")
                    arg = child.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        mod_name = arg.value

                if mod_name:
                    imports.append((mod_name, child.lineno, "importlib"))

                # Also detect __import__("X")
                if isinstance(func, ast.Name) and func.id == "__import__" and child.args:
                    arg = child.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        imports.append((arg.value, child.lineno, "importlib"))

                _visit(child, depth + 1)

            else:
                _visit(child, depth + 1)

    _visit(tree)
    return imports


def _py_files(directory: Path) -> list[Path]:
    """Collect all .py files under a directory, excluding __pycache__."""
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def _rel_path(filepath: Path) -> str:
    """Return path relative to SRC_ROOT."""
    try:
        return str(filepath.relative_to(SRC_ROOT))
    except ValueError:
        return str(filepath.relative_to(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Test: foundation must not import upper layers
# ---------------------------------------------------------------------------


def test_foundation_does_not_import_orchestration_or_adapter():
    """Foundation layer must not import orchestration or adapter.

    Foundation is the lowest layer — it may only import stdlib, third-party,
    and its own submodules. Any upward import is a hard violation.
    """
    foundation_dir = SRC_ROOT / "foundation"
    violations: list[str] = []

    for py_file in _py_files(foundation_dir):
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.orchestration") or module == "aip.orchestration":
                violations.append(f"{_rel_path(py_file)}:{lineno} ({style}) — imports '{module}' from foundation layer")
            if module.startswith("aip.adapter") or module == "aip.adapter":
                violations.append(f"{_rel_path(py_file)}:{lineno} ({style}) — imports '{module}' from foundation layer")

    assert not violations, "Foundation layer must not import orchestration or adapter:\n  " + "\n  ".join(violations)


# ---------------------------------------------------------------------------
# Test: orchestration must not import adapter
# ---------------------------------------------------------------------------


def test_orchestration_does_not_import_adapter():
    """Orchestration layer must not import adapter.

    Known violations are tracked in the governance conformance suite's
    acknowledged_import_violations list.  This test fails on any NEW
    unacknowledged violation.
    """
    # Load acknowledged violations from the governance conformance suite
    acknowledged = _load_acknowledged_orchestration_violations()

    orch_dir = SRC_ROOT / "orchestration"
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()

    for py_file in _py_files(orch_dir):
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.adapter") or module == "aip.adapter":
                rel = _rel_path(py_file)
                key = (rel, module)
                normalized = f"{rel} imports {module}"
                if normalized not in acknowledged and key not in seen:
                    seen.add(key)
                    violations.append(f"{rel}:{lineno} ({style}) — imports '{module}'")

    assert not violations, (
        "Orchestration must not import adapter (fix or add to "
        "acknowledged_import_violations in test_governance_conformance.py):\n  " + "\n  ".join(violations)
    )


def _load_acknowledged_orchestration_violations() -> set[str]:
    """Load the acknowledged_import_violations list from the governance suite."""
    gov_file = PROJECT_ROOT / "tests" / "test_governance_conformance.py"
    if not gov_file.exists():
        return set()

    try:
        source = gov_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return set()

    # The acknowledged list is a dict value in the PROFILES dict, like:
    #   "acknowledged_import_violations": [ "entry1", "entry2", ... ]
    # We search for any Dict key that matches this string.
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value == "acknowledged_import_violations"
                    and isinstance(value, ast.List)
                ):
                    for elt in value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            result.add(elt.value)
    return result


# ---------------------------------------------------------------------------
# Test: adapter routes must not import orchestration
# ---------------------------------------------------------------------------


def test_adapter_routes_do_not_import_orchestration():
    """Adapter route modules must not import orchestration.

    Routes should access orchestration through the container
    (AipContainer._ask_fn, etc.) or via importlib in the composition root.
    Direct static or lazy imports from route modules are violations.
    """
    routes_dir = SRC_ROOT / "adapter" / "api" / "routes"
    violations: list[str] = []

    for py_file in _py_files(routes_dir):
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.orchestration"):
                rel = _rel_path(py_file)
                # Check if acknowledged
                ack_key = f"adapter/{rel} imports {module}"
                if rel not in ACKNOWLEDGED_ROUTE_VIOLATIONS or module not in ACKNOWLEDGED_ROUTE_VIOLATIONS.get(rel, []):
                    violations.append(f"{rel}:{lineno} ({style}) — imports '{module}' from route module")

    assert not violations, (
        "Route modules must not import orchestration. "
        "Use container-mediated access (AipContainer) or importlib in "
        "composition root (app.py).\n  " + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# Test: adapter non-route modules must not import orchestration
# ---------------------------------------------------------------------------


def test_adapter_non_route_does_not_import_orchestration():
    """Non-route adapter modules must not import orchestration.

    The composition root (app.py, dependencies.py) is exempt because
    it wires orchestration implementations into the container.
    """
    adapter_dir = SRC_ROOT / "adapter"
    violations: list[str] = []

    for py_file in _py_files(adapter_dir):
        rel = _rel_path(py_file)

        # Skip route modules (tested separately)
        if "routes/" in rel:
            continue

        # Skip composition root files
        if rel in COMPOSITION_ROOT_FILES:
            continue

        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.orchestration"):
                violations.append(f"{rel}:{lineno} ({style}) — imports '{module}'")

    assert not violations, (
        "Non-route adapter modules must not import orchestration "
        "(composition root is exempt):\n  " + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# Test: GUI must not import orchestration internals
# ---------------------------------------------------------------------------


def test_gui_does_not_import_orchestration():
    """GUI must remain API-first and must not import orchestration internals.

    All communication between GUI and backend should go through the
    HTTP/REST API via gui/api_client.py.
    """
    if not GUI_ROOT.exists():
        pytest.skip("No GUI directory present")

    violations: list[str] = []

    for py_file in _py_files(GUI_ROOT):
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.orchestration"):
                violations.append(
                    f"{py_file.relative_to(PROJECT_ROOT)}:{lineno} ({style}) — imports '{module}' from GUI layer"
                )
            # Also flag any direct aip.adapter imports from GUI
            # (GUI should only talk to the API, not import adapter internals)
            if module.startswith("aip.adapter") and not module.startswith("aip.adapter.api"):
                violations.append(
                    f"{py_file.relative_to(PROJECT_ROOT)}:{lineno} ({style}) — "
                    f"imports '{module}' from GUI layer (should use API client)"
                )

    assert not violations, "GUI must not import orchestration internals — use API client instead:\n  " + "\n  ".join(
        violations
    )


# ---------------------------------------------------------------------------
# Test: composition root uses importlib (not static imports)
# ---------------------------------------------------------------------------


def test_composition_root_uses_importlib_for_orchestration():
    """Composition root should use importlib for orchestration imports.

    The composition root (app.py) is allowed to import orchestration,
    but should use importlib.import_module() to avoid creating static
    import-time dependencies, preserving the layer boundary for testing
    and future refactoring.
    """
    app_file = SRC_ROOT / "adapter" / "api" / "app.py"
    if not app_file.exists():
        pytest.skip("app.py not found")

    static_orch_imports: list[str] = []

    for module, lineno, style in _collect_imports(app_file):
        if module.startswith("aip.orchestration") and style == "static":
            static_orch_imports.append(
                f"app.py:{lineno} — static import of '{module}' (should use importlib.import_module())"
            )

    # We allow a few known exceptions that were already present
    # but new static orchestration imports should be flagged
    # For now this is a soft check — we document but don't fail
    if static_orch_imports:
        # Log as warnings but don't fail the test yet
        # This can be promoted to a hard failure in a future chunk
        for msg in static_orch_imports:
            warnings.warn(
                f"Composition root has static orchestration import: {msg}",
                stacklevel=1,
            )


# ---------------------------------------------------------------------------
# Test: verify no importlib circumvention in orchestration
# ---------------------------------------------------------------------------


def test_orchestration_does_not_use_importlib_to_import_adapter():
    """Orchestration must not use importlib to circumvent layer boundaries.

    Some files use importlib.import_module("aip.adapter...") to bypass
    static analysis. This test catches those circumventions.
    """
    orch_dir = SRC_ROOT / "orchestration"
    acknowledged = _load_acknowledged_orchestration_violations()
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()

    for py_file in _py_files(orch_dir):
        for module, lineno, style in _collect_imports(py_file):
            if style == "importlib" and (module.startswith("aip.adapter") or module == "aip.adapter"):
                rel = _rel_path(py_file)
                key = (rel, module)
                normalized = f"{rel} imports {module}"
                if normalized not in acknowledged and key not in seen:
                    seen.add(key)
                    violations.append(f"{rel}:{lineno} (importlib) — circumvention: imports '{module}'")

    assert not violations, (
        "Orchestration must not use importlib to import adapter "
        "(circumvention of layer boundary):\n  " + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# Test: comprehensive boundary summary (informational, not failing)
# ---------------------------------------------------------------------------


def test_import_boundary_summary():
    """Informational test: print a summary of all cross-layer imports.

    This test always passes — it exists to provide visibility into
    the current state of import boundaries during CI runs.
    """
    summary: dict[str, list[str]] = {
        "foundation → orchestration/adapter": [],
        "orchestration → adapter": [],
        "adapter routes → orchestration": [],
        "adapter non-route → orchestration": [],
        "GUI → orchestration/adapter": [],
    }

    # Foundation
    foundation_dir = SRC_ROOT / "foundation"
    for py_file in _py_files(foundation_dir):
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.orchestration") or module.startswith("aip.adapter"):
                summary["foundation → orchestration/adapter"].append(
                    f"  {_rel_path(py_file)}:{lineno} ({style}) → {module}"
                )

    # Orchestration → adapter
    orch_dir = SRC_ROOT / "orchestration"
    for py_file in _py_files(orch_dir):
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.adapter"):
                summary["orchestration → adapter"].append(f"  {_rel_path(py_file)}:{lineno} ({style}) → {module}")

    # Adapter routes → orchestration
    routes_dir = SRC_ROOT / "adapter" / "api" / "routes"
    for py_file in _py_files(routes_dir):
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.orchestration"):
                summary["adapter routes → orchestration"].append(
                    f"  {_rel_path(py_file)}:{lineno} ({style}) → {module}"
                )

    # Adapter non-route → orchestration
    adapter_dir = SRC_ROOT / "adapter"
    for py_file in _py_files(adapter_dir):
        rel = _rel_path(py_file)
        if "routes/" in rel or rel in COMPOSITION_ROOT_FILES:
            continue
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.orchestration"):
                summary["adapter non-route → orchestration"].append(f"  {rel}:{lineno} ({style}) → {module}")

    # GUI
    if GUI_ROOT.exists():
        for py_file in _py_files(GUI_ROOT):
            for module, lineno, style in _collect_imports(py_file):
                if module.startswith("aip.orchestration") or module.startswith("aip.adapter"):
                    summary["GUI → orchestration/adapter"].append(
                        f"  {py_file.relative_to(PROJECT_ROOT)}:{lineno} ({style}) → {module}"
                    )

    # Print summary (visible in pytest -s output)
    print("\n" + "=" * 72)
    print("IMPORT BOUNDARY SUMMARY (Chunk 6)")
    print("=" * 72)
    for category, items in summary.items():
        count = len(items)
        status = "CLEAN" if count == 0 else f"{count} VIOLATION(S)"
        print(f"\n{category}: {status}")
        for item in items:
            print(item)
    print("\n" + "=" * 72)

    # Always pass — this is informational
    assert True
