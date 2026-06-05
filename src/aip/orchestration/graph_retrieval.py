"""Graph-augmented retrieval using Personalized PageRank.

HippoRAG-inspired: seed PPR on query entities to activate relevant
subgraph in a single traversal. Surfaces cross-domain connections
without explicit queries.

Layer: orchestration. May import foundation, stdlib, networkx.
May NOT import adapter directly — GraphStore injected via dependency.

Reference: HippoRAG (arXiv:2405.14831, NeurIPS 2024)
"""

from __future__ import annotations

from typing import Any


class GraphRetriever:
    """PPR-based graph retrieval over the knowledge graph.

    graph_store: injected GraphStore instance (any object with
    get_all_nodes(), get_all_edges(), get_neighbors() methods).
    """

    def __init__(self, graph_store: Any) -> None:
        self._store = graph_store

    def expand_query_via_graph(
        self,
        seed_entities: list[str],
        max_hops: int = 2,
        top_k: int = 10,
        min_confidence: float = 0.4,
    ) -> list[str]:
        """Run Personalized PageRank from seed_entities.

        Returns list of entity/domain names that are graph-neighbors
        of the seeds, ranked by PageRank score.
        """
        try:
            import networkx as nx
        except ImportError:
            return []

        nodes = self._store.get_all_nodes(min_confidence=min_confidence)
        edges = self._store.get_all_edges(min_confidence=min_confidence)

        if not nodes:
            return []

        G: nx.DiGraph = nx.DiGraph()
        for n in nodes:
            G.add_node(n.id, label=n.canonical_name, entity_type=n.entity_type)
        for e in edges:
            if G.has_node(e.source_id) and G.has_node(e.target_id):
                G.add_edge(e.source_id, e.target_id, weight=float(e.weight or 1.0))

        if len(G.nodes) == 0:
            return []

        seed_set = {s.lower() for s in seed_entities}
        personalization: dict[str, float] = {}
        for node_id in G.nodes:
            label = G.nodes[node_id].get("label", node_id)
            if node_id.lower() in seed_set or label.lower() in seed_set:
                personalization[node_id] = 1.0
            else:
                personalization[node_id] = 0.0

        if not any(v > 0 for v in personalization.values()):
            return []

        try:
            scores = nx.pagerank(G, personalization=personalization, alpha=0.85, max_iter=100)
        except Exception:
            return []

        sorted_nodes = sorted(scores.items(), key=lambda kv: -kv[1])
        results = []
        for node_id, _ in sorted_nodes:
            if node_id.lower() not in seed_set and len(results) < top_k:
                label = G.nodes[node_id].get("label", node_id)
                results.append(label)
        return results

    def get_domain_neighbors(self, domain: str) -> list[str]:
        """Return domains directly connected to given domain via bridge edges.

        Used for lightweight domain context expansion in augmented chat.
        No PPR — direct adjacency only for speed.
        """
        try:
            neighbors = self._store.get_neighbors(domain, min_confidence=0.4)
            return [n.canonical_name for n in neighbors if n.id != domain]
        except Exception:
            return []
