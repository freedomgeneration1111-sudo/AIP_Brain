"""Entity alias loader — parses docs/entity_aliases.md.

Used by Beast graph extraction to resolve entity mentions to canonical
names before creating nodes. Prevents co-reference fragmentation.

Layer: adapter. Imports only stdlib.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class AliasEntry:
    canonical_name: str
    entity_type: str
    domain: str
    aliases: list[str]
    deprecated: list[str]


class EntityAliasRegistry:
    """Parsed registry of canonical entity names and their aliases.

    Loaded from docs/entity_aliases.md. Used to resolve ad-hoc mentions
    to canonical names before graph node creation.
    """

    def __init__(self, alias_path: str) -> None:
        self._entries: dict[str, AliasEntry] = {}  # canonical_name -> entry
        self._alias_map: dict[str, str] = {}  # lowercased alias -> canonical_name
        self._deprecated: set[str] = set()
        if os.path.isfile(alias_path):
            self._load(alias_path)

    def _load(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()

        current: dict = {}

        def _flush() -> None:
            cn = current.get("canonical_name", "").strip()
            et = current.get("entity_type", "CONCEPT").strip()
            dom = current.get("domain", "").strip()
            aliases_raw = current.get("aliases", "[]")
            deprecated_raw = current.get("deprecated", "[]")

            if not cn:
                return

            def _parse_list(raw: str) -> list[str]:
                raw = raw.strip()
                if not raw or raw in ("[]", ""):
                    return []
                # Strip outer brackets and split by commas inside quotes
                inner = raw.strip("[]")
                import re

                items = re.findall(r'"([^"]+)"', inner)
                return [i.strip() for i in items if i.strip()]

            aliases = _parse_list(aliases_raw)
            deprecated = _parse_list(deprecated_raw)

            entry = AliasEntry(
                canonical_name=cn,
                entity_type=et,
                domain=dom,
                aliases=aliases,
                deprecated=deprecated,
            )
            self._entries[cn] = entry
            self._alias_map[cn.lower()] = cn
            for alias in aliases:
                self._alias_map[alias.lower()] = cn
            for dep in deprecated:
                self._deprecated.add(dep.lower())

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("##"):
                if current.get("canonical_name"):
                    _flush()
                current = {}
                continue
            if line.startswith("canonical_name:"):
                if current.get("canonical_name"):
                    _flush()
                current = {}
                current["canonical_name"] = line.split(":", 1)[1].strip()
            elif line.startswith("aliases:"):
                current["aliases"] = line.split(":", 1)[1].strip()
            elif line.startswith("deprecated:"):
                current["deprecated"] = line.split(":", 1)[1].strip()
            elif line.startswith("entity_type:"):
                current["entity_type"] = line.split(":", 1)[1].strip()
            elif line.startswith("domain:"):
                current["domain"] = line.split(":", 1)[1].strip()
            elif current.get("aliases") and not any(
                line.startswith(k) for k in ("canonical_name:", "entity_type:", "domain:", "deprecated:")
            ):
                # Multi-line aliases continuation (lines starting with spaces inside aliases list)
                if current.get("aliases", "").rstrip().endswith(",") or current.get("aliases", "").rstrip().endswith(
                    "["
                ):
                    current["aliases"] = current["aliases"] + " " + line

        if current.get("canonical_name"):
            _flush()

    def resolve(self, mention: str) -> str | None:
        """Resolve a mention string to canonical_name, or None if unknown/deprecated."""
        if not mention:
            return None
        key = mention.strip().lower()
        if key in self._deprecated:
            return None
        return self._alias_map.get(key)

    def get_entity_type(self, canonical_name: str) -> str | None:
        entry = self._entries.get(canonical_name)
        return entry.entity_type if entry else None

    def get_domain(self, canonical_name: str) -> str | None:
        entry = self._entries.get(canonical_name)
        return entry.domain if entry else None

    def all_canonical_names(self) -> list[str]:
        return list(self._entries.keys())

    def get_entry(self, canonical_name: str) -> AliasEntry | None:
        return self._entries.get(canonical_name)

    def __len__(self) -> int:
        return len(self._entries)
