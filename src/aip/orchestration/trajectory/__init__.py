"""L4 trajectory regulation and context reset (CHUNK-5.6+).

Per Phase 3 BuildSpec Rev 1.1 and Architecture §10.1/§10.2.
"""

from .context_reset import execute_context_reset, inject_deterministic_recovery

__all__ = ["execute_context_reset", "inject_deterministic_recovery"]
