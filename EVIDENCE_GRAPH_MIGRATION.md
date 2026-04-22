# Evidence-Graph Migration + Package Rename — Running Tracking Doc

**Status:** Phase 4 done — CLI's `fairscape build evidence-graph` and `utils.build_utils.process_evidence_graph` now call `EvidenceGraphBuilder` through `LocalGraphSource`/`LocalResultSink`; the old `EvidenceGraphJSON` + `generate_evidence_graph_from_rocrate` are deleted. Next: Phase 5 (hardening). Byte-identical fixture-crate regression + server-vs-CLI cross-check still TODO (needs a live Mongo + server run).
**Last updated:** 2026-04-22
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

No behavior change. One coordinated set of commits (one per repo):
- `fairscape_graph_tools` (branch `main`): `542c9a2`
- `mds_python` (branch `intepret`): `6642227`
- `fairscape-cli` (branch `interpret`): `f2b9eb2`

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

### Phase 1 — Move `EvidenceGraph` model + extract pure projection ✅ DONE (2026-04-21)

Commits:
- `fairscape_graph_tools` @ `main`: `0db0525`
- `mds_python` @ `intepret`: `6e0c5d8`


- [x] Create `fairscape_graph_tools/src/fairscape_graph_tools/models/evidence_graph.py` — `EvidenceGraph`, `EvidenceNode`, `EvidenceGraphCreate`. Stripped `build_graph` method from the shared `EvidenceGraph`.
- [x] Create `fairscape_graph_tools/src/fairscape_graph_tools/pipeline/evidence_graph.py` — pure projection:
  - `build_graph_dict(start_node_id, node_cache) -> (graph_dict, outputs)`
  - `_build_node_from_cache`, `_extract_referenced_ids`, `_process_used_dataset`, `_flatten_metadata`, `_is_rocrate`, `_get_rocrate_outputs`
- [x] Shrink `mds_python/mds/src/fairscape_mds/models/evidence_graph.py` to a shim. Re-exports `EvidenceGraphCreate` / `EvidenceNode` straight, and subclasses shared `EvidenceGraph` server-side to carry a temporary Mongo-aware `build_graph` (deleted in Phase 3 when `crud/evidence_graph.py` starts calling `EvidenceGraphBuilder`). `EvidenceGraphBuildRequest` + `list_evidence_graphs_from_db` stay in the shim.

**Source material (original, 425 lines):**
```bash
git -C mds_python log --oneline -- mds/src/fairscape_mds/models/evidence_graph.py | head -5
```

### Phase 2 — Ports + `EvidenceGraphBuilder` + Mongo adapters ✅ DONE (2026-04-22)

- [x] `fairscape_graph_tools/src/fairscape_graph_tools/ports.py` — add `find_many` to `GraphSource`, `persist_evidence_graph` to `ResultSink`.
- [x] `fairscape_graph_tools/src/fairscape_graph_tools/evidence_graph_builder.py` — `EvidenceGraphBuilder`. Internal flow: BFS via `find_many` → `condense_evidence_graph_cache` → `_build_node_from_cache` → assemble Pydantic model → `sink.persist_evidence_graph`. Mirrors the shim's pre-condense output derivation (see Phase 2 decision below) rather than delegating to `build_graph_dict`, so Phase 3's byte-identical regression stays intact.
- [x] `mds_python/mds/src/fairscape_mds/crud/interpret_adapters.py`:
  - `MongoGraphSource.find_many` — batch `$in` find + pipeline's `_flatten_metadata` (imported as `_flatten_for_evidence_graph` so it doesn't collide with the module-local adapter flatten, which has different semantics).
  - `MongoResultSink.persist_evidence_graph` — ports the `StoredIdentifier` + `Permissions` + `hasEvidenceGraph` writes from today's `build_evidence_graph_for_node` (lines 163–210 of `crud/evidence_graph.py`).
  - `MongoResultSink.__init__` — added `owner_groups` kwarg; `persist_evidence_graph` picks `owner_groups[0]` for the `Permissions(group=...)` field, matching today's CRUD behavior.
  - Docstring updated — adapters now serve both `Interpreter` and `EvidenceGraphBuilder`.

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

### Phase 3 — Shrink server CRUD + drop `condense` flag ✅ DONE (2026-04-22, awaiting regression run)

- [x] `mds_python/.../crud/evidence_graph.py`:
  - `FairscapeEvidenceGraphRequest.build_evidence_graph_for_node(requesting_user, naan, postfix)` — dropped `condense`, `condense_threshold`. Builds `MongoGraphSource` + `MongoResultSink(owner_email, owner_groups)`, calls `EvidenceGraphBuilder(source, sink).build(node_id, owner_email=..., name=..., description=...)`, round-trips via `get_evidence_graph` for the `FairscapeResponse` envelope.
  - 404 / 409 / idempotency status codes preserved by keeping the source-node pre-check and wrapping the builder call in a `DuplicateKeyError` handler. 201 vs 200 preserved by checking `hasEvidenceGraph` ourselves before delegating.
  - `create_evidence_graph`, `get_evidence_graph`, `delete_evidence_graph`, `list_evidence_graphs` untouched. Final size 163 lines (was 212); shy of the ~90 target because the other four methods stayed verbatim.
- [x] `mds_python/.../routers/evidence_graph.py` — dropped `condense` + `condense_threshold` `Query` params from `initiate_build_evidence_graph_for_node_route` and from the Celery `.delay(...)` kwargs. `Query` import removed since no query params remain.
- [x] `mds_python/.../worker.py` — dropped `condense`, `condense_threshold` from `build_evidence_graph_task` signature and from the forwarded `build_evidence_graph_for_node` call.
- [x] `mds_python/.../models/evidence_graph.py` shim — dropped `condense` / `condense_threshold` from `EvidenceGraphBuildRequest`. Also deleted the Phase 1 temporary `class EvidenceGraph(_SharedEvidenceGraph)` subclass with the Mongo-aware `build_graph` method (dead once CRUD calls `EvidenceGraphBuilder`); shim now re-exports the shared `EvidenceGraph` directly and drops the pipeline-helper + `pymongo` imports that only the subclass used. `list_evidence_graphs_from_db` + `EvidenceGraphBuildRequest` stay.

**Phase 3 acceptance:** byte-identical regression on a fixture crate. Only `dateCreated`/`dateModified` should differ between pre- and post-refactor stored docs. **Status:** code landed; regression run blocked on a live Mongo + fixture crate — re-verify before cutting the Phase 3 commit(s). Import-level smoke (`EvidenceGraphBuildRequest` fields drop `condense*`; `EvidenceGraph` has no `build_graph` anymore; CRUD imports builder + adapters) passes.

### Phase 4 — CLI catchup ✅ DONE (2026-04-22, awaiting server-vs-CLI cross-check)

- [x] `fairscape-cli/src/fairscape_cli/interpret/local_graph.py` — added `find_many(ark_ids) -> dict[str, dict]`. Exact-match only against the merged `_index`, matching `MongoGraphSource.find_many` semantics. `find_entity` retains its dash-tolerant fallback for the root-id resolution step.
- [x] `fairscape-cli/src/fairscape_cli/interpret/local_sink.py` — added `persist_evidence_graph(evidence_graph, source_node_id) -> str` that `mkdir -p`s the output path's parent and writes the Pydantic payload as JSON (by_alias, exclude_none). Doesn't back-annotate `localEvidenceGraph` on the source crate — the command does that *after* HTML generation so the reference points at the rendered `.html`.
- [x] `fairscape-cli/src/fairscape_cli/commands/build_commands.py` — `generate_evidence_graph` rewired: build `LocalGraphSource(primary_path=metadata_file)`, resolve the user's ARK via `source.find_entity(ark_id)` (so dash-tolerant lookup stays), then run `EvidenceGraphBuilder(source, LocalResultSink(output_path=...)).build(resolved_id, owner_email=..., name=..., description=...)`. HTML generation + `localEvidenceGraph` back-annotation blocks kept verbatim. `generate_evidence_graph_from_rocrate` import dropped.
- [x] `fairscape-cli/src/fairscape_cli/utils/build_utils.py:process_evidence_graph` — rewired identically to the command (same three-adapter construction, same `find_entity` resolve step); HTML + `localEvidenceGraph` back-annotation blocks unchanged.
- [x] Deleted `fairscape-cli/src/fairscape_cli/datasheet_builder/evidence_graph/graph_builder.py` (`EvidenceGraphJSON` + `generate_evidence_graph_from_rocrate` dead). `__init__.py` had no exports to clean; `html_builder.py` untouched.
- [x] `tests/commands/build_commands/test_build.py` — removed the five mock-based error-path tests (`_generation_error`, `_html_import_error`, `_html_generation_error`, `_html_returns_false`, `_metadata_update_error`); they all patched the now-deleted `generate_evidence_graph_from_rocrate` seam. Updated `test_build_evidence_graph_success`'s `@id` assertion to the new server-style shape (`ark:59852/evidence-graph-<postfix>`). Phase 5's E2E tests will replace the deleted error-path coverage with fixture-based tests.

**Phase 4 acceptance:** `fairscape build evidence-graph <fixture> <ark>` sidecar matches server `StoredIdentifier.metadata.@graph` exactly. **Status:** code landed; smoke checks confirm `LocalResultSink.persist_evidence_graph` writes a valid pydantic payload, `LocalGraphSource.find_many` returns the expected shape, and `build_commands` imports cleanly with zero stale symbols. Server-vs-CLI cross-fixture byte-match still needs a live Mongo + both CLIs running on the same fixture crate — punt to Phase 5 alongside the server regression run.

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
- **2026-04-21 (Phase 1)** — Server-side shim uses a Pydantic subclass `class EvidenceGraph(SharedEvidenceGraph)` that re-adds the legacy `build_graph(mongo_collection, …)` method. Rationale: `crud/evidence_graph.py:155` still calls `evidence_graph.build_graph(…)` and doesn't get rewired until Phase 3; subclassing (rather than monkey-patching or editing the caller now) keeps the server byte-identical while the shared `EvidenceGraph` stays method-free. Subclass disappears in Phase 3 when the CRUD starts calling `EvidenceGraphBuilder`.
- **2026-04-21 (Phase 1)** — Shim's `build_graph` preserves the original control flow (derive `output_nodes` + `start_rocrate_outputs` pre-BFS, condense, then project) rather than delegating to `build_graph_dict`. Rationale: `build_graph_dict(start_node_id, node_cache)` derives outputs from the *post-condense* cache, which could diverge from the current server behavior in the edge case where condensation prunes the start node (unlikely in practice but enough to break the "byte-identical on fixture crate" Phase 3 acceptance test). Using `_build_node_from_cache` directly in the shim keeps server output pre-Phase-3 byte-identical; the Phase 2 orchestrator can reconsider whether to capture outputs pre- or post-condense.
- **2026-04-22 (Phase 2)** — `EvidenceGraphBuilder` replicates the shim's pre-condense output-derivation flow rather than switching to `build_graph_dict`. Rationale: Phase 3 acceptance is a byte-identical fixture-crate regression; keeping the pre-condense derivation in the orchestrator makes the Phase 3 rewire mechanically trivial (same control flow, different data-access seam) and removes any risk of a divergence slipping in between "move method into class" and "swap for builder." `build_graph_dict` stays available as the pure-projection helper for future consumers (e.g., a CLI codepath that never condenses) but is not on the server hot path.
- **2026-04-22 (Phase 2)** — `EvidenceGraphBuilder.build` short-circuits when the start node already carries `hasEvidenceGraph`, returning the existing id without calling `sink.persist_evidence_graph`. Rationale: keeps the idempotency contract from today's `build_evidence_graph_for_node` inside the orchestrator so Phase 3's CRUD can stay thin (it will just round-trip the returned id through `get_evidence_graph`). Builder never re-validates the existing stored doc — that's the caller's job on the return trip.
- **2026-04-22 (Phase 2)** — `MongoGraphSource.find_many` uses `fairscape_graph_tools.pipeline.evidence_graph._flatten_metadata` (imported as `_flatten_for_evidence_graph`), not the module-local `_flatten_metadata` that `find_entity` uses. Rationale: the two helpers have genuinely different semantics — the pipeline's preserves sibling top-level storage fields while lifting `metadata.*`, whereas the adapter's strips everything outside `metadata` + `@id`/`@type`. The shim BFS used the pipeline version; matching that keeps Phase 3's byte-identical regression intact. The local helper stays correct for `find_entity` / the condensation pipeline, so we didn't collapse the two.
- **2026-04-22 (Phase 3)** — CRUD still does its own `hasEvidenceGraph` pre-check *before* calling the builder, even though the builder also short-circuits on it. Rationale: without the pre-check we can't distinguish "fresh build" (201) from "already existed" (200) based only on the returned id. Keeping the CRUD check is the least-surprising way to preserve HTTP semantics without threading a `was_new` flag through the port contract. The builder's own short-circuit stays useful for other callers (CLI, tests) and as a defense-in-depth guard against back-pointers written between our check and our build.
- **2026-04-22 (Phase 3)** — CRUD round-trips through `get_evidence_graph(returned_id)` and then overrides `statusCode=201` on the success envelope, rather than having the builder return the full `StoredIdentifier`. Rationale: the builder stays storage-agnostic and returns just the id; the CRUD owns the FastAPI-facing status-code policy. Tradeoff: one extra Mongo read per build. Acceptable (this is a 202 async task already).
- **2026-04-22 (Phase 3)** — The Phase 1 `class EvidenceGraph(_SharedEvidenceGraph)` subclass (holder for the temporary Mongo-aware `build_graph`) is removed. The shim now re-exports the shared `EvidenceGraph` directly. Rationale: once CRUD calls `EvidenceGraphBuilder`, nothing on the server calls `.build_graph()` on the model, and keeping the subclass around would drift from "model in the shared pkg is the model" — the whole point of the rename-and-extract. Grepped both `mds_python/src` and `fairscape-cli/src` to confirm no `.build_graph(` callers on the model remain (the CLI hit in `datasheet_builder/evidence_graph/graph_builder.py` is on the unrelated `EvidenceGraphJSON` class, which Phase 4 deletes outright).
- **2026-04-22 (Phase 4)** — CLI command resolves the user's ARK via `source.find_entity(ark_id)` (dash-tolerant) *before* feeding the canonical id into `EvidenceGraphBuilder.build(...)`. Rationale: the builder's internal lookups use `find_many`, which is exact-match-only to stay aligned with `MongoGraphSource.find_many` for byte-match semantics. If we lost dash-tolerance at the CLI root, users with slightly-mis-dashed ARKs would silently get error-stub graphs. Resolving up front means the builder sees the canonical id either way and CLI users keep the convenience.
- **2026-04-22 (Phase 4)** — CLI evidence-graph `@id` shape changes from `{node_id}-evidence-graph` (old `EvidenceGraphJSON`) to `ark:NAAN/evidence-graph-<postfix>` (the builder's `_derive_evidence_graph_id`). Rationale: the Phase 4 acceptance target is "sidecar matches server `StoredIdentifier.metadata.@graph` exactly"; aligning the outer `@id` shape with the server convention is the whole point. Test suite updated to assert the new shape.
- **2026-04-22 (Phase 4)** — The five mock-based error-path tests that patched `generate_evidence_graph_from_rocrate` are deleted rather than partially rewired. Rationale: they tested implementation details of the pre-refactor generator (single patch point, specific error messages); the new flow has no equivalent seam and Phase 5 writes fixture-based CLI E2E tests with materially better coverage. Keeping the old tests half-wired to arbitrary patch targets would encode fragile assumptions about the builder's internals.
- **2026-04-22 (Phase 4)** — `LocalResultSink.persist_evidence_graph` does NOT back-annotate `localEvidenceGraph` on the source crate. Rationale: the existing CLI contract writes that field pointing at the *HTML* visualization, which isn't produced until after the JSON sidecar lands. The command/utility code owns the HTML → JSON ordering, so back-annotation stays there — the sink only writes the JSON payload and returns.

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
