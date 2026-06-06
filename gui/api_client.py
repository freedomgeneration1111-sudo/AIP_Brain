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
import logging
from typing import Any

import httpx

# Module-level logger for the API client
log = logging.getLogger("gui.api_client")

# Default backend URL — configurable via environment variable
import os

# Load .env file on import so AIP_OPENAI_API_KEY is available immediately.
# This MUST happen before reading any env vars.
try:
    from dotenv import load_dotenv
    _env_loaded = load_dotenv()
except ImportError:
    _env_loaded = False

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
        self._openrouter_api_key: str | None = None

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
        resp = await client.get(f"{self.base_url}/api/v1/health", timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    async def is_backend_reachable(self) -> bool:
        """Quick check if the backend is reachable (non-throwing)."""
        try:
            await self.check_health()
            return True
        except Exception:
            return False

    async def check_api_key_status(self) -> dict[str, Any]:
        """Check whether the backend has a valid API key configured.

        Calls GET /api/v1/models/api_key_status which inspects the
        actual resolved configuration (env vars + TOML config) on the
        backend, not just the local process env var.

        Returns dict with 'has_any_key' (bool) and 'slots' (dict of
        slot_name -> {has_key, provider}).
        """
        client = self._get_http_client()
        resp = await client.get(
            f"{self.base_url}/api/v1/models/api_key_status",
            timeout=5.0,
        )
        resp.raise_for_status()
        return resp.json()

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
        resp = await client.get(f"{self.base_url}/api/v1/models/slots", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        return data.get("slots", [])

    async def list_model_library(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        """Fetch model library from GET /api/v1/models/library.

        Returns a list of model dicts from the enabled_models table.
        If enabled_only=True, filters to models where enabled=1.
        Each dict has: model_id, display_name, provider, cost_input/output_per_million,
        context_length, supports_vision, supports_tools, enabled, etc.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(f"{self.base_url}/api/v1/models/library", timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            if enabled_only:
                return [m for m in items if m.get("enabled") == 1]
            return items
        except Exception as exc:
            log.warning("model_library_fetch_failed: %s", exc)
            return []

    async def beast_scan(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Fire Beast corpus scan via GET /api/v1/beast/scan.

        Per AIP_UNIFIED_CHAT_SPEC §Beast Pane: non-blocking, fires AFTER
        the chat response in bare mode. Returns scan results or error dict.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/beast/scan",
                params={"query": query, "limit": limit},
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("beast_scan_failed: %s", exc)
            return {"error": str(exc)}

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

        resp = await client.post(f"{self.base_url}/api/v1/sessions", json=payload, timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """Get session details."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_session_context(self, session_id: str) -> dict[str, Any]:
        """Get session context (turn count, context window estimate)."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/sessions/{session_id}/context")
        resp.raise_for_status()
        return resp.json()

    async def update_session(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update session metadata via PATCH /api/v1/sessions/{session_id}.

        Used to toggle auto_save, change mode, update role, etc.
        Returns the updated session metadata.
        """
        client = self._get_http_client()
        resp = await client.patch(f"{self.base_url}/api/v1/sessions/{session_id}", json=updates)
        resp.raise_for_status()
        return resp.json()

    async def delete_session(self, session_id: str) -> dict[str, Any]:
        """Delete a session via DELETE /api/v1/sessions/{session_id}."""
        client = self._get_http_client()
        resp = await client.delete(f"{self.base_url}/api/v1/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    async def list_projects(self) -> list[dict[str, Any]]:
        """Fetch all projects via GET /api/v1/projects.

        Returns a list of project dicts with at least:
          project_id, name, domain
        Used by the AUGMENTED panel to resolve a valid project_name
        for the /api/v1/ask endpoint.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/projects", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        # API returns {"projects": [...]}
        if isinstance(data, dict):
            return data.get("projects", [])
        if isinstance(data, list):
            return data
        return []

    # ------------------------------------------------------------------
    # Review Queue
    # ------------------------------------------------------------------

    async def list_pending_reviews(self) -> list[dict[str, Any]]:
        """Fetch pending items from the review queue."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/reviews")
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingest_conversation(
        self,
        conversation_id: str,
        turns: list[dict[str, str]],
        *,
        title: str | None = None,
        domain: str = "chat",
        source_format: str = "plaintext",
    ) -> dict[str, Any]:
        """Ingest a conversation via POST /api/v1/ingest/conversation.

        Args:
            conversation_id: Unique ID for this conversation.
            turns: List of dicts with 'role', 'content', optional 'timestamp'.
            title: Optional conversation title.
            domain: Domain for indexing (default: "chat").
            source_format: Source format hint (default: "plaintext").

        Returns an IngestionResult summary dict.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "conversation_id": conversation_id,
            "turns": turns,
            "domain": domain,
            "source_format": source_format,
        }
        if title:
            payload["title"] = title

        resp = await client.post(f"{self.base_url}/api/v1/ingest/conversation", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def ingest_file(
        self,
        path: str,
        *,
        domain: str = "imported",
        source_format: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a conversation file via POST /api/v1/ingest/file.

        Args:
            path: File path to ingest.
            domain: Domain for indexing (default: "imported").
            source_format: Optional format override.

        Returns a dict with list of IngestionResult summaries.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "path": path,
            "domain": domain,
        }
        if source_format:
            payload["source_format"] = source_format

        resp = await client.post(f"{self.base_url}/api/v1/ingest/file", json=payload)
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
        log.info("chat_ws: connecting to %s (slot=%s)", ws_url, model_slot)

        try:
            import websockets

            async with websockets.connect(ws_url, open_timeout=10, close_timeout=5) as ws:
                # Send the user message
                msg_payload: dict[str, Any] = {
                    "type": "message",
                    "content": message,
                }
                if model_slot:
                    msg_payload["model_slot"] = model_slot

                await ws.send(json.dumps(msg_payload))
                log.info("chat_ws: sent message (len=%d)", len(message))

                # Wait for response(s) with a timeout for the model call
                # The backend may send multiple messages (gate then response)
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120.0)
                    except asyncio.TimeoutError:
                        log.error("chat_ws: timed out waiting for response (120s)")
                        if on_error:
                            on_error({"type": "error", "content": "Model call timed out (120s). The model may be unavailable."})
                        break
                    try:
                        resp = json.loads(raw)
                    except json.JSONDecodeError:
                        log.error("chat_ws: invalid JSON from backend")
                        if on_error:
                            on_error({"type": "error", "content": "Invalid JSON from backend"})
                        break

                    resp_type = resp.get("type", "")
                    log.info("chat_ws: received type=%s", resp_type)

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
                        log.warning("chat_ws: unknown response type=%s", resp_type)
                        if on_error:
                            on_error(resp)
                        break

        except ImportError:
            # websockets not installed — fall back to httpx WebSocket
            log.info("chat_ws: websockets not available, using httpx fallback")
            await self._chat_via_httpx_ws(
                ws_url, message, on_response, on_error, on_gate, model_slot
            )
        except Exception as exc:
            log.error("chat_ws: connection failed: %s", exc)
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
    ) -> dict[str, Any]:
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

    # ------------------------------------------------------------------
    # Ask Pipeline (Knowledge Augmented Queries)
    # ------------------------------------------------------------------

    async def ask(
        self,
        question: str,
        project_name: str,
        *,
        source: str = "all",
        max_sources: int = 10,
        save_artifact: bool = False,
        model_slot: str = "synthesis",
    ) -> dict[str, Any]:
        """Submit a source-grounded ask query via POST /api/v1/ask.

        Returns AskResult dict with status, answer, sources, and metadata.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "question": question,
            "project_name": project_name,
            "source": source,
            "max_sources": max_sources,
            "save_artifact": save_artifact,
            "model_slot": model_slot,
        }
        resp = await client.post(f"{self.base_url}/api/v1/ask", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def augmented_ask(
        self,
        query: str,
        *,
        project_name: str = "default",
        source: str = "all",
        max_sources: int = 10,
        model_slot: str = "synthesis",
        system_prompt_modifier: str = "",
    ) -> dict[str, Any]:
        """Submit a knowledge-augmented query via POST /api/v1/ask.

        Convenience wrapper for the ask endpoint used by the AUGMENTED
        chat panel.  The backend requires ``question`` and ``project_name``
        as required fields; this method maps the GUI's ``query`` param
        to the backend's ``question`` field and defaults project_name
        to "default" so callers don't need to know the schema.

        The system_prompt_modifier is prepended to the synthesis system
        prompt by the backend (per AIP_UNIFIED_CHAT_SPEC §Chat Mode Picker).

        Returns the raw AskResult dict: status, answer, sources (list of
        {source_id, source_type, title, score, content_snippet, domain,
        metadata}), model_slot, model_provider, artifact_id, errors, etc.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "question": query,
            "project_name": project_name,
            "source": source,
            "max_sources": max_sources,
            "model_slot": model_slot,
        }
        if system_prompt_modifier:
            payload["system_prompt_modifier"] = system_prompt_modifier
        resp = await client.post(
            f"{self.base_url}/api/v1/ask",
            json=payload,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def ask_retrieve(
        self,
        question: str,
        *,
        domain: str | None = None,
        project_name: str | None = None,
        source: str = "all",
        max_sources: int = 20,
    ) -> dict[str, Any]:
        """Retrieve sources for a query without generating an answer.

        Uses POST /api/v1/ask/retrieve. Returns matching sources from
        LexicalStore + VectorStore without dispatching to a model.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "question": question,
            "source": source,
            "max_sources": max_sources,
        }
        if domain:
            payload["domain"] = domain
        if project_name:
            payload["project_name"] = project_name
        resp = await client.post(f"{self.base_url}/api/v1/ask/retrieve", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Memory Search (Vector + Lexical)
    # ------------------------------------------------------------------

    async def search_memory(self, q: str) -> dict[str, Any]:
        """Hybrid search via GET /api/v1/memory/search.

        Returns lexical + vector results for the query.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/memory/search", params={"q": q})
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Knowledge Browser
    # ------------------------------------------------------------------

    async def list_knowledge(
        self,
        *,
        domain: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """List compiled knowledge items via GET /api/v1/knowledge."""
        client = self._get_http_client()
        params: dict[str, str] = {}
        if domain:
            params["domain"] = domain
        if state:
            params["state"] = state
        resp = await client.get(f"{self.base_url}/api/v1/knowledge", params=params)
        resp.raise_for_status()
        return resp.json()

    async def list_wiki_articles(
        self,
        *,
        state: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """List wiki articles from artifacts + ecs_state via GET /api/v1/wiki/articles."""
        client = self._get_http_client()
        params: dict[str, str] = {}
        if state:
            params["state"] = state
        if domain:
            params["domain"] = domain
        resp = await client.get(f"{self.base_url}/api/v1/wiki/articles", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_knowledge(self, knowledge_id: str) -> dict[str, Any]:
        """Get a specific knowledge item via GET /api/v1/knowledge/{id}."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/knowledge/{knowledge_id}")
        resp.raise_for_status()
        return resp.json()

    async def search_knowledge(self, q: str, *, domain: str | None = None, limit: int = 10) -> dict[str, Any]:
        """Search compiled knowledge via GET /api/v1/knowledge/search."""
        client = self._get_http_client()
        params: dict[str, Any] = {"q": q, "limit": limit}
        if domain:
            params["domain"] = domain
        resp = await client.get(f"{self.base_url}/api/v1/knowledge/search", params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # ECS Graph
    # ------------------------------------------------------------------

    async def get_ecs_graph(self) -> dict[str, Any]:
        """Get ECS state graph + artifact distribution via GET /api/v1/ecs/graph."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/ecs/graph")
        resp.raise_for_status()
        return resp.json()

    async def get_ecs_artifact(self, artifact_id: str) -> dict[str, Any]:
        """Get ECS state + history for an artifact via GET /api/v1/ecs/artifacts/{id}."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/ecs/artifacts/{artifact_id}")
        resp.raise_for_status()
        return resp.json()

    async def list_ecs_artifacts(self, *, state: str | None = None) -> dict[str, Any]:
        """List ECS artifacts via GET /api/v1/ecs/artifacts."""
        client = self._get_http_client()
        params: dict[str, str] = {}
        if state:
            params["state"] = state
        resp = await client.get(f"{self.base_url}/api/v1/ecs/artifacts", params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Sources Browser
    # ------------------------------------------------------------------

    async def list_sources(
        self,
        *,
        domain: str | None = None,
        source_type: str | None = None,
    ) -> dict[str, Any]:
        """List indexed sources via GET /api/v1/sources."""
        client = self._get_http_client()
        params: dict[str, str] = {}
        if domain:
            params["domain"] = domain
        if source_type:
            params["source_type"] = source_type
        resp = await client.get(f"{self.base_url}/api/v1/sources", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_sources_stats(self) -> dict[str, Any]:
        """Get aggregate source statistics via GET /api/v1/sources/stats."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/sources/stats")
        resp.raise_for_status()
        return resp.json()

    async def get_corpus_stats(self) -> dict[str, Any]:
        """Get corpus turn statistics via GET /api/v1/corpus/stats."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/corpus/stats")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Budget Status
    # ------------------------------------------------------------------

    async def get_budget_status(self, scope: str = "session", scope_id: str = "default") -> dict[str, Any]:
        """Get budget status for a scope via GET /api/v1/admin/budget.

        Scopes: session, project, daily. The scope_id identifies the specific
        session, project, or day (ISO date string for daily).
        Returns consumed_tokens, limit, remaining, fraction_used, etc.
        """
        client = self._get_http_client()
        resp = await client.get(
            f"{self.base_url}/api/v1/admin/budget",
            params={"scope": scope, "scope_id": scope_id},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Review Queue
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Direct OpenRouter Chat (fallback when backend is unreachable)
    # ------------------------------------------------------------------

    async def chat_direct_openrouter(
        self,
        model: str,
        messages: list[dict[str, str]],
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat completion directly to OpenRouter API.

        This is used as a fallback when the AIP backend is not running.
        The GUI still prefers the backend (which handles sessions, auto-save,
        actors, etc.) but can fall back to direct OpenRouter calls for basic chat.

        Returns a dict with: content, model, tokens_used, latency_ms.
        """
        key = api_key or self.get_openrouter_api_key()
        if not key:
            return {"error": True, "content": "No OpenRouter API key set. Go to Models page to enter one."}

        log.info("direct_openrouter: calling model=%s", model)
        client = self._get_http_client()
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8080",
            "X-Title": "AIP_Brain",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
        }

        import time
        start = time.monotonic()
        try:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=60.0,
            )
            latency_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code != 200:
                error_detail = resp.text[:500]
                log.error("direct_openrouter: API error status=%d detail=%s", resp.status_code, error_detail[:200])
                return {"error": True, "content": f"OpenRouter API error ({resp.status_code}): {error_detail}"}

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            usage = data.get("usage", {})
            model_used = data.get("model", model)
            tokens_used = usage.get("total_tokens", 0)

            log.info("direct_openrouter: success model=%s tokens=%d latency=%dms",
                     model_used, tokens_used, latency_ms)

            return {
                "error": False,
                "content": content,
                "model": model_used,
                "tokens_used": tokens_used,
                "latency_ms": latency_ms,
            }
        except Exception as exc:
            log.error("direct_openrouter: request failed: %s", exc)
            return {"error": True, "content": f"OpenRouter request failed: {exc}"}

    # ------------------------------------------------------------------
    # OpenRouter Model Catalog
    # ------------------------------------------------------------------

    async def list_openrouter_models(self, api_key: str | None = None) -> list[dict[str, Any]]:
        """Fetch available text models from OpenRouter API.

        Calls GET https://openrouter.ai/api/v1/models with optional auth.
        Returns a list of model dicts with id, name, pricing, context_length, etc.
        Filters to chat/completion models only (excludes embedding/image).
        """
        client = self._get_http_client()
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = await client.get(
            "https://openrouter.ai/api/v1/models",
            headers=headers,
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        models = data.get("data", [])
        # Filter to text/chat models only
        text_models = []
        for m in models:
            mid = m.get("id", "")
            # Skip non-text models (image gen, TTS, etc.)
            if any(skip in mid.lower() for skip in ["image", "dall", "tts", "whisper", "stable-diffusion", "midjourney"]):
                continue
            # Only include models with text output modality
            arch = m.get("architecture", {})
            modality = arch.get("modality", "")
            if modality == "text->image" or modality == "image->image":
                continue
            text_models.append(m)
        return text_models

    async def list_openrouter_embedding_models(self, api_key: str | None = None) -> list[dict[str, Any]]:
        """Fetch embedding models from OpenRouter API.

        Calls GET https://openrouter.ai/api/v1/models?output_modalities=embeddings
        Returns a list of model dicts with id, name, pricing, context_length, etc.
        """
        client = self._get_http_client()
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = await client.get(
            "https://openrouter.ai/api/v1/models",
            params={"output_modalities": "embeddings"},
            headers=headers,
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])

    # ------------------------------------------------------------------
    # OpenRouter API Key Management
    # ------------------------------------------------------------------

    def get_openrouter_api_key(self) -> str | None:
        """Get the stored OpenRouter API key.

        Priority: 1) in-memory key, 2) AIP_OPENAI_API_KEY env var.
        """
        if self._openrouter_api_key:
            return self._openrouter_api_key
        return os.environ.get("AIP_OPENAI_API_KEY")

    def set_openrouter_api_key(self, key: str) -> None:
        """Store the OpenRouter API key in memory and set the env var.

        Setting the env var ensures the backend's ModelSlotResolver
        picks it up as AIP_OPENAI_API_KEY on next request.
        """
        self._openrouter_api_key = key
        os.environ["AIP_OPENAI_API_KEY"] = key

    def has_openrouter_api_key(self) -> bool:
        """Check if an OpenRouter API key is available."""
        key = self.get_openrouter_api_key()
        return key is not None and len(key.strip()) > 0

    async def update_slot_model(self, slot_name: str, model: str, api_key: str | None = None) -> dict[str, Any]:
        """Update the model for a slot at runtime via PATCH /api/v1/models/slots/{slot_name}/model.

        This sets the AIP_<SLOT>_MODEL env var in the backend process,
        which has the highest priority in ModelSlotResolver._resolve_slot_config().
        The change persists until the server restarts.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {"model": model}
        if api_key:
            payload["api_key"] = api_key
        resp = await client.patch(
            f"{self.base_url}/api/v1/models/slots/{slot_name}/model",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    async def approve_review(self, artifact_id: str) -> dict[str, Any]:
        """Approve a review item via POST /api/v1/reviews/{id}/approve."""
        client = self._get_http_client()
        resp = await client.post(f"{self.base_url}/api/v1/reviews/{artifact_id}/approve")
        resp.raise_for_status()
        return resp.json()

    async def approve_all_reviews(self) -> dict[str, Any]:
        """Bulk approve all pending beast artifacts via POST /api/v1/reviews/approve-all."""
        client = self._get_http_client()
        resp = await client.post(f"{self.base_url}/api/v1/reviews/approve-all")
        resp.raise_for_status()
        return resp.json()

    async def trigger_backfill(self, domain: str | None = None, limit: int = 500, batch_size: int = 50, dry_run: bool = False) -> dict[str, Any]:
        """Trigger backfill via POST /api/v1/admin/embeddings/backfill (now non-blocking)."""
        client = self._get_http_client()
        payload: dict[str, Any] = {"limit": limit, "batch_size": batch_size, "dry_run": dry_run}
        if domain:
            payload["domain"] = domain
        resp = await client.post(f"{self.base_url}/api/v1/admin/embeddings/backfill", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_backfill_status(self) -> dict[str, Any]:
        """Get backfill status via GET /api/v1/admin/embeddings/backfill/status."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/admin/embeddings/backfill/status")
        resp.raise_for_status()
        return resp.json()

    async def reject_review(self, artifact_id: str) -> dict[str, Any]:
        """Reject a review item via POST /api/v1/reviews/{id}/reject."""
        client = self._get_http_client()
        resp = await client.post(f"{self.base_url}/api/v1/reviews/{artifact_id}/reject")
        resp.raise_for_status()
        return resp.json()

    async def get_graph_stats(self) -> dict[str, Any]:
        """Get knowledge graph node/edge statistics via GET /api/v1/graph/stats."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/graph/stats")
        resp.raise_for_status()
        return resp.json()

    async def get_graph_node(self, node_id: str) -> dict[str, Any]:
        """Get graph node neighbors via GET /api/v1/graph/neighbors/{node_id}."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/graph/neighbors/{node_id}")
        resp.raise_for_status()
        return resp.json()


# Module-level singleton for the GUI to use
_api_client: AipApiClient | None = None


def get_api_client() -> AipApiClient:
    """Get or create the shared AipApiClient singleton."""
    global _api_client
    if _api_client is None:
        _api_client = AipApiClient()
    return _api_client
