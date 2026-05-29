"""AipMcpServer.

Per spec: takes AipContainer, supports stdio/sse, list_tools() with McpToolDef
(autonomy_level + model_gen_assumption), enforces gate for write/admin tools before dispatch.
Appendix D: MCP routes through Protocols, not around them (no direct store access).
"""

from __future__ import annotations

from typing import Any

from aip.foundation.schemas import McpToolDef, coerce_autonomy_level, coerce_mcp_autonomy_level

# Tool registry (autonomy declared per spec)
TOOLS: list[dict[str, Any]] = [
    {
        "name": "aip_search",
        "autonomy": "read",
        "model_gen": "Models may hallucinate without retrieved context",
        "desc": "Hybrid lexical + semantic search via Protocols",
    },
    {"name": "aip_project_list", "autonomy": "read", "model_gen": None, "desc": "List projects"},
    {"name": "aip_project_create", "autonomy": "write", "model_gen": None, "desc": "Create project (write gate)"},
    {"name": "aip_artifact_list", "autonomy": "read", "model_gen": None, "desc": "List artifacts"},
    {
        "name": "aip_artifact_approve",
        "autonomy": "admin",
        "model_gen": "Models should not autonomously approve artifacts",
        "desc": "Approve artifact (admin gate + canonical promotion)",
    },
    {"name": "aip_trace_query", "autonomy": "read", "model_gen": None, "desc": "Query trace events"},
    {"name": "aip_config_read", "autonomy": "read", "model_gen": None, "desc": "Read config"},
    {
        "name": "aip_config_write",
        "autonomy": "admin",
        "model_gen": "Models should not autonomously modify harness config",
        "desc": "Write config (admin gate)",
    },
]


class AipMcpServer:
    def __init__(self, container: Any) -> None:
        self.container = container
        self._running = False

    async def start(self, transport: str = "stdio") -> None:
        self._running = True
        # In real impl: stdio loop or SSE server using the container
        # For 8.5 scaffold we just mark running

    async def stop(self) -> None:
        self._running = False

    def list_tools(self) -> list[McpToolDef]:
        defs = []
        for t in TOOLS:
            defs.append(
                McpToolDef(
                    tool_name=t["name"],
                    description=t["desc"],
                    input_schema={},
                    autonomy_level=coerce_mcp_autonomy_level(t["autonomy"]),
                    model_gen_assumption=t["model_gen"],
                ),
            )
        return defs

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Dispatch with autonomy enforcement (same gate as REST/CLI)."""
        tool_def = next((t for t in TOOLS if t["name"] == name), None)
        if not tool_def:
            return {"error": "unknown tool"}

        level = tool_def["autonomy"]
        if level in ("write", "admin") and self.container.autonomy_gate:
            esc = await self.container.autonomy_gate.escalate(
                action_type=f"mcp_{name}",
                resource_id=arguments.get("artifact_id") or arguments.get("name") or "mcp",
                requested_level=coerce_autonomy_level("admin" if level == "admin" else "write"),
                requested_by="mcp",
            )
            if not esc.granted:
                return {"error": f"Autonomy gate blocked: {esc.reason}", "escalation": esc}

        # Dispatch to real Protocol impls via container (no direct storage)
        if name == "aip_search":
            # Uses LexicalStore + VectorStore (8.0b) via container
            return {"results": []}  # scaffold
        if name == "aip_artifact_approve":
            # Would call the same logic as review approve (ECS + Canonical + Gate already checked)
            return {"approved": True, "canonical": True}
        # ... other tools delegate similarly
        return {"ok": True, "tool": name, "args": arguments}
