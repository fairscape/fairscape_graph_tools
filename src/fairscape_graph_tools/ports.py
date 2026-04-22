"""Protocol-based ports for the interpretation pipeline.

The `Condenser` and `Interpreter` orchestrators depend only on these four
`Protocol`s. Consumers (the mds_python server, the fairscape-cli) each
supply their own concrete adapters — Mongo-backed on the server,
filesystem-backed in the CLI.

The shared package never imports server or CLI concerns (MongoDB, FastAPI,
Celery, Click, `StoredIdentifier`, `Permissions`, etc.). If you find
yourself wanting to widen a port signature with storage-shaped arguments,
stop — that logic belongs inside the adapter that implements the port.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from fairscape_graph_tools.models.annotated_computation import AnnotatedComputation
from fairscape_graph_tools.models.annotated_evidence_graph import AnnotatedEvidenceGraph
from fairscape_graph_tools.models.evidence_graph import EvidenceGraph


@runtime_checkable
class GraphSource(Protocol):
    """Read-side port: fetch entities and full graphs by ARK identifier.

    Server implementation crawls MongoDB's identifierCollection with
    dash-tolerant ARK regex. CLI implementation indexes one primary
    RO-Crate plus any --reference crates loaded from disk.
    """

    def find_entity(self, ark_id: str) -> dict | None:
        """Look up a single entity by ARK. Returns the flattened entity
        dict (the metadata payload, not a StoredIdentifier wrapper) or
        None if not found. Must apply dash-tolerant / ark-prefix-flexible
        matching on miss (see `flexible_ark_query`)."""
        ...

    def find_many(self, ark_ids: Iterable[str]) -> dict[str, dict]:
        """Batch-fetch entities by ARK. Returns `{ark_id: flattened_dict}`
        for every id that resolved; missing ids are simply absent from the
        result (callers decide how to stub them).

        Used by `EvidenceGraphBuilder`'s BFS to fan out one level at a
        time with a single `$in` query instead of N single-entity calls.
        Exact match only — dash-tolerant matching belongs in
        `find_entity`, which the builder calls only for the root lookup.
        """
        ...

    def find_dataset_stats(self, ark_ids: Iterable[str]) -> dict[str, dict]:
        """Batch-fetch pre-computed descriptive and split statistics for
        a set of datasets. Returns `{ark_id: {"descriptiveStatistics":
        {...}, "splitStatistics": {...}}}`. Datasets with no stored stats
        are simply absent from the result."""
        ...

    def build_full_graph(self, rocrate_id: str) -> list[dict]:
        """Collect every node reachable from the RO-Crate, resolving
        cross-crate ARK references. Return a flat list of flattened
        entity dicts suitable for `condense_graph()`.

        Server: BFS across identifierCollection following
        `ARK_REF_FIELDS`. CLI: flattens primary + reference crates into
        a single index."""
        ...


@runtime_checkable
class ResultSink(Protocol):
    """Write-side port: persist the two artifacts the pipeline produces.

    Server implementation wraps each in a StoredIdentifier document,
    inserts into identifierCollection, and writes back-pointers. CLI
    implementation writes sidecar JSON files and never modifies the
    source RO-Crate.
    """

    def persist_condensed(
        self,
        condensed_id: str,
        condensed_metadata: dict,
        source_rocrate_id: str,
        stats: dict,
    ) -> str:
        """Store a condensed RO-Crate and set any source back-pointer.

        `condensed_metadata` is the full ro-crate JSON (already contains
        `@context`, `@graph`, etc.). `stats` is the condensation-stat
        dict returned by `condense_graph` for diagnostics.

        Returns the stored `@id` of the condensed crate (server returns
        the same id that was passed in; CLI may return a file path or
        the same id depending on `--save-condensed`)."""
        ...

    def persist_aeg(
        self,
        aeg: AnnotatedEvidenceGraph,
        rocrate_id: str,
        step_annotations: list[AnnotatedComputation],
    ) -> str:
        """Store the AnnotatedEvidenceGraph plus any reverse links.

        Server: insert StoredIdentifier, set `hasAnnotatedEvidenceGraph`
        on the source crate, and `$addToSet` `evi:annotatedBy` on each
        annotated computation. CLI: write sidecar JSON.

        Returns the stored `@id` of the AEG."""
        ...

    def persist_evidence_graph(
        self,
        evidence_graph: EvidenceGraph,
        source_node_id: str,
    ) -> str:
        """Store the standard (non-annotated) evidence graph and set the
        back-pointer on the source node.

        Server: wrap in a StoredIdentifier, insert into
        identifierCollection, and `$set metadata.hasEvidenceGraph = {@id}`
        on the source node. CLI: write a sidecar JSON file next to the
        source RO-Crate and annotate `localEvidenceGraph` on the source
        node.

        Returns the stored `@id` of the evidence graph."""
        ...


@runtime_checkable
class TaskTracker(Protocol):
    """Progress + LLM-trace port. Never fails the pipeline on its own —
    tracker updates are best-effort side channels for observability.

    Server implementation writes to `asyncCollection` for the Celery
    task document. CLI implementation prints to stdout (or a Click
    progress bar) and optionally appends to a `.llm-trace.jsonl` file.
    """

    def update(self, updates: dict) -> None:
        """Merge-update the task document with free-form fields, e.g.
        `{"current_step": "PROMPTING", "status": "PROMPTING"}` or
        `{"total_computations": 7, "computation_details": [...]}`."""
        ...

    def update_computation_status(self, comp_id: str, updates: dict) -> None:
        """Update the per-computation status entry (done/error/etc.).

        Server maps to `$set: {"computation_details.$.status": ...}`
        with a positional filter. CLI updates its in-memory list."""
        ...

    def increment_completed(self) -> None:
        """Increment the `completed_computations` counter by one.
        Server uses `$inc`; CLI just bumps an integer."""
        ...

    def push_llm_result(self, label: str, raw_output: dict) -> None:
        """Append a raw LLM response to the task's trace log for
        debugging. Server `$push`es onto `llm_results`; CLI appends a
        JSONL line when `--debug-llm` is set, else no-ops."""
        ...


@runtime_checkable
class SoftwareFetcher(Protocol):
    """Fetch source code for a Software node. The one port where server
    and CLI genuinely differ in capability.

    Server: `/software/download/` with a bearer token (baseUrl ->
    internalUrl rewrite), falls back to GitHub via shared helpers, then
    to empty. CLI: local `contentUrl` relative to owning crate, falls
    back to GitHub, then to a `[source not fetched]` placeholder.
    """

    def fetch(self, software_node: dict) -> str:
        """Return source code text (possibly empty, possibly a
        placeholder). Must not raise on unfetchable sources — return an
        empty or placeholder string and let annotation continue."""
        ...
