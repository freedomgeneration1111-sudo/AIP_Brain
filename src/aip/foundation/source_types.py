"""Source model provenance types — cross-layer shared constants.

These constants identify export formats / provenance tags for ingested
content.  They are NOT model API names — they describe the *format*
a conversation or document was exported from, so that the correct parser
can be selected.

All vendor-specific provenance tags are centralised here so that
production code never contains hardcoded provider name strings.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Provenance tags for conversation export formats
# ---------------------------------------------------------------------------

#: Provenance tag for chat-export-format conversations
CHAT_EXPORT_FORMAT = "chat_export"

#: Provenance tag for generic conversation sources
CONVERSATION_EXPORT_FORMAT = "conversation_export"

# ---------------------------------------------------------------------------
# Set of provenance tags that indicate a conversation (not a document)
# ---------------------------------------------------------------------------

#: Source models whose content is conversational rather than document-based
CONVERSATION_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "chat_export",
        "conversation_export",
        "aip_chat",
    }
)

# ---------------------------------------------------------------------------
# Legacy-to-neutral mapping
# ---------------------------------------------------------------------------

#: Maps legacy provenance strings to their neutral replacements.
#: Used during ingestion to normalise source_model values.
LEGACY_SOURCE_MODEL_MAP: dict[str, str] = {
    "claude": CHAT_EXPORT_FORMAT,
    "gpt": CONVERSATION_EXPORT_FORMAT,
    "deepseek": CHAT_EXPORT_FORMAT,
    "glm": CHAT_EXPORT_FORMAT,
    "gemini": CHAT_EXPORT_FORMAT,
    "grok": CHAT_EXPORT_FORMAT,
}
