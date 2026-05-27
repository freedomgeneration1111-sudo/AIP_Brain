"""Adapter package for compiled knowledge storage (CHUNK-10.0b).

Exports the SQLite implementation of KnowledgeStore.
"""

from aip.adapter.knowledge.sqlite_knowledge_store import SqliteKnowledgeStore

__all__ = ["SqliteKnowledgeStore"]
