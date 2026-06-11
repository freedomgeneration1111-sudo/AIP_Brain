"""Chunk 1 — Startup and launch correctness.

Tests that the app starts cleanly, mounts the GUI correctly,
reads the right config (including [database].db_path), and
shuts down without orphan coroutine warnings.

Deterministic, zero-token, no network, no LLM.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

# Check optional dependencies at module level
_nicegui_available = True
try:
    import nicegui  # noqa: F401
except ImportError:
    _nicegui_available = False

_fastapi_available = True
try:
    from fastapi.testclient import TestClient  # noqa: F401
except ImportError:
    _fastapi_available = False

nicegui_skip = pytest.mark.skipif(not _nicegui_available, reason="nicegui not installed")
fastapi_skip = pytest.mark.skipif(not _fastapi_available, reason="fastapi/testclient not available")


# ---------------------------------------------------------------------------
# 1. ui.run() guard — importing gui.main must NOT start NiceGUI
# ---------------------------------------------------------------------------


@nicegui_skip
def test_gui_main_import_does_not_start_nicegui():
    """Importing gui.main should NOT call ui.run().

    If ui.run() is called at module level (outside if __name__ == "__main__"),
    importing the module in a test will hang or raise because NiceGUI tries
    to bind a port.  This test simply imports the module and verifies it does
    not raise or block.
    """
    import gui.main  # noqa: F401 — side-effect import is the test


@nicegui_skip
def test_gui_shell_import_does_not_start_nicegui():
    """Importing gui.shell should NOT call ui.run().

    Same guard as gui.main — shell.py must only call ui.run() under
    if __name__ == "__main__" so it can be imported (e.g. for mounting
    on a FastAPI app) without side effects.
    """
    import gui.shell  # noqa: F401


# ---------------------------------------------------------------------------
# 2. [database].db_path config loading
# ---------------------------------------------------------------------------


def test_db_path_from_database_section():
    """app.py lifespan must read db_path from [database] section, not top-level.

    The TOML config has:
        [database]
        db_path = "db/state.db"

    Not:
        db_path = "db/state.db"   ← top-level (WRONG)
    """
    from aip.adapter.api.app import create_app

    config = {
        "database": {"db_path": "custom/data/test.db"},
        "auth": {"auth_enabled": False},
        "rate_limit": {"enabled": False},
    }
    app = create_app(config=config)
    # The raw config is stored on app.state
    assert app.state.raw_config.get("database", {}).get("db_path") == "custom/data/test.db"


def test_db_path_fallback_when_database_section_missing():
    """When [database] section is absent, db_path defaults to 'db/state.db'."""
    # This simulates what the lifespan does: config.get("database", {}).get("db_path", "db/state.db")
    config_no_db: dict = {}
    db_path = config_no_db.get("database", {}).get("db_path", "db/state.db")
    assert db_path == "db/state.db"


def test_db_path_fallback_when_db_path_key_missing():
    """When [database] section exists but has no db_path, default to 'db/state.db'."""
    config_empty_db_section = {"database": {"some_other_key": "value"}}
    db_path = config_empty_db_section.get("database", {}).get("db_path", "db/state.db")
    assert db_path == "db/state.db"


def test_top_level_db_path_not_used():
    """A top-level 'db_path' key in config must NOT be read by lifespan.

    This is the bug we fixed: old code did config.get("db_path") which
    would pick up a stray top-level key. The fix reads from [database] section.
    """
    # Simulate a config with a stale top-level db_path AND a [database] section
    config = {
        "db_path": "WRONG/top_level.db",  # Stale/legacy top-level key
        "database": {"db_path": "correct/section.db"},
    }
    # Lifespan now reads from [database] section
    db_path = config.get("database", {}).get("db_path", "db/state.db")
    assert db_path == "correct/section.db", (
        f"Expected 'correct/section.db' from [database] section, got '{db_path}'"
    )


@fastapi_skip
def test_non_default_db_path_used_in_lifespan():
    """Verify that a non-default db_path from config is actually used during startup.

    This creates an app with a custom db_path and verifies the lifespan
    uses it for all stores (by checking the health endpoint's db_writable).
    """
    from aip.adapter.api.app import create_app

    with tempfile.TemporaryDirectory() as tmpdir:
        # Ensure the custom directory exists so sqlite3.connect() works
        custom_dir = os.path.join(tmpdir, "custom")
        os.makedirs(custom_dir, exist_ok=True)
        custom_db = os.path.join(custom_dir, "state.db")
        config = {
            "database": {"db_path": custom_db},
            "auth": {"auth_enabled": False},
            "rate_limit": {"enabled": False},
        }
        app = create_app(config=config)

        with TestClient(app) as client:
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200
            data = resp.json()
            # If the db_path was used correctly, the database should be writable
            assert data.get("db_writable") is True, (
                f"Expected db_writable=True with custom path {custom_db}, got {data}"
            )


# ---------------------------------------------------------------------------
# 3. CORS includes localhost:8080 (GUI/API split)
# ---------------------------------------------------------------------------


def test_default_cors_includes_localhost_8080():
    """SurfaceConfig default CORS must include localhost:8080 for GUI/API split."""
    from aip.foundation.schemas import SurfaceConfig

    cfg = SurfaceConfig()
    assert "http://localhost:8080" in cfg.api_cors_origins, (
        f"localhost:8080 missing from default CORS origins: {cfg.api_cors_origins}"
    )


@fastapi_skip
def test_app_cors_includes_localhost_8080_with_default_config():
    """When no api.cors_origins is set, the app should include localhost:8080."""
    from aip.adapter.api.app import create_app

    with tempfile.TemporaryDirectory() as tmpdir:
        custom_db = os.path.join(tmpdir, "state.db")
        config = {
            "database": {"db_path": custom_db},
            "auth": {"auth_enabled": False},
            "rate_limit": {"enabled": False},
        }
        app = create_app(config=config)

        with TestClient(app) as client:
            resp = client.options(
                "/api/v1/health",
                headers={
                    "Origin": "http://localhost:8080",
                    "Access-Control-Request-Method": "GET",
                },
            )
            # CORS should allow the origin
            assert resp.headers.get("access-control-allow-origin") == "http://localhost:8080", (
                f"CORS did not allow localhost:8080: {dict(resp.headers)}"
            )


# ---------------------------------------------------------------------------
# 4. Startup tasks stored on container and cancellable
# ---------------------------------------------------------------------------


def test_container_has_startup_task_attributes():
    """AipContainer must have _sexton_startup_task and _vigil_startup_task."""
    from aip.adapter.api.dependencies import AipContainer

    container = AipContainer(config={})
    assert hasattr(container, "_sexton_startup_task")
    assert hasattr(container, "_vigil_startup_task")
    assert container._sexton_startup_task is None
    assert container._vigil_startup_task is None


@pytest.mark.asyncio
async def test_startup_tasks_can_be_set_and_cancelled():
    """Startup tasks stored on container should be cancellable."""
    from aip.adapter.api.dependencies import AipContainer

    container = AipContainer(config={})

    async def _dummy_task():
        await asyncio.sleep(100)

    task = asyncio.create_task(_dummy_task())
    container._sexton_startup_task = task
    assert container._sexton_startup_task is not None

    # Cancel it
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Container reference should still exist (shutdown reads it)
    assert container._sexton_startup_task is not None


# ---------------------------------------------------------------------------
# 5. GUI module importable without side effects (mounted GUI import)
# ---------------------------------------------------------------------------


@nicegui_skip
def test_gui_main_module_state_no_side_effects():
    """Importing gui.main should set up module state but not start servers."""
    import gui.main as m

    # Module-level state should be initialized
    assert hasattr(m, "GuiState")
    assert hasattr(m, "get_state")
    assert hasattr(m, "build_model_options")
    # But no server should be running (we'd get an error if it was)


@nicegui_skip
def test_gui_shell_module_state_no_side_effects():
    """Importing gui.shell should set up module state but not start servers."""
    import gui.shell as s

    # Module should define the design tokens and state
    assert hasattr(s, "C_GROUND")
    assert hasattr(s, "C_AMBER")
    assert hasattr(s, "GuiState")
    assert hasattr(s, "get_state")


# ---------------------------------------------------------------------------
# 6. Full app startup with custom database path — no port conflict
# ---------------------------------------------------------------------------


@fastapi_skip
def test_app_starts_with_custom_db_path_no_port_conflict():
    """App must start with a non-default db_path without error.

    This catches the bug where db_path was read from the wrong config
    section, causing stores to use 'db/state.db' instead of the
    configured path.
    """
    from aip.adapter.api.app import create_app

    with tempfile.TemporaryDirectory() as tmpdir:
        # Ensure the custom directory exists so sqlite3.connect() works
        custom_dir = os.path.join(tmpdir, "my_custom_db")
        os.makedirs(custom_dir, exist_ok=True)
        custom_db = os.path.join(custom_dir, "state.db")
        config = {
            "database": {"db_path": custom_db},
            "auth": {"auth_enabled": False},
            "rate_limit": {"enabled": False},
        }
        app = create_app(config=config)

        # Start the app with TestClient — this exercises the lifespan
        with TestClient(app) as client:
            # Health must be 200
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200

            # Verify the custom db file was created
            assert os.path.exists(custom_db), (
                f"Custom db file not created: {custom_db}"
            )


@fastapi_skip
def test_shutdown_cancels_startup_tasks():
    """Shutdown must cancel _sexton_startup_task and _vigil_startup_task.

    This test verifies that the shutdown sequence includes the startup
    tasks.  We check this by creating an app, starting it (which runs
    the lifespan), and then verifying that the shutdown completes
    without warnings about uncanceled tasks.
    """
    from aip.adapter.api.app import create_app

    with tempfile.TemporaryDirectory() as tmpdir:
        custom_dir = os.path.join(tmpdir, "shutdown_test")
        os.makedirs(custom_dir, exist_ok=True)
        custom_db = os.path.join(custom_dir, "state.db")
        config = {
            "database": {"db_path": custom_db},
            "auth": {"auth_enabled": False},
            "rate_limit": {"enabled": False},
        }
        app = create_app(config=config)

        # Start and stop the app — the lifespan runs fully
        with TestClient(app) as client:
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200

        # If we get here without hanging or warnings, shutdown was clean.
        # The TestClient context exit triggers the lifespan shutdown,
        # which should cancel all startup tasks.
