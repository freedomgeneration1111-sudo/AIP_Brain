"""SqliteSessionStore — implements AuthStore (CHUNK-9.0b).

Per spec: session tokens + API key management with bcrypt.
Laptop profile (auth_enabled=False): all requests treated as DEFINER.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Any

import bcrypt

from aip.foundation.protocols import AuthStore
from aip.foundation.schemas import AuthConfig


class SqliteSessionStore(AuthStore):
    """SQLite implementation of AuthStore Protocol (Phase 7)."""

    def __init__(self, db_path: str, config: AuthConfig) -> None:
        self._db_path = db_path
        self._config = config
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_token TEXT PRIMARY KEY,
                    identity TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_name TEXT PRIMARY KEY,
                    identity TEXT NOT NULL,
                    role TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked INTEGER DEFAULT 0
                )
            """)
            conn.commit()
        finally:
            conn.close()

    async def initialize(self) -> None:
        self._ensure_tables()

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def create_session(self, identity: str, role: str) -> str:
        conn = self._get_conn()
        try:
            token = secrets.token_urlsafe(32)
            now = datetime.utcnow()
            expires = now + timedelta(minutes=self._config.session_timeout_minutes)
            conn.execute(
                "INSERT INTO sessions (session_token, identity, role, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (token, identity, role, now.isoformat() + "Z", expires.isoformat() + "Z"),
            )
            conn.commit()
            return token
        finally:
            pass

    async def validate_session(self, session_token: str) -> dict | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT identity, role, expires_at FROM sessions WHERE session_token = ?",
                (session_token,),
            ).fetchone()
            if not row:
                return None
            expires = datetime.fromisoformat(row["expires_at"].replace("Z", ""))
            if expires < datetime.utcnow():
                return None
            return {"identity": row["identity"], "role": row["role"]}
        finally:
            pass

    async def revoke_session(self, session_token: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM sessions WHERE session_token = ?", (session_token,))
            conn.commit()
        finally:
            pass

    async def create_api_key(self, identity: str, role: str, key_name: str) -> str:
        conn = self._get_conn()
        try:
            raw_key = secrets.token_urlsafe(32)
            key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12)).decode()
            now = datetime.utcnow().isoformat() + "Z"
            conn.execute(
                "INSERT INTO api_keys (key_name, identity, role, key_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (key_name, identity, role, key_hash, now),
            )
            conn.commit()
            return raw_key  # One-time display
        finally:
            pass

    async def validate_api_key(self, api_key: str) -> dict | None:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT identity, role, key_hash FROM api_keys WHERE revoked = 0"
            ).fetchall()
            for row in rows:
                if bcrypt.checkpw(api_key.encode(), row["key_hash"].encode()):
                    # Update last_used_at (best effort)
                    conn.execute(
                        "UPDATE api_keys SET last_used_at = ? WHERE key_name = (SELECT key_name FROM api_keys WHERE key_hash = ? LIMIT 1)",
                        (datetime.utcnow().isoformat() + "Z", row["key_hash"]),
                    )
                    conn.commit()
                    return {"identity": row["identity"], "role": row["role"]}
            return None
        finally:
            pass

    async def revoke_api_key(self, key_name: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute("UPDATE api_keys SET revoked = 1 WHERE key_name = ?", (key_name,))
            conn.commit()
        finally:
            pass

    async def list_api_keys(self) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT key_name, identity, role, created_at, last_used_at, revoked FROM api_keys ORDER BY created_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            pass
