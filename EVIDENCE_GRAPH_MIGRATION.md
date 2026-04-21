# Evidence-Graph Migration + Package Rename — Running Tracking Doc

**Status:** Phase 0 done — rename landed. Next: Phase 1 (move `EvidenceGraph` + extract pure projection)
**Last updated:** 2026-04-21
**Driver:** Justin Niestroy (jniestroy@gmail.com)

> This is the **running** handoff doc for the evidence-graph migration and the `fairscape_interpret` → `fairscape_graph_tools` rename. Update as you finish tasks — check boxes, record decisions, note surprises. Future-you will re-read this cold.
>
> **Canonical plan (static):** `/Users/justin/.claude/plans/snug-bubbling-nebula.md`
> **Sibling doc (interpret migration, done):** `./MIGRATION.md`

---

## Why this refactor exists

FAIRSCAPE builds the standard (non-annotated) evidence graph on two surfaces:

- **Server** (`mds_python`): `FairscapeEvidenceGraphRequest.build_evidence_graph_for_node` → `EvidenceGraph.build_graph` (BFS over `identifierCollection`, optional condensation, hierarchical projection).
- **CLI** (`fairscape-cli`): `fairscape build evidence-graph` → `EvidenceGraphJSON` (parallel reimplementation; no condensation, no DatasetGroup, no ROCrate outputs unwrap, no `usedMLModel`, no dash-tolerant ARK matching).

Every server tweak requires a CLI re-derivation; in practice the CLI has drifted behind. Same ergonomic pain that drove the interpret migration (see `MIGRATION.md`).

**Goal:** extract the evidence-graph core into the same shared sibling package, which we are simultaneously renaming `fairscape_interpret` → `fairscape_graph_tools` to match the GitHub repo rename. Both server and CLI call into the shared builder; the CLI catches up automatically.

**Two simplifications accepted by the user (2026-04-21):**
- **Always condense.** Drop the `condense` / `condense_threshold` flag from the server endpoint, Celery task, `EvidenceGraphBuildRequest`, and any CLI surface. Threshold becomes an internal default (5).
- **Keep the current `EvidenceGraph` hierarchical output shape** (`@graph` as id-keyed dict, `outputs` list, recursive `generatedBy`/`usedDataset`). Don't switch to returning the condensed RO-Crate directly.

**Server behavior invariant:** stored docs and endpoint responses byte-identical except timestamps.
**CLI upgrade:** catches up to server-grade output (condensation, DatasetGroup, outputs, MLModel, dash-tolerant lookup).

---

## Architecture (target)

```
fairscape_graph_tools/                  (renamed from fairscape_interpret)
  src/fairscape_graph_tools/
    models/
      evidence_graph.py                 ← NEW: EvidenceGraph, EvidenceNode, EvidenceGraphCreate
    pipeline/
      evidence_graph.py                 ← NEW: pure projection node_cache → (graph_dict, outputs)
      condense.py                       existing; condense_evidence_graph_cache lives here
    ports.py                            widened: GraphSource.find_many, ResultSink.persist_evidence_graph
    evidence_graph_builder.py           ← NEW: EvidenceGraphBuilder orchestrator
```

**Port deltas (no new ports):**
- `GraphSource.find_many(ark_ids) -> dict[ark_id, dict]` — batch entity lookup
- `ResultSink.persist_evidence_graph(evidence_graph, source_node_id) -> str` — store + reverse link

**Orchestrator:** `EvidenceGraphBuilder(source, sink, *, condense_threshold=5)` with `.build(node_id, *, owner_email, name=None, description=None) -> str`.

---

## Phase progress

### Phase 0 — Rename `fairscape_interpret` → `fairscape_graph_tools` ✅ DONE (2026-04-21)

No behavior change. One coordinated set of commits (one per repo). SHAs recorded below.

- [x] `fairscape_interpret/` directory → `fairscape_graph_tools/`
- [x] `fairscape_graph_tools/pyproject.toml` — `name`, `[tool.setuptools.package-data]`
- [x] `fairscape_graph_tools/src/fairscape_interpret/` → `src/fairscape_graph_tools/`
- [x] Internal imports inside the pkg: `src/` tree, `scripts/phase3_e2e.sh`, `README.md`
- [x] `mds_python/pyproject.toml` — dep name + `[tool.uv.sources]`
- [x] `mds_python` source imports: `crud/{interpretation,condensation,interpret_adapters,fairscape_request}.py`, `models/{annotated_evidence_graph,annotated_computation}.py`, `tests/crud/test_interpretation.py`
- [x] `fairscape-cli/pyproject.toml` — dep name + `[tool.uv.sources]`
- [x] `fairscape-cli` source imports: `commands/interpret.py`, `interpret/{local_graph,local_sink,local_software,__init__}.py`
- [x] `.claude/settings.local.json` — no references found, nothing to update
- [x] Update `MIGRATION.md` with a rename note in Decisions log (appended, historical entries untouched)
- [x] Phase 0 smoke check passes

**Phase 0 smoke check:**
```bash
PYTHONPATH=/Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/fairscape_graph_tools/src:/Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/mds_python/mds/src python -c "
from fairscape_mds.crud import interpretation, condensation, fairscape_request, evidence_graph
from fairscape_graph_tools.condenser import Condenser
from fairscape_graph_tools.interpreter import Interpreter
from fairscape_graph_tools.pipeline.condense import condense_graph, condense_evidence_graph_cache
print('rename OK')
"
```

### Phase 1 — Move `EvidenceGraph` model + extract pure projection ⏸️ PENDING

- [ ] Create `fairscape_graph_tools/src/fairscape_graph_tools/models/evidence_graph.py` — `EvidenceGraph`, `EvidenceNode`, `EvidenceGraphCreate`. Strip `build_graph` method from `EvidenceGraph`.
- [ ] Create `fairscape_graph_tools/src/fairscape_graph_tools/pipeline/evidence_graph.py` — pure projection functions extracted from today's `EvidenceGraph` class:
  - `build_graph_dict(start_node_id, node_cache) -> (graph_dict, outputs)`
  - `_build_node_from_cache`, `_extract_referenced_ids`, `_process_used_dataset`, `_flatten_metadata`, `_is_rocrate`, `_get_rocrate_outputs`
- [ ] Shrink `mds_python/mds/src/fairscape_mds/models/evidence_graph.py` to a re-export shim. Keep `EvidenceGraphBuildRequest` and `list_evidence_graphs_from_db` (still Mongo-bound).

**Source material (original, 425 lines):**
```bash
git -C mds_python log --oneline -- mds/src/fairscape_mds/models/evidence_graph.py | head -5
```

### Phase 2 — Ports + `EvidenceGraphBuilder` + Mongo adapters ⏸️ PENDING

- [ ] `fairscape_graph_tools/src/fairscape_graph_tools/ports.py` — add `find_many` to `GraphSource`, `persist_evidence_graph` to `ResultSink`.
- [ ] `fairscape_graph_tools/src/fairscape_graph_tools/evidence_graph_builder.py` — `EvidenceGraphBuilder`. Internal flow: BFS via `find_many` → `condense_evidence_graph_cache` → `build_graph_dict` → assemble Pydantic model → `sink.persist_evidence_graph`.
- [ ] `mds_python/mds/src/fairscape_mds/crud/interpret_adapters.py`:
  - `MongoGraphSource.find_many` — batch `$in` find + `_flatten_metadata`
  - `MongoResultSink.persist_evidence_graph` — port the `StoredIdentifier` + `Permissions` + `hasEvidenceGraph` writes from today's `build_evidence_graph_for_node` (lines 163–210 of `crud/evidence_graph.py`)
  - `MongoResultSink.__init__` — add `owner_groups` kwarg (needed for `Permissions(group=...)`)
  - Update module docstring — adapters now serve both `Interpreter` and `EvidenceGraphBuilder`.

**Phase 2 smoke check:**
```bash
PYTHONPATH=/Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/fairscape_graph_tools/src python -c "
from fairscape_graph_tools.evidence_graph_builder import EvidenceGraphBuilder
from fairscape_graph_tools.models.evidence_graph import EvidenceGraph, EvidenceNode
from fairscape_graph_tools.pipeline.evidence_graph import build_graph_dict
from fairscape_graph_tools.ports import GraphSource, ResultSink
print('evidence-graph shared pkg imports OK')
"
```

### Phase 3 — Shrink server CRUD + drop `condense` flag ⏸️ PENDING

- [ ] `mds_python/.../crud/evidence_graph.py`:
  - `FairscapeEvidenceGraphRequest.build_evidence_graph_for_node(requesting_user, naan, postfix)` — drop `condense`, `condense_threshold`. Build 2 Mongo adapters, call `EvidenceGraphBuilder(source, sink).build(node_id, owner_email=..., name=..., description=...)`, round-trip via `get_evidence_graph` for the `FairscapeResponse` envelope.
  - Target size: ~90 lines (was 212).
  - `create_evidence_graph`, `get_evidence_graph`, `delete_evidence_graph`, `list_evidence_graphs` untouched.
- [ ] `mds_python/.../routers/evidence_graph.py` — drop `condense` + `condense_threshold` `Query` params from `initiate_build_evidence_graph_for_node_route`.
- [ ] `mds_python/.../worker.py` — drop `condense`, `condense_threshold` from `build_evidence_graph_task` signature and propagated calls.
- [ ] `mds_python/.../models/evidence_graph.py` shim — drop `condense` and `condense_threshold` from `EvidenceGraphBuildRequest`.

**Phase 3 acceptance:** byte-identical regression on a fixture crate. Only `dateCreated`/`dateModified` should differ between pre- and post-refactor stored docs.

### Phase 4 — CLI catchup ⏸️ PENDING

- [ ] `fairscape-cli/src/fairscape_cli/interpret/local_graph.py` — add `find_many(ark_ids) -> dict[ark_id, dict]`.
- [ ] `fairscape-cli/src/fairscape_cli/interpret/local_sink.py` — add `persist_evidence_graph(evidence_graph, source_node_id) -> str` that writes the JSON sidecar.
- [ ] `fairscape-cli/src/fairscape_cli/commands/build_commands.py` — rewire `generate_evidence_graph`:
  - Load via `ReadROCrateMetadata` (per `feedback_rocrate_loading.md`, `project_rocrate_content_url.md`)
  - `LocalGraphSource` + `LocalResultSink` → `EvidenceGraphBuilder.build(ark_id, ...)`
  - Keep HTML generation via `generate_evidence_graph_html(...)`
  - Keep `localEvidenceGraph` back-annotation
- [ ] Delete `fairscape-cli/src/fairscape_cli/datasheet_builder/evidence_graph/graph_builder.py` — `EvidenceGraphJSON` + `generate_evidence_graph_from_rocrate` are dead.
- [ ] Update `fairscape-cli/src/fairscape_cli/utils/build_utils.py:process_evidence_graph` to call the new path.
- [ ] Keep `datasheet_builder/evidence_graph/html_builder.py` (visualization) unchanged.

**Phase 4 acceptance:** `fairscape build evidence-graph <fixture> <ark>` sidecar matches server `StoredIdentifier.metadata.@graph` exactly.

### Phase 5 — Hardening ⏸️ PENDING

- [ ] Unit tests: `test_evidence_graph_projection.py` (fixture node_cache → expected output), `test_evidence_graph_builder.py` (fake source+sink, idempotency short-circuit).
- [ ] Server regression: byte-identical diff on a fixture crate.
- [ ] Tests for the new thin `crud/evidence_graph.py` (extend `tests/crud/test_evidence_graph.py` if present, else create).
- [ ] CLI E2E: computation-as-start-node, dataset-as-start-node, rocrate-root-as-start-node, >threshold sibling datasets → DatasetGroup.
- [ ] Cross-consistency: server vs CLI same fixture crate.

---

## Invariants — don't break these

- `FairscapeEvidenceGraphRequest.{create_evidence_graph, get_evidence_graph, delete_evidence_graph, list_evidence_graphs}` — untouched.
- `FairscapeEvidenceGraphRequest.build_evidence_graph_for_node(requesting_user, naan, postfix)` — same name; shorter signature (no `condense`, no `condense_threshold`).
- `POST /evidencegraph/build/ark:{NAAN}/{postfix}` — still 202, same response shape. `condense` / `condense_threshold` query params removed (accepted breaking change per user decision 2026-04-21).
- `EvidenceGraph` Pydantic model — same field set + alias map. `from fairscape_mds.models.evidence_graph import EvidenceGraph` still works via shim.
- Stored `StoredIdentifier` docs for evidence graphs — shape byte-identical to pre-refactor.
- `fairscape build evidence-graph <rocrate> <ark>` — same CLI signature; output now richer.
- **`fairscape_graph_tools` has NO dependency on `fairscape_mds`.** Never import the other way.

---

## Reference-code recovery

If in doubt about original behavior, look at git — don't re-derive.

| Original file | Recover with |
|---|---|
| `crud/evidence_graph.py` (212 lines) | `git -C mds_python show HEAD:mds/src/fairscape_mds/crud/evidence_graph.py` |
| `models/evidence_graph.py` (425 lines) | `git -C mds_python show HEAD:mds/src/fairscape_mds/models/evidence_graph.py` |
| `datasheet_builder/.../graph_builder.py` (243 lines) | `git -C fairscape-cli show HEAD:src/fairscape_cli/datasheet_builder/evidence_graph/graph_builder.py` |

Record the actual pre-refactor SHAs below as they're committed (so future-you has concrete anchors rather than "HEAD"):

- `mds_python` pre-rename SHA: `dbc9922` (branch `intepret`, with uncommitted `tests/crud/test_interpretation.py` changes)
- `fairscape_interpret` pre-rename SHA: `af53568` (branch `main`)
- `fairscape-cli` pre-rename SHA: `ab8d1ce` (branch `interpret`)

---

## Decisions log

- **2026-04-21** — Rename scope is "full rename in one plan" (local dir + pyproject + Python module + all import sites) rather than shim-based compat. Rationale: recent migration, import sites well-known, avoids lingering dual-name state.
- **2026-04-21** — CLI catchup is "upgrade `fairscape build evidence-graph` in place" rather than new subcommand. Rationale: preserves muscle memory; old `EvidenceGraphJSON` is dead weight not worth keeping.
- **2026-04-21** — Always-condense is a hard break: `condense` / `condense_threshold` removed from router Query params, Celery task signature, `EvidenceGraphBuildRequest`. No compat shim. Rationale: the condense=False code path is unreachable in practice (every caller defaulted to True) and keeping a dead branch muddies the builder.
- **2026-04-21** — Evidence-graph output shape stays hierarchical (`@graph` as id-keyed dict, `outputs` list). Don't pivot to "just return the condensed RO-Crate" — frontend would need coordination, and the hierarchical shape is what consumers actually read.
- **2026-04-21** — Reuse existing `condense_evidence_graph_cache` in `pipeline/condense.py` rather than building a new condenser. Evidence-graph builder is orthogonal to `Condenser` (the interpret-pipeline's RO-Crate-level condensation orchestrator); they happen to share the same condensation module but otherwise don't interact.
- **2026-04-21** — `EvidenceGraphBuildRequest` stays in `mds_python` (Celery/async-task-shaped, server-only). Don't move to shared pkg.
- **2026-04-21** — `EvidenceGraphCreate` moves to shared pkg (it's a domain input model used by both the router and any future CLI create path). Low-risk move.
- **2026-04-21** — `interpret_adapters.py` keeps its name even though it now also serves `EvidenceGraphBuilder`. Rename is churn; docstring note is sufficient. Revisit if a third orchestrator lands.
- **2026-04-21** — Extend `LocalResultSink` and `LocalGraphSource` with the new methods rather than creating a separate `LocalEvidenceGraphSink`/`LocalEvidenceGraphSource`. Matches the "adapters implement all port methods" precedent from the interpret phase.

---

## Handoff checklist for next-session-Claude

1. Read this file top-to-bottom.
2. Read the canonical plan: `/Users/justin/.claude/plans/snug-bubbling-nebula.md`.
3. Read the sibling doc `./MIGRATION.md` — the interpret migration is the pattern being mirrored.
4. Run the appropriate smoke check for the current phase. If it fails, diagnose before proceeding.
5. Pick the lowest-numbered unchecked task in the current phase. Open any original file being ported via `git show HEAD:<path>` before writing new code.
6. After finishing each task: check the box, add `(commit <sha>)` if committed, update the Decisions log if you deviated from plan.
7. One task, one commit where practical.

---

## File map (post-rename, post-migration)

```
fairscape-repos/
├── fairscape_graph_tools/                         (renamed from fairscape_interpret)
│   ├── pyproject.toml                             (name = "fairscape-graph-tools")
│   ├── MIGRATION.md                               (interpret migration, done)
│   ├── EVIDENCE_GRAPH_MIGRATION.md                ← this file
│   └── src/fairscape_graph_tools/
│       ├── ports.py                               (widened: find_many, persist_evidence_graph)
│       ├── condenser.py                           (unchanged)
│       ├── interpreter.py                         (unchanged)
│       ├── evidence_graph_builder.py              ← NEW
│       ├── models/
│       │   ├── annotated_computation.py           (unchanged)
│       │   ├── annotated_evidence_graph.py        (unchanged)
│       │   └── evidence_graph.py                  ← NEW
│       └── pipeline/
│           ├── condense.py                        (unchanged)
│           ├── evidence_graph.py                  ← NEW
│           └── ...                                (unchanged)
│
├── mds_python/mds/src/fairscape_mds/
│   ├── crud/
│   │   ├── evidence_graph.py                      (212 → ~90 lines)
│   │   └── interpret_adapters.py                  (extended with new adapter methods)
│   ├── routers/evidence_graph.py                  (dropped condense query params)
│   ├── worker.py                                  (dropped condense task params)
│   └── models/evidence_graph.py                   (becomes re-export shim)
│
└── fairscape-cli/src/fairscape_cli/
    ├── commands/build_commands.py                 (rewired generate_evidence_graph)
    ├── interpret/
    │   ├── local_graph.py                         (+ find_many)
    │   └── local_sink.py                          (+ persist_evidence_graph)
    ├── datasheet_builder/evidence_graph/
    │   ├── graph_builder.py                       DELETED
    │   └── html_builder.py                        (unchanged)
    └── utils/build_utils.py                       (process_evidence_graph rewired)
```
