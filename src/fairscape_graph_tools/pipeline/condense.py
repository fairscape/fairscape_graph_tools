"""RO-Crate provenance-graph condensation.

Collapses repetitive provenance chains (sibling datasets with identical
software/input structure) into summary DatasetGroup nodes. Works on any
flat `@graph` list[dict]; the adapter is responsible for assembling the
full cross-crate graph before calling `condense_graph`.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Dict, List

from fairscape_graph_tools.pipeline.graph_utils import (
    get_evi_type,
    get_generatedby_ids,
    get_id_list,
    is_computation,
    is_dataset,
    is_rocrate_root,
    is_software,
    make_id_ref,
)

# Fields that contain ARK references to other entities. Used by callers that
# assemble the full graph across multiple sources.
ARK_REF_FIELDS = (
    "hasPart", "EVI:outputs", "outputs",
    "generatedBy", "prov:wasGeneratedBy",
    "usedDataset", "usedSoftware", "usedMLModel",
    "derivedFrom", "prov:wasDerivedFrom",
    "usedSample", "usedInstrument", "usedTreatment", "usedStain",
    "generated", "prov:used",
)


# ---------------------------------------------------------------------------
# Provenance Signature
# ---------------------------------------------------------------------------

def compute_provenance_signature(
    dataset_id: str,
    index: Dict[str, dict],
    cache: Dict[str, tuple],
) -> tuple:
    """Compute a hashable signature for a dataset's provenance structure.

    Two datasets with the same signature went through identical software
    pipelines, regardless of which specific data/computation instances were
    involved.
    """
    if dataset_id in cache:
        return cache[dataset_id]

    dataset = index.get(dataset_id)
    if dataset is None:
        sig = ("unknown", (), None)
        cache[dataset_id] = sig
        return sig

    fmt = dataset.get("format", "unknown")
    schema_ids = tuple(sorted(get_id_list(dataset, "evi:Schema")))

    gen_comp_ids = get_generatedby_ids(dataset)

    if not gen_comp_ids:
        sig = (fmt, schema_ids, None)
    else:
        comp_sigs = []
        for comp_id in sorted(gen_comp_ids):
            comp = index.get(comp_id)
            if comp is None:
                comp_sigs.append(((), ()))
                continue

            sw_ids = tuple(sorted(get_id_list(comp, "usedSoftware")))

            input_dataset_ids = get_id_list(comp, "usedDataset")
            input_sigs = tuple(sorted(
                compute_provenance_signature(ds_id, index, cache)
                for ds_id in input_dataset_ids
            ))

            comp_sigs.append((sw_ids, input_sigs))

        sig = (fmt, schema_ids, tuple(sorted(comp_sigs)))

    cache[dataset_id] = sig
    return sig


# ---------------------------------------------------------------------------
# Output-first backward traversal with inline condensation
# ---------------------------------------------------------------------------

def traverse_and_condense(
    index: Dict[str, dict],
    threshold: int,
    max_member_ids: int = 0,
):
    """Traverse backward from the primary crate's outputs, keeping everything
    reachable and collapsing large groups of sibling datasets that share the
    same provenance signature.

    Returns:
        keep_ids, remove_ids, group_nodes, comp_updates
    """
    # Find root crate node
    root_id = None
    for nid, node in index.items():
        if is_rocrate_root(node):
            root_id = nid
            break

    if root_id is None:
        return set(index.keys()), set(), [], {}

    root = index[root_id]

    output_ids = get_id_list(root, "EVI:outputs")
    part_ids = get_id_list(root, "hasPart", "EVI:outputs")

    keep_ids: set = {root_id, "ro-crate-metadata.json"}
    collapsed_ids: set = set()
    group_nodes: List[dict] = []
    comp_updates: Dict[str, list] = defaultdict(list)
    sig_cache: Dict[str, tuple] = {}

    # Pre-pass: condense repetitive top-level outputs
    output_dataset_ids = [
        oid for oid in output_ids
        if oid in index and is_dataset(index[oid])
    ]
    if len(output_dataset_ids) > threshold:
        sig_to_ids: Dict[tuple, list] = defaultdict(list)
        for ds_id in output_dataset_ids:
            sig = compute_provenance_signature(ds_id, index, sig_cache)
            sig_to_ids[sig].append(ds_id)

        for sig, member_ids in sig_to_ids.items():
            if len(member_ids) > threshold:
                representative_id = sorted(member_ids)[0]
                non_rep_ids = [mid for mid in member_ids
                               if mid != representative_id]
                collapsed_ids.update(non_rep_ids)

                for mid in non_rep_ids:
                    _collect_exclusive_backward(
                        mid, representative_id, index, collapsed_ids,
                    )

                group = {
                    "consuming_comp_id": root_id,
                    "signature": sig,
                    "member_ids": member_ids,
                    "representative_id": representative_id,
                }
                group_node = create_dataset_group_node(group, index, max_member_ids)
                group_nodes.append(group_node)
                comp_updates[root_id].append(
                    (member_ids, group_node["@id"])
                )

    # Backward traversal
    visited: set = set()
    stack = list(part_ids)

    while stack:
        current_id = stack.pop()
        if current_id in visited or current_id in collapsed_ids:
            continue
        visited.add(current_id)
        keep_ids.add(current_id)

        node = index.get(current_id)
        if node is None:
            continue

        evi_type = get_evi_type(node)

        if is_dataset(node):
            for comp_id in get_generatedby_ids(node):
                stack.append(comp_id)
            for schema_id in get_id_list(node, "evi:Schema"):
                stack.append(schema_id)

        elif evi_type == "Computation":
            for sw_id in get_id_list(node, "usedSoftware"):
                keep_ids.add(sw_id)
                stack.append(sw_id)

            for ml_id in get_id_list(node, "usedMLModel"):
                keep_ids.add(ml_id)
                stack.append(ml_id)

            input_ids = get_id_list(node, "usedDataset")

            if len(input_ids) > threshold:
                sig_to_ids = defaultdict(list)
                for ds_id in input_ids:
                    sig = compute_provenance_signature(ds_id, index, sig_cache)
                    sig_to_ids[sig].append(ds_id)

                for sig, member_ids in sig_to_ids.items():
                    if len(member_ids) > threshold:
                        representative_id = sorted(member_ids)[0]
                        non_rep_ids = [mid for mid in member_ids
                                       if mid != representative_id]
                        collapsed_ids.update(non_rep_ids)

                        for mid in non_rep_ids:
                            _collect_exclusive_backward(
                                mid, representative_id, index, collapsed_ids,
                            )

                        group = {
                            "consuming_comp_id": current_id,
                            "signature": sig,
                            "member_ids": member_ids,
                            "representative_id": representative_id,
                        }
                        group_node = create_dataset_group_node(group, index, max_member_ids)
                        group_nodes.append(group_node)
                        comp_updates[current_id].append(
                            (member_ids, group_node["@id"])
                        )

                        stack.append(representative_id)
                    else:
                        for ds_id in member_ids:
                            stack.append(ds_id)
            else:
                for ds_id in input_ids:
                    stack.append(ds_id)

        elif evi_type == "Experiment":
            for ref_id in get_id_list(node, "usedSample", "usedInstrument",
                                      "usedTreatment", "usedStain"):
                stack.append(ref_id)

        elif is_software(node):
            pass

    # Keep all software nodes
    for nid, n in index.items():
        if is_software(n):
            keep_ids.add(nid)

    keep_ids -= collapsed_ids

    return keep_ids, collapsed_ids, group_nodes, comp_updates


# ---------------------------------------------------------------------------
# Evidence-graph condensation (operates on node_cache in-place)
# ---------------------------------------------------------------------------

def condense_evidence_graph_cache(
    node_cache: Dict[str, dict],
    threshold: int = 5,
    max_member_ids: int = 0,
) -> dict:
    """Condense an evidence graph's node_cache in-place by collapsing sibling
    datasets at each Computation that share identical provenance signatures.

    Mutates node_cache: adds DatasetGroup nodes, removes collapsed nodes,
    updates Computation usedDataset references.

    Returns a stats dict.
    """
    original_count = len(node_cache)
    sig_cache: Dict[str, tuple] = {}
    all_group_nodes: List[dict] = []
    total_collapsed: set = set()

    # Snapshot computation IDs (we'll mutate node_cache during iteration)
    comp_ids = [
        nid for nid, node in node_cache.items()
        if is_computation(node)
    ]

    for comp_id in comp_ids:
        node = node_cache.get(comp_id)
        if node is None:
            continue

        input_ids = get_id_list(node, "usedDataset")
        # Only consider inputs that are actual datasets present in the cache
        input_dataset_ids = [
            ds_id for ds_id in input_ids
            if ds_id in node_cache and is_dataset(node_cache[ds_id])
        ]

        if len(input_dataset_ids) <= threshold:
            continue

        sig_to_ids: Dict[tuple, list] = defaultdict(list)
        for ds_id in input_dataset_ids:
            sig = compute_provenance_signature(ds_id, node_cache, sig_cache)
            sig_to_ids[sig].append(ds_id)

        for sig, member_ids in sig_to_ids.items():
            if len(member_ids) <= threshold:
                continue

            representative_id = sorted(member_ids)[0]
            non_rep_ids = [mid for mid in member_ids if mid != representative_id]

            # Collect exclusive backward chains for non-representatives
            collapsed_here: set = set()
            for mid in non_rep_ids:
                _collect_exclusive_backward(mid, representative_id, node_cache, collapsed_here)

            # Build group node
            group = {
                "consuming_comp_id": comp_id,
                "signature": sig,
                "member_ids": member_ids,
                "representative_id": representative_id,
            }
            group_node = create_dataset_group_node(group, node_cache, max_member_ids)
            all_group_nodes.append(group_node)

            # Update computation's usedDataset: replace members with group ref
            member_set = set(member_ids)
            current_used = node.get("usedDataset", [])
            if isinstance(current_used, dict):
                current_used = [current_used]
            kept = [ref for ref in current_used
                    if not (isinstance(ref, dict) and ref.get("@id") in member_set)]
            kept.append(make_id_ref(group_node["@id"]))
            node["usedDataset"] = kept

            # Track all collapsed IDs
            total_collapsed.update(collapsed_here)

    # Remove collapsed nodes from cache
    for cid in total_collapsed:
        node_cache.pop(cid, None)

    # Add group nodes to cache
    for gn in all_group_nodes:
        node_cache[gn["@id"]] = gn

    condensed_count = len(node_cache)

    if not all_group_nodes:
        return {
            "condensed": False,
            "originalEntityCount": original_count,
            "condensedEntityCount": original_count,
            "datasetGroupCount": 0,
        }

    return {
        "condensed": True,
        "originalEntityCount": original_count,
        "condensedEntityCount": condensed_count,
        "datasetGroupCount": len(all_group_nodes),
        "entitiesRemoved": len(total_collapsed),
        "groups": [
            {
                "memberCount": gn["evi:memberCount"],
                "format": gn.get("format", "unknown"),
                "groupId": gn["@id"],
            }
            for gn in all_group_nodes
        ],
    }


def _collect_exclusive_backward(
    dataset_id: str,
    representative_id: str,
    index: Dict[str, dict],
    collapsed: set,
) -> None:
    """Collect backward chain entities of a non-representative dataset that are
    NOT shared with the representative's chain. Add them to collapsed set.
    """
    rep_chain = _collect_backward_chain(representative_id, index)
    stack = [dataset_id]
    visited = set()

    while stack:
        cid = stack.pop()
        if cid in visited:
            continue
        visited.add(cid)

        if cid in rep_chain:
            continue

        node = index.get(cid)
        if node is None:
            continue

        if is_software(node):
            continue

        collapsed.add(cid)

        if is_dataset(node):
            for comp_id in get_generatedby_ids(node):
                stack.append(comp_id)

        if is_computation(node):
            for ref_id in get_id_list(node, "usedDataset"):
                stack.append(ref_id)


def _collect_backward_chain(dataset_id: str, index: Dict[str, dict]) -> set:
    """Collect all entity @ids in the backward provenance chain."""
    visited = set()
    stack = [dataset_id]
    while stack:
        cid = stack.pop()
        if cid in visited:
            continue
        visited.add(cid)
        node = index.get(cid)
        if node is None:
            continue
        if is_dataset(node):
            for comp_id in get_generatedby_ids(node):
                stack.append(comp_id)
        if is_computation(node):
            for ref_id in get_id_list(node, "usedDataset", "usedSoftware",
                                      "usedMLModel"):
                stack.append(ref_id)
    return visited


# ---------------------------------------------------------------------------
# Build condensed graph
# ---------------------------------------------------------------------------

def create_dataset_group_node(
    group: dict,
    index: Dict[str, dict],
    max_member_ids: int = 0,
) -> dict:
    """Create a DatasetGroup summary node for a group of similar datasets."""
    representative = index[group["representative_id"]]
    member_ids = group["member_ids"]
    count = len(member_ids)
    sig = group["signature"]

    common_sw_ids = []
    if sig[2]:
        for comp_sig in sig[2]:
            sw_ids, _ = comp_sig
            common_sw_ids.extend(sw_ids)
    common_sw_ids = sorted(set(common_sw_ids))

    fmt = sig[0]

    consuming_node = index.get(group["consuming_comp_id"], {})
    if is_rocrate_root(consuming_node):
        crate_name = consuming_node.get("name", "unknown").lower().replace(" ", "-")
        group_id = f"ark:group/{crate_name}-{fmt.replace('/', '_').lstrip('.')}-outputs"
    else:
        comp_name = consuming_node.get("name", "unknown").lower().replace(" ", "-")
        group_id = f"ark:group/{comp_name}-{fmt.replace('/', '_').lstrip('.')}-inputs"

    sw_names = []
    for sw_id in common_sw_ids:
        sw = index.get(sw_id, {})
        sw_names.append(sw.get("name", sw_id))

    description = f"{count} {fmt} files with identical provenance structure."
    if sw_names:
        description += f" All processed by {', '.join(sw_names)}."

    schema_ids = list(sig[1]) if sig[1] else []

    node = {
        "@id": group_id,
        "@type": ["prov:Entity", "https://w3id.org/EVI#DatasetGroup"],
        "name": f"{representative.get('name', fmt + ' files')} (and {count - 1} similar)",
        "description": description,
        "format": fmt,
        "evi:memberCount": count,
        "evi:representativeDataset": make_id_ref(group["representative_id"]),
        "evi:commonFormat": fmt,
        "evi:commonSoftware": [make_id_ref(sw_id) for sw_id in common_sw_ids],
        "evi:provenanceSignature": str(sig),
        "evi:memberIds": _truncate_member_ids(sorted(member_ids), max_member_ids),
    }

    if schema_ids:
        node["evi:commonSchema"] = [make_id_ref(sid) for sid in schema_ids]

    return node


def _truncate_member_ids(ids: List[str], max_ids: int) -> List[str]:
    """Truncate member ID list if max_ids > 0, appending a summary entry."""
    if max_ids <= 0 or len(ids) <= max_ids:
        return ids
    excluded = len(ids) - max_ids
    return ids[:max_ids] + [f"... and {excluded} more (total: {len(ids)})"]


def condense_graph(
    graph: List[dict],
    threshold: int,
    max_member_ids: int = 0,
):
    """Condense an RO-Crate @graph by collapsing repetitive provenance.

    Returns (condensed_graph, stats).
    """
    index: Dict[str, dict] = {}
    for node in graph:
        node_id = node.get("@id")
        if node_id:
            index[node_id] = node

    original_count = len(graph)

    keep_ids, collapsed_ids, group_nodes, comp_updates = \
        traverse_and_condense(index, threshold, max_member_ids)

    if not group_nodes:
        stats = {
            "condensed": False,
            "originalEntityCount": original_count,
            "condensedEntityCount": original_count,
            "datasetGroupCount": 0,
            "note": "No repetitive provenance found above threshold.",
        }
        return graph, stats

    new_graph = []
    for node in graph:
        node_id = node.get("@id")

        if node_id not in keep_ids:
            continue

        if node_id in comp_updates:
            node = dict(node)
            for member_ids, group_id in comp_updates[node_id]:
                member_set = set(member_ids)

                if "usedDataset" in node and node["usedDataset"]:
                    kept = [ref for ref in node["usedDataset"]
                            if ref.get("@id") not in member_set]
                    kept.append(make_id_ref(group_id))
                    node["usedDataset"] = kept

                if "prov:used" in node and node["prov:used"]:
                    kept = [ref for ref in node["prov:used"]
                            if ref.get("@id") not in member_set]
                    kept.append(make_id_ref(group_id))
                    node["prov:used"] = kept

        if is_rocrate_root(node):
            node = dict(node)
            condensed_count = len(keep_ids) + len(group_nodes)
            node["evi:condensed"] = True
            node["evi:condensationThreshold"] = threshold
            node["evi:condensationDate"] = str(datetime.date.today())
            node["evi:originalEntityCount"] = original_count
            node["evi:condensedEntityCount"] = condensed_count
            node["evi:datasetGroupCount"] = len(group_nodes)

            total_collapsed = len(collapsed_ids)
            node["evi:condensationNote"] = (
                f"Condensed from {original_count} entities to "
                f"{condensed_count}. "
                f"{len(group_nodes)} dataset group(s) created by collapsing "
                f"{total_collapsed} datasets with identical provenance signatures "
                f"(same software chain). Full member lists preserved in evi:memberIds."
            )

            if "hasPart" in node and node["hasPart"]:
                kept = [ref for ref in node["hasPart"]
                        if ref.get("@id") not in collapsed_ids]
                for gn in group_nodes:
                    kept.append(make_id_ref(gn["@id"]))
                node["hasPart"] = kept

            if node_id in comp_updates:
                for member_ids, group_id in comp_updates[node_id]:
                    member_set = set(member_ids)
                    if "EVI:outputs" in node and node["EVI:outputs"]:
                        kept = [ref for ref in node["EVI:outputs"]
                                if ref.get("@id") not in member_set]
                        kept.append(make_id_ref(group_id))
                        node["EVI:outputs"] = kept

        new_graph.append(node)

    new_graph.extend(group_nodes)

    # Clean up dangling references
    final_ids = {n.get("@id") for n in new_graph if "@id" in n}
    ref_fields = ("usedDataset", "usedSoftware", "usedMLModel", "generated",
                  "hasPart", "prov:used", "generatedBy", "prov:wasGeneratedBy",
                  "derivedFrom", "prov:wasDerivedFrom", "usedByComputation",
                  "isPartOf", "EVI:outputs")

    for i, node in enumerate(new_graph):
        modified = False
        node_copy = None
        for field in ref_fields:
            val = node.get(field)
            if val is None:
                continue
            if isinstance(val, dict) and "@id" in val:
                if val["@id"] not in final_ids:
                    if not modified:
                        node_copy = dict(node)
                        modified = True
                    node_copy[field] = []
            elif isinstance(val, list):
                cleaned = [ref for ref in val
                           if not (isinstance(ref, dict) and "@id" in ref
                                   and ref["@id"] not in final_ids)]
                if len(cleaned) != len(val):
                    if not modified:
                        node_copy = dict(node)
                        modified = True
                    node_copy[field] = cleaned
        if modified:
            new_graph[i] = node_copy

    condensed_count = len(new_graph)
    groups_info = []
    for gn in group_nodes:
        groups_info.append({
            "memberCount": gn["evi:memberCount"],
            "format": gn["format"],
            "groupId": gn["@id"],
        })

    stats = {
        "condensed": True,
        "originalEntityCount": original_count,
        "condensedEntityCount": condensed_count,
        "datasetGroupCount": len(group_nodes),
        "entitiesRemoved": len(collapsed_ids),
        "groups": groups_info,
    }

    return new_graph, stats
