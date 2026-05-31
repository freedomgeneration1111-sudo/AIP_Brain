"""AIP API Client — adapter layer for GUI-to-backend communication.

This module is the SOLE interface between the NiceGUI frontend and the
AIP FastAPI backend. It communicates exclusively through REST and WebSocket
endpoints — never by importing from aip.orchestration or directly accessing
AipContainer.

This follows the API-First approach agreed upon in the Phase 1 plan:
the GUI is treated like CLI/MCP surfaces, communicating via FastAPI routes
and WebSocket, not in-process container access.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

# Default backend URL — configurable via environment variable
import os

AIP_BACKEND_URL = os.getenv("AIP_BACKEND_URL", "http://127.0.0.1:8000")


class AipApiClient:
    """HTTP + WebSocket client for communicating with the AIP FastAPI backend.

    All GUI components should use this client rather than making direct
    HTTP calls to Ollama or OpenRouter. The backend handles model routing,
    session management, and actor dispatch.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or AIP_BACKEND_URL).rstrip("/")
        self._http_client: httpx.AsyncClient | None = None
        self._ws_session_id: str | None = None

    # ------------------------------------------------------------------
    # HTTP Client Management
    # ------------------------------------------------------------------

    def _get_http_client(self) -> httpx.AsyncClient:
        """Lazily create and reuse an httpx.AsyncClient."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def check_health(self) -> dict[str, Any]:
        """Check if the AIP backend is reachable and healthy.

        Returns the health response dict, or raises on connection failure.
        Used for graceful degradation in the GUI.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/health")
        resp.raise_for_status()
        return resp.json()

    async def is_backend_reachable(self) -> bool:
        """Quick check if the backend is reachable (non-throwing)."""
        try:
            await self.check_health()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Model Slots
    # ------------------------------------------------------------------

    async def list_model_slots(self) -> list[dict[str, Any]]:
        """Fetch available model slots from the backend.

        Returns a list of slot info dicts with:
          slot_name, provider, model, base_url, has_fallback, ...
        This replaces the old direct read of enabled_models.json.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/models/slots")
        resp.raise_for_status()
        data = resp.json()
        return data.get("slots", [])

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    async def create_session(
        self,
        *,
        role: str | None = None,
        model_slot: str = "synthesis",
        mode: str = "normal",
        project_id: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Create a new chat session via POST /api/v1/sessions.

        Returns session metadata including the session_id needed for
        WebSocket communication.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "model_slot": model_slot,
            "mode": mode,
        }
        if role:
            payload["role"] = role
        if project_id:
            payload["project_id"] = project_id
        if domain:
            payload["domain"] = domain

        resp = await client.post(f"{self.base_url}/api/v1/sessions", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """Get session details."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Actor Status
    # ------------------------------------------------------------------

    async def get_actors_status(self) -> dict[str, Any]:
        """Fetch status of all actors from the backend.

        Returns dict with 'actors' key containing Beast, Vigil, Sexton status.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/actors/status")
        resp.raise_for_status()
        return resp.json()

    async def trigger_actor_cycle(self, actor_name: str) -> dict[str, Any]:
        """Manually trigger an actor cycle (admin/debug)."""
        client = self._get_http_client()
        resp = await client.post(f"{self.base_url}/api/v1/actors/{actor_name}/trigger")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # WebSocket Chat
    # ------------------------------------------------------------------

    async def chat_via_websocket(
        self,
        session_id: str,
        message: str,
        on_response: Any = None,
        on_error: Any = None,
        on_gate: Any = None,
        model_slot: str | None = None,
    ) -> None:
        """Send a chat message via WebSocket and handle the streaming response.

        Connects to ws://<backend>/api/v1/chat/<session_id> and sends
        a JSON message. Calls the appropriate callback for each response type:
          - on_response(response_dict) — normal chat response
          - on_error(error_dict) — error from the backend
          - on_gate(gate_dict) — DEFINER gate prompt requiring approval

        Args:
            session_id: The session ID obtained from create_session().
            message: The user's chat message text.
            on_response: Callback for normal responses.
            on_error: Callback for error responses.
            on_gate: Callback for gate (DEFINER review) prompts.
            model_slot: Optional per-message model slot override.
        """
        # Build WebSocket URL
        ws_base = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_base}/api/v1/chat/{session_id}"

        try:
            import websockets

            async with websockets.connect(ws_url) as ws:
                # Send the user message
                msg_payload: dict[str, Any] = {
                    "type": "message",
                    "content": message,
                }
                if model_slot:
                    msg_payload["model_slot"] = model_slot

                await ws.send(json.dumps(msg_payload))

                # Wait for response(s)
                # The backend may send multiple messages (gate then response)
                while True:
                    raw = await ws.recv()
                    try:
                        resp = json.loads(raw)
                    except json.JSONDecodeError:
                        if on_error:
                            on_error({"type": "error", "content": "Invalid JSON from backend"})
                        break

                    resp_type = resp.get("type", "")

                    if resp_type == "response":
                        if on_response:
                            on_response(resp)
                        break  # Got the final response — done with this message

                    elif resp_type == "gate":
                        if on_gate:
                            on_gate(resp)
                        # Don't break — wait for the gate_response flow
                        # The GUI will handle this separately

                    elif resp_type == "error":
                        if on_error:
                            on_error(resp)
                        break

                    elif resp_type == "pong":
                        # Keepalive response — ignore
                        continue

                    else:
                        if on_error:
                            on_error(resp)
                        break

        except ImportError:
            # websockets not installed — fall back to httpx WebSocket
            await self._chat_via_httpx_ws(
                ws_url, message, on_response, on_error, on_gate, model_slot
            )
        except Exception as exc:
            if on_error:
                on_error({"type": "error", "content": f"WebSocket connection failed: {exc}"})

    async def _chat_via_httpx_ws(
        self,
        ws_url: str,
        message: str,
        on_response: Any,
        on_error: Any,
        on_gate: Any,
        model_slot: str | None,
    ) -> None:
        """Fallback WebSocket using httpx (if websockets library not available)."""
        try:
            async with httpx.AsyncClient() as client:
                async with client.websocket_connect(ws_url) as ws:
                    msg_payload: dict[str, Any] = {
                        "type": "message",
                        "content": message,
                    }
                    if model_slot:
                        msg_payload["model_slot"] = model_slot

                    await ws.send(json.dumps(msg_payload))

                    while True:
                        raw = await ws.receive_text()
                        try:
                            resp = json.loads(raw)
                        except json.JSONDecodeError:
                            if on_error:
                                on_error({"type": "error", "content": "Invalid JSON from backend"})
                            break

                        resp_type = resp.get("type", "")

                        if resp_type == "response":
                            if on_response:
                                on_response(resp)
                            break
                        elif resp_type == "gate":
                            if on_gate:
                                on_gate(resp)
                        elif resp_type == "error":
                            if on_error:
                                on_error(resp)
                            break
                        else:
                            if on_error:
                                on_error(resp)
                            break
        except Exception as exc:
            if on_error:
                on_error({"type": "error", "content": f"WebSocket connection failed: {exc}"})

    async def send_gate_response(
        self,
        session_id: str,
        approved: bool,
    ) -> None:
        """Send a gate approval/rejection response via WebSocket.

        Called when the user responds to a DEFINER gate prompt.
        """
        ws_base = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_base}/api/v1/chat/{session_id}"

        try:
            import websockets

            async with websockets.connect(ws_url) as ws:
                await ws.send(json.dumps({
                    "type": "gate_response",
                    "approved": approved,
                }))
                # Wait for the resumed response
                raw = await ws.recv()
                return json.loads(raw)
        except ImportError:
            async with httpx.AsyncClient() as client:
                async with client.websocket_connect(ws_url) as ws:
                    await ws.send(json.dumps({
                        "type": "gate_response",
                        "approved": approved,
                    }))
                    raw = await ws.receive_text()
                    return json.loads(raw)
        except Exception as exc:
            return {"type": "error", "content": f"Gate response failed: {exc}"}


# Module-level singleton for the GUI to use
_api_client: AipApiClient | None = None


def get_api_client() -> AipApiClient:
    """Get or create the shared AipApiClient singleton."""
    global _api_client
    if _api_client is None:
        _api_client = AipApiClient()
    return _api_client
