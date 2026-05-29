"""
CHUNK-11.2: Async Event Loop Fix — Phase 9 gate tests.

Verifies that async test infrastructure is properly configured:
- pytest-asyncio asyncio_mode="auto" in pyproject.toml
- No "no current event loop" errors in any test
- Knowledge store, knowledge compiler, collaborator, plugin, and profiler tests pass
- All async tests use proper pytest-asyncio fixtures
"""

import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


def test_pytest_asyncio_mode_auto_configured():
    """pyproject.toml must have asyncio_mode = 'auto' per Phase 9 spec §6."""
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml not found"

    with open(pyproject_path, "rb") as f:
        config = tomllib.load(f)

    pytest_config = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    asyncio_mode = pytest_config.get("asyncio_mode")
    assert asyncio_mode == "auto", (
        f"Expected asyncio_mode='auto', got '{asyncio_mode}'. "
        "Per Phase 9 spec §6: pytest-asyncio asyncio_mode='auto' is required."
    )


@pytest.mark.asyncio
async def test_knowledge_store_crud_no_event_loop_error():
    """KnowledgeStore async operations must not raise 'no current event loop' errors."""
    import tempfile

    from aip.adapter.knowledge.sqlite_knowledge_store import SqliteKnowledgeStore

    # Need mock vector_store and lexical_store for the constructor
    class MockVectorStore:
        async def upsert(self, *a, **kw):
            pass

        async def retrieve(self, *a, **kw):
            return []

        async def delete(self, *a, **kw):
            pass

        async def count(self, *a, **kw):
            return 0

    class MockLexicalStore:
        async def index_document(self, *a, **kw):
            pass

        async def search(self, *a, **kw):
            return []

        async def close(self):
            pass

    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteKnowledgeStore(
            db_path=f"{tmp}/knowledge.db",
            vector_store=MockVectorStore(),
            lexical_store=MockLexicalStore(),
        )
        await store.initialize()

        # Store a document
        await store.store_compiled(
            knowledge_id="test-k1",
            content="Test knowledge content",
            source_canonical_ids=["c1"],
            domain="test",
            metadata={"source": "gate_test", "state": "SPECIFIED"},
        )

        # Read it back
        result = await store.get_compiled("test-k1")
        assert result is not None
        assert result["content"] == "Test knowledge content"

        await store.close()


@pytest.mark.asyncio
async def test_knowledge_compiler_produces_artifact():
    """KnowledgeCompiler must produce an artifact without event loop errors.

    The compiler requires many injected dependencies. We provide mocks
    to verify the async pipeline works without event loop issues.
    """
    from aip.foundation.schemas import KnowledgeCompilationConfig
    from aip.orchestration.compilation import KnowledgeCompiler

    class _MockStore:
        async def store_compiled(self, *a, **kw):
            pass

        async def get_compiled(self, *a, **kw):
            return None

        async def list_compiled(self, *a, **kw):
            return []

        async def update_state(self, *a, **kw):
            pass

        async def get_provenance(self, *a, **kw):
            return []

        async def search_compiled(self, *a, **kw):
            return []

        async def list_canonical(self, *a, **kw):
            return []

        async def write_canonical(self, *a, **kw):
            pass

        async def upsert(self, *a, **kw):
            pass

        async def retrieve(self, *a, **kw):
            return []

        async def index_document(self, *a, **kw):
            pass

        async def search(self, *a, **kw):
            return []

        async def write_event(self, *a, **kw):
            pass

        async def query(self, *a, **kw):
            return []

        async def current_state(self, *a, **kw):
            return "REVIEWED"

        async def read(self, *a, **kw):
            return "content"

        async def record_vigil_check(self, *a, **kw):
            pass

        async def transition(self, *a, **kw):
            pass

        async def embed(self, text):
            return [0.0] * 384

    config = KnowledgeCompilationConfig()
    mock = _MockStore()

    compiler = KnowledgeCompiler(
        config=config,
        knowledge_store=mock,
        canonical_store=mock,
        vector_store=mock,
        lexical_store=mock,
        model_provider=mock,
        embedding_provider=mock,
        trace_store=mock,
        event_store=mock,
        ecs_store=mock,
        vigil_store=mock,
    )

    result = await compiler.compile_from_canonicals(
        domain="test",
        topic="Test compilation topic",
        source_canonical_ids=["c1"],
    )
    assert result is not None


@pytest.mark.asyncio
async def test_collaborator_role_enforcement():
    """Collaborator access must enforce role restrictions without event loop errors."""
    import tempfile

    from aip.adapter.auth.session_store import SqliteSessionStore
    from aip.foundation.schemas import AuthConfig

    with tempfile.TemporaryDirectory() as tmp:
        auth_config = AuthConfig()
        store = SqliteSessionStore(f"{tmp}/auth.db", auth_config)
        await store.initialize()

        # Cannot create definer role
        result = await store.create_user("test-user", "definer")
        assert result is False

        # Can create collaborator
        result = await store.create_user("test-collab", "collaborator")
        assert result is True

        await store.close()


@pytest.mark.asyncio
async def test_plugin_manager_health_check():
    """PluginManager health_check must work without event loop errors."""
    from aip.foundation.schemas import PluginConfig
    from aip.orchestration.plugins import PluginManager

    config = PluginConfig(sandbox_mode=True)
    pm = PluginManager(
        config=config,
        plugin_loader=None,
        model_slot_resolver=None,
    )
    results = await pm.health_check_all()
    assert isinstance(results, dict)


@pytest.mark.asyncio
async def test_performance_profiler_metrics():
    """PerformanceProfiler must return real metrics without event loop errors."""
    from aip.foundation.schemas import PerformanceConfig
    from aip.orchestration.perf import PerformanceProfiler

    config = PerformanceConfig()
    profiler = PerformanceProfiler(config=config, trace_store=None)

    metrics = await profiler.get_system_metrics()
    assert "cpu_percent" in metrics
    assert "memory_mb" in metrics
    assert isinstance(metrics["memory_mb"], (int, float))
    # psutil should be available (installed as Phase 9 dependency)
    assert metrics["memory_mb"] > 0, "psutil should report positive memory usage"
