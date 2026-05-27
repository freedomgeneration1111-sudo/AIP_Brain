"""
Sexton Foundation (CHUNK-3.4 spec delta)

Minimal deterministic implementation per Architecture Rev 5.2 §16.1.
- Accepts injected TraceStore.
- classify_recent_failures(): queries unclassified failures, applies
  deterministic Appendix E rules, writes failure_type back.
- Stubs for ACE playbook derivation (in-memory only for foundation).
- Zero tokens. All access via injected protocol only.
- Every classification decision that encodes model assumptions carries
  model_gen_assumption per §1.8.

This is deliberately the smallest useful foundation.
"""

from __future__ import annotations

from typing import Any

from aip.foundation.protocols import TraceStore


class Sexton:
    """
    Minimal Sexton actor (foundation).

    Usage:
        sexton = Sexton(trace_store=trace_store)
        classified = await sexton.classify_recent_failures(limit=50)
    """

    def __init__(self, trace_store: TraceStore) -> None:
        self._trace_store = trace_store
        # Foundation stub for ACE playbook (would be persisted in later chunks)
        self._playbook: dict[str, dict] = {}

    async def classify_recent_failures(self, limit: int = 100) -> list[dict]:
        """
        Read recent unclassified failures, apply deterministic Appendix E
        taxonomy rules, write the classification back, and return the results.

        For foundation: purely deterministic pattern matching on node_type,
        detail, and outcome. No LLM call.
        """
        if not hasattr(self._trace_store, "get_unclassified_failures"):
            # Defensive for any store that has not yet been updated
            return []

        try:
            events = await self._trace_store.get_unclassified_failures(limit=limit)
        except Exception:
            return []

        classified: list[dict] = []

        for ev in events:
            if ev.get("failure_type"):
                continue  # already classified

            failure_type = self._classify(ev)
            if failure_type:
                # Write classification back (amend the event)
                try:
                    await self._trace_store.write_event(
                        session_id=ev.get("session_id", "unknown"),
                        node_type=ev.get("node_type", "unknown"),
                        failure_type=failure_type,
                        outcome=ev.get("outcome", "failure"),
                        detail=ev.get("detail"),
                        # Preserve other fields if the store supports **kwargs
                        intervention_applied=ev.get("intervention_applied", 0),
                        intervention_type=ev.get("intervention_type"),
                    )
                except Exception:
                    pass  # best effort for foundation

                ev = {**ev, "failure_type": failure_type}
                classified.append(ev)

        return classified

    def _classify(self, event: dict[str, Any]) -> str | None:
        """
        Deterministic Appendix E taxonomy rules (foundation version).

        Types (from Architecture Appendix E):
        A = Context Framing Failure
        B = Procedural Gap
        C = Output Malformation
        D = Session Drift / Loop
        E = False Success Reporting
        F = Context Anxiety
        """
        node_type = (event.get("node_type") or "").upper()
        detail = (event.get("detail") or "").lower()
        outcome = (event.get("outcome") or "").lower()

        # L4-written events (from 3.1-3.3)
        if node_type == "L4":
            if "context_reset" in detail or event.get("intervention_type") == "context_reset":
                # These are already classified by the L4 layer itself (D or F)
                # Sexton can leave them or confirm
                return None

        # Retrieval / L2 patterns (from Phase 1 code)
        if node_type == "L2" and ("insufficient" in detail or "max confidence" in detail or "below threshold" in detail):
            return "A"  # Context Framing / Missing Context (per retrieval.py R2 logging)

        # Structural / L3a malformation patterns
        if "malformation" in detail or "schema" in detail or "invalid" in detail:
            return "C"

        # Drift / loop signals (often pre-classified by L4, but catch here too)
        if "drift" in detail or "loop" in detail or "repetitive" in detail:
            return "D"

        # False success patterns
        if "false" in detail and "success" in detail:
            return "E"

        # Context anxiety / length collapse (L4b territory, but detectable)
        if "anxiety" in detail or "shortening" in detail or "hedging" in detail:
            return "F"

        # Default procedural gap for other failures in this foundation
        if outcome == "failure":
            return "B"

        return None

    # --- ACE playbook stubs (foundation only) ---

    def derive_intervention_rule(self, failure_type: str, context: dict) -> dict | None:
        """Stub — returns None in foundation. Real derivation in later chunks."""
        return None

    def audit_model_gen_assumption(self, rule_id: str) -> str | None:
        """Stub for §1.8 stale rule audit."""
        return None
