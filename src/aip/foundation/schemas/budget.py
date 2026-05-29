"""Budget-related types.

Token budget scoping and configuration for session, project,
and daily token limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Type alias for budget scoping
BudgetScope = Literal["session", "project", "daily"]


@dataclass
class BudgetConfig:
    """Token budget configuration.

    Budget Store Protocol required.
    Parallel nodes inherit parent budget.
    All limits toggleable via config.
    """

    session_token_limit: int = 500000
    project_token_limit: int = 5000000
    daily_token_limit: int = 10000000
    budget_warning_threshold: float = 0.80
    budget_hard_stop: bool = True


__all__ = [
    "BudgetScope",
    "BudgetConfig",
]
