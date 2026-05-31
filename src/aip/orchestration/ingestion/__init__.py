"""Conversation ingestion pipeline.

Parses external conversation formats (ChatGPT export JSON, markdown
transcripts, plain text) into AIP's internal representation, then
persists and indexes them using existing stores.

No parallel storage system — uses ArtifactStore, LexicalStore,
VectorStore, and EventStore directly.
"""

from __future__ import annotations

from .chunker import chunk_conversation
from .pipeline import ingest_conversation, ingest_file

__all__ = [
    "chunk_conversation",
    "ingest_conversation",
    "ingest_file",
]
