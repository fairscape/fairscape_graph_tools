"""Pure projection functions for the standard evidence graph.

These are the helpers lifted out of the old `EvidenceGraph.build_graph`
method. They do not touch Mongo, ROCrates on disk, or any condensation
state — they take a pre-built `node_cache` (`@id` → flattened node dict)
and project it into the hierarchical (`graph_dict`, `outputs`) shape that
the Pydantic `EvidenceGraph` model exposes.

The orchestrator in `fairscape_graph_tools.evidence_graph_builder` is
responsible for populating `node_cache` via the `GraphSource` port
(BFS + optional condensation) before calling `build_graph_dict`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


def _flatten_metadata(node: Dict) -> Dict:
    if "metadata" not in node:
        return node

    flattened = {k: v for k, v in node.items() if k != "metadata"}
    metadata_content = node["metadata"]
    if isinstance(metadata_content, dict):
        for key, value in metadata_content.items():
            if key not in flattened:
                flattened[key] = value
    return flattened


def _is_rocrate(node_type_field: Any) -> bool:
    if isinstance(node_type_field, list):
        return any("ROCrate" in str(t) for t in node_type_field)
    elif isinstance(node_type_field, str):
        return "ROCrate" in node_type_field
    return False


def _get_rocrate_outputs(node: Dict) -> List[Dict]:
    output_fields = ["https://w3id.org/EVI#outputs", "EVI:outputs", "outputs"]

    for field in output_fields:
        if field in node:
            outputs = node[field]
            if isinstance(outputs, list):
                return outputs
            elif isinstance(outputs, dict):
                return [outputs]
    return []


def _extract_referenced_ids(node: Dict) -> Set[str]:
    referenced_ids = set()

    node_type_field = node.get("@type", "")
    current_node_type_str = ""
    if isinstance(node_type_field, list):
        if "Dataset" in node_type_field: current_node_type_str = "Dataset"
        elif "Computation" in node_type_field: current_node_type_str = "Computation"
        elif "Sample" in node_type_field: current_node_type_str = "Sample"
        elif "Software" in node_type_field: current_node_type_str = "Software"
        elif "MLModel" in node_type_field: current_node_type_str = "MLModel"
        elif "Experiment" in node_type_field: current_node_type_str = "Experiment"
        elif "Activity" in node_type_field: current_node_type_str = "Activity"
        elif node_type_field: current_node_type_str = node_type_field[-1]
    elif isinstance(node_type_field, str):
        current_node_type_str = node_type_field

    if "Dataset" in current_node_type_str or \
       "Sample" in current_node_type_str or \
       "Instrument" in current_node_type_str or \
       "Software" in current_node_type_str or \
       "MLModel" in current_node_type_str:
        generated_by_info = node.get("generatedBy")
        if generated_by_info:
            if isinstance(generated_by_info, list) and generated_by_info:
                comp_id = generated_by_info[0].get("@id")
                if comp_id:
                    referenced_ids.add(comp_id)
            elif isinstance(generated_by_info, dict):
                comp_id = generated_by_info.get("@id")
                if comp_id:
                    referenced_ids.add(comp_id)

    elif "Computation" in current_node_type_str or \
         "Experiment" in current_node_type_str or \
         "Annotation" in current_node_type_str or \
         "Activity" in current_node_type_str:
        for field_name in ["usedDataset", "usedSoftware", "usedSample", "usedInstrument", "usedMLModel"]:
            field_info = node.get(field_name)
            if field_info:
                items = field_info if isinstance(field_info, list) else [field_info]
                for item in items:
                    if isinstance(item, dict) and item.get("@id"):
                        referenced_ids.add(item.get("@id"))

    return referenced_ids


def _process_used_dataset(dataset_info: Any, node_cache: Dict[str, Dict]) -> List[Dict[str, str]]:
    datasets_to_process = []

    if isinstance(dataset_info, list):
        datasets_to_process = dataset_info
    elif isinstance(dataset_info, dict):
        datasets_to_process = [dataset_info]
    else:
        return []

    result_refs = []

    for dataset_ref in datasets_to_process:
        if not dataset_ref.get("@id"):
            continue

        dataset_id = dataset_ref.get("@id")
        dataset_node = node_cache.get(dataset_id)

        if dataset_node and "error" not in dataset_node:
            node_type = dataset_node.get("@type", "")

            if _is_rocrate(node_type):
                outputs = _get_rocrate_outputs(dataset_node)
                if outputs:
                    for output_ref in outputs:
                        if output_ref.get("@id"):
                            result_refs.append({"@id": output_ref.get("@id")})
                else:
                    result_refs.append({"@id": dataset_id})
            else:
                result_refs.append({"@id": dataset_id})
        else:
            result_refs.append({"@id": dataset_id})

    return result_refs


def _build_node_from_cache(
    node_id: str,
    node_cache: Dict[str, Dict],
    graph_dict: Dict[str, Dict],
    start_rocrate_id: Optional[str] = None,
    rocrate_outputs: Optional[List[Dict]] = None,
) -> None:
    if node_id in graph_dict:
        return

    node = node_cache.get(node_id)
    if not node:
        graph_dict[node_id] = {"@id": node_id, "error": "not found"}
        return

    if "error" in node:
        graph_dict[node_id] = node
        return

    result_node = {
        "@id": node.get("@id"),
        "@type": node.get("@type"),
        "name": node.get("name"),
        "description": node.get("description"),
    }

    created_by = node.get("createdBy")
    if created_by:
        result_node["createdBy"] = created_by

    node_type_field = node.get("@type", "")

    if start_rocrate_id and node_id == start_rocrate_id and _is_rocrate(node_type_field):
        if rocrate_outputs:
            result_node["hasOutputs"] = rocrate_outputs

    current_node_type_str = ""
    if isinstance(node_type_field, list):
        if "Dataset" in node_type_field: current_node_type_str = "Dataset"
        elif "Computation" in node_type_field: current_node_type_str = "Computation"
        elif "Sample" in node_type_field: current_node_type_str = "Sample"
        elif "Software" in node_type_field: current_node_type_str = "Software"
        elif "MLModel" in node_type_field: current_node_type_str = "MLModel"
        elif "Experiment" in node_type_field: current_node_type_str = "Experiment"
        elif "Activity" in node_type_field: current_node_type_str = "Activity"
        elif node_type_field: current_node_type_str = node_type_field[-1]
    elif isinstance(node_type_field, str):
        current_node_type_str = node_type_field

    if "Dataset" in current_node_type_str or \
       "Sample" in current_node_type_str or \
       "Instrument" in current_node_type_str or \
       "Software" in current_node_type_str or \
       "MLModel" in current_node_type_str:
        generated_by_info = node.get("generatedBy")
        if generated_by_info:
            if isinstance(generated_by_info, list) and generated_by_info:
                comp_id = generated_by_info[0].get("@id")
            elif isinstance(generated_by_info, dict):
                comp_id = generated_by_info.get("@id")
            else:
                comp_id = None

            if comp_id:
                _build_node_from_cache(comp_id, node_cache, graph_dict, start_rocrate_id, rocrate_outputs)
                result_node["generatedBy"] = {"@id": comp_id}
            elif generated_by_info:
                result_node["generatedBy"] = generated_by_info

    elif "Computation" in current_node_type_str or \
         "Experiment" in current_node_type_str or \
         "Annotation" in current_node_type_str or \
         "Activity" in current_node_type_str:
        used_dataset_info = node.get("usedDataset")
        if used_dataset_info:
            dataset_refs = _process_used_dataset(used_dataset_info, node_cache)
            if dataset_refs:
                result_node["usedDataset"] = dataset_refs
                for ref in dataset_refs:
                    if ref.get("@id"):
                        _build_node_from_cache(ref.get("@id"), node_cache, graph_dict, start_rocrate_id, rocrate_outputs)

        used_software_info = node.get("usedSoftware")
        if used_software_info:
            software_refs = []
            if isinstance(used_software_info, list):
                for item in used_software_info:
                    if item.get("@id"):
                        _build_node_from_cache(item.get("@id"), node_cache, graph_dict, start_rocrate_id, rocrate_outputs)
                        software_refs.append({"@id": item.get("@id")})
            elif isinstance(used_software_info, dict) and used_software_info.get("@id"):
                _build_node_from_cache(used_software_info.get("@id"), node_cache, graph_dict, start_rocrate_id, rocrate_outputs)
                software_refs.append({"@id": used_software_info.get("@id")})
            if software_refs:
                result_node["usedSoftware"] = software_refs

        used_sample_info = node.get("usedSample")
        if used_sample_info:
            sample_refs = []
            if isinstance(used_sample_info, list):
                for item in used_sample_info:
                    if item.get("@id"):
                        _build_node_from_cache(item.get("@id"), node_cache, graph_dict, start_rocrate_id, rocrate_outputs)
                        sample_refs.append({"@id": item.get("@id")})
            elif isinstance(used_sample_info, dict) and used_sample_info.get("@id"):
                _build_node_from_cache(used_sample_info.get("@id"), node_cache, graph_dict, start_rocrate_id, rocrate_outputs)
                sample_refs.append({"@id": used_sample_info.get("@id")})
            if sample_refs:
                result_node["usedSample"] = sample_refs

        used_instrument_info = node.get("usedInstrument")
        if used_instrument_info:
            instrument_refs = []
            if isinstance(used_instrument_info, list):
                for item in used_instrument_info:
                    if item.get("@id"):
                        _build_node_from_cache(item.get("@id"), node_cache, graph_dict, start_rocrate_id, rocrate_outputs)
                        instrument_refs.append({"@id": item.get("@id")})
            elif isinstance(used_instrument_info, dict) and used_instrument_info.get("@id"):
                _build_node_from_cache(used_instrument_info.get("@id"), node_cache, graph_dict, start_rocrate_id, rocrate_outputs)
                instrument_refs.append({"@id": used_instrument_info.get("@id")})
            if instrument_refs:
                result_node["usedInstrument"] = instrument_refs

        used_mlmodel_info = node.get("usedMLModel")
        if used_mlmodel_info:
            mlmodel_refs = []
            if isinstance(used_mlmodel_info, list):
                for item in used_mlmodel_info:
                    if item.get("@id"):
                        _build_node_from_cache(item.get("@id"), node_cache, graph_dict, start_rocrate_id, rocrate_outputs)
                        mlmodel_refs.append({"@id": item.get("@id")})
            elif isinstance(used_mlmodel_info, dict) and used_mlmodel_info.get("@id"):
                _build_node_from_cache(used_mlmodel_info.get("@id"), node_cache, graph_dict, start_rocrate_id, rocrate_outputs)
                mlmodel_refs.append({"@id": used_mlmodel_info.get("@id")})
            if mlmodel_refs:
                result_node["usedMLModel"] = mlmodel_refs

    # Preserve DatasetGroup summary fields and recurse into the
    # representative dataset so it actually appears in the graph.
    # The other members are dropped by condensation by design.
    if isinstance(node_type_field, list) and any("DatasetGroup" in str(t) for t in node_type_field):
        for field in ("evi:memberCount", "evi:representativeDataset",
                      "evi:commonFormat", "evi:commonSoftware", "format",
                      "evi:memberIds"):
            if field in node:
                result_node[field] = node[field]

        rep_ref = node.get("evi:representativeDataset")
        rep_id = None
        if isinstance(rep_ref, dict):
            rep_id = rep_ref.get("@id")
        elif isinstance(rep_ref, str):
            rep_id = rep_ref
        if rep_id:
            _build_node_from_cache(
                rep_id, node_cache, graph_dict,
                start_rocrate_id, rocrate_outputs,
            )

    graph_dict[node_id] = result_node


def build_graph_dict(
    start_node_id: str, node_cache: Dict[str, Dict]
) -> Tuple[Dict[str, Dict], List[Dict[str, str]]]:
    """Project a populated `node_cache` into `(graph_dict, outputs)`.

    `node_cache` must key the start node under `start_node_id` plus any
    reachable referenced nodes. Missing nodes produce `{"error": "not found"}`
    placeholders in `graph_dict` — matching the server's historical shape.
    """
    graph_dict: Dict[str, Dict] = {}
    output_nodes: List[Dict[str, str]] = []

    start_node = node_cache.get(start_node_id)
    if not start_node:
        output_nodes.append({"@id": start_node_id})
        graph_dict[start_node_id] = {"@id": start_node_id, "error": "not found"}
        return graph_dict, output_nodes

    node_type = start_node.get("@type", "")
    start_rocrate_id: Optional[str] = None
    start_rocrate_outputs: Optional[List[Dict]] = None

    if _is_rocrate(node_type):
        rocrate_outputs = _get_rocrate_outputs(start_node)
        start_rocrate_outputs = list(rocrate_outputs) if rocrate_outputs else []
        traversal_outputs = list(start_rocrate_outputs)
        traversal_outputs.append({"@id": start_node_id})
        start_rocrate_id = start_node_id
        if traversal_outputs:
            for output_ref in traversal_outputs:
                if output_ref.get("@id"):
                    output_nodes.append({"@id": output_ref.get("@id")})
        else:
            output_nodes.append({"@id": start_node_id})
    else:
        output_nodes.append({"@id": start_node_id})

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
