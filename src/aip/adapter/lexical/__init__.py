"""LexicalStore adapter implementations (CHUNK-8.0b)."""

from .sqlite_fts5_store import SqliteFts5LexicalStore

__all__ = ["SqliteFts5LexicalStore"]
