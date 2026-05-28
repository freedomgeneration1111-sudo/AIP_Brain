"""SqliteSessionStore — implements AuthStore (CHUNK-9.0b).

Per spec: session tokens + API key management with bcrypt.
Laptop profile (auth_enabled=False): all requests treated as DEFINER.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    identity TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    password_hash TEXT,
                    created_at TEXT NOT NULL,
                    last_active_at TEXT,
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
            now = datetime.now(timezone.utc)
            expires = now + timedelta(seconds=self._config.session_timeout_seconds)
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
            if expires < datetime.now(timezone.utc):
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
            now = datetime.now(timezone.utc).isoformat() + "Z"
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
                        (datetime.now(timezone.utc).isoformat() + "Z", row["key_hash"]),
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

    # --- AuthStore Protocol methods (Phase 8 / CHUNK-10.0a) ---

    async def get_definer_identity(self) -> dict | None:
        """Return the single DEFINER identity (or None if not configured)."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT identity, role, created_at FROM users WHERE role = 'definer' AND revoked = 0 LIMIT 1"
            ).fetchone()
            if row:
                return {"identity": row["identity"], "role": row["role"], "created_at": row["created_at"]}
            # Fallback: if no users table entry, return implicit definer
            return {"identity": "definer", "role": "definer"}
        except Exception:
            return {"identity": "definer", "role": "definer"}

    async def list_users(self) -> list[dict]:
        """List all user identities."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT identity, role, created_at, last_active_at, revoked FROM users ORDER BY created_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    async def create_user(self, identity: str, role: str, password_hash: str | None = None) -> bool:
        """Create a collaborator or readonly user. Cannot create definer role."""
        if role == "definer":
            return False
        conn = self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            conn.execute(
                "INSERT OR IGNORE INTO users (identity, role, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (identity, role, password_hash, now),
            )
            conn.commit()
            # Return True if created, False if already exists
            row = conn.execute(
                "SELECT identity FROM users WHERE identity = ? AND revoked = 0", (identity,)
            ).fetchone()
            return row is not None
        except Exception:
            return False

    async def update_user_role(self, identity: str, new_role: str) -> bool:
        """Update a user's role. Cannot change the DEFINER's role."""
        conn = self._get_conn()
        try:
            # Check that user exists and is not definer
            row = conn.execute(
                "SELECT role FROM users WHERE identity = ? AND revoked = 0", (identity,)
            ).fetchone()
            if not row or row["role"] == "definer":
                return False
            conn.execute(
                "UPDATE users SET role = ? WHERE identity = ?", (new_role, identity)
            )
            conn.commit()
            return True
        except Exception:
            return False

    async def revoke_user(self, identity: str) -> bool:
        """Remove a user. Cannot revoke the DEFINER."""
        conn = self._get_conn()
        try:
            # Check that user exists and is not definer
            row = conn.execute(
                "SELECT role FROM users WHERE identity = ? AND revoked = 0", (identity,)
            ).fetchone()
            if not row or row["role"] == "definer":
                return False
            # Revoke user
            conn.execute(
                "UPDATE users SET revoked = 1 WHERE identity = ?", (identity,)
            )
            # Revoke all sessions for this user
            conn.execute(
                "DELETE FROM sessions WHERE identity = ?", (identity,)
            )
            # Revoke all API keys for this user
            conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE identity = ?", (identity,)
            )
            conn.commit()
            return True
        except Exception:
            return False
