"""SQLite FTS5 implementation of LexicalStore Protocol.

LexicalStore Protocol.
Adapter imports only foundation (schemas + protocols).
Local, deterministic, laptop-viable (no external services).
Uses aiosqlite for async-safe database access.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.foundation.protocols import LexicalStore
from aip.foundation.schemas import Chunk


class SqliteFts5LexicalStore(LexicalStore):
    """SQLite + FTS5 implementation of LexicalStore.

    Maintains a regular documents table + FTS5 virtual index.
    Tokenizer defaults to unicode61 (configurable via [lexical] in future).
    All methods are deterministic for CI (uses provided db_path, usually :memory: or temp file in tests).
    Uses aiosqlite for async-compatible database access.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._ensure_tables_sync()

    def _ensure_tables_sync(self) -> None:
        """Synchronous table creation during init (runs once at startup)."""
        conn = sqlite3.connect(self._db_path)
        try:
            # Regular documents table (source of truth + metadata)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fts_documents (
                    doc_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    metadata TEXT NOT NULL,  -- JSON
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            # FTS5 virtual table (search index)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_index
                USING fts5(content, domain, metadata, tokenize=unicode61)
            """)
            conn.commit()
        finally:
            conn.close()

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def _ensure_tables(self) -> None:
        conn = await aiosqlite.connect(self._db_path)
        try:
            # Regular documents table (source of truth + metadata)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS fts_documents (
                    doc_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    metadata TEXT NOT NULL,  -- JSON
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            # FTS5 virtual table (search index)
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_index
                USING fts5(content, domain, metadata, tokenize=unicode61)
            """)
            await conn.commit()
        finally:
            await conn.close()

    async def initialize(self) -> None:
        """Idempotent table creation (called by lifespan / DI container)."""
        await self._ensure_tables()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def index_document(self, doc_id: str, content: str, domain: str, metadata: dict) -> None:
        conn = await self._get_conn()
        try:
            meta_json = json.dumps(metadata or {})
            now = datetime.now(timezone.utc).isoformat() + "Z"

            # Upsert into documents table
            await conn.execute(
                """
                INSERT OR REPLACE INTO fts_documents (doc_id, content, domain, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (doc_id, content, domain, meta_json, now),
            )

            # Update FTS index (delete old + insert new for idempotency)
            await conn.execute(
                "DELETE FROM fts_index WHERE rowid = (SELECT rowid FROM fts_documents WHERE doc_id = ?)",
                (doc_id,),
            )
            await conn.execute(
                "INSERT INTO fts_index (rowid, content, domain, metadata) "
                "VALUES ((SELECT rowid FROM fts_documents WHERE doc_id = ?), ?, ?, ?)",
                (doc_id, content, domain, meta_json),
            )
            await conn.commit()
        finally:
            await conn.close()
            self._conn = None

    async def search(self, query: str, domain: str | None = None, limit: int = 10) -> list[Chunk]:
        conn = await self._get_conn()
        try:
            sql = """
                SELECT d.doc_id, d.content, d.domain, d.metadata, f.rank
                FROM fts_index f
                JOIN fts_documents d ON f.rowid = d.rowid
                WHERE fts_index MATCH ?
            """
            params: list[Any] = [query]
            if domain:
                sql += " AND d.domain = ?"
                params.append(domain)
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)

            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            results: list[Chunk] = []
            for row in rows:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
                results.append(
                    Chunk(
                        id=row["doc_id"],
                        content=row["content"],
                        score=float(row["rank"]) if row["rank"] is not None else 0.0,
                        metadata=meta,
                        domain=row["domain"],
                    ),
                )
            return results
        finally:
            await conn.close()
            self._conn = None

    async def delete_document(self, doc_id: str) -> None:
        conn = await self._get_conn()
        try:
            # Delete from FTS5 virtual table index first
            await conn.execute(
                "DELETE FROM fts_index WHERE rowid = (SELECT rowid FROM fts_documents WHERE doc_id = ?)",
                (doc_id,),
            )
            # Then delete from the documents table
            await conn.execute("DELETE FROM fts_documents WHERE doc_id = ?", (doc_id,))
            await conn.commit()
        finally:
            await conn.close()
            self._conn = None
