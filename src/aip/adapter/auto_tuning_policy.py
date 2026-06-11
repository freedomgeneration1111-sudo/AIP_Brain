"""Auto-tuning policy engine — configurable thresholds and safeguards.

Sprint 5.26: Exposes key auto-tuning parameters via ``aip.config.toml``
so operators can adjust behavior without code changes.  Integrates with
the hot-reload system for live updates and validates policy values.

Configuration section::

    [auto_tuning_policy]
    # Read pool auto-sizing policy
    read_pool_exhaustion_threshold = 0.3      # Exhaustion rate > this triggers sizing
    read_pool_auto_apply_consecutive = 5       # Consecutive observations before auto-apply
    read_pool_auto_apply_max_increase = 4      # Max connections above configured
    read_pool_auto_apply_max_pool = 12         # Absolute pool size cap
    read_pool_auto_rollback_enabled = true
    read_pool_auto_rollback_consecutive = 5    # Low-exhaustion obs before rollback
    read_pool_auto_rollback_healthy = 0.15     # Exhaustion below this = healthy

    # Graph extraction batch auto-tuning policy
    graph_batch_decrease_threshold = 0.3       # Failure rate above → decrease
    graph_batch_increase_threshold = 0.1        # Failure rate below → increase
    graph_batch_auto_tune_window = 5           # Batches to consider
    graph_batch_min_size = 1
    graph_batch_max_size = 8

    # Cooldown period between auto-tuning adjustments (seconds)
    cooldown_seconds = 60

All values have safe defaults and are validated on load and on hot-reload.
Invalid values are rejected with clear logging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aip.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AutoTuningPolicy:
    """Policy parameters for auto-tuning behavior.

    All parameters have safe defaults matching Sprint 5.25 behavior.
    Values are validated on construction and on hot-reload.

    Attributes
    ----------
    read_pool_exhaustion_threshold:
        Exhaustion rate above which a store is considered under pressure.
        Range: [0.1, 0.9]. Default 0.3.
    read_pool_auto_apply_consecutive:
        Number of consecutive high-exhaustion observations before auto-applying.
        Range: [2, 20]. Default 5.
    read_pool_auto_apply_max_increase:
        Maximum additional connections auto-applied above configured pool_size.
        Range: [1, 10]. Default 4.
    read_pool_auto_apply_max_pool:
        Absolute maximum pool size (hard cap).
        Range: [3, 20]. Default 12.
    read_pool_auto_rollback_enabled:
        Whether auto-rollback is enabled when exhaustion recovers.
        Default True.
    read_pool_auto_rollback_consecutive:
        Consecutive low-exhaustion observations before auto-rollback.
        Range: [2, 20]. Default 5.
    read_pool_auto_rollback_healthy:
        Exhaustion rate below which a store is considered healthy for rollback.
        Range: [0.01, 0.5]. Default 0.15.
    graph_batch_decrease_threshold:
        Failure rate above which batch size is decreased.
        Range: [0.1, 0.8]. Default 0.3.
    graph_batch_increase_threshold:
        Failure rate below which batch size is increased.
        Range: [0.0, 0.5]. Default 0.1.
    graph_batch_auto_tune_window:
        Number of batches to consider for auto-tuning decisions.
        Range: [2, 20]. Default 5.
    graph_batch_min_size:
        Minimum batch size.
        Range: [1, 4]. Default 1.
    graph_batch_max_size:
        Maximum batch size.
        Range: [2, 16]. Default 8.
    cooldown_seconds:
        Minimum time between auto-tuning adjustments.
        Range: [0, 3600]. Default 60.
    """

    # Read pool policy
    read_pool_exhaustion_threshold: float = 0.3
    read_pool_auto_apply_consecutive: int = 5
    read_pool_auto_apply_max_increase: int = 4
    read_pool_auto_apply_max_pool: int = 12
    read_pool_auto_rollback_enabled: bool = True
    read_pool_auto_rollback_consecutive: int = 5
    read_pool_auto_rollback_healthy: float = 0.15

    # Graph batch policy
    graph_batch_decrease_threshold: float = 0.3
    graph_batch_increase_threshold: float = 0.1
    graph_batch_auto_tune_window: int = 5
    graph_batch_min_size: int = 1
    graph_batch_max_size: int = 8

    # General policy
    cooldown_seconds: int = 60

    # Validation errors from last validation (populated by validate())
    _validation_errors: list[str] = field(default_factory=list, repr=False)

    def validate(self) -> list[str]:
        """Validate all policy values and return a list of error strings.

        Populates self._validation_errors.  Returns empty list if all valid.
        """
        errors: list[str] = []

        # Read pool policy validation
        if not (0.1 <= self.read_pool_exhaustion_threshold <= 0.9):
            errors.append(
                f"read_pool_exhaustion_threshold={self.read_pool_exhaustion_threshold} must be between 0.1 and 0.9"
            )
        if not (2 <= self.read_pool_auto_apply_consecutive <= 20):
            errors.append(
                f"read_pool_auto_apply_consecutive={self.read_pool_auto_apply_consecutive} must be between 2 and 20"
            )
        if not (1 <= self.read_pool_auto_apply_max_increase <= 10):
            errors.append(
                f"read_pool_auto_apply_max_increase={self.read_pool_auto_apply_max_increase} must be between 1 and 10"
            )
        if not (3 <= self.read_pool_auto_apply_max_pool <= 20):
            errors.append(
                f"read_pool_auto_apply_max_pool={self.read_pool_auto_apply_max_pool} must be between 3 and 20"
            )
        if not (2 <= self.read_pool_auto_rollback_consecutive <= 20):
            errors.append(
                f"read_pool_auto_rollback_consecutive={self.read_pool_auto_rollback_consecutive} "
                f"must be between 2 and 20"
            )
        if not (0.01 <= self.read_pool_auto_rollback_healthy <= 0.5):
            errors.append(
                f"read_pool_auto_rollback_healthy={self.read_pool_auto_rollback_healthy} must be between 0.01 and 0.5"
            )

        # Cross-field validation: rollback healthy must be less than exhaustion threshold
        if self.read_pool_auto_rollback_healthy >= self.read_pool_exhaustion_threshold:
            errors.append(
                f"read_pool_auto_rollback_healthy ({self.read_pool_auto_rollback_healthy}) "
                f"must be less than read_pool_exhaustion_threshold "
                f"({self.read_pool_exhaustion_threshold})"
            )

        # Graph batch policy validation
        if not (0.1 <= self.graph_batch_decrease_threshold <= 0.8):
            errors.append(
                f"graph_batch_decrease_threshold={self.graph_batch_decrease_threshold} must be between 0.1 and 0.8"
            )
        if not (0.0 <= self.graph_batch_increase_threshold <= 0.5):
            errors.append(
                f"graph_batch_increase_threshold={self.graph_batch_increase_threshold} must be between 0.0 and 0.5"
            )
        if self.graph_batch_increase_threshold >= self.graph_batch_decrease_threshold:
            errors.append(
                f"graph_batch_increase_threshold ({self.graph_batch_increase_threshold}) "
                f"must be less than graph_batch_decrease_threshold "
                f"({self.graph_batch_decrease_threshold})"
            )
        if not (2 <= self.graph_batch_auto_tune_window <= 20):
            errors.append(f"graph_batch_auto_tune_window={self.graph_batch_auto_tune_window} must be between 2 and 20")
        if not (1 <= self.graph_batch_min_size <= 4):
            errors.append(f"graph_batch_min_size={self.graph_batch_min_size} must be between 1 and 4")
        if not (2 <= self.graph_batch_max_size <= 16):
            errors.append(f"graph_batch_max_size={self.graph_batch_max_size} must be between 2 and 16")
        if self.graph_batch_min_size >= self.graph_batch_max_size:
            errors.append(
                f"graph_batch_min_size ({self.graph_batch_min_size}) "
                f"must be less than graph_batch_max_size ({self.graph_batch_max_size})"
            )

        # General policy validation
        if not (0 <= self.cooldown_seconds <= 3600):
            errors.append(f"cooldown_seconds={self.cooldown_seconds} must be between 0 and 3600")

        self._validation_errors = errors
        return errors

    def is_valid(self) -> bool:
        """Return True if all policy values are valid."""
        return len(self.validate()) == 0

    def to_dict(self) -> dict:
        """Return all policy values as a dict."""
        return {
            "read_pool_exhaustion_threshold": self.read_pool_exhaustion_threshold,
            "read_pool_auto_apply_consecutive": self.read_pool_auto_apply_consecutive,
            "read_pool_auto_apply_max_increase": self.read_pool_auto_apply_max_increase,
            "read_pool_auto_apply_max_pool": self.read_pool_auto_apply_max_pool,
            "read_pool_auto_rollback_enabled": self.read_pool_auto_rollback_enabled,
            "read_pool_auto_rollback_consecutive": self.read_pool_auto_rollback_consecutive,
            "read_pool_auto_rollback_healthy": self.read_pool_auto_rollback_healthy,
            "graph_batch_decrease_threshold": self.graph_batch_decrease_threshold,
            "graph_batch_increase_threshold": self.graph_batch_increase_threshold,
            "graph_batch_auto_tune_window": self.graph_batch_auto_tune_window,
            "graph_batch_min_size": self.graph_batch_min_size,
            "graph_batch_max_size": self.graph_batch_max_size,
            "cooldown_seconds": self.cooldown_seconds,
        }


def load_policy_from_config(config: dict) -> AutoTuningPolicy:
    """Load auto-tuning policy from the TOML config dict.

    Reads the ``[auto_tuning_policy]`` section and creates an
    AutoTuningPolicy instance.  Unknown keys are ignored.
    Validates the resulting policy and logs warnings for any
    invalid values (using defaults instead).

    Parameters
    ----------
    config:
        The full TOML config dict.

    Returns a validated AutoTuningPolicy.
    """
    section = config.get("auto_tuning_policy", {})
    if not isinstance(section, dict):
        section = {}

    # Map config keys to AutoTuningPolicy fields
    field_map = {
        "read_pool_exhaustion_threshold": float,
        "read_pool_auto_apply_consecutive": int,
        "read_pool_auto_apply_max_increase": int,
        "read_pool_auto_apply_max_pool": int,
        "read_pool_auto_rollback_enabled": bool,
        "read_pool_auto_rollback_consecutive": int,
        "read_pool_auto_rollback_healthy": float,
        "graph_batch_decrease_threshold": float,
        "graph_batch_increase_threshold": float,
        "graph_batch_auto_tune_window": int,
        "graph_batch_min_size": int,
        "graph_batch_max_size": int,
        "cooldown_seconds": int,
    }

    kwargs: dict[str, Any] = {}
    for key, type_fn in field_map.items():
        if key in section:
            try:
                kwargs[key] = type_fn(section[key])
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "auto_tuning_policy_invalid_type",
                    key=key,
                    value=section[key],
                    expected_type=type_fn.__name__,
                    error=str(exc),
                )

    policy = AutoTuningPolicy(**kwargs)

    # Validate and log warnings for any invalid values
    errors = policy.validate()
    if errors:
        for err in errors:
            logger.warning("auto_tuning_policy_validation_error", error=err)

    return policy


def apply_policy_to_auto_sizer(
    policy: AutoTuningPolicy,
    auto_sizer: Any,
) -> list[str]:
    """Apply validated policy values to a ReadPoolAutoSizer instance.

    Only applies values that pass validation.  Returns a list of
    applied parameter names.
    """
    if not policy.is_valid():
        logger.warning(
            "auto_tuning_policy_skipped_invalid",
            errors=policy._validation_errors,
        )
        return []

    applied = []

    # Apply read pool policy
    auto_sizer._consecutive_threshold = policy.read_pool_auto_apply_consecutive
    applied.append("read_pool_auto_apply_consecutive")

    auto_sizer.auto_apply_enabled = True  # Always enabled when policy is active
    auto_sizer._auto_apply_consecutive_threshold = policy.read_pool_auto_apply_consecutive
    applied.append("auto_apply_consecutive_threshold")

    auto_sizer._auto_apply_max_increase = policy.read_pool_auto_apply_max_increase
    applied.append("auto_apply_max_increase")

    auto_sizer._auto_apply_max_pool = policy.read_pool_auto_apply_max_pool
    applied.append("auto_apply_max_pool")

    auto_sizer.auto_rollback_enabled = policy.read_pool_auto_rollback_enabled
    applied.append("auto_rollback_enabled")

    auto_sizer._auto_rollback_consecutive_threshold = policy.read_pool_auto_rollback_consecutive
    applied.append("auto_rollback_consecutive_threshold")

    auto_sizer._auto_rollback_healthy_threshold = policy.read_pool_auto_rollback_healthy
    applied.append("auto_rollback_healthy_threshold")

    # Sprint 5.27: Apply exhaustion threshold to auto-sizer
    auto_sizer._exhaustion_threshold = policy.read_pool_exhaustion_threshold
    applied.append("exhaustion_threshold")

    logger.info(
        "auto_tuning_policy_applied_to_sizer",
        applied_params=applied,
    )

    return applied


def apply_policy_to_sexton(
    policy: AutoTuningPolicy,
    sexton: Any,
) -> list[str]:
    """Apply validated policy values to a Sexton actor instance.

    Only applies values that pass validation.  Returns a list of
    applied parameter names.
    """
    if not policy.is_valid():
        return []

    applied = []
    config = getattr(sexton, "_config", None)
    if config is None:
        return []

    # Apply graph batch policy
    if hasattr(config, "graph_extraction_auto_tune_decrease_threshold"):
        config.graph_extraction_auto_tune_decrease_threshold = policy.graph_batch_decrease_threshold
        applied.append("graph_batch_decrease_threshold")

    if hasattr(config, "graph_extraction_auto_tune_increase_threshold"):
        config.graph_extraction_auto_tune_increase_threshold = policy.graph_batch_increase_threshold
        applied.append("graph_batch_increase_threshold")

    if hasattr(config, "graph_extraction_auto_tune_window"):
        config.graph_extraction_auto_tune_window = policy.graph_batch_auto_tune_window
        applied.append("graph_batch_auto_tune_window")

    if hasattr(config, "graph_extraction_batch_size_min"):
        config.graph_extraction_batch_size_min = policy.graph_batch_min_size
        applied.append("graph_batch_min_size")

    if hasattr(config, "graph_extraction_batch_size_max"):
        config.graph_extraction_batch_size_max = policy.graph_batch_max_size
        applied.append("graph_batch_max_size")

    logger.info(
        "auto_tuning_policy_applied_to_sexton",
        applied_params=applied,
    )

    return applied
