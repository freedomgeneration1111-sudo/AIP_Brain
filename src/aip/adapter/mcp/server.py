"""AipMcpServer — MCP tool server with real dispatch.

Takes AipContainer, supports stdio/sse, list_tools() with McpToolDef
(autonomy_level + model_gen_assumption), enforces gate for write/admin tools before dispatch.
MCP routes through Protocols, not around them (no direct store access).

Response contract:
  Success: {"ok": true, "result": {...}}
  Error:   {"ok": false, "error": {"code": "...", "message": "...", "details": {}}}

Error codes: NOT_FOUND, UNAUTHORIZED, FORBIDDEN, BACKEND_UNAVAILABLE,
             VALIDATION_ERROR, NOT_IMPLEMENTED, EVALUATION_FAILED,
             PROMOTION_BLOCKED, INTERNAL_ERROR
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.schemas import McpToolDef, coerce_autonomy_level, coerce_mcp_autonomy_level

logger = logging.getLogger(__name__)

# Error code constants for structured responses
NOT_FOUND = "NOT_FOUND"
UNAUTHORIZED = "UNAUTHORIZED"
FORBIDDEN = "FORBIDDEN"
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
VALIDATION_ERROR = "VALIDATION_ERROR"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
EVALUATION_FAILED = "EVALUATION_FAILED"
PROMOTION_BLOCKED = "PROMOTION_BLOCKED"
INTERNAL_ERROR = "INTERNAL_ERROR"


def _ok(result: dict) -> dict:
    """Build a success response."""
    return {"ok": True, "result": result}


def _error(code: str, message: str, details: dict | None = None) -> dict:
    """Build an error response."""
    return {"ok": False, "error": {"code": code, "message": message, "details": details or {}}}


# Tool definitions with real input schemas
TOOLS: list[dict[str, Any]] = [
    {
        "name": "aip_search",
        "autonomy": "read",
        "model_gen": "Models may hallucinate without retrieved context",
        "desc": "Hybrid lexical + semantic search via Protocols",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text"},
                "domain": {"type": "string", "description": "Optional domain filter"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "aip_project_list",
        "autonomy": "read",
        "model_gen": None,
        "desc": "List projects",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results to return"},
            },
        },
    },
    {
        "name": "aip_project_create",
        "autonomy": "write",
        "model_gen": None,
        "desc": "Create project (write gate)",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "description": {"type": "string", "description": "Project description"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "aip_artifact_list",
        "autonomy": "read",
        "model_gen": None,
        "desc": "List artifacts",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Filter by project ID"},
                "limit": {"type": "integer", "description": "Max results to return"},
            },
        },
    },
    {
        "name": "aip_artifact_approve",
        "autonomy": "admin",
        "model_gen": "Models should not autonomously approve artifacts",
        "desc": "Approve artifact (admin gate + canonical promotion)",
        "input_schema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "Artifact ID to approve"},
                "notes": {"type": "string", "description": "Approval notes"},
            },
            "required": ["artifact_id"],
        },
    },
    {
        "name": "aip_trace_query",
        "autonomy": "read",
        "model_gen": None,
        "desc": "Query trace events",
        "input_schema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "Filter by artifact ID"},
                "event_type": {"type": "string", "description": "Filter by event type"},
                "limit": {"type": "integer", "description": "Max results to return"},
            },
        },
    },
    {
        "name": "aip_config_read",
        "autonomy": "read",
        "model_gen": None,
        "desc": "Read config",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "Config section to read"},
            },
        },
    },
    {
        "name": "aip_config_write",
        "autonomy": "admin",
        "model_gen": "Models should not autonomously modify harness config",
        "desc": "Write config (admin gate)",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "Config section to write"},
                "values": {"type": "object", "description": "Config values to set"},
            },
            "required": ["section", "values"],
        },
    },
]


class AipMcpServer:
    """MCP server with real tool dispatch via container Protocols.

    Tool dispatch delegates to real implementations in tools/ module or
    returns structured NOT_IMPLEMENTED for tools without implementations.
    All write/admin operations go through AutonomyGate before dispatch.
    """

    def __init__(self, container: Any) -> None:
        self.container = container
        self._running = False

    async def start(self, transport: str = "stdio") -> None:
        """Start the MCP server.

        For alpha, this marks the server as running. Full stdio/SSE
        transport implementation is deferred — the server is usable
        via direct call_tool() invocation (e.g., from tests or embedded use).
        """
        self._running = True
        logger.info("MCP server started (transport=%s, direct-invocation mode)", transport)

    async def stop(self) -> None:
        """Stop the MCP server."""
        self._running = False
        logger.info("MCP server stopped")

    def list_tools(self) -> list[McpToolDef]:
        """List all registered MCP tools with their autonomy levels and schemas."""
        defs = []
        for t in TOOLS:
            defs.append(
                McpToolDef(
                    tool_name=t["name"],
                    description=t["desc"],
                    input_schema=t["input_schema"],
                    autonomy_level=coerce_mcp_autonomy_level(t["autonomy"]),
                    model_gen_assumption=t["model_gen"],
                ),
            )
        return defs

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Dispatch tool call with autonomy enforcement and real implementation.

        Enforces AutonomyGate for write/admin tools before dispatching to
        real tool implementations. Returns structured responses per contract.
        """
        tool_def = next((t for t in TOOLS if t["name"] == name), None)
        if not tool_def:
            return _error(NOT_FOUND, f"Unknown tool: {name}", {"tool_name": name})

        # Autonomy gate enforcement for write/admin tools
        level = tool_def["autonomy"]
        if level in ("write", "admin") and self.container.autonomy_gate:
            esc = await self.container.autonomy_gate.escalate(
                action_type=f"mcp_{name}",
                resource_id=arguments.get("artifact_id") or arguments.get("name") or "mcp",
                requested_level=coerce_autonomy_level("admin" if level == "admin" else "write"),
                requested_by="mcp",
            )
            if not esc.granted:
                return _error(FORBIDDEN, f"Autonomy gate blocked: {esc.reason}", {"escalation_reason": esc.reason})

        # Dispatch to real implementations
        try:
            if name == "aip_search":
                return await self._dispatch_search(arguments)
            elif name == "aip_artifact_approve":
                return await self._dispatch_artifact_approve(arguments)
            elif name == "aip_project_list":
                return await self._dispatch_project_list(arguments)
            elif name == "aip_project_create":
                return await self._dispatch_project_create(arguments)
            elif name == "aip_artifact_list":
                return await self._dispatch_artifact_list(arguments)
            elif name == "aip_trace_query":
                return await self._dispatch_trace_query(arguments)
            elif name == "aip_config_read":
                return await self._dispatch_config_read(arguments)
            elif name == "aip_config_write":
                return await self._dispatch_config_write(arguments)
            else:
                return _error(NOT_IMPLEMENTED, f"Tool '{name}' is not implemented", {"tool_name": name})
        except Exception as exc:
            logger.error("MCP tool '%s' dispatch failed: %s", name, exc, exc_info=True)
            return _error(INTERNAL_ERROR, f"Internal error in tool '{name}': {exc}", {"tool_name": name})

    # ---- Real tool dispatch implementations ----

    async def _dispatch_search(self, arguments: dict) -> dict:
        """aip_search: Real hybrid lexical + semantic search."""
        query = arguments.get("query", "").strip()
        if not query:
            return _error(VALIDATION_ERROR, "Query parameter is required", {"argument": "query"})

        domain = arguments.get("domain")

        # Check if any search backend is available
        has_lexical = self.container.lexical_store is not None
        has_vector = self.container.vector_store is not None
        if not has_lexical and not has_vector:
            return _error(
                BACKEND_UNAVAILABLE,
                "No search backend available (neither lexical nor vector store is configured)",
                {"lexical_available": False, "vector_available": False},
            )

        # Use the real search implementation from tools/
        from aip.adapter.mcp.tools.search import aip_search

        try:
            results = await aip_search(self.container, query, domain=domain)
            return _ok({"results": results, "count": len(results)})
        except Exception as exc:
            logger.error("MCP search failed: %s", exc, exc_info=True)
            return _error(INTERNAL_ERROR, f"Search failed: {exc}", {})

    async def _dispatch_artifact_approve(self, arguments: dict) -> dict:
        """aip_artifact_approve: Real artifact approval via ECS + Canonical + Review pipeline."""
        artifact_id = arguments.get("artifact_id", "").strip()
        if not artifact_id:
            return _error(VALIDATION_ERROR, "artifact_id parameter is required", {"argument": "artifact_id"})

        # notes = arguments.get("notes", "")  # Future: pass to review queue

        # Verify required stores are available
        if not self.container.ecs_store:
            return _error(BACKEND_UNAVAILABLE, "ECS store not configured", {})
        if not self.container.canonical_store:
            return _error(BACKEND_UNAVAILABLE, "Canonical store not configured", {})

        # Check that artifact exists
        if self.container.artifact_store:
            try:
                content = await self.container.artifact_store.read(artifact_id)
                if content is None:
                    return _error(NOT_FOUND, f"Artifact not found: {artifact_id}", {"artifact_id": artifact_id})
            except Exception as exc:
                # If artifact_store.read raises on missing, treat as not found
                if "not found" in str(exc).lower() or "not exist" in str(exc).lower():
                    return _error(NOT_FOUND, f"Artifact not found: {artifact_id}", {"artifact_id": artifact_id})
                # Other errors — artifact may exist but read failed
                logger.warning("MCP artifact read check failed (proceeding): %s", exc)

        # Verify the artifact is in a state that can be approved
        try:
            current = await self.container.ecs_store.current_state(artifact_id)
        except Exception:
            return _error(
                NOT_FOUND, f"Could not read ECS state for artifact: {artifact_id}", {"artifact_id": artifact_id}
            )

        if current is None:
            return _error(
                NOT_FOUND,
                f"Artifact has no ECS state: {artifact_id}",
                {"artifact_id": artifact_id, "current_state": str(current)},
            )

        # Only REVIEWED artifacts can be approved
        if current != "REVIEWED":
            return _error(
                PROMOTION_BLOCKED,
                f"Artifact cannot be approved from state '{current}'. Must be in REVIEWED state.",
                {"artifact_id": artifact_id, "current_state": str(current), "required_state": "REVIEWED"},
            )

        # Use the real approval implementation from tools/
        from aip.adapter.mcp.tools.artifacts import aip_artifact_approve

        try:
            result = await aip_artifact_approve(self.container, artifact_id)
            if result.get("approved"):
                return _ok(
                    {
                        "approved": True,
                        "artifact_id": artifact_id,
                        "canonical_written": result.get("canonical_written", False),
                    }
                )
            else:
                return _error(PROMOTION_BLOCKED, result.get("reason", "Approval failed"), {"artifact_id": artifact_id})
        except Exception as exc:
            logger.error("MCP artifact approve failed: %s", exc, exc_info=True)
            return _error(INTERNAL_ERROR, f"Approval failed: {exc}", {"artifact_id": artifact_id})

    async def _dispatch_project_list(self, arguments: dict) -> dict:
        """aip_project_list: List projects via ProjectStore."""
        if not self.container.project_store:
            return _error(BACKEND_UNAVAILABLE, "Project store not configured", {})

        try:
            limit = arguments.get("limit", 100)
            projects = await self.container.project_store.list_projects(limit=limit)
            return _ok({"projects": projects, "count": len(projects)})
        except Exception as exc:
            logger.error("MCP project list failed: %s", exc, exc_info=True)
            return _error(INTERNAL_ERROR, f"Failed to list projects: {exc}", {})

    async def _dispatch_project_create(self, arguments: dict) -> dict:
        """aip_project_create: Create project via ProjectStore."""
        name = arguments.get("name", "").strip()
        if not name:
            return _error(VALIDATION_ERROR, "name parameter is required", {"argument": "name"})

        if not self.container.project_store:
            return _error(BACKEND_UNAVAILABLE, "Project store not configured", {})

        description = arguments.get("description", "")
        try:
            project_id = await self.container.project_store.create_project(name=name, description=description)
            return _ok({"project_id": project_id, "name": name})
        except Exception as exc:
            logger.error("MCP project create failed: %s", exc, exc_info=True)
            return _error(INTERNAL_ERROR, f"Failed to create project: {exc}", {})

    async def _dispatch_artifact_list(self, arguments: dict) -> dict:
        """aip_artifact_list: List artifacts via ArtifactStore."""
        if not self.container.artifact_store:
            return _error(BACKEND_UNAVAILABLE, "Artifact store not configured", {})

        try:
            project_id = arguments.get("project_id")
            limit = arguments.get("limit", 100)
            # ArtifactStore may not have a list method — return NOT_IMPLEMENTED if not
            if not hasattr(self.container.artifact_store, "list_artifacts"):
                return _error(NOT_IMPLEMENTED, "Artifact listing not implemented in current store", {})
            artifacts = await self.container.artifact_store.list_artifacts(project_id=project_id, limit=limit)
            return _ok({"artifacts": artifacts, "count": len(artifacts)})
        except Exception as exc:
            logger.error("MCP artifact list failed: %s", exc, exc_info=True)
            return _error(INTERNAL_ERROR, f"Failed to list artifacts: {exc}", {})

    async def _dispatch_trace_query(self, arguments: dict) -> dict:
        """aip_trace_query: Query trace events via EventStore."""
        if not self.container.event_store:
            return _error(BACKEND_UNAVAILABLE, "Event store not configured", {})

        try:
            artifact_id = arguments.get("artifact_id")
            event_type = arguments.get("event_type")
            limit = arguments.get("limit", 100)
            events = await self.container.event_store.query_events(
                artifact_id=artifact_id,
                event_type=event_type,
                limit=limit,
            )
            return _ok({"events": events, "count": len(events)})
        except Exception as exc:
            logger.error("MCP trace query failed: %s", exc, exc_info=True)
            return _error(INTERNAL_ERROR, f"Failed to query events: {exc}", {})

    async def _dispatch_config_read(self, arguments: dict) -> dict:
        """aip_config_read: Read configuration."""
        section = arguments.get("section")
        config = self.container.config or {}
        if section:
            value = config.get(section)
            if value is None:
                return _error(NOT_FOUND, f"Config section not found: {section}", {"section": section})
            return _ok({"section": section, "values": value})
        return _ok({"config": {k: v for k, v in config.items() if k not in ("password", "secret", "token")}})

    async def _dispatch_config_write(self, arguments: dict) -> dict:
        """aip_config_write: Write configuration.

        Config writes are dangerous — for alpha, we return NOT_IMPLEMENTED
        to prevent accidental or automated config modification through MCP.
        The REST/CLI paths should be used for config changes.
        """
        section = arguments.get("section", "").strip()
        return _error(
            NOT_IMPLEMENTED,
            "Config write through MCP is not implemented. Use REST API or CLI for configuration changes.",
            {"section": section, "reason": "MCP config write requires additional safety validation"},
        )
