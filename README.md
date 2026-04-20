# fairscape-interpret

Storage-agnostic core of the FAIRSCAPE AI-interpretation pipeline.

Both `mds_python` (FastAPI server, MongoDB-backed) and `fairscape-cli` (local
RO-Crate tool) consume this package and supply their own adapters for
entity lookup, result persistence, progress tracking, and software fetching.

Contents of this package (by module):

- `fairscape_interpret.models` — Pydantic models for `AnnotatedComputation`
  and `AnnotatedEvidenceGraph` (plus assumption / error / evidence types).
- `fairscape_interpret.pipeline.graph_utils` — `@graph` traversal helpers
  (type detection, DAG ordering, ARK ID matching).
- `fairscape_interpret.pipeline.condense` — provenance-graph condensation.
- `fairscape_interpret.pipeline.stats` — dataset statistics prompt formatting.
- `fairscape_interpret.pipeline.github` — GitHub source-code fetching.
- `fairscape_interpret.prompts` — system + synthesis prompts per audience.
- `fairscape_interpret.runtime` — async rate limiter, retry helper,
  Celery-safe event loop.

Phase 0 of the extraction: helpers, prompts, models moved here; the
`Condenser` / `Interpreter` orchestrators and the `GraphSource` /
`ResultSink` / `TaskTracker` / `SoftwareFetcher` ports land in later phases.
