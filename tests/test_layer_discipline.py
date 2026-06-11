"""Chunk 2 — Layer discipline import boundary test.

Static AST-based test that ensures:
  1. Orchestration never imports from aip.adapter.api.app
  2. Route modules never import from aip.orchestration at module level
  3. adapter layer never imports from orchestration at module level

This test is intentionally strict: any regression in layer discipline
will cause it to fail, preventing silent architecture erosion.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "aip"

# Modules that are allowed to use importlib.import_module() to reach across
# layer boundaries at runtime (not statically importable).  These are the
# known "wiring" modules that mediate between layers.
_IMPORTLIB_ALLOWED = {
    "aip/adapter/api/app.py",  # wires orchestration into container via importlib
    "aip/orchestration/embed_providers.py",  # lazy adapter imports
}


def _get_module_path(relative_path: str) -> Path:
    return SRC_ROOT / relative_path


def _collect_python_files(base: Path, prefix: str = "") -> list[tuple[str, Path]]:
    """Collect all .py files under base, returning (module_path, file_path) pairs."""
    results = []
    for root, _dirs, files in os.walk(base):
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            full = Path(root) / f
            rel = full.relative_to(SRC_ROOT)
            module_path = str(rel)
            results.append((module_path, full))
    return results


def _extract_static_imports(filepath: Path) -> list[tuple[str, str, int]]:
    """Extract all static import targets from a Python file.

    Returns list of (module_name, symbol, line_number).
    Catches: `from X.Y import Z` and `import X.Y`.
    """
    try:
        source = filepath.read_text(encoding="utf-8")
    except Exception:
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append((module, alias.name, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, "", node.lineno))
    return imports


# ---------------------------------------------------------------------------
# Test 1: Orchestration must not import from adapter.api.app
# ---------------------------------------------------------------------------


def test_orchestration_does_not_import_adapter_api_app():
    """No orchestration module may import from aip.adapter.api.app.

    This was the primary layer violation: orchestration pulled
    _create_embedding_provider and _load_toml_config from the FastAPI
    app factory, creating a circular dependency. These functions now
    live in aip.adapter.embedding.factory and aip.config.loader.
    """
    orch_root = SRC_ROOT / "orchestration"
    violations = []

    for module_path, filepath in _collect_python_files(orch_root):
        for module, symbol, lineno in _extract_static_imports(filepath):
            # Check for direct imports from adapter.api.app
            if module == "aip.adapter.api.app" or module.startswith("aip.adapter.api.app."):
                violations.append(f"{module_path}:{lineno} — imports '{symbol}' from {module}")

    assert not violations, (
        "Orchestration must not import from aip.adapter.api.app. "
        "Use aip.adapter.embedding.factory or aip.config.loader instead.\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 2: Route modules must not import from orchestration at module level
# ---------------------------------------------------------------------------


def test_routes_do_not_import_orchestration():
    """No route module may import from aip.orchestration at module level.

    Routes should access orchestration functions through the container
    (AipContainer._ask_fn, etc.) or via importlib.import_module() for
    runtime-only access.
    """
    routes_root = SRC_ROOT / "adapter" / "api" / "routes"
    violations = []

    for module_path, filepath in _collect_python_files(routes_root):
        for module, symbol, lineno in _extract_static_imports(filepath):
            if module.startswith("aip.orchestration"):
                violations.append(f"{module_path}:{lineno} — imports '{symbol}' from {module}")

    assert not violations, (
        "Route modules must not import from aip.orchestration. "
        "Use container-mediated access (AipContainer._ask_fn, etc.) "
        "or importlib.import_module() for runtime access.\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 3: adapter (non-route) must not import from orchestration at module level
# ---------------------------------------------------------------------------


def test_adapter_does_not_import_orchestration():
    """Non-route adapter modules must not import from aip.orchestration at module level.

    The exception is aip.adapter.api.app which wires orchestration into
    the container using importlib.import_module() (runtime-only, not static).
    """
    adapter_root = SRC_ROOT / "adapter"
    violations = []

    for module_path, filepath in _collect_python_files(adapter_root):
        # Skip route modules (tested separately) and the app factory
        if "routes/" in module_path:
            continue
        if module_path in _IMPORTLIB_ALLOWED:
            continue

        for module, symbol, lineno in _extract_static_imports(filepath):
            if module.startswith("aip.orchestration"):
                violations.append(f"{module_path}:{lineno} — imports '{symbol}' from {module}")

    assert not violations, (
        "Adapter modules must not import from aip.orchestration. "
        "Use container-mediated access or importlib.import_module().\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 4: Verify canonical modules exist and are importable
# ---------------------------------------------------------------------------


def test_canonical_embedding_factory_exists():
    """aip.adapter.embedding.factory must exist and export create_embedding_provider."""
    factory_path = SRC_ROOT / "adapter" / "embedding" / "factory.py"
    assert factory_path.is_file(), f"Missing canonical module: {factory_path}"

    source = factory_path.read_text()
    assert "def create_embedding_provider" in source, "factory.py must define create_embedding_provider function"


def test_canonical_config_loader_exists():
    """aip.config.loader must exist and export load_toml_config and load_dotenv."""
    loader_path = SRC_ROOT / "config" / "loader.py"
    assert loader_path.is_file(), f"Missing canonical module: {loader_path}"

    source = loader_path.read_text()
    assert "def load_toml_config" in source, "loader.py must define load_toml_config"
    assert "def load_dotenv" in source, "loader.py must define load_dotenv"


def test_retrieval_protocols_exist():
    """aip.foundation.protocols.retrieval must exist with protocol definitions."""
    retrieval_path = SRC_ROOT / "foundation" / "protocols" / "retrieval.py"
    assert retrieval_path.is_file(), f"Missing protocol module: {retrieval_path}"

    source = retrieval_path.read_text()
    assert "AskStoresProtocol" in source, "retrieval.py must define AskStoresProtocol"
    assert "AskPipelineFn" in source, "retrieval.py must define AskPipelineFn"


# ---------------------------------------------------------------------------
# Test 5: Container has orchestration function attributes
# ---------------------------------------------------------------------------


def test_container_has_orchestration_function_refs():
    """AipContainer must have attributes for container-mediated orchestration access."""
    from aip.adapter.api.dependencies import AipContainer

    container = AipContainer(config={})
    assert hasattr(container, "_ask_fn")
    assert hasattr(container, "_ask_stores_class")
    assert hasattr(container, "_search_sources_fn")
    assert hasattr(container, "_sanitize_fts_query_fn")
    assert hasattr(container, "_ingest_conversation_fn")
    assert hasattr(container, "_ingest_file_fn")
    # Chunk 6: Container-mediated corpus ingest functions
    assert hasattr(container, "_corpus_ingest_config_class")
    assert hasattr(container, "_ingest_directory_to_corpus_fn")
    assert hasattr(container, "_ingest_file_to_corpus_fn")
    # Chunk 6: Container-mediated retrieval orchestrator access
    assert hasattr(container, "_get_orchestrator_cache_fn")
    assert hasattr(container, "_builtin_channels")
    # Chunk 6: Container-mediated retrieval dashboard classes
    assert hasattr(container, "_orchestrator_config_class")
    assert hasattr(container, "_adaptive_budget_tuner_class")

    # All should be None initially (populated in lifespan)
    assert container._ask_fn is None
    assert container._ask_stores_class is None
    assert container._search_sources_fn is None
    assert container._sanitize_fts_query_fn is None
    assert container._ingest_conversation_fn is None
    assert container._ingest_file_fn is None
    assert container._corpus_ingest_config_class is None
    assert container._ingest_directory_to_corpus_fn is None
    assert container._ingest_file_to_corpus_fn is None
    assert container._get_orchestrator_cache_fn is None
    assert container._builtin_channels is None
    assert container._orchestrator_config_class is None
    assert container._adaptive_budget_tuner_class is None
