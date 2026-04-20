"""Graph traversal helpers: type detection, reference resolution, DAG ordering,
ARK ID matching. All functions operate on raw RO-Crate @graph dicts and are
storage-agnostic.

Type detection lives in two forms:
- The interpretation pipeline's original loose matchers (`_is_computation`,
  `_is_rocrate_root`) that only peek at the @type field.
- The condensation pipeline's stricter matchers (`get_evi_type`, `is_dataset`,
  `is_computation`, `is_software`, `is_rocrate_root`) that filter by a known
  set of EVI types.

Both are exported here so consumers can pick whichever fits.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Type detection -- loose (interpretation-pipeline style)
# ---------------------------------------------------------------------------

def _extract_short_types_loose(node: dict) -> list:
    """Extract short type names from a node's @type field (unfiltered)."""
    raw = node.get("@type", [])
    if isinstance(raw, str):
        raw = [raw]
    shorts = []
    for t in raw:
        short = t.split("#")[-1] if "#" in t else t.split(":")[-1] if ":" in t else t
        shorts.append(short)
    return shorts


def _is_computation(node: dict) -> bool:
    """Loose match: any @type containing 'Computation'."""
    return "Computation" in _extract_short_types_loose(node)


def _is_rocrate_root(node: dict) -> bool:
    """Loose match: any @type containing 'ROCrate'."""
    return "ROCrate" in _extract_short_types_loose(node)


# ---------------------------------------------------------------------------
# Type detection -- strict (condensation-pipeline style)
# ---------------------------------------------------------------------------

EVI_TYPES = {
    "Dataset", "Software", "MLModel", "Computation", "Annotation",
    "Experiment", "ROCrate", "CreativeWork", "Schema",
}


def _extract_short_types(node: dict) -> list:
    """Extract short type names, filtered to the known EVI type vocabulary."""
    raw = node.get("@type", [])
    if isinstance(raw, str):
        raw = [raw]
    shorts = []
    for t in raw:
        short = t.split("#")[-1] if "#" in t else t.split(":")[-1] if ":" in t else t
        if short in EVI_TYPES:
            shorts.append(short)
    return shorts


def get_evi_type(node: dict) -> Optional[str]:
    """Extract the primary EVI type from a node's @type field."""
    shorts = _extract_short_types(node)
    if not shorts:
        return None
    for preferred in ("ROCrate", "Computation", "Software", "MLModel",
                      "Experiment", "Annotation", "Schema"):
        if preferred in shorts:
            return preferred
    return shorts[0]


def is_dataset(node: dict) -> bool:
    types = _extract_short_types(node)
    return "Dataset" in types and "ROCrate" not in types


def is_computation(node: dict) -> bool:
    return get_evi_type(node) == "Computation"


def is_software(node: dict) -> bool:
    return get_evi_type(node) == "Software"


def is_rocrate_root(node: dict) -> bool:
    return "ROCrate" in _extract_short_types(node)


# ---------------------------------------------------------------------------
# Reference helpers
# ---------------------------------------------------------------------------

def _resolve_refs(field) -> list:
    """Normalize a reference field to a list of @id strings.

    Handles string, {"@id": ...} dict, list-of-strings, and list-of-dicts.
    """
    if field is None:
        return []
    if isinstance(field, str):
        return [field]
    if isinstance(field, dict):
        return [field.get("@id", "")] if "@id" in field else []
    if isinstance(field, list):
        result = []
        for item in field:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict) and "@id" in item:
                result.append(item["@id"])
        return result
    return []


def get_id_list(node: dict, *fields) -> List[str]:
    """Extract a list of @id strings from one or more reference fields."""
    ids = []
    for field in fields:
        val = node.get(field, [])
        if val is None:
            continue
        if isinstance(val, dict):
            val = [val]
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and "@id" in item:
                    ids.append(item["@id"])
                elif isinstance(item, str):
                    ids.append(item)
    return ids


def get_generatedby_ids(dataset: dict) -> List[str]:
    """Get the @ids of computations that generated this dataset."""
    return get_id_list(dataset, "generatedBy", "prov:wasGeneratedBy")


def make_id_ref(entity_id: str) -> Dict[str, str]:
    """Create a {"@id": ...} reference."""
    return {"@id": entity_id}


def _build_index(graph: list) -> dict:
    """Build {@id -> node} index from a @graph array."""
    index = {}
    for node in graph:
        node_id = node.get("@id")
        if node_id:
            index[node_id] = node
    return index


# ---------------------------------------------------------------------------
# ARK identifier matching
# ---------------------------------------------------------------------------

def flexible_ark_query(guid: str):
    """Build a MongoDB query that matches an ARK with or without dashes and
    with or without a slash after 'ark:'. Returns None if guid doesn't look
    like an ARK. Matches the ARK SPEC.

    The return shape is a MongoDB-style dict because the server uses it to
    query identifierCollection. CLI callers can inspect the `$regex` pattern
    and run it against an in-memory dict of entities.
    """
    ark_match = re.match(r'^ark:/?([\d]+)/(.*)', guid)
    if not ark_match:
        return None
    naan = ark_match.group(1)
    postfix = ark_match.group(2)
    stripped = postfix.replace('-', '')
    fuzzy_postfix = '-?'.join(re.escape(c) for c in stripped)
    pattern = f'^ark:{naan}/{fuzzy_postfix}$'
    return {"@id": {"$regex": pattern}}


# ---------------------------------------------------------------------------
# DAG ordering (topological sort for synthesis-prompt step enumeration)
# ---------------------------------------------------------------------------

def _compute_dag_order(
    step_annotations: List[Any],
    graph_dict: Dict[str, Dict[str, Any]],
) -> List[Tuple[str, Any]]:
    """Compute topological order of step annotations based on the DAG.

    `step_annotations` is a list of AnnotatedComputation objects (each exposing
    a `.annotates` IdentifierValue pointing at the underlying Computation
    @id); `graph_dict` is the full index keyed by @id.

    Returns a list of (label, annotation) tuples where label is e.g. "1",
    "1a", "1b", "2", etc. — single-node levels get a plain integer label;
    parallel levels get integer+letter suffixes.
    """
    # Map computation @id -> annotation
    ann_by_comp: Dict[str, Any] = {}
    for ann in step_annotations:
        annotates = getattr(ann, "annotates", None)
        if hasattr(annotates, "guid"):
            comp_id = annotates.guid
        elif isinstance(annotates, dict):
            comp_id = annotates.get("@id", "")
        else:
            comp_id = str(annotates) if annotates is not None else ""
        ann_by_comp[comp_id] = ann

    # Build adjacency: comp_id -> set of comp_ids it depends on
    deps: Dict[str, set] = {cid: set() for cid in ann_by_comp}
    # Map dataset @id -> comp_id that generated it
    generated_by: Dict[str, str] = {}
    for node in graph_dict.values():
        if not isinstance(node, dict):
            continue
        gen = node.get("generatedBy")
        if gen:
            gen_ids = _resolve_refs(gen)
            node_id = node.get("@id", "")
            for gid in gen_ids:
                if gid in ann_by_comp:
                    generated_by[node_id] = gid

    for comp_id in ann_by_comp:
        comp_node = graph_dict.get(comp_id, {})
        input_refs = _resolve_refs(comp_node.get("usedDataset"))
        for ds_id in input_refs:
            producer = generated_by.get(ds_id)
            if producer and producer != comp_id and producer in ann_by_comp:
                deps[comp_id].add(producer)

    # Kahn's algorithm for topological sort with level tracking
    in_degree = {cid: len(d) for cid, d in deps.items()}
    # Reverse adjacency for decrementing
    rdeps: Dict[str, set] = {cid: set() for cid in ann_by_comp}
    for cid, dep_set in deps.items():
        for d in dep_set:
            rdeps[d].add(cid)

    queue = [cid for cid, deg in in_degree.items() if deg == 0]
    levels: List[List[str]] = []
    visited: set = set()

    while queue:
        levels.append(sorted(queue))  # sort for determinism
        visited.update(queue)
        next_queue = []
        for cid in queue:
            for child in rdeps.get(cid, set()):
                if child in visited:
                    continue
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_queue.append(child)
        queue = next_queue

    # Handle any remaining nodes (cycles) -- add them at the end
    remaining = [cid for cid in ann_by_comp if cid not in visited]
    if remaining:
        levels.append(sorted(remaining))

    # Assign labels
    result: List[Tuple[str, Any]] = []
    for level_num, level_cids in enumerate(levels, 1):
        if len(level_cids) == 1:
            result.append((str(level_num), ann_by_comp[level_cids[0]]))
        else:
            for branch_idx, cid in enumerate(level_cids):
                letter = chr(ord('a') + branch_idx) if branch_idx < 26 else str(branch_idx)
                result.append((f"{level_num}{letter}", ann_by_comp[cid]))

    # Fallback: if somehow empty, return original order
    if not result:
        return [(str(i), ann) for i, ann in enumerate(step_annotations, 1)]

    return result
