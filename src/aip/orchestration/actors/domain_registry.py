"""Domain registry loader for Beast turn tagging.

Beast reads this registry as its cognitive map. It never invents
domain_ids — only uses approved ones from here, and files proposals
(via beast_domain_proposal GENERATED artifacts) when it discovers
new patterns that don't fit.

Layer: orchestration/actors. Imports only stdlib + foundation (none here).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class DomainEntry:
    domain_id: str
    description: str
    core_keywords: list[str]
    exclude_note: str
    importance_floor: float


@dataclass
class ConnectorEntry:
    domain_a: str
    domain_b: str
    bridge_tag: str  # e.g. "nbcm->theology_research"
    description: str


@dataclass
class DomainRegistry:
    domains: dict[str, DomainEntry]  # domain_id -> entry
    connectors: list[ConnectorEntry]
    version: str
    loaded_at: str  # ISO timestamp

    def get_domain_ids(self) -> list[str]:
        return list(self.domains.keys())

    def get_domain(self, domain_id: str) -> DomainEntry | None:
        return self.domains.get(domain_id)

    def get_approved_bridge_tags(self) -> list[str]:
        return [c.bridge_tag for c in self.connectors]

    def is_approved_domain(self, domain_id: str) -> bool:
        return domain_id in self.domains

    def is_approved_bridge(self, bridge_tag: str) -> bool:
        return bridge_tag in self.get_approved_bridge_tags()


def load_registry(registry_path: str) -> DomainRegistry:
    """
    Parse docs/beast_domain_registry_v1.md into a DomainRegistry.

    Parsing rules:
    - Lines starting with DOMAIN_ID: start a new domain entry
    - Lines starting with DESCRIPTION:, CORE_KEYWORDS:, EXCLUDE:,
      IMPORTANCE_FLOOR: populate the current entry
    - Lines matching "domain_a->domain_b" under the connector section
      create ConnectorEntry objects
    - Version extracted from "v1.0" line in VERSION HISTORY section

    Returns DomainRegistry with all parsed entries.
    Raises FileNotFoundError if path doesn't exist.
    Raises ValueError if file parses to 0 domains.
    Never raises for individual malformed entries — skip and continue.
    """
    if not os.path.isfile(registry_path):
        raise FileNotFoundError(f"Registry file not found: {registry_path}")

    with open(registry_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    domains: dict[str, DomainEntry] = {}
    connectors: list[ConnectorEntry] = []
    version = "v1.0"
    current_domain: str | None = None
    desc_lines: list[str] = []
    core_keywords: list[str] = []
    exclude_note = ""
    importance_floor = 0.0

    in_connectors_section = False
    current_bridge: str | None = None
    bridge_desc_lines: list[str] = []

    def _flush_domain() -> None:
        nonlocal current_domain, desc_lines, core_keywords, exclude_note, importance_floor
        if current_domain:
            desc = " ".join(desc_lines).strip() if desc_lines else ""
            # de-dup keywords but preserve order of first seen
            seen = set()
            kws = []
            for k in core_keywords:
                kk = k.strip().lower()
                if kk and kk not in seen:
                    seen.add(kk)
                    kws.append(k.strip())
            try:
                fl = float(importance_floor)
            except Exception:
                fl = 0.0
            domains[current_domain] = DomainEntry(
                domain_id=current_domain,
                description=desc,
                core_keywords=kws,
                exclude_note=exclude_note.strip(),
                importance_floor=fl,
            )
        current_domain = None
        desc_lines = []
        core_keywords = []
        exclude_note = ""
        importance_floor = 0.0

    def _flush_bridge() -> None:
        nonlocal current_bridge, bridge_desc_lines
        if current_bridge:
            desc = " ".join(bridge_desc_lines).strip() if bridge_desc_lines else ""
            if "->" in current_bridge:
                parts = current_bridge.split("->", 1)
                da = parts[0].strip()
                db = parts[1].strip() if len(parts) > 1 else ""
                if da and db:
                    tag = f"{da}->{db}"
                    # avoid exact dups
                    if not any(c.bridge_tag == tag for c in connectors):
                        connectors.append(ConnectorEntry(da, db, tag, desc))
        current_bridge = None
        bridge_desc_lines = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # Allow ## section headers through (they start with # but drive parser state);
        # skip only non-header comment lines.
        if line.startswith("#") and not line.startswith("##"):
            continue

        # Section detection (must come before general # skip would have caught them)
        if line.startswith("## APPROVED CONNECTOR"):
            _flush_domain()
            in_connectors_section = True
            continue
        if line.startswith("## PROPOSAL PROTOCOL"):
            _flush_bridge()
            in_connectors_section = False
            continue
        if line.startswith("## VERSION HISTORY"):
            _flush_domain()
            _flush_bridge()
            in_connectors_section = False
            continue
        if line.startswith("v1.0 —") or line.startswith("v1.0"):
            version = "v1.0"
            continue

        # DOMAIN_ID starts new domain (also ends connectors mode)
        if line.startswith("DOMAIN_ID:"):
            _flush_bridge()
            _flush_domain()
            current_domain = line.split(":", 1)[1].strip()
            in_connectors_section = False
            continue

        if in_connectors_section:
            # bridge tag line e.g. nbcm->theology_research
            if "->" in line and not line.startswith("DOMAIN_ID") and not line[0].isspace() and "domain->domain" not in line.lower():
                _flush_bridge()
                current_bridge = line
                bridge_desc_lines = []
                continue
            # description continuation for current bridge (indented or following)
            if current_bridge and (line.startswith("  ") or (not line.startswith("DOMAIN") and not any(line.startswith(p) for p in ("##", "DOMAIN_ID:", "DESCRIPTION:", "CORE_KEYWORDS:", "EXCLUDE:", "IMPORTANCE_FLOOR:")))):
                bridge_desc_lines.append(line)
                continue

        # Domain field population (when we have an active domain)
        if current_domain and not in_connectors_section:
            if line.startswith("DESCRIPTION:"):
                val = line.split(":", 1)[1].strip()
                desc_lines = [val] if val else []
                continue
            if line.startswith("CORE_KEYWORDS:"):
                val = line.split(":", 1)[1].strip()
                core_keywords = [k.strip() for k in val.split(",") if k.strip()]
                continue
            if line.startswith("EXCLUDE:"):
                exclude_note = line.split(":", 1)[1].strip()
                continue
            if line.startswith("IMPORTANCE_FLOOR:"):
                try:
                    importance_floor = float(line.split(":", 1)[1].strip())
                except Exception:
                    importance_floor = 0.0
                continue
            # Continuation lines for DESCRIPTION (until next key or section)
            if desc_lines and not any(
                line.startswith(p)
                for p in ("DOMAIN_ID:", "DESCRIPTION:", "CORE_KEYWORDS:", "EXCLUDE:", "IMPORTANCE_FLOOR:", "##")
            ):
                desc_lines.append(line)
                continue

    # final flushes
    _flush_bridge()
    _flush_domain()

    if len(domains) == 0:
        raise ValueError(f"Registry at {registry_path} parsed to 0 domains")

    loaded_at = datetime.now(timezone.utc).isoformat()
    return DomainRegistry(
        domains=domains,
        connectors=connectors,
        version=version,
        loaded_at=loaded_at,
    )
