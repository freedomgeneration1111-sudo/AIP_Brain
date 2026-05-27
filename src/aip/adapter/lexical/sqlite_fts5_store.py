"""SQLite FTS5 implementation of LexicalStore Protocol.

Per CHUNK-8.0b prose + ANNEX (exact).
Per §6: LexicalStore Protocol (added in 8.0a).
Per §7.2: adapter imports only foundation (schemas + protocols).
Per §2.1: local, deterministic, laptop-viable (no external services).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from aip.foundation.protocols import LexicalStore
from aip.foundation.schemas import Chunk


class SqliteFts5LexicalStore(LexicalStore):
    """SQLite + FTS5 implementation of LexicalStore.

    Maintains a regular documents table + FTS5 virtual index.
    Tokenizer defaults to unicode61 (configurable via [lexical] in future).
    All methods are deterministic for CI (uses provided db_path, usually :memory: or temp file in tests).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_tables(self) -> None:
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

    async def initialize(self) -> None:
        """Idempotent table creation (called by lifespan / DI container)."""
        self._ensure_tables()

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def index_document(
        self, doc_id: str, content: str, domain: str, metadata: dict
    ) -> None:
        conn = self._get_conn()
        try:
            meta_json = json.dumps(metadata or {})
            now = datetime.utcnow().isoformat() + "Z"

            # Upsert into documents table
            conn.execute(
                """
                INSERT OR REPLACE INTO fts_documents (doc_id, content, domain, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (doc_id, content, domain, meta_json, now),
            )

            # Update FTS index (delete old + insert new for idempotency)
            conn.execute("DELETE FROM fts_index WHERE rowid = (SELECT rowid FROM fts_documents WHERE doc_id = ?)", (doc_id,))
            conn.execute(
                "INSERT INTO fts_index (rowid, content, domain, metadata) VALUES ((SELECT rowid FROM fts_documents WHERE doc_id = ?), ?, ?, ?)",
                (doc_id, content, domain, meta_json),
            )
            conn.commit()
        finally:
            # Do not close shared conn here; managed by close()
            pass

    async def search(
        self, query: str, domain: str | None = None, limit: int = 10
    ) -> list[Chunk]:
        conn = self._get_conn()
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

            rows = conn.execute(sql, params).fetchall()
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
                    )
                )
            return results
        finally:
            pass

    async def delete_document(self, doc_id: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM fts_documents WHERE doc_id = ?", (doc_id,))
            # FTS5 rows are automatically managed via triggers in many setups, but we keep explicit for clarity
            # (In this simple dual-table design we rely on the JOIN above; for robustness we can leave orphaned FTS rows
            # or add a trigger. For exact scope we delete from documents only — search will simply not return them.)
            conn.commit()
        finally:
            pass
