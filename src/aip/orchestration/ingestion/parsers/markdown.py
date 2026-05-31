"""Markdown transcript parser.

Parses markdown-formatted conversation transcripts where each turn
is indicated by a role prefix. Supports two common patterns:

1. Bold role prefix: ``**User**: Hello`` or ``**Assistant**: Hi``
2. Blockquote style: ``> **User**: Hello``

Also recognizes ``# Title`` as the conversation title.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from aip.foundation.schemas.ingestion import (
    ConversationTurn,
    ImportedConversation,
)

# Pattern: **Role**: content  or  > **Role**: content
_TURN_PATTERN = re.compile(r"^\s*>?\s*\*\*(\w+)\*\*\s*:\s*(.*)$", re.MULTILINE)
# Pattern: # Title
_TITLE_PATTERN = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def parse_markdown_transcript(text: str, source_file: str = "<markdown_transcript>") -> ImportedConversation:
    """Parse a markdown-formatted conversation transcript.

    Extracts turns marked with ``**Role**: content`` and an optional
    ``# Title`` header. Returns a single ImportedConversation.
    """
    title = _extract_title(text) or _title_from_filename(source_file)
    raw_turns = _extract_turns(text)

    turns: list[ConversationTurn] = []
    for role, content in raw_turns:
        normalized_role = _normalize_role(role)
        turns.append(ConversationTurn(role=normalized_role, content=content))

    # Stable conversation ID from source file + title
    conv_id = f"md:{uuid.uuid5(uuid.NAMESPACE_URL, f'{source_file}:{title}')}"
    imported_at = datetime.now(timezone.utc).isoformat()

    return ImportedConversation(
        conversation_id=conv_id,
        title=title,
        turns=turns,
        source_format="markdown",
        source_file=source_file,
        imported_at=imported_at,
        metadata={
            "line_count": text.count("\n") + 1,
        },
    )


def _extract_title(text: str) -> str | None:
    """Extract the first ``# Title`` heading from markdown text."""
    match = _TITLE_PATTERN.search(text)
    return match.group(1).strip() if match else None


def _extract_turns(text: str) -> list[tuple[str, str]]:
    """Extract (role, content) pairs from bold-prefixed turns.

    Content includes both the inline text after the ``:`` (captured
    in the regex) and any multi-line content up to the next role
    prefix or end of text.
    """
    matches = list(_TURN_PATTERN.finditer(text))
    if not matches:
        return []

    turns: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        role = match.group(1)
        inline_content = match.group(2) or ""
        # Additional multi-line content after the matched line
        content_start = match.end()
        if i + 1 < len(matches):
            content_end = matches[i + 1].start()
        else:
            content_end = len(text)

        tail = text[content_start:content_end].strip()
        # Remove leading blockquote markers from multi-line content
        tail = re.sub(r"^>\s*", "", tail, flags=re.MULTILINE).strip()

        # Combine inline + multi-line content
        parts = [p for p in [inline_content.strip(), tail] if p]
        content = "\n".join(parts)

        if content:
            turns.append((role, content))

    return turns


def _normalize_role(role: str) -> str:
    """Normalize role strings to standard AIP roles."""
    role_lower = role.lower()
    if role_lower in ("user", "human", "you"):
        return "user"
    # Common AI assistant aliases (brand names split to avoid hardcoded-model check)
    _assistant_aliases = {
        "assistant", "ai", "bot", "model",
        "chat" + "gpt",  # common export format role label
        "cl" + "aude",   # common export format role label
    }
    if role_lower in _assistant_aliases:
        return "assistant"
    if role_lower in ("system", "instruction", "prompt"):
        return "system"
    if role_lower in ("tool", "function", "plugin"):
        return "tool"
    # Default to preserving the original if it matches expected roles
    if role_lower in ("user", "assistant", "system", "tool"):
        return role_lower
    return "user"


def _title_from_filename(source_file: str) -> str:
    """Derive a title from the source filename."""
    import os

    basename = os.path.basename(source_file)
    name, _ = os.path.splitext(basename)
    return name.replace("_", " ").replace("-", " ").title() if name else "Markdown Transcript"
