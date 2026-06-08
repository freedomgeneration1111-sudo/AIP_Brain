"""DEFINER Profile loader for injection into augmented chat system prompts.

Layer: adapter. Imports only foundation and stdlib. No orchestration imports.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any


class DefinerProfile:
    """Loads and caches the DEFINER profile for injection into augmented chat.

    Caches for 300 seconds (5 minutes) to allow live edits by DEFINER without
    server restart. Re-reads on cache expiry.
    """

    def __init__(self, profile_path: str) -> None:
        self._path = profile_path
        self._content: str | None = None
        self._loaded_at: float = 0.0
        self._cache_ttl: int = 300  # 5 minutes

    def load(self) -> str:
        """Load profile from disk (with caching).

        Strips markdown comments (lines starting with #).

        Returns:
            Profile content as string, or "" if file missing/empty/error.
        """
        now = time.time()
        if self._content is not None and (now - self._loaded_at) < self._cache_ttl:
            return self._content

        path = Path(self._path)
        if not path.exists():
            self._content = ""
            self._loaded_at = now
            return self._content

        try:
            raw = path.read_text(encoding="utf-8")
            # Strip lines starting with # (markdown comments/metadata header).
            # Keep ## headers and content (do not strip headers which start with ##).
            lines = raw.splitlines()
            stripped = "\n".join(
                line for line in lines
                if not (line.strip().startswith("#") and not line.strip().startswith("##"))
            )
            self._content = stripped.strip()
            self._loaded_at = now
            return self._content
        except Exception:
            self._content = ""
            self._loaded_at = now
            return self._content

    def get_injection_block(self, max_tokens_estimate: int = 800) -> str:
        """Return formatted profile block for system prompt.

        If content empty: return "" (inject nothing).

        Truncates roughly to max_tokens_estimate * 4 chars if too long,
        appending truncation note.
        """
        content = self.load()
        if not content:
            return ""

        # Rough token estimate: ~4 chars per token
        max_chars = max_tokens_estimate * 4
        if len(content) > max_chars:
            content = content[:max_chars] + "\n[Profile truncated for context budget]"

        return (
            "=== DEFINER PROFILE ===\n"
            f"{content}\n"
            "=== END DEFINER PROFILE ===\n"
        )
