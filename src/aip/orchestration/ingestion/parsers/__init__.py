"""Conversation format parsers.

Each parser reads a specific source format and returns an
ImportedConversation. Auto-detection delegates to the appropriate
parser based on file extension and content heuristics.
"""

from __future__ import annotations

from aip.foundation.schemas.ingestion import SourceFormat

from .chatgpt import parse_chatgpt_export
from .markdown import parse_markdown_transcript
from .plaintext import parse_plaintext_transcript

__all__ = [
    "parse_chatgpt_export",
    "parse_markdown_transcript",
    "parse_plaintext_transcript",
    "detect_format",
]


def detect_format(path: str, content: str | None = None) -> SourceFormat:
    """Auto-detect the conversation format from file path and content.

    Priority: file extension first, then content heuristics.
    Falls back to ``plaintext`` when no stronger signal exists.
    """
    path_lower = path.lower()

    # ChatGPT export is always JSON — check extension
    if path_lower.endswith(".json"):
        # Could be ChatGPT export — check content if available
        if content is not None:
            stripped = content.strip()
            if stripped.startswith("[") or stripped.startswith("{"):
                return "chatgpt_json"
        return "chatgpt_json"

    # Markdown files
    if path_lower.endswith(".md") or path_lower.endswith(".markdown"):
        return "markdown"

    # Everything else defaults to plain text
    return "plaintext"
