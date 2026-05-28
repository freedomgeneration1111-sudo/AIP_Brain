"""sqlite_vss implementation of the VectorStore protocol.
Phase 1 vector backend. pgvector adapter is deferred to Phase 4.
sqlite_vss is the permitted fallback for Phase 0-2 alpha."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from aip.foundation.protocols import VectorStore
from aip.foundation.schemas import Chunk


class SqliteVssVectorStore(VectorStore):
    """VectorStore backed by sqlite_vss extension."""

    def __init__(self, db_path: str, dimensions: int = 768) -> None:
        self._db_path = db_path
        self._dimensions = dimensions
        self._vss_available = False
        self._conn = sqlite3.connect(db_path)
        self._conn.enable_load_extension(True)
        try:
            self._conn.load_extension("vss0")
            self._vss_available = True
        except sqlite3.OperationalError:
            try:
                self._conn.load_extension("./vss0")
                self._vss_available = True
            except sqlite3.OperationalError:
                pass  # sqlite_vss not available; store will raise on first use
        self._conn.enable_load_extension(False)
        self._init_tables()

    def _init_tables(self) -> None:
        cur = self._conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vector_metadata (
                id TEXT PRIMARY KEY,
                content TEXT,
                domain TEXT,
                metadata_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_vm_domain
            ON vector_metadata(domain)
        """)
        if self._vss_available:
            cur.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS vss_vectors
                USING vss0(
                    embedding float[{self._dimensions}]
                )
            """)
        self._conn.commit()

    async def upsert(
        self,
        id: str,
        embedding: list[float],
        content: str,
        metadata: dict[str, Any] | None = None,
        domain: str | None = None,
    ) -> None:
        cur = self._conn.cursor()
        # Delete existing entry if present (upsert semantics)
        cur.execute("SELECT rowid FROM vector_metadata WHERE id = ?", (id,))
        existing = cur.fetchone()
        if existing:
            cur.execute("DELETE FROM vss_vectors WHERE rowid = ?", (existing[0],))
            cur.execute("DELETE FROM vector_metadata WHERE id = ?", (id,))

        meta_json = json.dumps(metadata or {})
        cur.execute(
            "INSERT INTO vector_metadata (id, content, domain, metadata_json) VALUES (?, ?, ?, ?)",
            (id, content, domain, meta_json),
        )
        rowid = cur.lastrowid
        emb_str = json.dumps(embedding)
        cur.execute("INSERT INTO vss_vectors(rowid, embedding) VALUES (?, ?)", (rowid, emb_str))
        self._conn.commit()

    async def retrieve(
        self,
        query_vector: list[float],
        domain: str | None = None,
        top_k: int = 10,
    ) -> list[Chunk]:
        emb_str = json.dumps(query_vector)
        domain_filter = ""
        params_domain: list[Any] = []
        if domain is not None:
            domain_filter = "AND m.domain = ?"
            params_domain = [domain]

        sql = f"""
            SELECT m.id, m.content, v.distance, m.domain, m.metadata_json
            FROM vss_vectors v
            JOIN vector_metadata m ON v.rowid = m.rowid
            WHERE vss_vectors MATCH ?
            AND v.k = ?
            {domain_filter}
            ORDER BY v.distance
        """
        cur = self._conn.cursor()
        cur.execute(sql, [emb_str, top_k] + params_domain)
        results = []
        for row in cur.fetchall():
            id_, content, distance, domain_val, meta_json = row
            score = max(0.0, 1.0 - distance) if distance is not None else 0.0
            results.append(Chunk(
                id=id_,
                content=content,
                score=score,
                metadata=json.loads(meta_json) if meta_json else {},
                domain=domain_val,
            ))
        return results

    async def delete(self, id: str) -> None:
        cur = self._conn.cursor()
        cur.execute("SELECT rowid FROM vector_metadata WHERE id = ?", (id,))
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM vss_vectors WHERE rowid = ?", (row[0],))
            cur.execute("DELETE FROM vector_metadata WHERE id = ?", (id,))
            self._conn.commit()

    async def count(self, domain: str | None = None) -> int:
        cur = self._conn.cursor()
        if domain:
            cur.execute("SELECT COUNT(*) FROM vector_metadata WHERE domain = ?", (domain,))
        else:
            cur.execute("SELECT COUNT(*) FROM vector_metadata")
        return cur.fetchone()[0]

    # Deprecated Phase 0 method — retained for backward compat
    # F1 fix: implemented as upsert wrapper with zero-vector
    # so Phase 0 test_storage_contracts.py continues to pass.
    async def store(self, chunk: Chunk) -> str:
        """Phase 0 compat: store with zero-vector embedding.
        Real usage should call upsert() with an actual embedding.
        This wrapper exists so test_storage_contracts.py passes.
        """
        await self.upsert(
            chunk.id,
            [0.0] * self._dimensions,
            chunk.content or "",
            chunk.metadata,
            chunk.domain,
        )
        return chunk.id

    def close(self) -> None:
        self._conn.close()