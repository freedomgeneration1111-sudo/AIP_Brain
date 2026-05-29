"""Tests for versioned artifact store (CHUNK-4.3)."""
import pytest

from aip.adapter.artifact_store_versioned import VersionedArtifactStore


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test_artifacts.db")
    s = VersionedArtifactStore(db_path)
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_write_creates_version_1(store):
    await store.write("a1", "First version", {"source": "test"})
    versions = await store.list_versions("a1")
    assert versions == [1]


@pytest.mark.asyncio
async def test_write_appends_versions(store):
    await store.write("a2", "Version 1", {})
    await store.write("a2", "Version 2", {})
    await store.write("a2", "Version 3", {})
    versions = await store.list_versions("a2")
    assert versions == [1, 2, 3]


@pytest.mark.asyncio
async def test_read_latest(store):
    await store.write("a3", "Old", {})
    await store.write("a3", "New", {})
    content = await store.read("a3")
    assert content == "New"


@pytest.mark.asyncio
async def test_read_specific_version(store):
    await store.write("a4", "V1", {})
    await store.write("a4", "V2", {})
    await store.write("a4", "V3", {})
    assert await store.read("a4", version=1) == "V1"
    assert await store.read("a4", version=2) == "V2"
    assert await store.read("a4", version=3) == "V3"


@pytest.mark.asyncio
async def test_read_nonexistent_raises(store):
    with pytest.raises(KeyError):
        await store.read("nonexistent")


@pytest.mark.asyncio
async def test_old_version_preserved(store):
    """Per §1.5: every version is preserved."""
    await store.write("a5", "Original content", {"note": "first"})
    await store.write("a5", "Updated content", {"note": "second"})
    # Old version still accessible
    assert await store.read("a5", version=1) == "Original content"
    # Latest version is the new one
    assert await store.read("a5") == "Updated content"
