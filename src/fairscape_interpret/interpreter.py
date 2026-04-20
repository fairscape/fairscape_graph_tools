"""Interpreter orchestrator -- drives the full AI-interpretation pipeline
over ports, with no storage-specific code.

Flow: ensure-condensed -> traverse -> prefetch software + stats ->
annotate each computation -> graph-level synthesis -> assemble
`AnnotatedEvidenceGraph` -> persist via `ResultSink.persist_aeg`.

The four ports (`GraphSource`, `ResultSink`, `TaskTracker`,
`SoftwareFetcher`) and the `Condenser` orchestrator are the only
collaborators. Consumers (mds_python server, fairscape-cli) supply
concrete adapters.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple

from fairscape_interpret.condenser import Condenser
from fairscape_interpret.models.annotated_computation import AnnotatedComputation
from fairscape_interpret.pipeline.annotate import annotate_computations_parallel
from fairscape_interpret.pipeline.build import build_aeg
from fairscape_interpret.pipeline.graph_utils import (
    _build_index,
    _is_computation,
    _resolve_refs,
)
from fairscape_interpret.pipeline.synthesize import synthesize_graph
from fairscape_interpret.ports import (
    GraphSource,
    ResultSink,
    SoftwareFetcher,
    TaskTracker,
)
from fairscape_interpret.runtime import AsyncRateLimiter

logger = logging.getLogger(__name__)


@dataclass
class InterpretConfig:
    """Per-run configuration for the interpretation pipeline.

    Defaults mirror the server's historical values so existing
    fixtures keep producing the same outputs.
    """

    llm_model: str = "google-gla:gemini-2.5-flash-lite"
    temperature: float = 0.2
    max_workers: int = 2
    rate_limiter_max_requests: int = 2
    rate_limiter_window_seconds: float = 10.0


class Interpreter:
    """Storage-agnostic orchestrator for AI-mediated RO-Crate interpretation."""

    def __init__(
        self,
        graph: GraphSource,
        sink: ResultSink,
        tracker: TaskTracker,
        software: SoftwareFetcher,
        condenser: Condenser,
        config: InterpretConfig,
    ):
        self.graph = graph
        self.sink = sink
        self.tracker = tracker
        self.software = software
        self.condenser = condenser
        self.config = config

    def run_sync(self, rocrate_id: str) -> str:
        """Run the full pipeline synchronously. Returns the persisted
        AnnotatedEvidenceGraph @id."""
        self.tracker.update({
            "status": "PROCESSING",
            "time_started": datetime.datetime.utcnow(),
        })

        try:
            graph_list, condensed_id, root_node = self._condense(rocrate_id)
            computations, index = self._find_computations(graph_list)

            if not computations:
                raise ValueError(
                    f"No Computation nodes found in RO-Crate {rocrate_id}"
                )

            software_cache = self._prefetch_software(computations, index)
            stats_cache = self._prefetch_stats(computations)

            rate_limiter = AsyncRateLimiter(
                max_requests=self.config.rate_limiter_max_requests,
                window_seconds=self.config.rate_limiter_window_seconds,
            )

            step_annotations = annotate_computations_parallel(
                self.tracker,
                computations,
                software_cache,
                index,
                self.config.llm_model,
                self.config.temperature,
                max_workers=self.config.max_workers,
                stats_cache=stats_cache,
                rate_limiter=rate_limiter,
            )

            synthesis, audience_perspectives = synthesize_graph(
                self.tracker,
                root_node,
                step_annotations,
                self.config.llm_model,
                self.config.temperature,
                rate_limiter=rate_limiter,
                graph_dict=index,
            )

            aeg_id = self._build_and_store(
                rocrate_id, graph_list, step_annotations,
                synthesis, audience_perspectives,
            )

            self.tracker.update({
                "status": "SUCCESS",
                "current_step": "COMPLETE",
                "time_finished": datetime.datetime.utcnow(),
            })
            return aeg_id

        except Exception as e:
            logger.exception(f"Interpretation failed for rocrate {rocrate_id}")
            self.tracker.update({
                "status": "FAILURE",
                "error": {"message": str(e), "error_type": type(e).__name__},
                "time_finished": datetime.datetime.utcnow(),
            })
            raise

    async def run_async(self, rocrate_id: str) -> str:
        """Await-friendly wrapper for callers already inside an event
        loop. Delegates to `run_sync` on a worker thread so the internal
        `run_async` event-loop helpers don't collide with the caller's
        loop."""
        return await asyncio.to_thread(self.run_sync, rocrate_id)

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def _condense(self, rocrate_id: str) -> Tuple[List[dict], str, dict]:
        self.tracker.update({"current_step": "CONDENSING", "status": "CONDENSING"})
        graph_list, condensed_id, root_node = self.condenser.ensure_condensed(rocrate_id)
        self.tracker.update({"condensed_rocrate_id": condensed_id})
        return graph_list, condensed_id, root_node

    def _find_computations(
        self, graph_list: List[dict]
    ) -> Tuple[List[dict], Dict[str, dict]]:
        self.tracker.update({"current_step": "TRAVERSING", "status": "TRAVERSING"})

        index = _build_index(graph_list)
        computations = [node for node in graph_list if _is_computation(node)]

        comp_details = [
            {
                "computation_id": c.get("@id", ""),
                "name": c.get("name", ""),
                "status": "pending",
            }
            for c in computations
        ]
        self.tracker.update({
            "total_computations": len(computations),
            "computation_details": comp_details,
        })

        logger.info(f"Found {len(computations)} computation(s) in graph")
        return computations, index

    def _prefetch_software(
        self, computations: List[dict], index: Dict[str, dict]
    ) -> Dict[str, str]:
        """Thin loop over `SoftwareFetcher.fetch`. The port handles all
        the environment-specific logic (server token + URL rewrite, CLI
        local paths, GitHub fallback); this stays a one-pass cache."""
        self.tracker.update({"current_step": "PREFETCHING", "status": "PREFETCHING"})

        software_cache: Dict[str, str] = {}
        for comp in computations:
            for sw_id in _resolve_refs(comp.get("usedSoftware")):
                if sw_id in software_cache:
                    continue
                sw_node = index.get(sw_id, {})
                software_cache[sw_id] = self.software.fetch(sw_node)
        return software_cache

    def _prefetch_stats(
        self, computations: List[dict]
    ) -> Dict[str, dict]:
        """Thin loop over `GraphSource.find_dataset_stats`. Batches all
        referenced dataset IDs into one port call."""
        dataset_ids: set[str] = set()
        for comp in computations:
            dataset_ids.update(_resolve_refs(comp.get("usedDataset")))
            dataset_ids.update(_resolve_refs(comp.get("generated")))
        if not dataset_ids:
            return {}
        return self.graph.find_dataset_stats(dataset_ids)

    def _build_and_store(
        self,
        rocrate_id: str,
        graph_list: List[dict],
        step_annotations: List[AnnotatedComputation],
        synthesis,
        audience_perspectives: List[dict],
    ) -> str:
        self.tracker.update({"current_step": "STORING", "status": "STORING"})

        aeg = build_aeg(
            rocrate_id,
            graph_list,
            step_annotations,
            synthesis,
            audience_perspectives,
            self.config.llm_model,
            self.config.temperature,
        )
        aeg_id = self.sink.persist_aeg(aeg, rocrate_id, step_annotations)
        self.tracker.update({"annotated_evidence_graph_id": aeg_id})
        logger.info(f"Stored AnnotatedEvidenceGraph {aeg_id}")
        return aeg_id
