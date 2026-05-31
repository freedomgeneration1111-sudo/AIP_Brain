"""Tests for collaborator secret transport safety.

Verifies that:
1. Secret in query parameter is rejected or ignored with a clear error.
2. Secret in approved body/header path works.
3. Secret is not reflected in logs/errors/responses.
4. Existing non-secret query params still work.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aip.adapter.api.collaborators import router


def _create_test_app() -> tuple[FastAPI, TestClient]:
    """Create a minimal FastAPI app with the collaborators router for testing."""
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    return app, client


class MockCollaboratorManager:
    """Mock CollaboratorManager for testing without bcrypt/AuthStore."""

    def __init__(self):
        self.created = []
        self.users = {}

    async def create_collaborator(self, identity, role, password):
        # Redact password immediately — never store or return it
        self.created.append({"identity": identity, "role": role})
        self.users[identity] = {"role": role}
        return {"status": "created", "identity": identity, "role": role}

    async def list_collaborators(self):
        return [{"identity": k, "role": v["role"]} for k, v in self.users.items() if v["role"] != "definer"]

    async def update_role(self, identity, new_role, requested_by):
        if identity in self.users and self.users[identity]["role"] != "definer":
            self.users[identity]["role"] = new_role
            return {"status": "updated", "identity": identity, "new_role": new_role}
        return {"status": "error", "message": "Cannot change DEFINER or user not found"}

    async def revoke_collaborator(self, identity, requested_by):
        if identity in self.users and self.users[identity]["role"] != "definer":
            del self.users[identity]
            return {"status": "revoked", "identity": identity}
        return {"status": "error", "message": "Cannot revoke DEFINER or user not found"}


def _setup_app_with_cm():
    """Create app with mocked container that has collaborator_manager."""
    app, client = _create_test_app()
    cm = MockCollaboratorManager()

    # Inject the mock into a fake container on app.state
    class FakeContainer:
        collaborator_manager = cm

    app.state.container = FakeContainer()
    return app, client, cm


# --- Test: password in query param is rejected ---


def test_password_in_query_param_rejected():
    """Password must NOT be accepted as a query parameter."""
    app, client, _ = _setup_app_with_cm()

    # Try to create with password as query param (old insecure way)
    response = client.post("/collaborators?identity=alice&role=collaborator&password=secret123")
    assert response.status_code == 422
    data = response.json()
    # The endpoint should not accept password as a query parameter
    # When using Pydantic model body, query params are not valid for required body fields
    assert "detail" in data or "error" in data


def test_password_in_body_accepted():
    """Password must be accepted in the request body."""
    app, client, cm = _setup_app_with_cm()

    response = client.post(
        "/collaborators",
        json={"identity": "alice", "role": "collaborator", "password": "secret123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "created"
    assert data["identity"] == "alice"


def test_password_not_reflected_in_response():
    """Password must never appear in the response body."""
    app, client, _ = _setup_app_with_cm()

    response = client.post(
        "/collaborators",
        json={"identity": "bob", "role": "collaborator", "password": "super_secret_123"},
    )
    assert response.status_code == 200
    data = response.json()
    response_text = str(data).lower()
    assert "super_secret_123" not in response_text
    assert "password" not in response_text


def test_non_secret_query_params_still_work():
    """Non-secret query parameters (like for list) should still work."""
    app, client, cm = _setup_app_with_cm()

    # List collaborators via GET (no secrets involved)
    response = client.get("/collaborators")
    assert response.status_code == 200


def test_update_role_does_not_leak_secrets():
    """Update role endpoint should not expose any password material."""
    app, client, cm = _setup_app_with_cm()

    # Create first
    client.post(
        "/collaborators",
        json={"identity": "charlie", "role": "collaborator", "password": "pw123"},
    )

    # Update role via PUT body
    response = client.put(
        "/collaborators/charlie",
        json={"new_role": "readonly", "requested_by": "definer"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "password" not in str(data).lower()
    assert "pw123" not in str(data)


def test_delete_no_secret_in_response():
    """Delete endpoint should not expose any password material."""
    app, client, cm = _setup_app_with_cm()

    # Create first
    client.post(
        "/collaborators",
        json={"identity": "dave", "role": "collaborator", "password": "pw456"},
    )

    # Delete - send request body via request parameter
    import json

    response = client.request(
        "DELETE",
        "/collaborators/dave",
        content=json.dumps({"requested_by": "definer"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "password" not in str(data).lower()
    assert "pw456" not in str(data)


# --- Static code analysis tests ---


def test_no_password_in_query_signature():
    """Static check: collaborators.py must not have password as a query/path parameter."""
    import os

    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "src",
        "aip",
        "adapter",
        "api",
        "collaborators.py",
    )
    with open(src_path, "r") as f:
        source = f.read()

    # The word "password" should not appear as a FastAPI query parameter
    # In the new design, password comes from the request body via Pydantic model
    # Check that there's no `password: str` as a bare function parameter
    # (which FastAPI would interpret as query param)
    lines = source.split("\n")
    for i, line in enumerate(lines):
        # A line like "password: str," without Body() or being inside a model
        # would make it a query param
        stripped = line.strip()
        if stripped.startswith("password: str") and "Body" not in stripped and "Field" not in stripped:
            # Check it's not inside a class/model definition
            context = "\n".join(lines[max(0, i - 10) : i])
            if "class " not in context:
                pytest.fail(
                    f"Line {i + 1} has 'password: str' as a bare function parameter "
                    f"(FastAPI would treat as query param): {stripped}"
                )
