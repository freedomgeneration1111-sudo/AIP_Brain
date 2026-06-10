"""Ask-related types.

Schemas for the source-grounded ask pipeline: source references,
ask results, and source selection types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AskSource = Literal["ingested", "artifacts", "all"]


@dataclass
class SourceReference:
    """A reference to a source used in generating an answer.

    Captures provenance back to the original ingested conversation
    or project artifact, enabling audit trails and verification.
    """

    source_id: str  # chunk_id or artifact_id
    source_type: str  # "conversation_chunk" | "artifact" | "compiled_knowledge"
    title: str  # conversation title or artifact name
    score: float  # retrieval score (lexical rank or vector similarity)
    content_snippet: str  # first ~200 chars of source content
    domain: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class AskResult:
    """Outcome of an ask query against the AIP knowledge substrate.

    Captures the full provenance chain: what was asked, what sources
    were found, what model was used, what answer was generated, and
    whether any artifacts were saved.

    Failure modes are explicit: status indicates the overall outcome
    and errors lists any problems encountered.

    Chunk 5 addition: ``retrieval_degradation`` carries an honest
    account of what retrieval backends were available, degraded, or
    absent.  The system is required to surface this information
    rather than silently pretending retrieval was healthier than it was.
    """

    status: str  # "OK" | "NO_PROJECT" | "NO_PROJECT_MEMORY" | "NEEDS_CONFIGURATION" | "MODEL_FAILURE" | "ARTIFACT_SAVE_FAILURE"
    answer: str  # generated answer or error message
    sources: list[SourceReference] = field(default_factory=list)
    model_slot: str = ""
    model_provider: str = ""
    artifact_id: str = ""  # set when --save-artifact succeeds
    session_id: str = ""
    project_id: str = ""
    project_name: str = ""
    prompt: str = ""
    errors: list[str] = field(default_factory=list)
    # Chunk 5: Retrieval honesty — honest degradation metadata
    retrieval_degradation: dict = field(default_factory=dict)


__all__ = [
    "AskSource",
    "SourceReference",
    "AskResult",
]
