# Interpretation Pipeline Migration — Running Tracking Doc

**Status:** Phase 1 in progress
**Last updated:** 2026-04-20
**Driver:** Justin Niestroy (jniestroy@gmail.com)

> This is the **running** handoff doc. Update it as you finish tasks — check boxes, record decisions, note surprises. Future-you will re-read this cold.

---

## Why this refactor exists

FAIRSCAPE has two consumers for AI-driven RO-Crate interpretation:

- **`mds_python`** (FastAPI server) — runs interpretation as a Celery task, persists results to MongoDB.
- **`fairscape-cli`** (planned) — user runs `fairscape interpret <rocrate-path>` locally, writes a sidecar JSON. Never modifies the source crate.

Previously, all interpretation logic lived in `mds_python/.../crud/interpretation.py` (~1650 lines) and `crud/condensation.py` (~1020 lines), tightly coupled to Mongo. Justin iterates frequently on prompt wording, scoring, and graph-condensation heuristics — duplicating the code into the CLI would mean every tweak requires two edits.

**Goal:** extract the storage-agnostic core into a shared sibling package `fairscape_interpret/`. Both consumers depend on it via editable install. Each supplies its own adapters.

**Canonical plan (static, does not change):** `/Users/justin/.claude/plans/in-fairscape-server-mds-python-i-breezy-noodle.md`
This file tracks *active progress* against that plan.

---

## Architecture

Port-and-adapter (hexagonal). Four `Protocol`s in `fairscape_interpret/ports.py`:

| Port | Server impl (Mongo) | CLI impl (local) |
|---|---|---|
| `GraphSource` | crawls `identifierCollection` | loads primary + reference crates from disk |
| `ResultSink` | inserts `StoredIdentifier` docs + back-pointers | writes sidecar JSON |
| `TaskTracker` | `$set`/`$push` on `asyncCollection` | stdout + optional JSONL trace |
| `SoftwareFetcher` | `/software/download/` + bearer token | local `contentUrl` → GitHub → placeholder |

Two orchestrators (also in the shared package):

- **`Condenser`** — `(graph, sink)` → condensed graph. `ensure_condensed(rocrate_id)` short-circuits if already condensed.
- **`Interpreter`** — `(graph, sink, tracker, software, condenser, config)`; runs condense → annotate → synthesize → build stages. Has `run_sync(rocrate_id)` and `run_async(rocrate_id)`.

---

## Phase progress

### Phase 0 — Skeleton ✅ DONE (2026-04-20)

- `mds_python` commit: `c07cf8a` on branch `intepret` (pre-refactor baseline: `6ec8eb6`)
- `fairscape_interpret` commit: `6ddf89a` (initial)


Pure helpers, prompts, runtime utilities, models, condensation helpers extracted.

- [x] `fairscape_interpret/` package created as sibling at repo root
- [x] `mds_python/pyproject.toml` — added `[tool.uv.sources]` editable dep
- [x] Pure helpers migrated: `graph_utils`, `stats`, `github`, `rate_limiter`, `agent_retry`, `event_loop`, `condense`
- [x] Prompts migrated: `datasci`, `biostat`, `clinician` + `AUDIENCE_CONFIGS`
- [x] Pydantic models moved: `annotated_computation.py`, `annotated_evidence_graph.py`
- [x] mds_python model files converted to re-export shims (44 + 22 lines)
- [x] `interpretation.py` 1650 → 1066 lines; `condensation.py` 1020 → 317 lines
- [x] `fairscape_request.py` imports `flexible_ark_query` from shared pkg
- [x] Smoke check: all imports resolve

### Phase 1 — Ports + Orchestrators + Mongo adapters 🟡 IN PROGRESS

Port definitions, `Condenser` + `Interpreter` orchestrators, Mongo adapters, thin server CRUD wrappers.

Before any task in this phase: open the original source to see what you're porting.

```bash
git -C /Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/mds_python show 6ec8eb6:mds/src/fairscape_mds/crud/interpretation.py
git -C /Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/mds_python show 6ec8eb6:mds/src/fairscape_mds/crud/condensation.py
```

Tasks (do in order):

- [ ] **#6 — `fairscape_interpret/src/fairscape_interpret/ports.py`** — 4 Protocols.
  - `GraphSource.find_entity(ark) -> dict | None`
  - `GraphSource.find_dataset_stats(arks) -> dict[str, dict]`
  - `GraphSource.build_full_graph(rocrate_id) -> list[dict]`
  - `ResultSink.persist_condensed(graph, source_id, stats) -> str`
  - `ResultSink.persist_aeg(aeg, rocrate_id, step_annotation_ids) -> str`
  - `TaskTracker.update(updates: dict) -> None`
  - `TaskTracker.update_computation_status(comp_id, updates) -> None`
  - `TaskTracker.increment_completed() -> None`
  - `TaskTracker.push_llm_result(label, output) -> None`
  - `TaskTracker.read() -> dict`
  - `SoftwareFetcher.fetch(software_node) -> str`
- [ ] **#7 — `fairscape_interpret/src/fairscape_interpret/condenser.py`** — `Condenser` class.
  - `__init__(self, source: GraphSource, sink: ResultSink, threshold=5, max_member_ids=0)`
  - `condense(rocrate_id) -> str` — build graph → condense → persist, return condensed-id
  - `ensure_condensed(rocrate_id) -> str` — if already condensed (check source or known suffix), return existing id; else `condense()`
- [ ] **#8 — Pipeline modules** extracted from `interpretation.py`:
  - `pipeline/annotate.py` — `build_computation_prompt`, `annotate_single_computation`, `annotate_computations_async`, `annotate_computations_parallel`. Takes `TaskTracker` + `SoftwareFetcher` + `GraphSource` as args, not `self`.
  - `pipeline/synthesize.py` — `synthesize_graph`, `GraphSynthesisResult` model.
  - `pipeline/build.py` — `build_aeg(root_node, step_annotations, synthesis_result, audience_perspectives) -> AnnotatedEvidenceGraph`. **No persistence.**
- [ ] **#9 — `fairscape_interpret/src/fairscape_interpret/interpreter.py`** — `Interpreter` class.
  - `__init__(self, graph: GraphSource, sink: ResultSink, tracker: TaskTracker, software: SoftwareFetcher, condenser: Condenser, config: InterpretConfig)`
  - `InterpretConfig` dataclass: llm_model, temperature, persona, max_concurrency, rate_limiter params
  - `run_sync(rocrate_id) -> str` and `run_async(rocrate_id) -> str` — returns stored AEG id
- [ ] **#10 — `mds_python/mds/src/fairscape_mds/crud/interpret_adapters.py`** — 4 Mongo-backed adapters.
  - `MongoGraphSource(FairscapeRequest)` — wraps `flexibleFind`, batch `identifierCollection.find`, reuses `build_full_graph_for_rocrate` logic from `condensation.py`.
  - `MongoResultSink(config)` — both `persist_condensed` and `persist_aeg`. Owns `StoredIdentifier`/`Permissions`/`PublicationStatusEnum` wrapping. Writes back-pointers.
  - `MongoTaskTracker(config, task_guid)` — `asyncCollection` writes.
  - `ServerSoftwareFetcher(config, user_token)` — `/software/download/` with `baseUrl → internalUrl` rewrite; GitHub fallback.
- [ ] **#11 — Shrink server CRUD classes** to thin wrappers (~80 lines each).
  - `FairscapeInterpretationRequest.interpret_rocrate(task_guid, user_token)` → build 4 Mongo adapters → `Interpreter.run_sync(rocrate_id_from_task)`
  - `FairscapeCondensationRequest.condense_rocrate(rocrate_id, ...)` → build `MongoGraphSource` + `MongoResultSink` → `Condenser.condense(rocrate_id)`
  - `FairscapeCondensationRequest.delete_condensed_rocrate(...)` — keep as-is (Mongo-specific deletion, no shared equivalent).

**Phase 1 acceptance:** run both `interpret_rocrate` and `condense_rocrate` on an existing crate; outputs byte-identical to pre-refactor except timestamps. No new behavior.

### Phase 2 — CLI adoption ⏸️ PENDING

- [ ] `fairscape-cli/src/fairscape_cli/interpret/command.py` — Click subcommand.
- [ ] `LocalGraphSource` — loads primary + reference crates via existing `ReadROCrateMetadata`.
- [ ] `LocalResultSink` — sidecar JSON only; optional `--save-condensed`.
- [ ] `InMemoryTaskTracker` — stdout progress + optional `--debug-llm` JSONL trace.
- [ ] `LocalSoftwareFetcher` — local path → GitHub → `[source not fetched]` placeholder.
- [ ] Register `fairscape interpret` subcommand in `__main__.py`.
- [ ] Add deps in `fairscape-cli/pyproject.toml`: `fairscape_interpret`, `pydantic-ai`, `httpx`.

**Phase 2 acceptance:** server vs CLI on same crate — AEG sidecars diff only in persistence wrapping (no `StoredIdentifier` envelope, no reverse links).

### Phase 3 — Hardening ⏸️ PENDING

- [ ] Unit tests in shared pkg: `_compute_dag_order`, `flexible_ark_query`, `condense_graph` on fixtures.
- [ ] Regression (server): before/after diff on a fixture crate.
- [ ] End-to-end (CLI): primary-only, primary + refs, pre-condensed, non-condensed.
- [ ] CI job: interpret (server + CLI) against fixture on every PR.

---

## Reference-code recovery

If in doubt about what the original server did — look. Don't re-derive.

| Original file | Recover with |
|---|---|
| `interpretation.py` (1650 lines) | `git -C mds_python show 6ec8eb6:mds/src/fairscape_mds/crud/interpretation.py` |
| `condensation.py` (1020 lines) | `git -C mds_python show 6ec8eb6:mds/src/fairscape_mds/crud/condensation.py` |
| `annotated_computation.py` | `git -C mds_python show 6ec8eb6:mds/src/fairscape_mds/models/annotated_computation.py` |
| `annotated_evidence_graph.py` | `git -C mds_python show 6ec8eb6:mds/src/fairscape_mds/models/annotated_evidence_graph.py` |

All pre-refactor code is safely in git. Nothing is actually deleted.

---

## Invariants — don't break these

- `FairscapeInterpretationRequest.interpret_rocrate(task_guid, user_token)` and `FairscapeCondensationRequest.condense_rocrate(rocrate_id, ...)` keep their existing signatures — routers and the Celery worker call them.
- Model shims `fairscape_mds.models.{annotated_computation, annotated_evidence_graph}` keep re-exporting the same public names — external code may still import via those paths.
- `flexible_ark_query` is importable from both `fairscape_interpret.pipeline.graph_utils` AND `fairscape_mds.crud.fairscape_request` (the latter re-exports).
- **`fairscape_interpret` has NO dependency on `fairscape_mds`.** Never import the other way. If you catch yourself reaching for Mongo, FastAPI, Celery, or `StoredIdentifier` from inside the shared pkg, stop — that belongs in an adapter.
- Phase 1 is structural only. No behavior changes. Regression output must match.

---

## Handoff checklist for next-session-Claude

1. Read this file top-to-bottom.
2. Read the canonical plan: `/Users/justin/.claude/plans/in-fairscape-server-mds-python-i-breezy-noodle.md`.
3. Run the smoke check below. If it fails, stop and diagnose — don't proceed with new work on a broken base.
4. Pick the lowest-numbered unchecked Phase-1 task. Open the original file for that area via `git show 6ec8eb6:<path>` before writing new code.
5. After finishing each task: check the box here, add `(commit <sha>)`, then commit. One task, one commit.
6. If you change direction from the plan, record the decision here under a new "Decisions" section with date + rationale.

### Smoke check

```bash
PYTHONPATH=/Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/fairscape_interpret/src:/Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/mds_python/mds/src python -c "
from fairscape_mds.crud import interpretation, condensation, fairscape_request
from fairscape_mds.models.annotated_computation import AnnotatedComputation
from fairscape_mds.models.annotated_evidence_graph import AnnotatedEvidenceGraph
from fairscape_interpret.pipeline.condense import condense_graph
from fairscape_interpret.pipeline.graph_utils import flexible_ark_query, _compute_dag_order
print('OK')
"
```

---

## File map (current, post-Phase-0)

```
fairscape-repos/
├── fairscape_interpret/                    (sibling pkg; editable install)
│   ├── pyproject.toml
│   ├── MIGRATION.md                        ← this file
│   └── src/fairscape_interpret/
│       ├── __init__.py
│       ├── py.typed
│       ├── models/
│       │   ├── annotated_computation.py
│       │   └── annotated_evidence_graph.py
│       ├── pipeline/
│       │   ├── graph_utils.py              (+ flexible_ark_query)
│       │   ├── stats.py
│       │   ├── github.py
│       │   └── condense.py
│       ├── prompts/
│       │   ├── datasci.py
│       │   ├── biostat.py
│       │   └── clinician.py
│       └── runtime/
│           ├── rate_limiter.py
│           ├── agent_retry.py
│           └── event_loop.py
│
└── mds_python/mds/src/fairscape_mds/
    ├── crud/
    │   ├── interpretation.py               (1066 lines; was 1650)
    │   ├── condensation.py                 (317 lines; was 1020)
    │   └── fairscape_request.py            (imports flexible_ark_query from shared pkg)
    └── models/
        ├── annotated_computation.py        (44-line shim)
        └── annotated_evidence_graph.py     (22-line shim)
```

### Files to create in Phase 1

```
fairscape_interpret/src/fairscape_interpret/
├── ports.py                                ← #6 — 4 Protocols
├── condenser.py                            ← #7
├── interpreter.py                          ← #9
└── pipeline/
    ├── annotate.py                         ← #8
    ├── synthesize.py                       ← #8
    └── build.py                            ← #8

mds_python/mds/src/fairscape_mds/crud/
└── interpret_adapters.py                   ← #10 — 4 Mongo adapters
```

---

## Decisions log

*(empty — add dated entries here if you diverge from the canonical plan)*
