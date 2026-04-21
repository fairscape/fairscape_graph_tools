"""Per-computation annotation -- builds the prompt for a single
Computation node, runs the datasci system-prompt LLM agent, and
converts the lightweight `LLMComputationAnnotation` response into a
full `AnnotatedComputation` model.

Depends on the `TaskTracker` port for progress + llm-trace updates;
all persistence concerns live outside this module. The pre-fetched
`software_cache` and `stats_cache` are passed in -- the orchestrator
is responsible for populating them (via `SoftwareFetcher.fetch` and
`GraphSource.find_dataset_stats`) before calling `annotate_*`.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from typing import Dict, List, Optional

from pydantic_ai import Agent

from fairscape_graph_tools.models.annotated_computation import (
    AnnotatedComputation,
    CodeAnalysis,
    DatasetSummary,
    LLMComputationAnnotation,
    normalize_assumption,
    normalize_error,
)
from fairscape_graph_tools.pipeline.graph_utils import _resolve_refs
from fairscape_graph_tools.pipeline.stats import _format_dataset_stats
from fairscape_graph_tools.ports import TaskTracker
from fairscape_graph_tools.prompts import DATASCI_SYSTEM_PROMPT
from fairscape_graph_tools.runtime import (
    AsyncRateLimiter,
    run_agent_with_retry,
    run_async,
)

logger = logging.getLogger(__name__)

MAX_PROMPT_DATASETS = 3


def build_computation_prompt(
    computation: dict,
    software_cache: dict,
    index: dict,
    stats_cache: Optional[Dict[str, dict]] = None,
) -> str:
    """Build the prompt for a single computation annotation."""
    parts: List[str] = []

    parts.append("## Computation")
    parts.append(f"**ID:** {computation.get('@id', 'unknown')}")
    parts.append(f"**Name:** {computation.get('name', 'unnamed')}")
    parts.append(f"**Description:** {computation.get('description', 'No description')}")
    parts.append(f"**Command:** {computation.get('command', 'N/A')}")
    parts.append(f"**Run By:** {computation.get('runBy', 'unknown')}")
    parts.append(f"**Date Created:** {computation.get('dateCreated', 'unknown')}")
    parts.append("")

    if stats_cache is None:
        stats_cache = {}

    input_refs = _resolve_refs(computation.get("usedDataset"))
    if input_refs:
        truncated_inputs = len(input_refs) > MAX_PROMPT_DATASETS
        display_inputs = input_refs[:MAX_PROMPT_DATASETS]
        parts.append("## Input Datasets")
        for ds_id in display_inputs:
            ds_node = index.get(ds_id, {})
            ds_name = ds_node.get("name", ds_id)
            parts.append(f"- **{ds_name}** ({ds_node.get('format', 'unknown format')})")
            parts.append(f"  ID: {ds_id}")
            parts.append(f"  Description: {ds_node.get('description', 'No description')}")
            if ds_node.get("keywords"):
                kw = ds_node["keywords"]
                kw_str = ", ".join(kw) if isinstance(kw, list) else kw
                parts.append(f"  Keywords: {kw_str}")
            if ds_id in stats_cache:
                formatted = _format_dataset_stats(ds_name, stats_cache[ds_id])
                if formatted:
                    parts.append("")
                    parts.append(formatted)
        if truncated_inputs:
            parts.append(
                f"*({len(input_refs) - MAX_PROMPT_DATASETS} more input datasets omitted)*"
            )
        parts.append("")

    software_refs = _resolve_refs(computation.get("usedSoftware"))
    if software_refs:
        parts.append("## Software")
        for sw_id in software_refs:
            sw_node = index.get(sw_id, {})
            parts.append(f"### {sw_node.get('name', sw_id)}")
            parts.append(f"**ID:** {sw_id}")
            parts.append(f"**Description:** {sw_node.get('description', 'No description')}")
            parts.append(f"**Content URL:** {sw_node.get('contentUrl', 'N/A')}")
            code = software_cache.get(sw_id, "")
            if code and not code.startswith("["):
                preview = (
                    f"{repr(code[:10])}…{repr(code[-10:])}"
                    if len(code) > 20
                    else repr(code)
                )
                logger.info(
                    f"Prompt build: inserted software {sw_id} ({len(code)} chars) preview={preview}"
                )
                parts.append(f"\n**Source Code:**\n```\n{code}\n```")
            else:
                logger.warning(
                    f"Prompt build: NO code for software {sw_id} "
                    f"(cache_hit={sw_id in software_cache!r}, value={repr((code or '')[:60])})"
                )
                parts.append(f"\n**Source Code:** {code}")
        parts.append("")

    output_refs = _resolve_refs(computation.get("generated"))
    if output_refs:
        truncated_outputs = len(output_refs) > MAX_PROMPT_DATASETS
        display_outputs = output_refs[:MAX_PROMPT_DATASETS]
        parts.append("## Output Datasets")
        for ds_id in display_outputs:
            ds_node = index.get(ds_id, {})
            ds_name = ds_node.get("name", ds_id)
            parts.append(f"- **{ds_name}** ({ds_node.get('format', 'unknown format')})")
            parts.append(f"  ID: {ds_id}")
            parts.append(f"  Description: {ds_node.get('description', 'No description')}")
            if ds_id in stats_cache:
                formatted = _format_dataset_stats(ds_name, stats_cache[ds_id])
                if formatted:
                    parts.append("")
                    parts.append(formatted)
        if truncated_outputs:
            parts.append(
                f"*({len(output_refs) - MAX_PROMPT_DATASETS} more output datasets omitted)*"
            )
        parts.append("")

    prompt = "\n".join(parts)
    comp_id = computation.get("@id", "unknown")
    est_tokens = len(prompt) // 4
    logger.info(
        f"Prompt for {comp_id}: ~{est_tokens} tokens estimated ({len(prompt)} chars)"
    )
    return prompt


def llm_to_annotated(
    llm_result: LLMComputationAnnotation,
    comp_id: str,
    llm_model: str,
    temperature: float,
) -> AnnotatedComputation:
    """Convert lightweight LLM output into a full AnnotatedComputation."""
    annotation_id = f"{comp_id}-annotation"
    now = datetime.datetime.utcnow().isoformat()

    code_analyses = [
        CodeAnalysis(
            software={"@id": ca.software_id},
            name=ca.name,
            summary=ca.summary,
            keyFunctions=ca.keyFunctions,
            assumptions=[normalize_assumption(c) for c in (ca.assumptions or [])],
        )
        for ca in (llm_result.codeAnalysis or [])
    ]

    input_summaries = [
        DatasetSummary(
            dataset={"@id": ds.dataset_id},
            name=ds.name,
            role=ds.role,
            description=ds.description,
            dataQuality=ds.dataQuality,
        )
        for ds in (llm_result.inputSummaries or [])
    ]
    output_summaries = [
        DatasetSummary(
            dataset={"@id": ds.dataset_id},
            name=ds.name,
            role=ds.role,
            description=ds.description,
            dataQuality=ds.dataQuality,
        )
        for ds in (llm_result.outputSummaries or [])
    ]

    step_summary = llm_result.stepSummary
    description = (
        step_summary[:200]
        if len(step_summary) >= 10
        else step_summary + " " * (10 - len(step_summary))
    )

    return AnnotatedComputation.model_validate({
        "@id": annotation_id,
        "name": f"Annotation of {comp_id}",
        "author": llm_model,
        "description": description,
        "evi:annotates": {"@id": comp_id},
        "evi:stepSummary": step_summary,
        "evi:codeAnalysis": [ca.model_dump(by_alias=True) for ca in code_analyses],
        "evi:inputSummaries": [ds.model_dump(by_alias=True) for ds in input_summaries],
        "evi:outputSummaries": [ds.model_dump(by_alias=True) for ds in output_summaries],
        "evi:assumptions": [
            normalize_assumption(a).model_dump() for a in (llm_result.assumptions or [])
        ],
        "evi:errors": [
            normalize_error(e).model_dump() for e in (llm_result.errors or [])
        ],
        "evi:computationStatus": llm_result.computationStatus or "clear",
        "evi:llmModel": llm_model,
        "evi:llmTemperature": temperature,
        "dateCreated": now,
    })


async def annotate_single_computation(
    tracker: TaskTracker,
    computation: dict,
    software_cache: dict,
    index: dict,
    llm_model: str,
    temperature: float,
    stats_cache: Optional[Dict[str, dict]] = None,
) -> AnnotatedComputation:
    """Annotate a single computation using PydanticAI.

    `software_cache` and `stats_cache` are populated by the orchestrator
    before this is called; the `SoftwareFetcher` and `GraphSource` ports
    are not reached from inside annotation.
    """
    prompt = build_computation_prompt(
        computation, software_cache, index, stats_cache=stats_cache
    )
    comp_id = computation.get("@id", f"ark:59853/computation-{uuid.uuid4()}")

    agent = Agent(
        llm_model,
        output_type=LLMComputationAnnotation,
        system_prompt=DATASCI_SYSTEM_PROMPT,
        retries=3,
    )

    result = await run_agent_with_retry(agent, prompt)
    llm_output: LLMComputationAnnotation = result.output

    tracker.push_llm_result(
        f"computation:{comp_id}",
        llm_output.model_dump(mode="json"),
    )

    return llm_to_annotated(llm_output, comp_id, llm_model, temperature)


async def annotate_computations_async(
    tracker: TaskTracker,
    computations: list,
    software_cache: dict,
    index: dict,
    llm_model: str,
    temperature: float,
    max_workers: int = 2,
    stats_cache: Optional[Dict[str, dict]] = None,
    rate_limiter: Optional[AsyncRateLimiter] = None,
) -> List[AnnotatedComputation]:
    """Annotate all computations concurrently via asyncio.gather."""
    tracker.update({"current_step": "PROMPTING", "status": "PROMPTING"})

    async def _annotate_one(comp):
        comp_id = comp.get("@id", "unknown")
        if rate_limiter:
            await rate_limiter.acquire()
        try:
            annotated = await annotate_single_computation(
                tracker,
                comp,
                software_cache,
                index,
                llm_model,
                temperature,
                stats_cache=stats_cache,
            )
            tracker.update_computation_status(comp_id, {"status": "done"})
            return ("ok", comp_id, annotated)
        except Exception as e:
            logger.error(f"Failed to annotate computation {comp_id}: {e}")
            tracker.update_computation_status(
                comp_id, {"status": "error", "error": str(e)}
            )
            return ("error", comp_id, str(e))
        finally:
            tracker.increment_completed()

    outcomes = await asyncio.gather(*[_annotate_one(c) for c in computations])

    results = [o[2] for o in outcomes if o[0] == "ok"]
    errors = [
        {"computation_id": o[1], "error": o[2]} for o in outcomes if o[0] == "error"
    ]

    if errors and not results:
        raise RuntimeError(
            f"All {len(errors)} computation annotations failed. "
            f"First error: {errors[0]['error']}"
        )
    if errors:
        logger.warning(
            f"{len(errors)} of {len(computations)} computation annotations failed"
        )

    return results


def annotate_computations_parallel(
    tracker: TaskTracker,
    computations: list,
    software_cache: dict,
    index: dict,
    llm_model: str,
    temperature: float,
    max_workers: int = 2,
    stats_cache: Optional[Dict[str, dict]] = None,
    rate_limiter: Optional[AsyncRateLimiter] = None,
) -> List[AnnotatedComputation]:
    """Annotate computations concurrently using async I/O."""
    return run_async(annotate_computations_async(
        tracker,
        computations,
        software_cache,
        index,
        llm_model,
        temperature,
        max_workers,
        stats_cache=stats_cache,
        rate_limiter=rate_limiter,
    ))
