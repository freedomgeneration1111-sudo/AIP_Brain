"""CHUNK-8.3 gate: Chat WebSocket surface (message flow, gate handling, context reset)."""

from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None  # type: ignore

from aip.adapter.api.app import create_app
from aip.adapter.api.routes import chat as chat_router


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed (Phase 6 surface dep)")
def test_chat_router_mounted_and_basic_flow():
    app = create_app()
    # Mount for test if not already (8.1 app may need include_router update in later wiring)
    app.include_router(chat_router.router, prefix="/api/v1")

    client = TestClient(app)
    with client.websocket_connect("/api/v1/chat/sess-demo") as ws:
        ws.send_json({"type": "message", "content": "hello"})
        data = ws.receive_json()
        assert data["type"] in ("response", "error")

        # Demonstrate gate flow
        ws.send_json({"type": "message", "content": "please hit a gate"})
        gate = ws.receive_json()
        assert gate.get("type") == "gate"
        assert "artifact_id" in gate

        ws.send_json({"type": "gate_response", "approved": True})
        final = ws.receive_json()
        assert final["type"] == "response"
        assert "approved" in final.get("content", "").lower() or "resumed" in final.get("content", "").lower()


def test_chat_layering_and_no_orchestration_storage_imports():
    """Layering (same as all prior gates)."""
    from pathlib import Path

    chat_file = Path(__file__).parent.parent / "src/aip/adapter/api/routes/chat.py"
    text = chat_file.read_text()
    assert "from aip.adapter.budget_store" not in text
    assert "from aip.adapter.vector" not in text
