"""SQLite FTS5 implementation of LexicalStore Protocol.

Adapter imports only foundation (schemas + protocols).
Local, deterministic, laptop-viable (no external services).
Uses aiosqlite for async-safe database access.

Sprint 5.13: Removed blocking sqlite3.connect() from __init__.
All table creation and I/O now goes through async initialize() and
_get_conn(). The constructor is lightweight (stores path only).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.foundation.protocols import LexicalStore
from aip.foundation.schemas import Chunk

# Single source of truth for DDL statements
_DDL_FTS_DOCUMENTS = """
    CREATE TABLE IF NOT EXISTS fts_documents (
        doc_id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        domain TEXT NOT NULL,
        metadata TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""

_DDL_FTS_INDEX = """
    CREATE VIRTUAL TABLE IF NOT EXISTS fts_index
    USING fts5(content, domain, metadata, tokenize=unicode61)
"""


class SqliteFts5LexicalStore(LexicalStore):
    """SQLite + FTS5 implementation of LexicalStore.

    Maintains a regular documents table + FTS5 virtual index.
    Tokenizer defaults to unicode61 (configurable via [lexical] in future).
    Uses aiosqlite for async-compatible database access.

    Sprint 5.13: Constructor is lightweight — stores path only.
    Call ``initialize()`` (async) to create tables before first use.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._tables_ready = False

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return a reusable connection, creating one if needed.

        Lazily ensures tables on first connection so that callers
        who bypass ``initialize()`` (e.g. tests) still get a working schema.
        """
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create FTS documents and index tables on the given connection."""
        await conn.execute(_DDL_FTS_DOCUMENTS)
        await conn.execute(_DDL_FTS_INDEX)
        await conn.commit()

    async def initialize(self) -> None:
        """Idempotent table creation (called by lifespan / DI container).

        Uses a short-lived connection to create tables, then discards it.
        Subsequent operations use the persistent connection from _get_conn().
        """
        if self._tables_ready:
            return
        conn = await aiosqlite.connect(self._db_path)
        try:
            await self._create_tables(conn)
            self._tables_ready = True
        finally:
            await conn.close()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def index_document(self, doc_id: str, content: str, domain: str, metadata: dict) -> None:
        conn = await self._get_conn()
        try:
            meta_json = json.dumps(metadata or {})
            now = datetime.now(timezone.utc).isoformat() + "Z"

            await conn.execute(
                """
                INSERT OR REPLACE INTO fts_documents (doc_id, content, domain, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (doc_id, content, domain, meta_json, now),
            )

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
            # Close connection after each write to avoid holding locks
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
            await conn.execute(
                "DELETE FROM fts_index WHERE rowid = (SELECT rowid FROM fts_documents WHERE doc_id = ?)",
                (doc_id,),
            )
            await conn.execute("DELETE FROM fts_documents WHERE doc_id = ?", (doc_id,))
            await conn.commit()
        finally:
            await conn.close()
            self._conn = None
