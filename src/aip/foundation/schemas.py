"""
Phase 0 foundation schemas only.
This file will be appended to (never rewritten) by CHUNK-1.0a.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class ContractTier(Enum):
    HARD = "HARD"
    SOFT = "SOFT"
    ASPIRATIONAL = "ASPIRATIONAL"


@dataclass
class ContractRule:
    """
    L1 environment contract rule. Per §1.8 (Harness Evolution Principle),
    any rule compensating for a model limitation must carry a non-null
    model_gen_assumption. Sexton audits these on model slot upgrades.
    """
    rule_id: str
    tier: ContractTier
    text: str
    domain: str | None
    model_gen_assumption: str | None  # Non-null = compensates for model limitation
    created: str
    deprecated: str | None


class EcsState(Enum):
    SPECIFIED = "SPECIFIED"
    GENERATED = "GENERATED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


# Literal types used by Phase 0 contracts
ModelSlotName = Literal["synthesis", "evaluation", "sexton", "embedding"]

FailureType = Literal["A", "B", "C", "D", "E", "F"]

OutcomeType = Literal[
    "success", "failure", "timeout", "gate_blocked", "insufficient_memory"
]