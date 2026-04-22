"""Unit tests for ``EvidenceGraphBuilder``.

The builder is exercised against in-memory ``FakeGraphSource`` and
``FakeResultSink`` objects that satisfy the ``GraphSource`` /
``ResultSink`` port protocols. No Mongo, no filesystem, no HTTP.

Scenarios covered here:

- Simple DS -> Comp -> DS chain (happy path).
- Multi-level BFS (output-first fan-out).
- Not-found start-node produces an error-stub graph but still persists.
- Idempotency short-circuit on pre-existing ``hasEvidenceGraph``.
- Condensation trigger: a Computation with > threshold sibling datasets
  sharing a provenance signature collapses into a DatasetGroup and the
  projection carries the group summary fields.
- ``condense_threshold`` override disables/enables condensation.
- Owner email / name / description flow through to the persisted model.
- ``_derive_evidence_graph_id`` on ARK + non-ARK inputs.

Server regression (byte-identical fixture crate) is not covered here --
that needs a live Mongo and is tracked separately in
``EVIDENCE_GRAPH_MIGRATION.md``.
"""

from __future__ import annotations

from typing import Iterable

import pytest

from fairscape_graph_tools.evidence_graph_builder import (
    EvidenceGraphBuilder,
    _derive_evidence_graph_id,
)
from fairscape_graph_tools.models.evidence_graph import EvidenceGraph


# ---------------------------------------------------------------------------
# Fakes implementing the port Protocols
# ---------------------------------------------------------------------------


class FakeGraphSource:
    """In-memory GraphSource backed by a dict of nodes.

    Tracks every ``find_many`` call so tests can assert BFS didn't
    re-fetch the same ids.
    """

    def __init__(self, nodes: dict[str, dict]):
        self._nodes = nodes
        self.find_many_calls: list[list[str]] = []

    # --- port methods -------------------------------------------------

    def find_entity(self, ark_id: str) -> dict | None:
        return self._nodes.get(ark_id)

    def find_many(self, ark_ids: Iterable[str]) -> dict[str, dict]:
        ids = list(ark_ids)
        self.find_many_calls.append(ids)
        return {i: self._nodes[i] for i in ids if i in self._nodes}

    def find_dataset_stats(self, ark_ids: Iterable[str]) -> dict[str, dict]:
        return {}

    def build_full_graph(self, rocrate_id: str) -> list[dict]:  # unused
        return list(self._nodes.values())


class FakeResultSink:
    """Captures persisted evidence graphs for assertions."""

    def __init__(self):
        self.persisted: list[tuple[EvidenceGraph, str]] = []

    # --- port methods -------------------------------------------------

    def persist_evidence_graph(
        self, evidence_graph: EvidenceGraph, source_node_id: str
    ) -> str:
        self.persisted.append((evidence_graph, source_node_id))
        return evidence_graph.guid

    def persist_condensed(self, *a, **k):  # pragma: no cover - unused here
        raise AssertionError("persist_condensed should not be called")

    def persist_aeg(self, *a, **k):  # pragma: no cover - unused here
        raise AssertionError("persist_aeg should not be called")


# ---------------------------------------------------------------------------
# _derive_evidence_graph_id
# ---------------------------------------------------------------------------


class TestDeriveEvidenceGraphId:
    @pytest.mark.parametrize(
        "node_id,expected",
        [
            ("ark:12345/abc", "ark:12345/evidence-graph-abc"),
            ("ark:12345/abc-def", "ark:12345/evidence-graph-abc-def"),
            ("ark:/12345/abc", "ark:12345/evidence-graph-abc"),
        ],
    )
    def test_ark_inputs(self, node_id, expected):
        assert _derive_evidence_graph_id(node_id) == expected

    def test_non_ark_falls_back_to_suffix(self):
        assert _derive_evidence_graph_id("my-node") == "my-node-evidence-graph"


# ---------------------------------------------------------------------------
# Builder happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def _simple_chain(self) -> dict[str, dict]:
        return {
            "ark:7/ds-out": {
                "@id": "ark:7/ds-out",
                "@type": "Dataset",
                "name": "output",
                "description": "the output",
                "generatedBy": {"@id": "ark:7/comp"},
            },
            "ark:7/comp": {
                "@id": "ark:7/comp",
                "@type": "Computation",
                "name": "comp",
                "usedDataset": [{"@id": "ark:7/in"}],
                "usedSoftware": [{"@id": "ark:7/sw"}],
            },
            "ark:7/in": {
                "@id": "ark:7/in",
                "@type": "Dataset",
                "name": "input",
            },
            "ark:7/sw": {
                "@id": "ark:7/sw",
                "@type": "Software",
                "name": "sw",
            },
        }

    def test_returns_derived_id_and_persists_once(self):
        src = FakeGraphSource(self._simple_chain())
        sink = FakeResultSink()
        out = EvidenceGraphBuilder(src, sink).build(
            "ark:7/ds-out",
            owner_email="u@x",
            name="My EG",
            description="desc",
        )
        assert out == "ark:7/evidence-graph-ds-out"
        assert len(sink.persisted) == 1

    def test_graph_includes_all_reachable_nodes(self):
        src = FakeGraphSource(self._simple_chain())
        sink = FakeResultSink()
        EvidenceGraphBuilder(src, sink).build(
            "ark:7/ds-out", owner_email="u@x",
        )
        eg = sink.persisted[0][0]
        assert set(eg.graph or {}) == {
            "ark:7/ds-out",
            "ark:7/comp",
            "ark:7/in",
            "ark:7/sw",
        }

    def test_owner_name_description_flow_through(self):
        src = FakeGraphSource(self._simple_chain())
        sink = FakeResultSink()
        EvidenceGraphBuilder(src, sink).build(
            "ark:7/ds-out",
            owner_email="alice@x",
            name="Custom EG name",
            description="Custom desc",
        )
        eg = sink.persisted[0][0]
        assert eg.owner == "alice@x"
        assert eg.name == "Custom EG name"
        assert eg.description == "Custom desc"

    def test_default_name_and_description_when_not_provided(self):
        src = FakeGraphSource(self._simple_chain())
        sink = FakeResultSink()
        EvidenceGraphBuilder(src, sink).build(
            "ark:7/ds-out", owner_email="u@x",
        )
        eg = sink.persisted[0][0]
        assert eg.name == "Evidence Graph for ark:7/ds-out"
        assert "ark:7/ds-out" in eg.description

    def test_edges_preserve_reference_shape(self):
        src = FakeGraphSource(self._simple_chain())
        sink = FakeResultSink()
        EvidenceGraphBuilder(src, sink).build(
            "ark:7/ds-out", owner_email="u@x",
        )
        eg = sink.persisted[0][0]
        assert eg.graph["ark:7/ds-out"]["generatedBy"] == {
            "@id": "ark:7/comp",
        }
        assert eg.graph["ark:7/comp"]["usedDataset"] == [
            {"@id": "ark:7/in"},
        ]
        assert eg.graph["ark:7/comp"]["usedSoftware"] == [
            {"@id": "ark:7/sw"},
        ]

    def test_outputs_list_points_at_start_node(self):
        src = FakeGraphSource(self._simple_chain())
        sink = FakeResultSink()
        EvidenceGraphBuilder(src, sink).build(
            "ark:7/ds-out", owner_email="u@x",
        )
        eg = sink.persisted[0][0]
        assert eg.outputs == [{"@id": "ark:7/ds-out"}]

    def test_source_node_id_passed_through_to_sink(self):
        src = FakeGraphSource(self._simple_chain())
        sink = FakeResultSink()
        EvidenceGraphBuilder(src, sink).build(
            "ark:7/ds-out", owner_email="u@x",
        )
        _, source_id = sink.persisted[0]
        assert source_id == "ark:7/ds-out"


# ---------------------------------------------------------------------------
# BFS behavior
# ---------------------------------------------------------------------------


class TestBFS:
    def test_multi_level_fans_out_through_all_levels(self):
        nodes = {
            "ark:7/level-0": {
                "@id": "ark:7/level-0",
                "@type": "Dataset",
                "generatedBy": {"@id": "ark:7/comp-A"},
            },
            "ark:7/comp-A": {
                "@id": "ark:7/comp-A",
                "@type": "Computation",
                "usedDataset": [{"@id": "ark:7/level-1"}],
            },
            "ark:7/level-1": {
                "@id": "ark:7/level-1",
                "@type": "Dataset",
                "generatedBy": {"@id": "ark:7/comp-B"},
            },
            "ark:7/comp-B": {
                "@id": "ark:7/comp-B",
                "@type": "Computation",
                "usedDataset": [{"@id": "ark:7/level-2"}],
            },
            "ark:7/level-2": {
                "@id": "ark:7/level-2",
                "@type": "Dataset",
            },
        }
        sink = FakeResultSink()
        EvidenceGraphBuilder(FakeGraphSource(nodes), sink).build(
            "ark:7/level-0", owner_email="u@x",
        )
        graph = sink.persisted[0][0].graph
        assert set(graph) == set(nodes)  # everything reachable is included

    def test_shared_downstream_fetched_once(self):
        """Two sibling datasets generated by the same upstream computation --
        the BFS must dedupe the shared upstream and fetch it exactly once."""
        nodes = {
            "ark:7/out": {
                "@id": "ark:7/out",
                "@type": "Dataset",
                "generatedBy": {"@id": "ark:7/comp-mid"},
            },
            "ark:7/comp-mid": {
                "@id": "ark:7/comp-mid",
                "@type": "Computation",
                "usedDataset": [
                    {"@id": "ark:7/ds-a"},
                    {"@id": "ark:7/ds-b"},
                ],
            },
            "ark:7/ds-a": {
                "@id": "ark:7/ds-a",
                "@type": "Dataset",
                "generatedBy": {"@id": "ark:7/comp-shared"},
            },
            "ark:7/ds-b": {
                "@id": "ark:7/ds-b",
                "@type": "Dataset",
                "generatedBy": {"@id": "ark:7/comp-shared"},
            },
            "ark:7/comp-shared": {
                "@id": "ark:7/comp-shared",
                "@type": "Computation",
                "usedDataset": [{"@id": "ark:7/ds-src"}],
            },
            "ark:7/ds-src": {"@id": "ark:7/ds-src", "@type": "Dataset"},
        }
        src = FakeGraphSource(nodes)
        EvidenceGraphBuilder(src, FakeResultSink()).build(
            "ark:7/out", owner_email="u@x",
        )
        all_fetched = [i for call in src.find_many_calls for i in call]
        # Every reachable id is fetched, each exactly once.
        for nid in nodes:
            assert all_fetched.count(nid) == 1, (nid, all_fetched)

    def test_missing_downstream_gets_error_stub(self):
        nodes = {
            "ark:7/ds": {
                "@id": "ark:7/ds",
                "@type": "Dataset",
                "generatedBy": {"@id": "ark:7/gone"},
            },
        }
        sink = FakeResultSink()
        EvidenceGraphBuilder(FakeGraphSource(nodes), sink).build(
            "ark:7/ds", owner_email="u@x",
        )
        graph = sink.persisted[0][0].graph
        assert graph["ark:7/gone"] == {
            "@id": "ark:7/gone",
            "error": "not found",
        }


# ---------------------------------------------------------------------------
# Not-found start node
# ---------------------------------------------------------------------------


class TestNotFoundStart:
    def test_persists_error_stub_graph_and_returns_derived_id(self):
        sink = FakeResultSink()
        out = EvidenceGraphBuilder(FakeGraphSource({}), sink).build(
            "ark:9/missing", owner_email="u@x",
        )
        assert out == "ark:9/evidence-graph-missing"
        assert len(sink.persisted) == 1
        eg = sink.persisted[0][0]
        assert eg.graph == {
            "ark:9/missing": {"@id": "ark:9/missing", "error": "not found"},
        }
        assert eg.outputs == [{"@id": "ark:9/missing"}]


# ---------------------------------------------------------------------------
# No idempotency short-circuit: the Phase 5 design keeps idempotency as a
# caller-side concern. A source node that already carries
# `hasEvidenceGraph` still triggers a fresh BFS + persist, so the caller
# sees the newly-derived id (the server CRUD uses its own pre-check to
# preserve 200 vs 201 HTTP semantics; the CLI intentionally rebuilds).
# ---------------------------------------------------------------------------


class TestRebuildsEvenWithExistingBackPointer:
    def test_builder_ignores_existing_has_evidence_graph(self):
        nodes = {
            "ark:7/ds": {
                "@id": "ark:7/ds",
                "@type": "Dataset",
                "name": "ds",
                "hasEvidenceGraph": {"@id": "ark:7/evidence-graph-ds-old"},
            },
        }
        src = FakeGraphSource(nodes)
        sink = FakeResultSink()
        out = EvidenceGraphBuilder(src, sink).build(
            "ark:7/ds", owner_email="u@x",
        )
        # Returns the freshly-derived id, *not* the pre-existing back-pointer.
        assert out == "ark:7/evidence-graph-ds"
        assert len(sink.persisted) == 1


# ---------------------------------------------------------------------------
# Condensation trigger (DatasetGroup)
# ---------------------------------------------------------------------------


class TestCondensation:
    def _sibling_dataset_crate(self, n_siblings: int = 7) -> dict[str, dict]:
        """Computation with `n_siblings` input datasets sharing format
        (so their provenance signatures match and condensation collapses
        them into a DatasetGroup)."""
        input_refs = [{"@id": f"ark:7/ds-{i:02d}"} for i in range(n_siblings)]
        nodes: dict[str, dict] = {
            "ark:7/out": {
                "@id": "ark:7/out",
                "@type": "Dataset",
                "name": "output",
                "generatedBy": {"@id": "ark:7/comp"},
            },
            "ark:7/comp": {
                "@id": "ark:7/comp",
                "@type": "Computation",
                "name": "comp",
                "usedDataset": input_refs,
                "usedSoftware": [{"@id": "ark:7/sw"}],
            },
            "ark:7/sw": {
                "@id": "ark:7/sw",
                "@type": "Software",
                "name": "sw",
            },
        }
        for i in range(n_siblings):
            nodes[f"ark:7/ds-{i:02d}"] = {
                "@id": f"ark:7/ds-{i:02d}",
                "@type": "Dataset",
                "name": f"input {i}",
                "format": "csv",
            }
        return nodes

    def test_dataset_group_replaces_siblings_at_threshold_trigger(self):
        sink = FakeResultSink()
        EvidenceGraphBuilder(
            FakeGraphSource(self._sibling_dataset_crate(n_siblings=7)),
            sink,
            condense_threshold=5,
        ).build("ark:7/out", owner_email="u@x")

        eg = sink.persisted[0][0]
        # Computation's usedDataset should now reference the synthesized
        # DatasetGroup instead of every sibling individually. Group nodes
        # get the `ark:group/...` id prefix and the EVI#DatasetGroup @type.
        used = eg.graph["ark:7/comp"]["usedDataset"]
        group_refs = [
            ref for ref in used
            if ref.get("@id", "").startswith("ark:group/")
        ]
        assert len(group_refs) == 1, used
        group_node = eg.graph[group_refs[0]["@id"]]
        assert any(
            "DatasetGroup" in t for t in (group_node.get("@type") or [])
        ), group_node
        assert group_node.get("evi:memberCount") == 7
        # Original sibling ids are no longer direct `usedDataset` refs.
        remaining_direct = {
            ref["@id"] for ref in used
            if not ref["@id"].startswith("ark:group/")
        }
        assert remaining_direct == set()

    def test_condensation_stats_populated(self):
        sink = FakeResultSink()
        EvidenceGraphBuilder(
            FakeGraphSource(self._sibling_dataset_crate(n_siblings=7)),
            sink,
            condense_threshold=5,
        ).build("ark:7/out", owner_email="u@x")
        stats = sink.persisted[0][0].condensation_stats
        assert stats is not None
        assert isinstance(stats, dict)

    def test_high_threshold_leaves_siblings_intact(self):
        sink = FakeResultSink()
        EvidenceGraphBuilder(
            FakeGraphSource(self._sibling_dataset_crate(n_siblings=7)),
            sink,
            condense_threshold=100,  # well above the 7 siblings
        ).build("ark:7/out", owner_email="u@x")

        eg = sink.persisted[0][0]
        used = eg.graph["ark:7/comp"]["usedDataset"]
        # Every original sibling still referenced; no DatasetGroup.
        used_ids = {ref["@id"] for ref in used}
        assert used_ids == {f"ark:7/ds-{i:02d}" for i in range(7)}
        assert all("DatasetGroup" not in i for i in used_ids)
