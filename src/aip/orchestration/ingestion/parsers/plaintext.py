"""Plain text transcript parser.

Parses simple plain text conversation transcripts where each turn
is indicated by a ``Role:`` prefix on a line. Supports common
patterns:

- ``User: Hello``
- ``Assistant: Hi there``

Lines without a recognized role prefix are appended to the
previous turn's content.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from aip.foundation.schemas.ingestion import (
    ConversationTurn,
    ImportedConversation,
)

# Pattern: Role: content  (Role can be User, Assistant, System, Tool, etc.)
_TURN_PATTERN = re.compile(
    r"^(User|Assistant|System|Tool|Human|AI|Bot|You|Model)\s*:\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)

# Also match bracketed forms: [User] content
_BRACKET_PATTERN = re.compile(
    r"^\[(User|Assistant|System|Tool|Human|AI|Bot|Model)\]\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_plaintext_transcript(text: str, source_file: str = "<plaintext_transcript>") -> ImportedConversation:
    """Parse a plain text conversation transcript.

    Extracts turns marked with ``Role: content`` or ``[Role] content``
    prefixes. Returns a single ImportedConversation.
    """
    turns = _extract_turns(text)

    title = _derive_title(text, source_file)

    conv_id = f"txt:{uuid.uuid5(uuid.NAMESPACE_URL, f'{source_file}:{title}')}"
    imported_at = datetime.now(timezone.utc).isoformat()

    return ImportedConversation(
        conversation_id=conv_id,
        title=title,
        turns=turns,
        source_format="plaintext",
        source_file=source_file,
        imported_at=imported_at,
        metadata={
            "line_count": text.count("\n") + 1,
        },
    )


def _extract_turns(text: str) -> list[ConversationTurn]:
    """Extract ConversationTurn objects from plain text.

    Handles both ``Role: content`` and ``[Role] content`` patterns.
    Lines between role markers are appended to the current turn.
    """
    lines = text.split("\n")
    turns: list[ConversationTurn] = []
    current_role: str | None = None
    current_content: list[str] = []

    for line in lines:
        # Check standard prefix
        std_match = _TURN_PATTERN.match(line)
        # Check bracket prefix
        brk_match = _BRACKET_PATTERN.match(line)

        if std_match:
            # Flush previous turn
            if current_role and current_content:
                content = "\n".join(current_content).strip()
                if content:
                    turns.append(ConversationTurn(role=_normalize_role(current_role), content=content))
            current_role = std_match.group(1)
            current_content = [std_match.group(2)] if std_match.group(2) else []

        elif brk_match:
            if current_role and current_content:
                content = "\n".join(current_content).strip()
                if content:
                    turns.append(ConversationTurn(role=_normalize_role(current_role), content=content))
            current_role = brk_match.group(1)
            current_content = [brk_match.group(2)] if brk_match.group(2) else []

        elif current_role is not None:
            # Continuation line — append to current turn
            current_content.append(line)
        # else: orphan line before first role marker — skip

    # Flush final turn
    if current_role and current_content:
        content = "\n".join(current_content).strip()
        if content:
            turns.append(ConversationTurn(role=_normalize_role(current_role), content=content))

    return turns


def _normalize_role(role: str) -> str:
    """Normalize role strings to standard AIP roles."""
    role_lower = role.lower()
    if role_lower in ("user", "human", "you"):
        return "user"
    if role_lower in ("assistant", "ai", "bot", "model"):
        return "assistant"
    if role_lower in ("system",):
        return "system"
    if role_lower in ("tool",):
        return "tool"
    return "user"


def _derive_title(text: str, source_file: str) -> str:
    """Derive a conversation title from the first line or filename."""
    import os

    # Try first non-empty line as title (if short enough)
    for line in text.split("\n"):
        line = line.strip()
        if line and len(line) <= 100 and not _TURN_PATTERN.match(line) and not _BRACKET_PATTERN.match(line):
            return line

    # Fall back to filename
    basename = os.path.basename(source_file)
    name, _ = os.path.splitext(basename)
    return name.replace("_", " ").replace("-", " ").title() if name else "Plain Text Transcript"
