"""Recursive character text chunker for ingestion.

Splits conversation content into overlapping chunks suitable for
lexical (FTS5) and vector indexing. Respects paragraph and sentence
boundaries where possible.

Keeps each chunk under ``max_chars`` with ``overlap_chars`` of
overlap for context continuity across chunk boundaries.
"""

from __future__ import annotations

from aip.foundation.schemas.ingestion import ImportedConversation

# Sensible defaults for conversation content
_DEFAULT_MAX_CHARS = 500
_DEFAULT_OVERLAP_CHARS = 50
# Minimum chunk size — avoids degenerate tiny chunks
_MIN_CHUNK_CHARS = 50


def _split_paragraphs(text: str) -> list[str]:
    """Split text on double-newline paragraph boundaries."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _split_sentences(text: str) -> list[str]:
    """Split text on sentence-ending punctuation.

    Keeps the punctuation attached to the preceding sentence.
    """
    import re

    parts = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in parts if s.strip()]


def _merge_chunks(candidates: list[str], max_chars: int) -> list[str]:
    """Merge short candidates up to max_chars."""
    merged: list[str] = []
    current = ""

    for candidate in candidates:
        if not current:
            current = candidate
        elif len(current) + 1 + len(candidate) <= max_chars:
            current = current + "\n" + candidate
        else:
            if len(current) >= _MIN_CHUNK_CHARS:
                merged.append(current)
            current = candidate

    if current and len(current) >= _MIN_CHUNK_CHARS:
        merged.append(current)
    elif current and merged:
        # Last piece too small — fold into previous chunk
        merged[-1] = merged[-1] + "\n" + current
    elif current:
        merged.append(current)

    return merged


def chunk_text(
    text: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
    overlap_chars: int = _DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split plain text into overlapping chunks.

    Strategy: paragraph boundaries first, then sentence boundaries,
    then hard character split with overlap.
    """
    if not text or not text.strip():
        return []

    if len(text) <= max_chars:
        return [text.strip()]

    # Try paragraph-level split first
    paragraphs = _split_paragraphs(text)
    if len(paragraphs) > 1:
        chunks = _merge_chunks(paragraphs, max_chars)
        if len(chunks) > 1:
            return _apply_overlap(chunks, overlap_chars)

    # Try sentence-level split
    sentences = _split_sentences(text)
    if len(sentences) > 1:
        chunks = _merge_chunks(sentences, max_chars)
        if len(chunks) > 1:
            return _apply_overlap(chunks, overlap_chars)

    # Hard character split with overlap
    return _hard_split(text, max_chars, overlap_chars)


def _apply_overlap(chunks: list[str], overlap_chars: int) -> list[str]:
    """Prepend overlap from the previous chunk to maintain context."""
    if overlap_chars <= 0 or len(chunks) <= 1:
        return chunks

    overlapped: list[str] = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_tail = chunks[i - 1][-overlap_chars:]
        overlapped.append(prev_tail + "\n" + chunks[i])
    return overlapped


def _hard_split(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Hard character-level split with overlap."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap_chars
        if start <= end - max_chars:
            # Prevent infinite loop on tiny overlap
            start = end

    return chunks


def chunk_conversation(
    conversation: ImportedConversation,
    max_chars: int = _DEFAULT_MAX_CHARS,
    overlap_chars: int = _DEFAULT_OVERLAP_CHARS,
) -> list[tuple[str, str]]:
    """Chunk an imported conversation into (chunk_id, chunk_text) pairs.

    Each turn is formatted as ``[role] content`` and turns are
    concatenated before chunking so that context spans turn
    boundaries where needed.

    Returns list of (chunk_id, chunk_text) where chunk_id is
    ``chunk:{conversation_id}:{index}``.
    """
    # Build a single text block from turns, preserving role labels
    turn_texts: list[str] = []
    for turn in conversation.turns:
        label = turn.role.upper()
        timestamp = f" ({turn.timestamp})" if turn.timestamp else ""
        turn_texts.append(f"[{label}{timestamp}] {turn.content}")

    full_text = "\n\n".join(turn_texts)

    if not full_text.strip():
        return []

    raw_chunks = chunk_text(full_text, max_chars=max_chars, overlap_chars=overlap_chars)

    result: list[tuple[str, str]] = []
    for idx, chunk_content in enumerate(raw_chunks):
        chunk_id = f"chunk:{conversation.conversation_id}:{idx}"
        result.append((chunk_id, chunk_content))

    return result
