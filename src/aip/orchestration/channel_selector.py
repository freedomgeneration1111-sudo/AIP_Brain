"""ChannelSelector — rule-based adaptive channel selection for RetrievalOrchestrator.

Provides lightweight, rule-based logic to auto-enable relevant
retrieval channels based on query characteristics.  This is intentionally
simple and overridable via explicit ``OrchestratorConfig`` settings.

Data-driven tuning has lowered the entity threshold (any entity mention
is a strong Graph signal), added Graph auto-enablement for Wiki and
Procedural queries, and introduced cross-domain signal detection for
queries spanning multiple concepts (e.g., "AIP and Knowledge Graph").

Rules:
  - **Entity signals** (capitalised words, proper nouns) → enable Graph channel.
  - **Procedural signals** ("how do I", "steps to", "guide", "tutorial") →
    enable Procedural + Graph channels.
  - **Wiki signals** (domain terminology, encyclopedic phrasing) → enable
    Wiki + Graph channels.
  - **Semantic signals** (conceptual questions, "what is", "explain") →
    enable Vector channel (if not already default).
  - **Cross-domain signals** (multiple entities, compound questions) →
    enable all channels for maximum coverage.

The selector returns a dict of suggested ``enable_*`` overrides that can
be merged into an ``OrchestratorConfig``.  Explicit user-provided settings
always take precedence over the selector's suggestions.

Layer: orchestration.  May import foundation, stdlib.  May NOT import
adapter directly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal patterns
# ---------------------------------------------------------------------------

# Procedural signals — queries asking for how-to / step-by-step guidance
_PROCEDURAL_PATTERNS = re.compile(
    r"(?i)\b("
    r"how\s+(do|should|can|to|does)\s+i\b|"
    r"steps?\s+to\b|"
    r"guide\s+(to|for|on)\b|"
    r"tutorial\b|"
    r"walk\s*through\b|"
    r"step\s*by\s*step\b|"
    r"instructions?\b|"
    r"procedur(e|al|ally)\b|"
    r"how\s+to\b|"
    r"process\s+(for|to)\b|"
    r"recipe\s+(for|to)\b|"
    r"set\s*up\b|"
    r"configure?\b|"
    r"install\b|"
    r"deploy\b"
    r")\b"
)

# Entity signals — queries containing proper nouns or multi-word capitalised
# phrases that suggest the user is asking about specific entities.
_ENTITY_PATTERNS = re.compile(
    r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b"  # multi-word caps: "Knowledge Graph"
    r"|\b[A-Z][a-z]{2,}\b"  # single cap word: "Python", "AIP" (but not "The")
)

# Cross-domain signals — queries that mention multiple distinct entities
# or use connecting words like "and", "vs", "between", "compared to" which
# suggest the user is asking about relationships (Graph strength).
_CROSS_DOMAIN_PATTERNS = re.compile(
    r"(?i)\b("
    r"\band\b"
    r"|\bvs\.?\b"
    r"|\bversus\b"
    r"|\bbetween\b"
    r"|\bcompared\s+to\b"
    r"|\brelates?\s+to\b"
    r"|\bconnect(?:ion|s|ed)?\s+(?:to|with)\b"
    r"|\bdiffers?\s+(?:from|between)\b"
    r")\b"
)

# Wiki signals — encyclopedic / definitional queries
_WIKI_PATTERNS = re.compile(
    r"(?i)\b("
    r"what\s+is\b|"
    r"what\s+are\b|"
    r"define\b|"
    r"definition\s+of\b|"
    r"explain\b|"
    r"overview\s+(of|for)\b|"
    r"tell\s+me\s+about\b|"
    r"describe\b|"
    r"background\s+(on|of)\b|"
    r"history\s+of\b|"
    r"introduction\s+to\b"
    r")\b"
)

# Known sentence starters that should NOT be treated as entity signals
_SENTENCE_STARTERS = frozenset({
    "The", "This", "That", "These", "Those", "What", "Which", "Who",
    "How", "When", "Where", "Why", "Is", "Are", "Was", "Were", "Can",
    "Could", "Should", "Would", "Will", "Do", "Does", "Did", "Has",
})


# ---------------------------------------------------------------------------
# Query analysis result
# ---------------------------------------------------------------------------

@dataclass
class QueryAnalysis:
    """Result of analysing a query for channel selection signals.

    Attributes:
        has_entity_signals: Whether the query contains strong entity mentions.
        has_procedural_signals: Whether the query asks for procedural/how-to info.
        has_wiki_signals: Whether the query has encyclopedic/definitional intent.
        has_cross_domain_signals: Whether the query mentions multiple entities
            or asks about relationships between concepts.
        entity_count: Number of distinct entity-like terms found.
        matched_patterns: Human-readable list of what patterns matched.
    """

    has_entity_signals: bool = False
    has_procedural_signals: bool = False
    has_wiki_signals: bool = False
    has_cross_domain_signals: bool = False
    entity_count: int = 0
    matched_patterns: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.matched_patterns is None:
            self.matched_patterns = []


def analyze_query(query: str) -> QueryAnalysis:
    """Analyze a query for channel selection signals.

    This is a pure function with no side effects, suitable for unit testing.

    Args:
        query: The user's query string.

    Returns:
        QueryAnalysis with signal detection results.
    """
    analysis = QueryAnalysis()

    # Entity signals
    entity_matches = _ENTITY_PATTERNS.findall(query)
    # Filter out sentence starters
    entity_terms = []
    for match in entity_matches:
        if isinstance(match, tuple):
            match = match[0]  # regex group
        if match not in _SENTENCE_STARTERS:
            entity_terms.append(match)

    if entity_terms:
        analysis.has_entity_signals = True
        analysis.entity_count = len(entity_terms)
        analysis.matched_patterns.append(f"entity_terms={entity_terms}")

    # Procedural signals
    proc_match = _PROCEDURAL_PATTERNS.search(query)
    if proc_match:
        analysis.has_procedural_signals = True
        analysis.matched_patterns.append(f"procedural={proc_match.group()}")

    # Wiki signals
    wiki_match = _WIKI_PATTERNS.search(query)
    if wiki_match:
        analysis.has_wiki_signals = True
        analysis.matched_patterns.append(f"wiki={wiki_match.group()}")

    # Cross-domain signals
    cross_match = _CROSS_DOMAIN_PATTERNS.search(query)
    if cross_match and analysis.entity_count >= 2:
        analysis.has_cross_domain_signals = True
        analysis.matched_patterns.append(f"cross_domain={cross_match.group()}")

    return analysis


# ---------------------------------------------------------------------------
# ChannelSelector
# ---------------------------------------------------------------------------

@dataclass
class ChannelSelectionResult:
    """Result of adaptive channel selection.

    Attributes:
        enable_graph: Suggested setting for the Graph channel.
        enable_wiki: Suggested setting for the Wiki channel.
        enable_procedural: Suggested setting for the Procedural channel.
        enable_vector: Suggested setting for the Vector channel.
        analysis: The underlying QueryAnalysis that drove the selection.
        auto_enabled_channels: List of channel names that were auto-enabled.
    """

    enable_graph: bool = False
    enable_wiki: bool = False
    enable_procedural: bool = False
    enable_vector: bool = True  # vector is default-on, stays on
    analysis: QueryAnalysis | None = None
    auto_enabled_channels: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.auto_enabled_channels is None:
            self.auto_enabled_channels = []


class ChannelSelector:
    """Rule-based adaptive channel selector.

    Analyses a query's characteristics and suggests which retrieval channels
    should be enabled.  The suggestions can be merged into an
    ``OrchestratorConfig``, with explicit user settings always taking
    precedence.

    The selector is intentionally lightweight — no LLM calls, no external
    models, just regex-based pattern matching.  This keeps it fast, testable,
    and predictable.

    Usage::

        selector = ChannelSelector()
        result = selector.select("How do I configure the Knowledge Graph?")
        # result.enable_graph → True
        # result.enable_procedural → True
        # result.enable_wiki → False

        # Merge into OrchestratorConfig:
        config = OrchestratorConfig(
            enable_graph=result.enable_graph,
            enable_procedural=result.enable_procedural,
            enable_wiki=result.enable_wiki,
        )
    """

    def __init__(
        self,
        entity_threshold: int = 0,
        enable_graph_on_entity: bool = True,
        enable_procedural_on_howto: bool = True,
        enable_wiki_on_definitional: bool = True,
        enable_vector_on_semantic: bool = True,
        enable_graph_on_wiki: bool = True,
        enable_graph_on_procedural: bool = True,
        enable_all_on_cross_domain: bool = True,
    ) -> None:
        """Initialise the channel selector with rule configuration.

        Args:
            entity_threshold: Minimum number of entity terms to trigger
                Graph channel enablement.  Default is 0 — any entity
                signal enables Graph.
            enable_graph_on_entity: Whether to enable Graph on entity signals.
            enable_procedural_on_howto: Whether to enable Procedural on
                how-to signals.
            enable_wiki_on_definitional: Whether to enable Wiki on
                definitional signals.
            enable_vector_on_semantic: Whether to enable Vector on semantic
                (conceptual) signals.
            enable_graph_on_wiki: Whether to also enable Graph when Wiki
                signals are detected.  Definitional queries about concepts
                often mention named entities that benefit from graph
                expansion.
            enable_graph_on_procedural: Whether to also enable Graph when
                Procedural signals are detected.  How-to queries about
                tools/protocols benefit from entity-linked context.
            enable_all_on_cross_domain: Whether to enable all channels
                when cross-domain signals are detected (multiple entities
                + relationship words like "vs", "and", "between").
        """
        self._entity_threshold = entity_threshold
        self._enable_graph_on_entity = enable_graph_on_entity
        self._enable_procedural_on_howto = enable_procedural_on_howto
        self._enable_wiki_on_definitional = enable_wiki_on_definitional
        self._enable_vector_on_semantic = enable_vector_on_semantic
        self._enable_graph_on_wiki = enable_graph_on_wiki
        self._enable_graph_on_procedural = enable_graph_on_procedural
        self._enable_all_on_cross_domain = enable_all_on_cross_domain

    def select(self, query: str) -> ChannelSelectionResult:
        """Analyze a query and suggest channel enablement.

        Args:
            query: The user's query string.

        Returns:
            ChannelSelectionResult with suggested settings.
        """
        analysis = analyze_query(query)
        result = ChannelSelectionResult(analysis=analysis)
        auto_enabled: list[str] = []

        # Entity signals → Graph channel
        if (
            self._enable_graph_on_entity
            and analysis.has_entity_signals
            and analysis.entity_count >= self._entity_threshold
        ):
            result.enable_graph = True
            auto_enabled.append("graph")

        # Procedural signals → Procedural channel
        if self._enable_procedural_on_howto and analysis.has_procedural_signals:
            result.enable_procedural = True
            auto_enabled.append("procedural")
            # Procedural queries also benefit from Graph
            # (e.g., "configure Knowledge Graph" → Graph finds KG entities)
            if self._enable_graph_on_procedural:
                result.enable_graph = True
                if "graph" not in auto_enabled:
                    auto_enabled.append("graph")

        # Wiki signals → Wiki channel
        if self._enable_wiki_on_definitional and analysis.has_wiki_signals:
            result.enable_wiki = True
            auto_enabled.append("wiki")
            # Wiki/definitional queries also benefit from Graph
            # (e.g., "What is AIP?" → Graph finds AIP entity and connections)
            if self._enable_graph_on_wiki:
                result.enable_graph = True
                if "graph" not in auto_enabled:
                    auto_enabled.append("graph")

        # Semantic signals → Vector channel (already default-on, but
        # explicitly confirm for wiki-like queries that also have
        # conceptual depth)
        if self._enable_vector_on_semantic and analysis.has_wiki_signals:
            result.enable_vector = True
            # Don't add to auto_enabled since it's already default-on

        # Cross-domain signals → enable all channels
        # Queries with multiple entities and relationship words ("and",
        # "vs", "between") benefit from all channels — FTS for exact
        # matches, Vector for semantic, Graph for relationships.
        if self._enable_all_on_cross_domain and analysis.has_cross_domain_signals:
            result.enable_graph = True
            result.enable_wiki = True
            result.enable_procedural = True
            result.enable_vector = True
            for ch in ("graph", "wiki", "procedural"):
                if ch not in auto_enabled:
                    auto_enabled.append(ch)

        result.auto_enabled_channels = auto_enabled
        return result

    def apply_to_config(
        self,
        query: str,
        config: OrchestratorConfig | None = None,
        explicit_channels: set[str] | None = None,
    ) -> "OrchestratorConfig":
        """Analyze a query and return an OrchestratorConfig with auto-enabled channels.

        **Important**: The selector only *enables* channels — it never
        disables a channel that is already enabled.  Channels listed in
        ``explicit_channels`` are never modified, preventing the selector
        from overriding explicit user intent.

        If the caller wants full control over channel selection, they should
        set ``auto_channel_selection=False`` in the ask pipeline and specify
        each channel manually.

        Args:
            query: The user's query string.
            config: Existing config to merge into.  If None, a default
                config is created.
            explicit_channels: Set of channel names whose enable_* settings
                were explicitly provided by the caller and should NOT be
                modified.  For example, if the user passed
                ``enable_graph=False``, include ``"graph"`` in this set.

        Returns:
            OrchestratorConfig with adaptive channel suggestions applied.
        """
        from aip.orchestration.retrieval_orchestrator import OrchestratorConfig

        if config is None:
            config = OrchestratorConfig()

        if explicit_channels is None:
            explicit_channels = set()

        result = self.select(query)

        # Only auto-enable channels that were NOT explicitly set by the caller.
        # The selector only turns channels ON, never OFF.
        if result.enable_graph and "graph" not in explicit_channels:
            config.enable_graph = True
        if result.enable_procedural and "procedural" not in explicit_channels:
            config.enable_procedural = True
        if result.enable_wiki and "wiki" not in explicit_channels:
            config.enable_wiki = True

        return config
