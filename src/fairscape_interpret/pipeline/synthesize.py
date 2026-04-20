"""Graph-level synthesis -- rolls per-computation annotations into a
single `AnnotatedEvidenceGraph`-shaped summary via an LLM call.

Depends on the `TaskTracker` port for the single `push_llm_result`
hook (used to capture the raw synthesis output for debugging). No
storage-specific concerns live here.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple

from pydantic import BaseModel
from pydantic_ai import Agent

from fairscape_interpret.models.annotated_computation import (
    AnnotatedComputation,
    LLMAssumption,
)
from fairscape_interpret.pipeline.graph_utils import (
    _compute_dag_order,
    _resolve_refs,
)
from fairscape_interpret.ports import TaskTracker
from fairscape_interpret.prompts import (
    AUDIENCE_CONFIGS,
    DATASCI_SYNTHESIS_PROMPT,
)
from fairscape_interpret.runtime import (
    AsyncRateLimiter,
    run_agent_with_retry,
    run_async,
)


class GraphSynthesisResult(BaseModel):
    """Output schema the LLM is asked to fill for a full-graph synthesis."""

    pipelineDescription: Optional[str] = None
    pipelineSteps: List[str] = []
    executiveSummary: str
    narrativeSummary: str
    keyFindings: List[str] = []
    assumptions: List[LLMAssumption] = []


def build_synthesis_prompt(
    root_node: dict,
    step_annotations: List[AnnotatedComputation],
    graph_dict: Optional[dict] = None,
) -> str:
    """Build the prompt that every synthesis persona consumes."""
    parts: List[str] = []
    parts.append("## RO-Crate Overview")
    parts.append(f"**Name:** {root_node.get('name', 'Unknown')}")
    parts.append(f"**Description:** {root_node.get('description', 'No description')}")
    parts.append(f"**Author:** {root_node.get('author', 'Unknown')}")
    parts.append(f"**Keywords:** {root_node.get('keywords', '')}")
    parts.append("")

    if graph_dict:
        ordered = _compute_dag_order(step_annotations, graph_dict)
    else:
        ordered = [(str(i), ann) for i, ann in enumerate(step_annotations, 1)]

    parts.append("## Pipeline DAG Structure")
    for label, ann in ordered:
        annotates = ann.annotates
        if hasattr(annotates, "guid"):
            comp_id = annotates.guid
        elif isinstance(annotates, dict):
            comp_id = annotates.get("@id", "")
        else:
            comp_id = str(annotates)
        comp_node = graph_dict.get(comp_id, {}) if graph_dict else {}
        comp_name = comp_node.get("name", comp_id)
        inputs = _resolve_refs(comp_node.get("usedDataset"))
        outputs = _resolve_refs(comp_node.get("hasOutputs")) or _resolve_refs(
            comp_node.get("generated")
        )
        input_names = ", ".join(
            graph_dict.get(i, {}).get("name", i) if graph_dict else i
            for i in inputs
        ) or "raw inputs"
        output_names = ", ".join(
            graph_dict.get(o, {}).get("name", o) if graph_dict else o
            for o in outputs
        ) or "outputs"
        parts.append(
            f"Step {label}: {comp_name} "
            f"(inputs: {input_names}; outputs: {output_names})"
        )
    parts.append("")

    parts.append("## Step Annotations")
    for label, ann in ordered:
        parts.append(f"### Step {label}: {ann.annotates}")
        parts.append(f"**Status:** {getattr(ann, 'computationStatus', 'clear')}")
        parts.append(f"**Summary:** {ann.stepSummary}")
        if ann.errors:
            parts.append(
                f"**Errors:** "
                + "; ".join(f"[{e.severity}] {e.description}" for e in ann.errors)
            )
        if ann.assumptions:
            parts.append(
                f"**Assumptions:** "
                + "; ".join(
                    f"[{a.impact.value}] {a.description}" for a in ann.assumptions
                )
            )
        if ann.codeAnalysis:
            for ca in ann.codeAnalysis:
                parts.append(f"**Code ({ca.name or ca.software}):** {ca.summary}")
        parts.append("")

    return "\n".join(parts)


def synthesize_graph(
    tracker: TaskTracker,
    root_node: dict,
    step_annotations: List[AnnotatedComputation],
    llm_model: str,
    temperature: float,
    *,
    rate_limiter: Optional[AsyncRateLimiter] = None,
    graph_dict: Optional[dict] = None,
) -> Tuple[GraphSynthesisResult, List[dict]]:
    """Run the data-scientist synthesis (and any enabled audience
    syntheses) against the full set of step annotations.

    Returns `(datasci_synthesis, audience_perspectives)`. The audience
    list has one dict per `AUDIENCE_CONFIGS` entry that successfully
    returned; each dict carries raw `LLMAssumption`s under
    `assumptions_raw` -- callers normalize them during AEG assembly.
    """
    tracker.update({"current_step": "SYNTHESIZING", "status": "SYNTHESIZING"})

    prompt = build_synthesis_prompt(root_node, step_annotations, graph_dict=graph_dict)

    async def _run_all_syntheses():
        async def _run_one(system_prompt: str, label: str) -> GraphSynthesisResult:
            if rate_limiter:
                await rate_limiter.acquire()
            agent = Agent(
                llm_model,
                output_type=GraphSynthesisResult,
                system_prompt=system_prompt,
                retries=2,
            )
            result = await run_agent_with_retry(agent, prompt)
            tracker.push_llm_result(label, result.output.model_dump(mode="json"))
            return result.output

        tasks = [_run_one(DATASCI_SYNTHESIS_PROMPT, "synthesis:datasci")]
        # Audience syntheses intentionally disabled for now -- re-enable here when ready:
        # for aud in AUDIENCE_CONFIGS:
        #     tasks.append(_run_one(aud["prompt"], f"synthesis:{aud['key']}"))
        return await asyncio.gather(*tasks)

    results = run_async(_run_all_syntheses())

    datasci_synthesis = results[0]
    audience_perspectives: List[dict] = []
    for i, aud in enumerate(AUDIENCE_CONFIGS):
        if i + 1 >= len(results):
            break
        aud_result = results[i + 1]
        audience_perspectives.append({
            "targetAudience": aud["key"],
            "audienceLabel": aud["label"],
            "executiveSummary": aud_result.executiveSummary,
            "narrativeSummary": aud_result.narrativeSummary,
            "keyFindings": aud_result.keyFindings,
            "assumptions_raw": aud_result.assumptions,
        })

    return datasci_synthesis, audience_perspectives
