"""Tests for SqliteProjectStore — concrete ProjectStore implementation."""

import os
import tempfile

import pytest

from aip.adapter.project.sqlite_project_store import SqliteProjectStore


@pytest.fixture
def project_store(tmp_path):
    """Create a SqliteProjectStore with a temp database."""
    db_path = str(tmp_path / "test_projects.db")
    store = SqliteProjectStore(db_path)
    return store


class TestSqliteProjectStore:
    @pytest.mark.asyncio
    async def test_list_projects_empty(self, project_store):
        result = await project_store.list_projects()
        assert result == []

    @pytest.mark.asyncio
    async def test_create_and_list_project(self, project_store):
        created = await project_store.create_project("p1", "Test Project", domain="test")
        assert created["project_id"] == "p1"
        assert created["name"] == "Test Project"
        assert created["status"] == "active"

        projects = await project_store.list_projects()
        assert len(projects) == 1
        assert projects[0]["project_id"] == "p1"
        assert projects[0]["name"] == "Test Project"

    @pytest.mark.asyncio
    async def test_list_projects_filter_by_status(self, project_store):
        await project_store.create_project("p1", "Active")
        await project_store.create_project("p2", "Also Active")

        active = await project_store.list_projects(status="active")
        assert len(active) == 2

        completed = await project_store.list_projects(status="completed")
        assert len(completed) == 0

    @pytest.mark.asyncio
    async def test_close_and_reuse(self, project_store):
        await project_store.create_project("p1", "Test")
        await project_store.close()

        # Should still work after close (new connection)
        projects = await project_store.list_projects()
        assert len(projects) == 1

    @pytest.mark.asyncio
    async def test_initialize(self, project_store):
        # initialize() should not raise
        await project_store.initialize()
        await project_store.close()

    @pytest.mark.asyncio
    async def test_project_has_required_fields(self, project_store):
        await project_store.create_project("p1", "Test", domain="my-domain")
        projects = await project_store.list_projects()

        assert "project_id" in projects[0]
        assert "name" in projects[0]
        assert "status" in projects[0]
        assert "domain" in projects[0]
        assert "created_at" in projects[0]
        assert "updated_at" in projects[0]
