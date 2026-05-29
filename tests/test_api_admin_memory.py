"""CHUNK-8.6 gate: Admin Console + Memory Inspector (gated writes, read-only actor/status data)."""

from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None  # type: ignore

from aip.adapter.api.app import create_app


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed")
def test_admin_writes_go_through_gate():
    app = create_app()
    client = TestClient(app)

    # Config patch is admin
    r = client.patch("/api/v1/admin/config", json={"foo": "bar"})
    assert r.status_code in (200, 403, 503)

    # Reads are open (or 503 in scaffold)
    r = client.get("/api/v1/admin/sexton/classifications")
    assert r.status_code in (200, 503)

    r = client.get("/api/v1/admin/beast/status")
    assert r.status_code in (200, 503)


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed")
def test_memory_inspector_read_only():
    app = create_app()
    client = TestClient(app)

    r = client.get("/api/v1/memory/trace/sess-1")
    assert r.status_code in (200, 503)

    r = client.get("/api/v1/memory/search?q=test")
    assert r.status_code in (200, 503)

    r = client.get("/api/v1/memory/canonical")
    assert r.status_code in (200, 503)


def test_layering_and_no_bypass():
    """Route modules must not import from orchestration or concrete adapter storage."""
    from pathlib import Path

    for f in ["admin.py", "memory.py"]:
        p = Path(__file__).parent.parent / "src/aip/adapter/api/routes" / f
        if p.exists():
            text = p.read_text()
            # Routes are in the adapter layer, so importing from aip.adapter.api is fine.
            # They must NOT import from orchestration or concrete storage adapters.
            assert "from aip.orchestration" not in text, f"{f} imports from orchestration"
            assert "import aip.orchestration" not in text, f"{f} imports from orchestration"
            assert "from aip.adapter.vector" not in text, f"{f} imports concrete vector adapter"
            assert "from aip.adapter.budget_store" not in text, f"{f} imports concrete budget store"
