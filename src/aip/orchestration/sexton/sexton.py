"""
Sexton Foundation (spec delta)

Minimal deterministic implementation per Architecture Rev 5.2.
- Accepts injected TraceStore.
- classify_recent_failures(): queries unclassified failures, applies
  deterministic Appendix E rules, writes failure_type back.
- Stubs for ACE playbook derivation (in-memory only for foundation).
- Zero tokens. All access via injected protocol only.
- Every classification decision that encodes model assumptions carries
  model_gen_assumption.

This is deliberately the smallest useful foundation.
"""

from __future__ import annotations

from typing import Any

from aip.foundation.protocols import EventStore, ModelProvider, TraceStore
from aip.foundation.schemas import FailureClassification, SextonConfig


class Sexton:
    """
    Sexton failure classification actor (implementation extending the
    foundation stub).

    Per Phase 5 spec: receives SextonConfig + ModelSlotResolver ("sexton" slot)
    + TraceStore + EventStore via injection. Never imports adapter storage
    implementations directly. Uses resolver for real classification; falls back
    to deterministic foundation logic in CI mode.

    The original foundation classify_recent_failures + _classify (Appendix E
    deterministic) are preserved and used for CI-mode fixtures and as the
    zero-token baseline.
    """

    def __init__(
        self,
        config: SextonConfig | None = None,
        model_resolver: ModelProvider | None = None,
        trace_store: TraceStore | None = None,
        event_store: EventStore | None = None,
    ) -> None:
        self._config = config or SextonConfig()
        self._model_resolver = model_resolver
        self._trace_store = trace_store
        self._event_store = event_store
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

    # full Sexton interface per Phase 5 spec (extends foundation)

    async def classify_failures(self) -> list[FailureClassification]:
        """Batch classify unclassified failures (7.1 prose)."""
        if self._trace_store is None:
            return []

        # Use foundation defensive path if the store lacks the expected method
        if not hasattr(self._trace_store, "get_unclassified_failures"):
            return []

        try:
            events = await self._trace_store.get_unclassified_failures(limit=self._config.classification_batch_size)
        except Exception:
            return []

        results: list[FailureClassification] = []
        for ev in events:
            if ev.get("failure_type"):
                continue

            fc = await self.classify_trace_event(ev.get("id") or 0, ev)
            if fc:
                results.append(fc)

        return results

    async def classify_trace_event(
        self,
        trace_event_id: int,
        event: dict[str, Any] | None = None,
    ) -> FailureClassification | None:
        """Classify a single trace event (7.1). Produces FailureClassification with model_gen_assumption."""
        if event is None and self._trace_store is not None:
            # Best-effort single fetch (foundation stores may not support id lookup)
            try:
                events = await self._trace_store.get_unclassified_failures(limit=1)
                event = events[0] if events else None
            except Exception:
                event = None

        if not event:
            return None

        failure_type = None
        confidence = 0.7
        rationale = "Deterministic foundation classification (CI mode or no resolver)"
        model_gen_assumption = (
            "Local deterministic rules (Qwen3-Coder equivalent in CI) may "
            "misclassify domain-adjacent or subtle failures; real model slot "
            "usage provides higher fidelity per §16.1."
        )

        if self._model_resolver is not None and not getattr(self._model_resolver, "_ci_mode", True):
            # Real path: use the "sexton" slot
            prompt = self._build_classification_prompt(event)
            try:
                resp = await self._model_resolver.call("sexton", [{"role": "user", "content": prompt}])
                parsed = self._parse_classification_response(resp.get("content", "{}"))
                failure_type = parsed.get("failure_type")
                confidence = float(parsed.get("confidence", 0.7))
                rationale = parsed.get("rationale", rationale)
                model_gen_assumption = parsed.get("model_gen_assumption", model_gen_assumption)
            except Exception:
                failure_type = self._classify(event)
        else:
            # CI / foundation deterministic path (reuses existing _classify)
            failure_type = self._classify(event)

        if not failure_type:
            return None

        # Write classification back exclusively via TraceStore (per spec)
        if self._trace_store is not None:
            try:
                await self._trace_store.write_event(
                    session_id=event.get("session_id", "unknown"),
                    node_type=event.get("node_type", "unknown"),
                    failure_type=failure_type,
                    outcome=event.get("outcome", "failure"),
                    detail=event.get("detail"),
                    intervention_applied=event.get("intervention_applied", 0),
                    intervention_type=event.get("intervention_type"),
                )
            except Exception:
                pass

        return FailureClassification(
            trace_event_id=trace_event_id or event.get("id", 0),
            failure_type=failure_type,
            confidence=confidence,
            rationale=rationale,
            model_slot_used="sexton",
            tokens_consumed=0,  # foundation / CI path is zero-token
            model_gen_assumption=model_gen_assumption,
            classified_at="",  # caller or store can set
        )

    async def count_unclassified(self) -> int:
        """Count failures still awaiting classification (7.1)."""
        if self._trace_store is None or not hasattr(self._trace_store, "get_unclassified_failures"):
            return 0
        try:
            events = await self._trace_store.get_unclassified_failures(limit=10_000)
            return len([e for e in events if not e.get("failure_type")])
        except Exception:
            return 0

    async def run_classification_cycle(self) -> None:
        """Main cadence entrypoint (7.1). Respects config thresholds and writes Events."""
        count = await self.count_unclassified()
        if count == 0:
            return

        if count > self._config.max_unclassified_before_alert and self._event_store is not None:
            try:
                await self._event_store.write_event(
                    {
                        "event_type": "sexton_alert",
                        "unclassified_count": count,
                        "threshold": self._config.max_unclassified_before_alert,
                    },
                )
            except Exception:
                pass

        await self.classify_failures()

        if self._event_store is not None:
            try:
                await self._event_store.write_event(
                    {
                        "event_type": "sexton_cycle_complete",
                        "classified_count": count,
                        "tokens_consumed": 0,
                    },
                )
            except Exception:
                pass

    def _build_classification_prompt(self, event: dict[str, Any]) -> str:
        """Build the prompt containing full Appendix E + trace data (per 7.1 prose)."""
        # Minimal faithful prompt; real impl would include the full taxonomy text
        return (
            "You are Sexton. Classify the following trace event failure using "
            "Appendix E taxonomy (A-F). Return JSON with failure_type, confidence, "
            "rationale, model_gen_assumption.\n\nEvent: " + str(event)
        )

    def _parse_classification_response(self, content: str) -> dict[str, Any]:
        """Very small JSON-ish extractor for the real model path."""
        import json

        try:
            return json.loads(content)
        except Exception:
            return {}

    # --- original foundation _classify preserved for CI deterministic fixtures ---

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
        if node_type == "L2" and (
            "insufficient" in detail or "max confidence" in detail or "below threshold" in detail
        ):
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

    def derive_ace_rules(self, classified_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Minimal deterministic ACE playbook derivation (foundation).

        For each classified failure event, produces a basic intervention rule stub.
        Every rule carries an explicit model_gen_assumption.
        In a later chunk these would be persisted, reviewed, and promoted to the
        live ACE playbook used by L2 retrieval etc.
        """
        rules: list[dict] = []
        seen: set[str] = set()

        for ev in classified_events:
            ft = ev.get("failure_type")
            if not ft or ft in ("", None):
                continue

            node = ev.get("node_type", "unknown")
            key = f"{ft}_{node}"
            if key in seen:
                continue
            seen.add(key)

            rule = {
                "rule_id": f"ace_{key}_{len(rules)}",
                "failure_type": ft,
                "node_type_pattern": node,
                "condition": f"failure_type == '{ft}' and node_type matches '{node}'",
                "recommended_action": self._default_action_for(ft),
                "source_event_count": 1,
                "model_gen_assumption": (
                    f"Rule derived directly from observed classified failure (type {ft}) "
                    f"in node {node}. Encodes the assumption that this pattern of "
                    f"failure will recur under similar conditions and benefits from "
                    f"the listed intervention. Per Architecture §16.1 and §1.8. "
                    f"Audit on model slot upgrade."
                ),
            }
            rules.append(rule)

        return rules

    def _default_action_for(self, failure_type: str) -> str:
        if failure_type == "A":
            return "strengthen_contract_rule_or_improve_retrieval"
        if failure_type == "B":
            return "add_or_retrieve_ace_playbook_entry"
        if failure_type == "C":
            return "apply_structural_validation_or_repair"
        if failure_type in ("D", "F"):
            return "trigger_context_reset_or_l4_intervention"
        if failure_type == "E":
            return "require_verify_step_before_commit"
        return "log_and_audit"

    # remaining foundation (trust scoring + stale rule audit)

    def trust_score(self, rule_or_event: dict[str, Any]) -> float:
        """
        Minimal deterministic trust score (0.0–1.0) for a rule or classified event.
        Foundation version: simple heuristic based on presence of supporting fields
        and model_gen_assumption tag. Real scoring would use more data over time.
        """
        if not rule_or_event:
            return 0.0
        score = 0.5
        if rule_or_event.get("model_gen_assumption"):
            score += 0.3
        if rule_or_event.get("source_event_count", 0) > 0:
            score += 0.1
        if rule_or_event.get("recommended_action"):
            score += 0.1
        return min(1.0, round(score, 2))

    def audit_model_gen_assumption(self, rules: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """
        implementation of the stale rule audit.
        Scans the provided rules (or internal derived rules if none passed) and
        returns those that are missing or have weak model_gen_assumption tagging.
        This fulfills the "stale rule audit" responsibility in the foundation.
        """
        if rules is None:
            # In foundation the class doesn't persist rules internally from derive;
            # caller (test or workflow) passes the list of rules to audit.
            rules = []

        stale: list[dict] = []
        for rule in rules:
            assumption = rule.get("model_gen_assumption", "")
            if not assumption or "§1.8" not in assumption or len(assumption) < 50:
                stale.append(
                    {
                        **rule,
                        "audit_reason": (
                            "Missing or weak model_gen_assumption tag "
                            "(per §1.8). Rule may be stale after model upgrade."
                        ),
                        "trust_score": self.trust_score(rule),
                    },
                )
        return stale
