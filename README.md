# fairscape-graph-tools

Storage-agnostic core for FAIRSCAPE RO-Crate graph tools — provenance-graph
condensation, evidence graph construction, and AI-driven interpretation.

Both `mds_python` (FastAPI server, MongoDB-backed) and `fairscape-cli` (local
RO-Crate tool) consume this package and supply their own adapters for
entity lookup, result persistence, progress tracking, and software fetching.

Contents of this package (by module):

- `fairscape_graph_tools.models` — Pydantic models for `AnnotatedComputation`,
  `AnnotatedEvidenceGraph`, `EvidenceGraph` (plus assumption / error /
  evidence types).
- `fairscape_graph_tools.pipeline.graph_utils` — `@graph` traversal helpers
  (type detection, DAG ordering, ARK ID matching).
- `fairscape_graph_tools.pipeline.condense` — provenance-graph condensation
  (`condense_graph` for RO-Crate-level, `condense_evidence_graph_cache` for
  evidence-graph-level).
- `fairscape_graph_tools.pipeline.stats` — dataset statistics prompt formatting.
- `fairscape_graph_tools.pipeline.github` — GitHub source-code fetching.
- `fairscape_graph_tools.prompts` — system + synthesis prompts per audience.
- `fairscape_graph_tools.runtime` — async rate limiter, retry helper,
  Celery-safe event loop.
- `fairscape_graph_tools.ports` — `GraphSource` / `ResultSink` / `TaskTracker`
  / `SoftwareFetcher` Protocol definitions.
- `fairscape_graph_tools.condenser` — `Condenser` orchestrator.
- `fairscape_graph_tools.interpreter` — `Interpreter` orchestrator.
- `fairscape_graph_tools.evidence_graph_builder` — `EvidenceGraphBuilder`
  orchestrator.

This package was renamed from `fairscape_interpret` on 2026-04-21 to reflect
the broader scope beyond AI interpretation. See `MIGRATION.md` and
`EVIDENCE_GRAPH_MIGRATION.md` for migration history and pending work.
