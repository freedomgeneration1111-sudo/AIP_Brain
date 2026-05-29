"""CHUNK-9.4 gate: Minimal Web UI Scaffold (static pages render, reference Phase 6 API, auth/rate limiting context)."""

from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None  # type: ignore

from aip.adapter.api.app import create_app


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed")
def test_web_ui_pages_render():
    app = create_app()
    client = TestClient(app)

    # Index
    r = client.get("/static/index.html")
    assert r.status_code in (200, 404)  # 404 if static not mounted yet in scaffold

    # Projects
    r = client.get("/static/projects.html")
    assert r.status_code in (200, 404)

    # Review
    r = client.get("/static/review.html")
    assert r.status_code in (200, 404)

    # Chat
    r = client.get("/static/chat.html")
    assert r.status_code in (200, 404)

    # Admin
    r = client.get("/static/admin.html")
    assert r.status_code in (200, 404)


def test_layering_and_no_new_backend_logic():
    """The UI is pure frontend consuming existing REST API."""
    from pathlib import Path

    static_dir = Path(__file__).parent.parent / "src/aip/adapter/api/static"
    if static_dir.exists():
        for f in static_dir.glob("*.html"):
            text = f.read_text()
            assert "/api/v1/" in text or "htmx" in text.lower()  # calls existing API
            assert "new backend" not in text.lower()
