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

    Receives SextonConfig + ModelSlotResolver ("sexton" slot)
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

    # Full Sexton interface (extends foundation)

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
                    event_type="sexton_alert",
                    actor="sexton",
                    artifact_id="",
                    metadata_json=str(count),
                )
            except Exception:
                pass

        await self.classify_failures()

        if self._event_store is not None:
            try:
                await self._event_store.write_event(
                    event_type="sexton_cycle_complete",
                    actor="sexton",
                    artifact_id="",
                    metadata_json=str(count),
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

        # Retrieval / L2 patterns (from existing retrieval code)
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
        """Derive a concrete intervention recommendation for a known condition.

        H. Runtime gap closure: Implements deterministic rule derivation for a
        small set of known conditions. For unknown conditions, returns None
        honestly instead of producing vague placeholder advice.

        Priority order:
        1. Special conditions (repeated failure, fixture leakage, etc.) — these
           provide more specific context and override generic type-based rules.
        2. Failure type codes (A-F) — generic rules based on classification.

        Each intervention includes:
        - reason: Why this intervention is recommended
        - severity: How urgent (critical, high, medium, low)
        - affected_component: What part of the system is affected
        - proposed_action: What should be done
        - requires_approval: Whether DEFINER approval is needed before acting
        """
        # --- Special conditions (higher priority than type-based rules) ---
        condition = context.get("condition", "")

        # Repeated evaluation failure
        if condition == "repeated_evaluation_failure":
            return {
                "intervention_id": f"intv_repeat_eval_{context.get('artifact_id', 'unknown')}",
                "failure_type": failure_type,
                "reason": "Repeated evaluation failure — the same artifact has failed "
                "evaluation multiple times. This may indicate a systemic issue with "
                "the evaluation pipeline or the artifact's quality.",
                "severity": "high",
                "affected_component": "evaluation",
                "proposed_action": "escalate_to_definer_with_full_context",
                "requires_approval": True,
                "model_gen_assumption": (
                    "Deterministic rule: Repeated failures indicate a systemic issue, "
                    "not a transient one. Per §16.1 and §1.8."
                ),
            }

        # Fixture leakage attempt
        if condition == "fixture_leakage_attempt":
            return {
                "intervention_id": f"intv_fixture_leak_{context.get('artifact_id', 'unknown')}",
                "failure_type": failure_type,
                "reason": "CI fixture data detected in production path — this indicates "
                "a potential safety bypass where test fixtures are being treated as "
                "real evaluation results.",
                "severity": "critical",
                "affected_component": "evaluation",
                "proposed_action": "block_promotion_and_alert_definer",
                "requires_approval": True,  # Must be reviewed by DEFINER
                "model_gen_assumption": (
                    "Deterministic rule: Fixture leakage in production is a critical "
                    "safety concern. Per §16.1 and §1.8."
                ),
            }

        # Canonical promotion blocked
        if condition == "canonical_promotion_blocked":
            return {
                "intervention_id": f"intv_promo_blocked_{context.get('artifact_id', 'unknown')}",
                "failure_type": failure_type,
                "reason": "Canonical promotion was blocked — the artifact met some "
                "criteria but failed others. A DEFINER review is needed to determine "
                "if the artifact can be improved or should be rejected.",
                "severity": "medium",
                "affected_component": "canonical_pipeline",
                "proposed_action": "request_definer_review_with_evaluation_context",
                "requires_approval": True,
                "model_gen_assumption": (
                    "Deterministic rule: Blocked promotions need human review. Per §16.1 and §1.8."
                ),
            }

        # Model-slot drift
        if condition == "model_slot_drift":
            return {
                "intervention_id": f"intv_slot_drift_{context.get('artifact_id', 'unknown')}",
                "failure_type": failure_type,
                "reason": "Model slot drift — the model configuration has changed since "
                "the artifact was last evaluated. Existing evaluations may be stale.",
                "severity": "high",
                "affected_component": "model_slot_resolver",
                "proposed_action": "trigger_vigil_re_evaluation",
                "requires_approval": False,  # Re-evaluation is a standard safety action
                "model_gen_assumption": (
                    "Deterministic rule: Model slot changes invalidate prior evaluations. Per §16.1 and §1.8."
                ),
            }

        # Budget breach
        if condition == "budget_breach":
            return {
                "intervention_id": f"intv_budget_{context.get('artifact_id', 'unknown')}",
                "failure_type": failure_type,
                "reason": "Budget breach — token consumption has exceeded configured limits. "
                "Further generation should be paused until budget is reviewed.",
                "severity": "high",
                "affected_component": "budget_manager",
                "proposed_action": "pause_generation_and_alert_definer",
                "requires_approval": True,
                "model_gen_assumption": (
                    "Deterministic rule: Budget breaches require review before resuming. Per §16.1 and §1.8."
                ),
            }

        # Auth/security violation
        if condition == "auth_security_violation":
            return {
                "intervention_id": f"intv_auth_{context.get('artifact_id', 'unknown')}",
                "failure_type": failure_type,
                "reason": "Authentication or authorization violation — an unauthorized "
                "actor attempted a privileged operation. This is a security event "
                "that requires DEFINER attention.",
                "severity": "critical",
                "affected_component": "auth",
                "proposed_action": "log_security_event_and_block_operation",
                "requires_approval": True,  # Security events always require review
                "model_gen_assumption": (
                    "Deterministic rule: Auth violations are always critical. Per §16.1 and §1.8."
                ),
            }

        # Workflow stuck waiting for approval
        if condition == "workflow_stuck_approval":
            return {
                "intervention_id": f"intv_stuck_{context.get('artifact_id', 'unknown')}",
                "failure_type": failure_type,
                "reason": "Workflow is stuck waiting for approval — a MANUAL review "
                "item has been pending beyond an expected timeframe. This may indicate "
                "a missing DEFINER review or a forgotten workflow.",
                "severity": "medium",
                "affected_component": "review_queue",
                "proposed_action": "notify_definer_of_pending_review",
                "requires_approval": False,  # Notification is safe to auto-send
                "model_gen_assumption": (
                    "Deterministic rule: Stuck workflows benefit from reminders. Per §16.1 and §1.8."
                ),
            }

        # --- Failure type codes (A-F) — generic rules ---

        # Type A: Context Framing Failure / Missing Context
        if failure_type == "A":
            return {
                "intervention_id": f"intv_A_{context.get('artifact_id', 'unknown')}",
                "failure_type": "A",
                "reason": "Context framing failure — insufficient or stale context was used for generation. "
                "The artifact may contain hallucinations or unsubstantiated claims.",
                "severity": "high",
                "affected_component": context.get("node_type", "retrieval"),
                "proposed_action": "strengthen_contract_rule_or_improve_retrieval",
                "requires_approval": True,
                "model_gen_assumption": (
                    "Deterministic rule: Type A failures indicate retrieval gaps that may "
                    "recur under similar conditions. Per §16.1 and §1.8."
                ),
            }

        # Type B: Procedural Gap
        if failure_type == "B":
            return {
                "intervention_id": f"intv_B_{context.get('artifact_id', 'unknown')}",
                "failure_type": "B",
                "reason": "Procedural gap — the system lacks a procedural rule to handle "
                "this failure pattern. ACE playbook entry may be missing or stale.",
                "severity": "medium",
                "affected_component": context.get("node_type", "orchestration"),
                "proposed_action": "add_or_retrieve_ace_playbook_entry",
                "requires_approval": True,
                "model_gen_assumption": (
                    "Deterministic rule: Type B failures indicate missing procedural coverage. Per §16.1 and §1.8."
                ),
            }

        # Type C: Output Malformation
        if failure_type == "C":
            return {
                "intervention_id": f"intv_C_{context.get('artifact_id', 'unknown')}",
                "failure_type": "C",
                "reason": "Output malformation — generated content does not conform to "
                "expected schema or structure. Structural validation may need strengthening.",
                "severity": "medium",
                "affected_component": context.get("node_type", "synthesis"),
                "proposed_action": "apply_structural_validation_or_repair",
                "requires_approval": False,  # Validation/repair is safe to auto-apply
                "model_gen_assumption": (
                    "Deterministic rule: Type C failures indicate structural quality issues "
                    "that validation can catch. Per §16.1 and §1.8."
                ),
            }

        # Type D: Session Drift / Loop
        if failure_type == "D":
            return {
                "intervention_id": f"intv_D_{context.get('artifact_id', 'unknown')}",
                "failure_type": "D",
                "reason": "Session drift or loop detected — the generation is stuck in "
                "a repetitive pattern or diverging from the intended trajectory. "
                "Context reset may be needed.",
                "severity": "high",
                "affected_component": "session",
                "proposed_action": "trigger_context_reset_or_l4_intervention",
                "requires_approval": False,  # Context reset is a standard recovery action
                "model_gen_assumption": (
                    "Deterministic rule: Type D failures indicate trajectory problems "
                    "that L4 can address. Per §16.1 and §1.8."
                ),
            }

        # Type E: False Success Reporting
        if failure_type == "E":
            return {
                "intervention_id": f"intv_E_{context.get('artifact_id', 'unknown')}",
                "failure_type": "E",
                "reason": "False success reporting — the system reported success but "
                "the output lacks substance or failed quality checks. This is a "
                "critical safety concern that requires immediate verification.",
                "severity": "critical",
                "affected_component": context.get("node_type", "evaluation"),
                "proposed_action": "require_verify_step_before_commit",
                "requires_approval": True,  # Must be approved — do not auto-commit
                "model_gen_assumption": (
                    "Deterministic rule: Type E failures are the most dangerous — they "
                    "indicate the system may approve low-quality content. Per §16.1 and §1.8."
                ),
            }

        # Type F: Context Anxiety
        if failure_type == "F":
            return {
                "intervention_id": f"intv_F_{context.get('artifact_id', 'unknown')}",
                "failure_type": "F",
                "reason": "Context anxiety — generated content shows signs of hedging, "
                "shortening, or uncertainty. The model may be operating at the edge "
                "of its capability.",
                "severity": "medium",
                "affected_component": "synthesis",
                "proposed_action": "trigger_context_reset_or_l4_intervention",
                "requires_approval": False,
                "model_gen_assumption": (
                    "Deterministic rule: Type F failures indicate model confidence issues "
                    "that may benefit from context refresh. Per §16.1 and §1.8."
                ),
            }

        # Unknown condition — return None honestly
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
