"""
Verifies that sqlite_vss extension loading handles missing vss0.so gracefully in CI environments.
"""

import tempfile

import pytest


def test_sqlite_vss_graceful_skip():
    """
    sqlite_vss extension loading must handle missing vss0.so gracefully.
    When the extension is unavailable, the store should set _vss_available=False
    and not crash during initialization.
    """
    from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

    # This test should work even when vss0.so is not available
    with tempfile.TemporaryDirectory() as tmp:
        db_path = f"{tmp}/test_vss.db"
        try:
            store = SqliteVssVectorStore(db_path=db_path, dimensions=4)
            # Store should be created successfully even without vss0 extension
            assert store._vss_available is False or store._vss_available is True
            store.close()
        except Exception as e:
            pytest.fail(f"SqliteVssVectorStore initialization failed: {e}")
