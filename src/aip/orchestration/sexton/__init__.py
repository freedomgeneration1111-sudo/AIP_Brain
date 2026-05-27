"""
Sexton — Failure Classification and ACE Playbook Actor (CHUNK-3.4 foundation)

Per Architecture Rev 5.2 §16.1.
Minimal deterministic foundation only. Full playbook persistence, trust scoring,
and model-assisted classification are deferred.
"""

from aip.orchestration.sexton.sexton import Sexton

__all__ = ["Sexton"]
