"""Condenser orchestrator -- storage-agnostic RO-Crate condensation.

Wraps the pure `condense_graph` algorithm with the RO-Crate envelope
assembly (file descriptor + root + rest) and the source -> persist flow
that both the server's /condense endpoint and the interpretation
pipeline's pre-step need.

Depends only on `GraphSource` and `ResultSink` from `ports`. The server
supplies Mongo-backed adapters; the CLI supplies local-file adapters.
"""

from __future__ import annotations

from typing import Tuple

from fairscape_interpret.pipeline.condense import condense_graph
from fairscape_interpret.pipeline.graph_utils import is_rocrate_root
from fairscape_interpret.ports import GraphSource, ResultSink


class Condenser:
    """Builds a condensed RO-Crate from a full provenance graph.

    Two entry points:
      - `condense(rocrate_id)`: always condense + persist. Raises if a
        condensed version already exists. Matches the behavior of the
        direct `/condense` endpoint on the server.
      - `ensure_condensed(rocrate_id)`: return an existing condensed
        crate if one is reachable via `hasCondensedROCrate` pointer or
        `evi:condensed` marker, otherwise condense now. Used by the
        interpretation pipeline as its pre-step.
    """

    def __init__(
        self,
        source: GraphSource,
        sink: ResultSink,
        *,
        threshold: int = 5,
        max_member_ids: int = 0,
    ):
        self.source = source
        self.sink = sink
        self.threshold = threshold
        self.max_member_ids = max_member_ids

    @staticmethod
    def condensed_id_for(rocrate_id: str) -> str:
        """The canonical id assigned to a condensed derivative."""
        return f"{rocrate_id}-condensed"

    def condense(self, rocrate_id: str) -> str:
        """Condense the crate and persist the result. Returns the
        persisted condensed @id.

        Raises `ValueError` if a condensed derivative already exists, to
        avoid silently overwriting — callers that want idempotent
        behavior should use `ensure_condensed` instead.
        """
        condensed_id = self.condensed_id_for(rocrate_id)
        if self.source.find_entity(condensed_id) is not None:
            raise ValueError(
                f"Condensed RO-Crate {condensed_id} already exists"
            )
        _, persisted_id = self._build_and_persist(rocrate_id)
        return persisted_id

    def ensure_condensed(
        self, rocrate_id: str
    ) -> Tuple[list[dict], str, dict]:
        """Return a flat `@graph` list, the condensed-crate id, and the
        root node. Returns an existing condensation when available:

          1. If the source RO-Crate has `hasCondensedROCrate`, fetch
             that crate and return its graph.
          2. Else if the source root itself has `evi:condensed: true`,
             treat the source as already condensed.
          3. Else run a fresh condensation and persist it.

        Raises `ValueError` if the RO-Crate doesn't exist.
        """
        entity = self.source.find_entity(rocrate_id)
        if entity is None:
            raise ValueError(f"RO-Crate {rocrate_id} not found")

        # Case 1: pointer to a pre-condensed derivative
        condensed_ref = entity.get("hasCondensedROCrate")
        if condensed_ref:
            condensed_id = (
                condensed_ref.get("@id")
                if isinstance(condensed_ref, dict)
                else condensed_ref
            )
            if condensed_id:
                condensed_entity = self.source.find_entity(condensed_id)
                if condensed_entity is not None:
                    graph = self._graph_as_list(condensed_entity.get("@graph"))
                    root = next((n for n in graph if is_rocrate_root(n)), {})
                    return graph, condensed_id, root

        # Case 2: the source crate is already condensed in place
        source_graph = self._graph_as_list(entity.get("@graph"))
        for node in source_graph:
            if is_rocrate_root(node) and node.get("evi:condensed") is True:
                return source_graph, rocrate_id, node

        # Case 3: no existing condensation -- build one
        graph, condensed_id = self._build_and_persist(rocrate_id)
        root = next((n for n in graph if is_rocrate_root(n)), {})
        return graph, condensed_id, root

    def _build_and_persist(
        self, rocrate_id: str
    ) -> Tuple[list[dict], str]:
        """Core flow: build full graph, run condensation, assemble the
        RO-Crate envelope, hand off to sink. Returns (graph_list,
        persisted_id). Returned graph is the same list that was
        persisted, so callers don't need to re-fetch (which lets
        sidecar-only sinks work without a round-trip)."""
        full_graph = self.source.build_full_graph(rocrate_id)
        if not full_graph:
            raise ValueError(
                f"No metadata found for RO-Crate {rocrate_id}"
            )

        condensed_graph, stats = condense_graph(
            full_graph, self.threshold, self.max_member_ids
        )
        condensed_metadata = self._assemble_metadata(
            rocrate_id, condensed_graph, stats
        )
        condensed_id = self.condensed_id_for(rocrate_id)

        persisted_id = self.sink.persist_condensed(
            condensed_id, condensed_metadata, rocrate_id, stats
        )
        return condensed_metadata["@graph"], persisted_id

    @staticmethod
    def _assemble_metadata(
        source_rocrate_id: str,
        condensed_graph: list[dict],
        stats: dict,
    ) -> dict:
        """Wrap the condensed graph in a proper RO-Crate envelope:
        @graph[0] = ro-crate-metadata.json file descriptor,
        @graph[1] = the root crate node (with sourceROCrate + stats),
        @graph[2..] = the rest of the condensed entities."""
        root_idx = None
        for idx, node in enumerate(condensed_graph):
            if is_rocrate_root(node):
                root_idx = idx
                break

        if root_idx is not None:
            root_node = dict(condensed_graph[root_idx])
            root_node["evi:sourceROCrate"] = {"@id": source_rocrate_id}
            root_node["evi:condensationStats"] = stats
            condensed_graph[root_idx] = root_node

        file_elem = {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.2-DRAFT"},
            "about": {"@id": source_rocrate_id},
        }

        ordered_graph: list[dict] = [file_elem]
        if root_idx is not None:
            ordered_graph.append(condensed_graph[root_idx])
        for idx, node in enumerate(condensed_graph):
            if idx == root_idx:
                continue
            if node.get("@id") == "ro-crate-metadata.json":
                continue
            ordered_graph.append(node)

        return {
            "@context": {"@vocab": "https://schema.org/"},
            "@graph": ordered_graph,
        }

    @staticmethod
    def _graph_as_list(graph) -> list[dict]:
        """Normalize @graph, which may be a list (modern) or a dict
        keyed by @id (older stored crates)."""
        if isinstance(graph, dict):
            return list(graph.values())
        return graph or []
