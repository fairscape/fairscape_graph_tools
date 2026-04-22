"""Orchestrator for the standard (non-annotated) evidence graph.

`EvidenceGraphBuilder` drives BFS across a `GraphSource`, condenses the
collected node cache, projects it into the hierarchical
(`graph_dict`, `outputs`) shape, wraps the result in the shared
`EvidenceGraph` Pydantic model, and hands it to a `ResultSink` for
storage. The server (Mongo) and CLI (local RO-Crate) adapters plug in
on either side; this module has zero knowledge of MongoDB, Celery,
FastAPI, Click, or on-disk RO-Crate layouts.

The internal control flow intentionally mirrors the server-side
pre-refactor `EvidenceGraph.build_graph` (now living on a temporary
subclass in `fairscape_mds/models/evidence_graph.py`): outputs are
derived from the start node *before* condensation, and projection walks
that output list explicitly rather than delegating to the generic
`build_graph_dict`. This preserves the Phase 3 "byte-identical on a
fixture crate" acceptance-test invariant — condensation could in
principle prune a rocrate output, which would shift the shape if
outputs were re-derived post-condense.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from fairscape_graph_tools.models.evidence_graph import EvidenceGraph
from fairscape_graph_tools.pipeline.condense import condense_evidence_graph_cache
from fairscape_graph_tools.pipeline.evidence_graph import (
    _build_node_from_cache,
    _extract_referenced_ids,
    _get_rocrate_outputs,
    _is_rocrate,
)
from fairscape_graph_tools.ports import GraphSource, ResultSink

_ARK_RE = re.compile(r"^ark:/?(\d+)/(.*)$")


def _derive_evidence_graph_id(node_id: str) -> str:
    """Turn `ark:NAAN/postfix` into `ark:NAAN/evidence-graph-postfix`.

    Non-ARK inputs get `<node_id>-evidence-graph` as a best-effort
    fallback so CLI-style guids still produce a sensible sidecar id.
    """
    match = _ARK_RE.match(node_id)
    if match:
        naan, postfix = match.group(1), match.group(2)
        return f"ark:{naan}/evidence-graph-{postfix}"
    return f"{node_id}-evidence-graph"


class EvidenceGraphBuilder:
    """Build + persist an evidence graph rooted at `node_id`.

    Construction takes the two storage-facing ports plus the
    condensation threshold (defaults to 5, the historical server
    value). `build` is the single entry point.
    """

    def __init__(
        self,
        source: GraphSource,
        sink: ResultSink,
        *,
        condense_threshold: int = 5,
    ):
        self.source = source
        self.sink = sink
        self.condense_threshold = condense_threshold

    def build(
        self,
        node_id: str,
        *,
        owner_email: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> str:
        """Build and persist the evidence graph, returning its `@id`.

        Always runs a fresh BFS + projection + persist. Idempotency
        (reusing a pre-existing `hasEvidenceGraph` back-pointer) is a
        caller-side concern -- the server CRUD needs it to distinguish
        200 from 201 status codes, and the CLI explicitly wants a
        rebuild when the user re-invokes `fairscape build evidence-graph`.
        Pushing that decision up keeps the builder a single-purpose
        primitive.
        """
        found = self.source.find_many([node_id])
        start_node = found.get(node_id)

        evidence_graph_id = _derive_evidence_graph_id(node_id)
        evidence_graph = EvidenceGraph.model_validate({
            "@id": evidence_graph_id,
            "@type": "evi:EvidenceGraph",
            "name": name or f"Evidence Graph for {node_id}",
            "description": description
                or f"Automatically generated Evidence Graph for node {node_id}",
            "owner": owner_email,
            "graph": None,
        })

        graph_dict, output_nodes = self._populate(node_id, start_node, evidence_graph)
        evidence_graph.outputs = output_nodes
        evidence_graph.graph = graph_dict

        return self.sink.persist_evidence_graph(evidence_graph, node_id)

    def _populate(
        self,
        start_node_id: str,
        start_node: Optional[Dict],
        evidence_graph: EvidenceGraph,
    ) -> tuple[Dict[str, Dict], List[Dict[str, str]]]:
        graph_dict: Dict[str, Dict] = {}
        output_nodes: List[Dict[str, str]] = []

        if start_node is None:
            output_nodes.append({"@id": start_node_id})
            graph_dict[start_node_id] = {"@id": start_node_id, "error": "not found"}
            return graph_dict, output_nodes

        node_cache: Dict[str, Dict] = {start_node_id: start_node}

        node_type = start_node.get("@type", "")
        start_rocrate_id: Optional[str] = None
        start_rocrate_outputs: Optional[List[Dict]] = None
        if _is_rocrate(node_type):
            rocrate_outputs = _get_rocrate_outputs(start_node)
            start_rocrate_outputs = list(rocrate_outputs) if rocrate_outputs else []
            traversal_outputs = list(start_rocrate_outputs)
            traversal_outputs.append({"@id": start_node_id})
            start_rocrate_id = start_node_id
            for output_ref in traversal_outputs:
                if output_ref.get("@id"):
                    output_nodes.append({"@id": output_ref.get("@id")})
        else:
            output_nodes.append({"@id": start_node_id})

        current_level = {ref["@id"] for ref in output_nodes}
        processed_ids: set[str] = set()

        while current_level:
            ids_to_fetch = current_level - processed_ids
            if not ids_to_fetch:
                break

            ids_not_in_cache = [nid for nid in ids_to_fetch if nid not in node_cache]
            if ids_not_in_cache:
                fetched = self.source.find_many(ids_not_in_cache)
                node_cache.update(fetched)
                for nid in ids_not_in_cache:
                    if nid not in node_cache:
                        node_cache[nid] = {"@id": nid, "error": "not found"}

            next_level: set[str] = set()
            for nid in ids_to_fetch:
                if nid in processed_ids:
                    continue
                processed_ids.add(nid)
                node = node_cache.get(nid)
                if node and "error" not in node:
                    next_level.update(_extract_referenced_ids(node))
            current_level = next_level

        evidence_graph.condensation_stats = condense_evidence_graph_cache(
            node_cache, self.condense_threshold
        )

        for output_node in output_nodes:
            output_id = output_node.get("@id")
            if output_id:
                _build_node_from_cache(
                    output_id,
                    node_cache,
                    graph_dict,
                    start_rocrate_id,
                    start_rocrate_outputs,
                )

        return graph_dict, output_nodes
