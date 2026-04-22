"""Unit tests for the pure projection helpers in
``fairscape_graph_tools.pipeline.evidence_graph``.

These helpers take a pre-populated ``node_cache`` (``@id`` -> flattened
node dict) and project it into the hierarchical ``(graph_dict, outputs)``
shape that the ``EvidenceGraph`` Pydantic model exposes. They do not
touch Mongo, RO-Crates on disk, or any condensation state, so the tests
work on plain-dict fixtures.

The orchestrator in ``fairscape_graph_tools.evidence_graph_builder`` is
tested separately in ``test_evidence_graph_builder.py``.
"""

from __future__ import annotations

import pytest

from fairscape_graph_tools.pipeline.evidence_graph import (
    _build_node_from_cache,
    _extract_referenced_ids,
    _flatten_metadata,
    _get_rocrate_outputs,
    _is_rocrate,
    _process_used_dataset,
    build_graph_dict,
)


# ---------------------------------------------------------------------------
# _flatten_metadata
# ---------------------------------------------------------------------------


class TestFlattenMetadata:
    def test_returns_node_unchanged_when_no_metadata_key(self):
        node = {"@id": "ark:1/a", "@type": "Dataset", "name": "A"}
        assert _flatten_metadata(node) is node

    def test_lifts_metadata_keys_into_top_level(self):
        node = {
            "@id": "ark:1/a",
            "metadata": {"@type": "Dataset", "name": "A"},
        }
        out = _flatten_metadata(node)
        assert out == {"@id": "ark:1/a", "@type": "Dataset", "name": "A"}

    def test_top_level_siblings_win_over_metadata_on_collision(self):
        node = {
            "@id": "ark:1/a",
            "name": "TopLevel",
            "metadata": {"name": "Inner", "@type": "Dataset"},
        }
        out = _flatten_metadata(node)
        # Top-level `name` wins; metadata-only keys still get lifted.
        assert out["name"] == "TopLevel"
        assert out["@type"] == "Dataset"

    def test_preserves_sibling_storage_fields(self):
        """This is what makes the helper different from the adapter's
        stricter local flatten: non-metadata top-level keys survive."""
        node = {
            "@id": "ark:1/a",
            "permissions": {"owner": "u@x"},
            "dateCreated": "2026-04-22",
            "metadata": {"@type": "Dataset", "name": "A"},
        }
        out = _flatten_metadata(node)
        assert out["permissions"] == {"owner": "u@x"}
        assert out["dateCreated"] == "2026-04-22"
        assert out["name"] == "A"

    def test_non_dict_metadata_is_ignored(self):
        node = {"@id": "ark:1/a", "@type": "Dataset", "metadata": "bogus"}
        out = _flatten_metadata(node)
        assert "metadata" not in out
        assert out["@type"] == "Dataset"


# ---------------------------------------------------------------------------
# _is_rocrate
# ---------------------------------------------------------------------------


class TestIsROCrate:
    def test_string_type_containing_rocrate(self):
        assert _is_rocrate("https://w3id.org/EVI#ROCrate") is True
        assert _is_rocrate("ROCrate") is True
        assert _is_rocrate("Dataset") is False

    def test_list_type_with_rocrate(self):
        assert _is_rocrate(["Dataset", "https://w3id.org/EVI#ROCrate"]) is True
        assert _is_rocrate(["Dataset", "Thing"]) is False

    def test_empty_or_none(self):
        assert _is_rocrate("") is False
        assert _is_rocrate([]) is False
        assert _is_rocrate(None) is False


# ---------------------------------------------------------------------------
# _get_rocrate_outputs
# ---------------------------------------------------------------------------


class TestGetROCrateOutputs:
    @pytest.mark.parametrize(
        "field",
        ["https://w3id.org/EVI#outputs", "EVI:outputs", "outputs"],
    )
    def test_picks_up_any_of_the_three_field_aliases(self, field):
        node = {field: [{"@id": "ark:1/a"}, {"@id": "ark:1/b"}]}
        assert _get_rocrate_outputs(node) == [
            {"@id": "ark:1/a"},
            {"@id": "ark:1/b"},
        ]

    def test_wraps_single_dict_in_list(self):
        node = {"outputs": {"@id": "ark:1/only"}}
        assert _get_rocrate_outputs(node) == [{"@id": "ark:1/only"}]

    def test_returns_empty_list_when_no_output_field(self):
        assert _get_rocrate_outputs({"@id": "ark:1/a"}) == []


# ---------------------------------------------------------------------------
# _extract_referenced_ids
# ---------------------------------------------------------------------------


class TestExtractReferencedIds:
    def test_dataset_follows_generated_by(self):
        node = {
            "@type": "Dataset",
            "generatedBy": {"@id": "ark:1/comp"},
        }
        assert _extract_referenced_ids(node) == {"ark:1/comp"}

    def test_dataset_generated_by_list_takes_first(self):
        node = {
            "@type": "Dataset",
            "generatedBy": [{"@id": "ark:1/comp-A"}, {"@id": "ark:1/comp-B"}],
        }
        # The helper only extracts the first entry when generatedBy is a list.
        assert _extract_referenced_ids(node) == {"ark:1/comp-A"}

    def test_computation_follows_used_edges(self):
        node = {
            "@type": "Computation",
            "usedDataset": [{"@id": "ark:1/in-a"}, {"@id": "ark:1/in-b"}],
            "usedSoftware": {"@id": "ark:1/sw"},
            "usedSample": [{"@id": "ark:1/samp"}],
            "usedInstrument": [{"@id": "ark:1/instr"}],
            "usedMLModel": [{"@id": "ark:1/mlm"}],
        }
        refs = _extract_referenced_ids(node)
        assert refs == {
            "ark:1/in-a",
            "ark:1/in-b",
            "ark:1/sw",
            "ark:1/samp",
            "ark:1/instr",
            "ark:1/mlm",
        }

    def test_software_follows_generated_by(self):
        # Software is in the "output-ish" bucket that follows generatedBy.
        node = {
            "@type": "Software",
            "generatedBy": {"@id": "ark:1/build-comp"},
        }
        assert _extract_referenced_ids(node) == {"ark:1/build-comp"}

    def test_list_type_uses_first_matching_evi_type(self):
        node = {
            "@type": ["Dataset", "Thing"],
            "generatedBy": {"@id": "ark:1/c"},
        }
        assert _extract_referenced_ids(node) == {"ark:1/c"}

    def test_activity_follows_used_edges(self):
        node = {
            "@type": "Activity",
            "usedDataset": [{"@id": "ark:1/ds"}],
        }
        assert _extract_referenced_ids(node) == {"ark:1/ds"}


# ---------------------------------------------------------------------------
# _process_used_dataset
# ---------------------------------------------------------------------------


class TestProcessUsedDataset:
    def test_plain_dataset_passes_through(self):
        cache = {"ark:1/ds": {"@id": "ark:1/ds", "@type": "Dataset"}}
        refs = _process_used_dataset({"@id": "ark:1/ds"}, cache)
        assert refs == [{"@id": "ark:1/ds"}]

    def test_rocrate_is_unwrapped_to_its_outputs(self):
        cache = {
            "ark:1/crate": {
                "@id": "ark:1/crate",
                "@type": ["Dataset", "https://w3id.org/EVI#ROCrate"],
                "outputs": [{"@id": "ark:1/out-a"}, {"@id": "ark:1/out-b"}],
            }
        }
        refs = _process_used_dataset({"@id": "ark:1/crate"}, cache)
        assert refs == [{"@id": "ark:1/out-a"}, {"@id": "ark:1/out-b"}]

    def test_rocrate_without_outputs_passes_through(self):
        cache = {
            "ark:1/crate": {
                "@id": "ark:1/crate",
                "@type": ["Dataset", "https://w3id.org/EVI#ROCrate"],
            }
        }
        refs = _process_used_dataset({"@id": "ark:1/crate"}, cache)
        assert refs == [{"@id": "ark:1/crate"}]

    def test_missing_from_cache_still_yields_ref(self):
        refs = _process_used_dataset({"@id": "ark:1/unknown"}, node_cache={})
        assert refs == [{"@id": "ark:1/unknown"}]

    def test_error_node_in_cache_still_yields_ref(self):
        node_cache = {"ark:1/x": {"@id": "ark:1/x", "error": "not found"}}
        refs = _process_used_dataset({"@id": "ark:1/x"}, node_cache)
        assert refs == [{"@id": "ark:1/x"}]

    def test_list_input(self):
        cache = {
            "ark:1/a": {"@id": "ark:1/a", "@type": "Dataset"},
            "ark:1/b": {"@id": "ark:1/b", "@type": "Dataset"},
        }
        refs = _process_used_dataset(
            [{"@id": "ark:1/a"}, {"@id": "ark:1/b"}], cache
        )
        assert refs == [{"@id": "ark:1/a"}, {"@id": "ark:1/b"}]


# ---------------------------------------------------------------------------
# _build_node_from_cache
# ---------------------------------------------------------------------------


class TestBuildNodeFromCache:
    def test_missing_node_produces_error_stub(self):
        graph: dict = {}
        _build_node_from_cache("ark:1/missing", node_cache={}, graph_dict=graph)
        assert graph["ark:1/missing"] == {
            "@id": "ark:1/missing",
            "error": "not found",
        }

    def test_error_node_passes_through(self):
        graph: dict = {}
        cache = {"ark:1/x": {"@id": "ark:1/x", "error": "bad"}}
        _build_node_from_cache("ark:1/x", cache, graph)
        assert graph["ark:1/x"] == {"@id": "ark:1/x", "error": "bad"}

    def test_cycle_short_circuit(self):
        """A node already in graph_dict is not re-entered."""
        cache = {
            "ark:1/a": {"@id": "ark:1/a", "@type": "Dataset", "name": "A"},
        }
        graph = {"ark:1/a": {"sentinel": True}}
        _build_node_from_cache("ark:1/a", cache, graph)
        # unchanged
        assert graph["ark:1/a"] == {"sentinel": True}

    def test_dataset_recurses_into_generated_by(self):
        cache = {
            "ark:1/ds": {
                "@id": "ark:1/ds",
                "@type": "Dataset",
                "name": "ds",
                "generatedBy": {"@id": "ark:1/c"},
            },
            "ark:1/c": {
                "@id": "ark:1/c",
                "@type": "Computation",
                "name": "c",
            },
        }
        graph: dict = {}
        _build_node_from_cache("ark:1/ds", cache, graph)
        assert graph["ark:1/ds"]["generatedBy"] == {"@id": "ark:1/c"}
        assert graph["ark:1/c"]["@id"] == "ark:1/c"

    def test_computation_recurses_into_used_edges(self):
        cache = {
            "ark:1/comp": {
                "@id": "ark:1/comp",
                "@type": "Computation",
                "name": "comp",
                "usedDataset": [{"@id": "ark:1/in"}],
                "usedSoftware": [{"@id": "ark:1/sw"}],
                "usedMLModel": {"@id": "ark:1/mlm"},
            },
            "ark:1/in": {"@id": "ark:1/in", "@type": "Dataset", "name": "in"},
            "ark:1/sw": {"@id": "ark:1/sw", "@type": "Software", "name": "sw"},
            "ark:1/mlm": {
                "@id": "ark:1/mlm",
                "@type": "MLModel",
                "name": "mlm",
            },
        }
        graph: dict = {}
        _build_node_from_cache("ark:1/comp", cache, graph)
        assert graph["ark:1/comp"]["usedDataset"] == [{"@id": "ark:1/in"}]
        assert graph["ark:1/comp"]["usedSoftware"] == [{"@id": "ark:1/sw"}]
        assert graph["ark:1/comp"]["usedMLModel"] == [{"@id": "ark:1/mlm"}]
        for downstream in ("ark:1/in", "ark:1/sw", "ark:1/mlm"):
            assert downstream in graph

    def test_dataset_group_preserves_summary_and_recurses_representative(self):
        cache = {
            "ark:1/group": {
                "@id": "ark:1/group",
                "@type": ["Dataset", "DatasetGroup"],
                "name": "group",
                "evi:memberCount": 7,
                "evi:representativeDataset": {"@id": "ark:1/rep"},
                "evi:commonFormat": "csv",
                "evi:commonSoftware": [{"@id": "ark:1/sw"}],
            },
            "ark:1/rep": {
                "@id": "ark:1/rep",
                "@type": "Dataset",
                "name": "representative",
            },
        }
        graph: dict = {}
        _build_node_from_cache("ark:1/group", cache, graph)
        assert graph["ark:1/group"]["evi:memberCount"] == 7
        assert graph["ark:1/group"]["evi:representativeDataset"] == {
            "@id": "ark:1/rep",
        }
        assert "ark:1/rep" in graph  # representative is recursed into

    def test_rocrate_start_node_gets_has_outputs_annotation(self):
        cache = {
            "ark:1/crate": {
                "@id": "ark:1/crate",
                "@type": ["Dataset", "https://w3id.org/EVI#ROCrate"],
                "name": "crate",
            },
        }
        graph: dict = {}
        outputs = [{"@id": "ark:1/a"}, {"@id": "ark:1/b"}]
        _build_node_from_cache(
            "ark:1/crate",
            cache,
            graph,
            start_rocrate_id="ark:1/crate",
            rocrate_outputs=outputs,
        )
        assert graph["ark:1/crate"]["hasOutputs"] == outputs


# ---------------------------------------------------------------------------
# build_graph_dict (top-level entry point)
# ---------------------------------------------------------------------------


class TestBuildGraphDict:
    def test_missing_start_node(self):
        graph, outputs = build_graph_dict("ark:1/missing", node_cache={})
        assert outputs == [{"@id": "ark:1/missing"}]
        assert graph == {
            "ark:1/missing": {"@id": "ark:1/missing", "error": "not found"},
        }

    def test_non_rocrate_start(self):
        cache = {
            "ark:1/ds": {
                "@id": "ark:1/ds",
                "@type": "Dataset",
                "name": "ds",
                "generatedBy": {"@id": "ark:1/c"},
            },
            "ark:1/c": {
                "@id": "ark:1/c",
                "@type": "Computation",
                "name": "c",
                "usedDataset": [{"@id": "ark:1/in"}],
            },
            "ark:1/in": {
                "@id": "ark:1/in",
                "@type": "Dataset",
                "name": "in",
            },
        }
        graph, outputs = build_graph_dict("ark:1/ds", cache)
        assert outputs == [{"@id": "ark:1/ds"}]
        assert set(graph) == {"ark:1/ds", "ark:1/c", "ark:1/in"}
        assert graph["ark:1/ds"]["generatedBy"] == {"@id": "ark:1/c"}
        assert graph["ark:1/c"]["usedDataset"] == [{"@id": "ark:1/in"}]

    def test_rocrate_start_fans_out_to_outputs(self):
        cache = {
            "ark:1/crate": {
                "@id": "ark:1/crate",
                "@type": ["Dataset", "https://w3id.org/EVI#ROCrate"],
                "name": "crate",
                "outputs": [{"@id": "ark:1/out"}],
            },
            "ark:1/out": {
                "@id": "ark:1/out",
                "@type": "Dataset",
                "name": "out",
            },
        }
        graph, outputs = build_graph_dict("ark:1/crate", cache)
        assert {ref["@id"] for ref in outputs} == {
            "ark:1/crate",
            "ark:1/out",
        }
        assert "ark:1/crate" in graph
        assert "ark:1/out" in graph
        # Start-rocrate gets the explicit hasOutputs annotation.
        assert graph["ark:1/crate"]["hasOutputs"] == [{"@id": "ark:1/out"}]

    def test_rocrate_start_without_outputs_still_self_entries(self):
        cache = {
            "ark:1/crate": {
                "@id": "ark:1/crate",
                "@type": ["Dataset", "https://w3id.org/EVI#ROCrate"],
                "name": "crate",
            },
        }
        graph, outputs = build_graph_dict("ark:1/crate", cache)
        # Even with no outputs, the crate itself lands in outputs+graph.
        assert {ref["@id"] for ref in outputs} == {"ark:1/crate"}
        assert "ark:1/crate" in graph

    def test_missing_downstream_gets_error_stub(self):
        cache = {
            "ark:1/ds": {
                "@id": "ark:1/ds",
                "@type": "Dataset",
                "generatedBy": {"@id": "ark:1/gone"},
            },
        }
        graph, _ = build_graph_dict("ark:1/ds", cache)
        assert graph["ark:1/gone"] == {
            "@id": "ark:1/gone",
            "error": "not found",
        }
