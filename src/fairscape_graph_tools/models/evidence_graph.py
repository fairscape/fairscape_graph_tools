"""Pydantic models for the standard (non-annotated) evidence graph.

These types are storage-agnostic — they describe the shape of an evidence
graph as FAIRSCAPE stores and returns it, but don't do BFS, condensation,
or projection themselves. The orchestrator in
`fairscape_graph_tools.evidence_graph_builder` is responsible for driving
the port-based adapters (Mongo on the server, local ROCrate on the CLI)
and the pure projection functions in
`fairscape_graph_tools.pipeline.evidence_graph`.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EvidenceNode:
    def __init__(self, id: str, type: str):
        self.id = id
        self.type = type
        self.usedSoftware: Optional[List[str]] = None
        self.usedDataset: Optional[List[str]] = None
        self.usedSample: Optional[List[str]] = None
        self.usedInstrument: Optional[List[str]] = None
        self.usedMLModel: Optional[List[str]] = None
        self.generatedBy: Optional[str] = None


class EvidenceGraph(BaseModel):
    metadataType: str = Field(default="evi:EvidenceGraph", alias="@type")
    guid: str = Field(alias="@id")
    owner: str
    description: str
    name: str = Field(default="Evidence Graph")
    outputs: Optional[List[Dict[str, str]]] = Field(default=None)
    graph: Optional[Dict[str, Dict[str, Any]]] = Field(default=None, alias="@graph")
    condensation_stats: Optional[Dict[str, Any]] = Field(default=None)

    class Config:
        extra = "allow"
        populate_by_name = True


class EvidenceGraphCreate(BaseModel):
    guid: str = Field(alias="@id")
    description: str
    name: str = Field(default="Evidence Graph")

    class Config:
        populate_by_name = True
