"""Adapter package for compiled knowledge storage.

Exports the SQLite implementation of KnowledgeStore.
"""

from aip.adapter.knowledge.sqlite_knowledge_store import SqliteKnowledgeStore

__all__ = ["SqliteKnowledgeStore"]
