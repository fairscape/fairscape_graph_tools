# Interpretation Pipeline Migration — Running Tracking Doc

**Status:** Phase 1 structural complete — regression run pending
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

### Phase 1 — Ports + Orchestrators + Mongo adapters 🟢 STRUCTURAL COMPLETE (regression pending)

Port definitions, `Condenser` + `Interpreter` orchestrators, Mongo adapters, thin server CRUD wrappers. All six tasks landed; Phase-1 acceptance (byte-identical regression run on a real crate) still outstanding.

Before any task in this phase: open the original source to see what you're porting.

```bash
git -C /Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/mds_python show 6ec8eb6:mds/src/fairscape_mds/crud/interpretation.py
git -C /Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/mds_python show 6ec8eb6:mds/src/fairscape_mds/crud/condensation.py
```

Tasks (do in order):

- [x] **#6 — `fairscape_interpret/src/fairscape_interpret/ports.py`** — 4 Protocols. *(done)*
  - `GraphSource.find_entity(ark) -> dict | None`
  - `GraphSource.find_dataset_stats(arks: Iterable[str]) -> dict[str, dict]`
  - `GraphSource.build_full_graph(rocrate_id) -> list[dict]`
  - `ResultSink.persist_condensed(condensed_id, condensed_metadata, source_rocrate_id, stats) -> str`
  - `ResultSink.persist_aeg(aeg, rocrate_id, step_annotations) -> str`
  - `TaskTracker.update(updates: dict) -> None`
  - `TaskTracker.update_computation_status(comp_id, updates) -> None`
  - `TaskTracker.increment_completed() -> None`
  - `TaskTracker.push_llm_result(label, raw_output) -> None`
  - `SoftwareFetcher.fetch(software_node) -> str`
  - **Note:** initial task config is NOT on `TaskTracker`. Server adapter reads
    `asyncCollection` to build the `InterpretConfig` before constructing
    `Interpreter`; CLI builds it from Click args. Keeps the tracker write-only.
- [x] **#7 — `fairscape_interpret/src/fairscape_interpret/condenser.py`** — `Condenser` class. *(done)*
  - `__init__(source, sink, *, threshold=5, max_member_ids=0)`
  - `condense(rocrate_id) -> str` — raises if already condensed (matches `/condense` endpoint behavior).
  - `ensure_condensed(rocrate_id) -> tuple[list[dict], str, dict]` — returns `(graph, condensed_id, root_node)`. Handles 3 cases: pointer, self-condensed, fresh condense.
  - Internal `_build_and_persist` returns graph + id so sidecar-only sinks don't need a round-trip through `find_entity`.
  - Smoke-tested with fake source/sink — all three ensure-paths exercised.
- [x] **#8 — Pipeline modules** extracted from `interpretation.py`:
  - [x] `pipeline/synthesize.py` — `GraphSynthesisResult`, `build_synthesis_prompt`, `synthesize_graph(tracker, root_node, step_annotations, llm_model, temperature, *, rate_limiter=None, graph_dict=None)`. Takes `TaskTracker` instead of `self`.
  - [x] `pipeline/build.py` — `build_aeg(rocrate_id, graph, step_annotations, synthesis, audience_perspectives, llm_model, temperature) -> AnnotatedEvidenceGraph`. Pure — no persistence, no ports.
  - [x] **`pipeline/annotate.py`** — done. Extracted from `interpretation.py` lines 350–621:
    - `build_computation_prompt(computation, software_cache, index, stats_cache=None)` — was `_build_computation_prompt` (350–443)
    - `llm_to_annotated(llm_result, comp_id, llm_model, temperature)` — was `_llm_to_annotated` (444–506)
    - `annotate_single_computation(tracker, software, graph, computation, software_cache, index, llm_model, temperature, stats_cache=None)` — was `_annotate_single_computation` (507–544). Takes `TaskTracker` + (optionally) `SoftwareFetcher`.
    - `annotate_computations_async(tracker, computations, software_cache, index, llm_model, temperature, max_workers=2, stats_cache=None, rate_limiter=None)` — was `_annotate_computations_async` (545–603). Uses `tracker.update_computation_status` and `tracker.increment_completed` in place of direct Mongo `$set`/`$inc`.
    - `annotate_computations_parallel(...)` — sync wrapper via `run_async` (605–621).
  - **Note on prefetch functions:** `prefetch_all_software` and `prefetch_dataset_statistics` in the server class are thin loops over `SoftwareFetcher.fetch` and `GraphSource.find_dataset_stats`. They can stay inside `Interpreter` (the orchestrator) — no need to put them in `pipeline/`.
- [x] **#9 — `fairscape_interpret/src/fairscape_interpret/interpreter.py`** — `Interpreter` class. *(done)*
  - `__init__(self, graph: GraphSource, sink: ResultSink, tracker: TaskTracker, software: SoftwareFetcher, condenser: Condenser, config: InterpretConfig)`
  - `InterpretConfig` dataclass: llm_model, temperature, max_workers, rate_limiter_max_requests, rate_limiter_window_seconds
  - `run_sync(rocrate_id) -> str` and `run_async(rocrate_id) -> str` (the async variant delegates to `asyncio.to_thread(self.run_sync, ...)` — see decision below)
  - `find_computations`, `_prefetch_software`, `_prefetch_stats` are methods on `Interpreter` (not in `pipeline/`), matching the plan note.
- [x] **#10 — `mds_python/mds/src/fairscape_mds/crud/interpret_adapters.py`** — 4 Mongo-backed adapters. *(done)*
  - `MongoGraphSource(FairscapeRequest)` — wraps `flexibleFind`, batch `identifierCollection.find`, reuses `build_full_graph_for_rocrate` via a composed `FairscapeCondensationRequest`.
  - `MongoResultSink(config, *, owner_email=...)` — both `persist_condensed` and `persist_aeg`. Owns `StoredIdentifier`/`Permissions`/`PublicationStatusEnum` wrapping and back-pointer writes.
  - `MongoTaskTracker(config, task_guid)` — `asyncCollection` writes.
  - `ServerSoftwareFetcher(config, user_token)` — `/software/download/` with `baseUrl → internalUrl` rewrite; GitHub fallback via shared `prefetch_software_code`.
- [x] **#11 — Shrink server CRUD classes** to thin wrappers. *(done; mds_python dbc9922)*
  - `interpretation.py`: 1066 → 88 lines. `FairscapeInterpretationRequest.interpret_rocrate(task_guid, user_token)` loads task config, builds 4 Mongo adapters + `Condenser` + `Interpreter`, calls `interpreter.run_sync(rocrate_id)`. Module-level re-exports (`_build_index`, `_is_computation`, `_is_rocrate_root`, `_resolve_refs`, `prefetch_software_code`, `GraphSynthesisResult`, `httpx`) preserve the import surface the test suite expects.
  - `condensation.py`: 317 → 251 lines. `FairscapeCondensationRequest.condense_rocrate(...)` pre-checks for an existing condensed crate (preserves 409 text), builds `MongoGraphSource` + `MongoResultSink`, delegates to `Condenser.condense`, maps `ValueError` → 404 and other exceptions → 500. `MongoResultSink.last_stats` carries condensation stats back out.
  - `FairscapeCondensationRequest.delete_condensed_rocrate` and `build_full_graph_for_rocrate` are untouched (the former is Mongo-specific, the latter is still consumed by `MongoGraphSource`).
  - Lazy `from fairscape_mds.crud.interpret_adapters import ...` inside `condense_rocrate` breaks the `condensation.py` ↔ `interpret_adapters.py` import cycle without restructuring the adapter module.

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

- **2026-04-20** — `synthesize_graph` takes `graph_dict` (keyword) not `index`. The server calls it as `index=index` but the parameter is the same thing (id → node dict). Keep this in mind when wiring the `Interpreter` orchestrator — pass `_build_index(graph)` as `graph_dict=...`.
- **2026-04-20** — Audience syntheses stay disabled in the loop (commented-out `for aud in AUDIENCE_CONFIGS`). Match server current behavior exactly; do not re-enable without Justin's sign-off.
- **2026-04-20** — `prefetch_all_software` and `prefetch_dataset_statistics` stay in the `Interpreter` orchestrator (not split into `pipeline/`). They are one-liners over the ports.
- **2026-04-20** — `GraphSynthesisResult` lives in `pipeline/synthesize.py`, not in a shared models module. `build.py` imports it from there.
- **2026-04-20** — `annotate_single_computation` signature dropped the optional `software`/`graph` parameters listed in the Phase 1 #8 plan. Rationale: the original `_annotate_single_computation` never consumed a `SoftwareFetcher` or raw `graph` — `software_cache` and `index` already cover its needs, and the orchestrator is responsible for prefetching. Added YAGNI-style; can be reintroduced if lazy-fetch ever replaces the prefetch pass.
- **2026-04-20** — `MAX_PROMPT_DATASETS = 3` is defined as a module-level constant in `pipeline/annotate.py`. `MAX_STATS_COLUMNS` stays in `mds_python/interpretation.py` for now — it is unused by the shared code path.
- **2026-04-20** — `InterpretConfig` omits the `persona` field listed in the plan. Rationale: the datasci persona is the only one exercised today (audience syntheses are intentionally disabled per the earlier decision) and adding a field we do not read is YAGNI. Reintroduce when audience syntheses are re-enabled.
- **2026-04-20** — `Interpreter.run_async` is an `asyncio.to_thread` wrapper over `run_sync`, not a genuinely async pipeline. Rationale: the internal `annotate_computations_async` / `synthesize_graph` paths already manage their own event loops (via `fairscape_interpret.runtime.run_async`), so nesting them inside the caller's loop would collide. The wrapper preserves the planned `async def` interface for callers that are already inside an event loop without requiring a second implementation. Rewrite to a native async flow only when a consumer actually demands one.
- **2026-04-20** — `MongoGraphSource` composes a `FairscapeCondensationRequest` for `build_full_graph` rather than inheriting from it. Rationale: inheritance would surface `condense_rocrate`/`delete_condensed_rocrate` on the adapter, which aren't part of the `GraphSource` port and don't belong on a port-shaped object. `_flatten_metadata` is duplicated from `condensation.py` (5 lines) to avoid reaching into a private helper across the module boundary.
- **2026-04-20** — `MongoResultSink.persist_condensed` ignores the separate `stats` argument because by the time it runs, `Condenser._assemble_metadata` has already written `evi:condensationStats` onto the root node. The extra arg is part of the port contract; preserving its position keeps the door open for sinks that want to log stats separately without rummaging through the metadata.
- **2026-04-20** — `MongoTaskTracker.update_computation_status` constructs the positional-`$`-filter payload from the `updates` dict rather than hard-coding `status` + `error`, so callers can add fields (e.g. `attempt_count`) without changing the adapter. Mirrors the shape the shared pipeline already sends.
- **2026-04-20** — `MongoResultSink` grew a `self.last_stats` field, populated in `persist_condensed`. This is an adapter-local extension (not a new port method) so the thin-wrapper `condense_rocrate` can return `{"condensed_id", "stats"}` to the Celery worker without widening the `ResultSink` contract or re-reading the condensed doc.
- **2026-04-20** — Seven unit tests in `tests/crud/test_interpretation.py` call methods on `FairscapeInterpretationRequest` that no longer exist after Phase 1 #11 (`_build_computation_prompt`, `ensure_condensed`, `_update_task`, `find_computations`). The file still *imports* cleanly — the re-export list in `interpretation.py` was chosen to match the test's `from ... import` line, and `import httpx` is retained so `patch("fairscape_mds.crud.interpretation.httpx.get")` still resolves. These seven tests are redundant with the Phase 3 plan to add pipeline-level tests against `fairscape_interpret` directly; left to fail rather than kept alive with method shims, to avoid ossifying the legacy shape.

## Next-session smoke check (post Phase 1 #8 — all three pipeline modules)

```bash
PYTHONPATH=/Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/fairscape_interpret/src python -c "
from fairscape_interpret.pipeline.synthesize import GraphSynthesisResult, synthesize_graph
from fairscape_interpret.pipeline.build import build_aeg
from fairscape_interpret.pipeline.annotate import (
    build_computation_prompt, annotate_computations_parallel,
)
from fairscape_interpret.condenser import Condenser
from fairscape_interpret.ports import GraphSource, ResultSink, TaskTracker, SoftwareFetcher
print('all pipeline + orchestrator + port imports OK')
"
```

