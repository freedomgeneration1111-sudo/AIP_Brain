"""ChatGPT export JSON parser.

Parses the standard ChatGPT ``conversations.json`` export format.
Each entry in the top-level array contains a ``mapping`` dict of
message nodes linked by parent/child IDs. This parser walks the
tree to reconstruct conversation threads in chronological order.

A single ChatGPT export file may contain many conversations — this
parser returns one ``ImportedConversation`` per conversation found.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from aip.foundation.schemas.ingestion import (
    ConversationTurn,
    ImportedConversation,
)


def parse_chatgpt_export(data: str | dict | list, source_file: str = "<chatgpt_export>") -> list[ImportedConversation]:
    """Parse ChatGPT conversations.json export.

    Accepts:
    - Raw JSON string
    - Parsed dict (single conversation)
    - Parsed list (array of conversations)

    Returns one ImportedConversation per conversation found.
    Skips conversations with no user-visible turns.
    """
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid ChatGPT export JSON: {exc}") from exc
    else:
        parsed = data

    # Normalize to list
    if isinstance(parsed, dict):
        conversations = [parsed]
    elif isinstance(parsed, list):
        conversations = parsed
    else:
        raise ValueError(f"Expected dict or list, got {type(parsed).__name__}")

    results: list[ImportedConversation] = []
    for conv in conversations:
        imported = _parse_single_conversation(conv, source_file)
        if imported and imported.turns:
            results.append(imported)

    return results


def _parse_single_conversation(conv: dict, source_file: str) -> ImportedConversation | None:
    """Parse a single ChatGPT conversation object."""
    title = conv.get("title", "Untitled Conversation")
    mapping = conv.get("mapping", {})
    create_time = conv.get("create_time")

    if not mapping:
        return None

    # Build parent→children map and find root nodes
    nodes: dict[str, dict] = {}
    root_id: str | None = None

    for node_id, node in mapping.items():
        nodes[node_id] = node
        parent = node.get("parent")
        if parent is None:
            root_id = node_id

    if root_id is None:
        return None

    # Walk the tree from root, following the first child at each step
    # (ChatGPT conversations are linear — each node has at most one child
    # for the main thread; branching is rare in exports)
    turns: list[ConversationTurn] = []
    visited: set[str] = set()
    current_id = root_id

    while current_id and current_id not in visited:
        visited.add(current_id)
        node = nodes.get(current_id, {})
        message = node.get("message")

        if message:
            author = message.get("author", {})
            role = author.get("role", "unknown")
            content_parts = message.get("content", {}).get("parts", [])

            # Extract text from content parts
            text_parts: list[str] = []
            for part in content_parts:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))

            content = "\n".join(text_parts).strip()

            if content and role in ("user", "assistant", "system", "tool"):
                ts = ""
                msg_time = message.get("create_time")
                if msg_time is not None:
                    try:
                        dt = datetime.fromtimestamp(float(msg_time), tz=timezone.utc)
                        ts = dt.isoformat()
                    except (ValueError, OSError):
                        ts = ""

                turns.append(ConversationTurn(role=role, content=content, timestamp=ts))

        # Follow first child (main thread)
        children = node.get("children", [])
        if children:
            current_id = children[0]
        else:
            break

    # Generate a stable conversation ID from title + create_time
    conv_id = f"chatgpt:{uuid.uuid5(uuid.NAMESPACE_URL, f'{source_file}:{title}:{create_time}')}"
    # Make it filesystem-safe
    conv_id = conv_id.replace(" ", "_")

    imported_at = datetime.now(timezone.utc).isoformat()

    return ImportedConversation(
        conversation_id=conv_id,
        title=title,
        turns=turns,
        source_format="chatgpt_json",
        source_file=source_file,
        imported_at=imported_at,
        metadata={
            "chatgpt_create_time": create_time,
            "chatgpt_update_time": conv.get("update_time"),
        },
    )
