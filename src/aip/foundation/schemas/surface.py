"""Surface (API, CLI, Chat, MCP) types.

Configuration and definitions for AIP interaction surfaces:
API routes, MCP tool definitions, chat messages, and surface config.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .auth import McpAutonomyLevel


@dataclass
class SurfaceConfig:
    """Configuration for AIP surfaces (API, CLI, Chat, MCP).

    All parameters toggleable via config.
    Surfaces must respect laptop-viable hardware profile.
    Surfaces are adapter-layer, composing Foundation and Orchestration.
    """

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    api_workers: int = 1
    chat_max_history_turns: int = 50
    review_page_size: int = 20
    artifact_page_size: int = 20


@dataclass
class ApiRoute:
    """A single REST API route definition.

    autonomy_gate=True routes enforce DEFINER sovereignty.
    All routes are adapter-layer compositions.
    """

    method: str
    path: str
    handler: str
    auth_required: bool = False
    autonomy_gate: bool = False


@dataclass
class McpToolDef:
    """A single MCP tool definition.

    MCP/API surface.
    Per Appendix D: "MCP ≠ bypass", "MCP ≠ vector_store.retrieve() directly."
    model_gen_assumption tags what model limitation this tool compensates for.
    """

    tool_name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    autonomy_level: McpAutonomyLevel = "read"
    model_gen_assumption: str | None = None


@dataclass
class ChatMessage:
    """A single chat message in the DEFINER conversation surface.

    Chat surface is the primary DEFINER interaction point.
    Context is assembled from explicit stores, not long chat history.
    """

    message_id: str
    session_id: str
    role: str  # user / assistant / system
    content: str = ""
    artifacts_referenced: list[str] = field(default_factory=list)
    tokens_used: int = 0
    created_at: str = ""  # REQUIRED — ISO 8601


__all__ = [
    "SurfaceConfig",
    "ApiRoute",
    "McpToolDef",
    "ChatMessage",
]
