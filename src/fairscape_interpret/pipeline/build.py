"""Pure AnnotatedEvidenceGraph assembly -- no persistence, no ports.

Takes the artifacts the pipeline has produced (the condensed graph,
per-computation annotations, the synthesis result, and any audience
perspectives) and returns a validated `AnnotatedEvidenceGraph` model.
The `ResultSink` adapter is responsible for persisting the return
value; that split lets the CLI write a sidecar while the server writes
a `StoredIdentifier` without either path leaking into the other.
"""

from __future__ import annotations

import datetime
import logging
import re
import uuid
from typing import List

from fairscape_interpret.models.annotated_computation import (
    AnnotatedComputation,
    normalize_assumption,
)
from fairscape_interpret.models.annotated_evidence_graph import (
    AnnotatedEvidenceGraph,
    AudiencePerspective,
    DataOverview,
    GraphAssumption,
)
from fairscape_interpret.pipeline.synthesize import GraphSynthesisResult

logger = logging.getLogger(__name__)


def build_aeg(
    rocrate_id: str,
    graph: list[dict],
    step_annotations: List[AnnotatedComputation],
    synthesis: GraphSynthesisResult,
    audience_perspectives: List[dict],
    llm_model: str,
    temperature: float,
) -> AnnotatedEvidenceGraph:
    """Assemble and validate an AnnotatedEvidenceGraph.

    `graph` is the condensed crate's flat `@graph` list. `synthesis`
    is the datasci persona output. `audience_perspectives` is a list
    of dicts as produced by `synthesize_graph` (each carrying an
    `assumptions_raw` list of LLMAssumption objects).
    """
    graph_dict: dict[str, dict] = {}
    for node in graph:
        node_id = node.get("@id")
        if node_id:
            graph_dict[node_id] = node

    for ann in step_annotations:
        ann_dict = ann.model_dump(by_alias=True, exclude_none=True, mode="json")
        graph_dict[ann.guid] = ann_dict

        comp_id = _extract_annotates_id(ann)
        if comp_id and comp_id in graph_dict:
            existing = graph_dict[comp_id].get("evi:annotatedBy", [])
            if isinstance(existing, dict):
                existing = [existing]
            elif not isinstance(existing, list):
                existing = []
            existing.append({"@id": ann.guid})
            graph_dict[comp_id]["evi:annotatedBy"] = existing
        else:
            logger.warning(
                "Could not back-link annotation %s -> computation %s "
                "(not found in graph_dict)",
                ann.guid,
                comp_id,
            )

    step_ann_refs = [{"@id": ann.guid} for ann in step_annotations]

    compiled_assumptions = _compile_assumptions(
        step_annotations, synthesis, rocrate_id
    )
    audiences = _build_audiences(audience_perspectives, rocrate_id)
    overview = _build_overview(
        rocrate_id, graph_dict, synthesis, compiled_assumptions
    )

    aeg_id = _mint_aeg_id(rocrate_id)
    now = datetime.datetime.utcnow().isoformat()
    root_name = graph_dict.get(rocrate_id, {}).get("name", rocrate_id)

    aeg_data = {
        "@id": aeg_id,
        "name": f"Annotated Evidence Graph: {root_name}",
        "description": f"AI-mediated interpretation of RO-Crate {rocrate_id}",
        "author": llm_model,
        "evi:annotates": {"@id": rocrate_id},
        "@graph": graph_dict,
        "evi:executiveSummary": synthesis.executiveSummary,
        "evi:narrativeSummary": synthesis.narrativeSummary,
        "evi:keyFindings": synthesis.keyFindings,
        "evi:assumptions": [
            a.model_dump(by_alias=True, mode="json") for a in compiled_assumptions
        ],
        "evi:overview": overview.model_dump(by_alias=True, mode="json"),
        "evi:audiences": [
            a.model_dump(by_alias=True, mode="json") for a in audiences
        ],
        "evi:stepAnnotations": step_ann_refs,
        "evi:llmModel": llm_model,
        "evi:llmTemperature": temperature,
        "dateCreated": now,
    }
    return AnnotatedEvidenceGraph.model_validate(aeg_data)


def _extract_annotates_id(ann: AnnotatedComputation) -> str:
    """Pull the @id of the computation this annotation targets. The
    field may be an IdentifierValue, a dict, or a bare string depending
    on how the model was hydrated."""
    annotates = ann.annotates
    if hasattr(annotates, "guid"):
        return annotates.guid
    if isinstance(annotates, dict):
        return annotates.get("@id", "")
    return str(annotates)


def _compile_assumptions(
    step_annotations: List[AnnotatedComputation],
    synthesis: GraphSynthesisResult,
    rocrate_id: str,
) -> List[GraphAssumption]:
    """Roll per-step and synthesis-level assumptions into a single
    list, each tagged with its source annotation."""
    compiled: List[GraphAssumption] = []
    for ann in step_annotations:
        for assumption in (ann.assumptions or []):
            compiled.append(GraphAssumption(
                impact=assumption.impact,
                name=assumption.name,
                description=assumption.description,
                downstreamImpacts=assumption.downstreamImpacts,
                evidence=assumption.evidence,
                reviewRecommended=getattr(assumption, "reviewRecommended", False),
                recommendedValidation=getattr(assumption, "recommendedValidation", None),
                sourceAnnotation={"@id": ann.guid},
            ))
    for llm_assumption in (synthesis.assumptions or []):
        normalized = normalize_assumption(llm_assumption)
        compiled.append(GraphAssumption(
            impact=normalized.impact,
            name=normalized.name,
            description=normalized.description,
            downstreamImpacts=normalized.downstreamImpacts,
            evidence=normalized.evidence,
            reviewRecommended=getattr(normalized, "reviewRecommended", False),
            recommendedValidation=getattr(normalized, "recommendedValidation", None),
            sourceAnnotation={"@id": rocrate_id},
        ))
    return compiled


def _build_audiences(
    audience_perspectives: List[dict], rocrate_id: str
) -> List[AudiencePerspective]:
    audiences: List[AudiencePerspective] = []
    for aud_data in audience_perspectives:
        aud_assumptions: List[GraphAssumption] = []
        for llm_a in (aud_data.get("assumptions_raw") or []):
            normalized = normalize_assumption(llm_a)
            aud_assumptions.append(GraphAssumption(
                impact=normalized.impact,
                name=normalized.name,
                description=normalized.description,
                downstreamImpacts=normalized.downstreamImpacts,
                evidence=normalized.evidence,
                reviewRecommended=getattr(normalized, "reviewRecommended", False),
                recommendedValidation=getattr(normalized, "recommendedValidation", None),
                sourceAnnotation={"@id": rocrate_id},
            ))
        audiences.append(AudiencePerspective(
            targetAudience=aud_data["targetAudience"],
            audienceLabel=aud_data["audienceLabel"],
            executiveSummary=aud_data["executiveSummary"],
            narrativeSummary=aud_data["narrativeSummary"],
            keyFindings=aud_data.get("keyFindings", []),
            assumptions=aud_assumptions,
        ))
    return audiences


def _build_overview(
    rocrate_id: str,
    graph_dict: dict,
    synthesis: GraphSynthesisResult,
    compiled_assumptions: List[GraphAssumption],
) -> DataOverview:
    root_entity = graph_dict.get(rocrate_id, {})
    root_name = root_entity.get("name", "")
    root_desc = root_entity.get("description", "")
    if root_name and root_desc:
        overview_description = f"{root_name}. {root_desc}"
    else:
        overview_description = root_name or root_desc or "No description available"

    data_formats = sorted({
        entity.get("format")
        for entity in graph_dict.values()
        if isinstance(entity, dict) and entity.get("format")
    })

    root_keywords = root_entity.get("keywords", [])
    if isinstance(root_keywords, str):
        root_keywords = [root_keywords]

    top_assumptions = [a for a in compiled_assumptions if a.impact == "CRITICAL"][:2]
    if len(top_assumptions) < 2:
        major = [a for a in compiled_assumptions if a.impact == "MAJOR"]
        top_assumptions.extend(major[:2 - len(top_assumptions)])

    return DataOverview(
        dataDescription=overview_description,
        dataFormats=data_formats,
        keywords=root_keywords,
        license=root_entity.get("license"),
        conditionsOfAccess=root_entity.get("conditionsOfAccess"),
        topAssumptions=top_assumptions,
        pipelineDescription=synthesis.pipelineDescription,
        pipelineSteps=synthesis.pipelineSteps or None,
    )


def _mint_aeg_id(rocrate_id: str) -> str:
    ark_match = re.match(r"ark:(\d+)/(.*)", rocrate_id)
    if ark_match:
        naan = ark_match.group(1)
        postfix = ark_match.group(2)
        return f"ark:{naan}/annotated-eg-{postfix}"
    return f"ark:59853/annotated-eg-{uuid.uuid4()}"
