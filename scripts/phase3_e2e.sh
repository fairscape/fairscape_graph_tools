#!/usr/bin/env bash
# Phase 3 end-to-end test harness for fairscape_graph_tools.
#
# Covers the items in Phase 3 of MIGRATION.md that don't need unit-test
# infrastructure:
#   - End-to-end (CLI): primary-only, primary + refs, --save-condensed,
#     --debug-llm trace
#   - Cross-consistency scaffolding (server vs CLI on the same crate) —
#     the server portion is commented out; uncomment or source the
#     FAIRSCAPE_* env vars and run those blocks once you have a token.
#
# The script does NOT validate correctness of the AEG output. It runs
# every variant, captures artifacts under ./phase3_out/, and prints
# where to look. You diff / eyeball / rerun as needed.
#
# Usage:
#   cd /Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos/fairscape_graph_tools
#   bash scripts/phase3_e2e.sh                       # CLI variants only
#   FAIRSCAPE_TOKEN=... FAIRSCAPE_URL=https://...  \
#       RUN_SERVER=1 bash scripts/phase3_e2e.sh     # also hit the server
#
# Env vars you may want to tweak:
#   LLM_MODEL       pydantic-ai model string (default google-gla:gemini-2.5-flash-lite)
#   TEMPERATURE     LLM temperature (default 0.2)
#   MAX_WORKERS     parallel annotation workers (default 2)
#   EXAMPLE_CRATE   path to interpret_example_splits (default repo default)
#   FIXED_CRATE     path to interpret_fixed_splits   (default repo default)
#   OUT_DIR         where to write artifacts         (default ./phase3_out)
#   RUN_SERVER      set to 1 to also run the server-side curls
#   FAIRSCAPE_URL   base URL of the mds_python server (e.g. https://fairscape.../api)
#   FAIRSCAPE_TOKEN bearer token — the script never prints it

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/Users/justin/Docs/Tim_Work/Git_Repos/fairscape-repos}"
EXAMPLE_CRATE="${EXAMPLE_CRATE:-$REPO_ROOT/Old/interpret_example_splits}"
FIXED_CRATE="${FIXED_CRATE:-$REPO_ROOT/Old/interpret_fixed_splits}"
OUT_DIR="${OUT_DIR:-$(pwd)/phase3_out}"
LLM_MODEL="${LLM_MODEL:-google-gla:gemini-2.5-flash-lite}"
TEMPERATURE="${TEMPERATURE:-0.2}"
MAX_WORKERS="${MAX_WORKERS:-2}"

FAIRSCAPE_CLI="${FAIRSCAPE_CLI:-fairscape-cli}"

banner() { printf '\n\033[1;36m==== %s ====\033[0m\n' "$*"; }
note()   { printf '   • %s\n' "$*"; }
warn()   { printf '\033[1;33m   ! %s\033[0m\n' "$*"; }

command -v "$FAIRSCAPE_CLI" >/dev/null 2>&1 || {
    warn "fairscape-cli not on PATH; try:  uv run --project $REPO_ROOT/fairscape-cli fairscape-cli ..."
    warn "or export FAIRSCAPE_CLI to point at your entrypoint."
    exit 1
}

[ -d "$EXAMPLE_CRATE" ] || { warn "missing crate: $EXAMPLE_CRATE"; exit 1; }
[ -d "$FIXED_CRATE" ]   || { warn "missing crate: $FIXED_CRATE";   exit 1; }

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"/{cli,server}

banner "Phase 3 harness"
note "repo:        $REPO_ROOT"
note "example:     $EXAMPLE_CRATE"
note "fixed:       $FIXED_CRATE"
note "output:      $OUT_DIR"
note "model:       $LLM_MODEL  (temp=$TEMPERATURE, workers=$MAX_WORKERS)"

run_cli() {
    # run_cli <label> <primary_crate> [<cli-args...>]
    local label="$1"; shift
    local primary="$1"; shift
    local out="$OUT_DIR/cli/${label}.json"
    local log="$OUT_DIR/cli/${label}.log"
    banner "CLI — $label"
    note "primary: $primary"
    note "extra args: $*"
    note "output:  $out"
    note "log:     $log"
    if "$FAIRSCAPE_CLI" interpret run "$primary" \
        --llm-model "$LLM_MODEL" \
        --temperature "$TEMPERATURE" \
        --max-workers "$MAX_WORKERS" \
        --output "$out" \
        "$@" 2>&1 | tee "$log"; then
        note "OK — $(wc -c <"$out" 2>/dev/null | tr -d ' ') bytes"
    else
        warn "FAILED — see $log"
    fi
}

#
# CLI variants
#
# 1. Primary-only on each crate (the "non-condensed" baseline — neither
#    fixture carries a hasCondensedROCrate pointer, so this exercises
#    Condenser.ensure_condensed's fresh-condense path).
run_cli example_primary_only "$EXAMPLE_CRATE"
run_cli fixed_primary_only   "$FIXED_CRATE"

# 2. Primary + reference, both directions. Cross-crate refs are the
#    reason LocalGraphSource merges reference crates into a flat index.
run_cli example_with_fixed_ref "$EXAMPLE_CRATE" --reference "$FIXED_CRATE"
run_cli fixed_with_example_ref "$FIXED_CRATE"   --reference "$EXAMPLE_CRATE"

# 3. --save-condensed writes out the Condenser pre-step artifact so you
#    can inspect it and feed it back in if you want to test the
#    self-condensed path (requires wrapping the JSON in an RO-Crate dir
#    — out of scope for this script, but the artifact is here).
run_cli example_with_condensed "$EXAMPLE_CRATE" \
    --save-condensed "$OUT_DIR/cli/example_with_condensed.condensed.json"

# 4. --debug-llm dumps every raw LLM response alongside the sidecar.
run_cli fixed_debug_llm "$FIXED_CRATE" --debug-llm

#
# Quick diff helpers — adjust as you like. These ignore the fields that
# are expected to differ between runs (timestamps, nondeterministic
# ARKs) so you can eyeball whether the actual evidence graph changed.
#
banner "CLI artifacts summary"
find "$OUT_DIR/cli" -maxdepth 1 -type f \( -name '*.json' -o -name '*.jsonl' \) \
    -exec ls -la {} + 2>/dev/null || true

if command -v jq >/dev/null 2>&1; then
    banner "Nondeterminism check — fixed_primary_only vs fixed_with_example_ref"
    note "strip @id, dateCreated, dateModified and diff the remaining metadata shape"
    jq 'del(.. | .["@id"]?, .dateCreated?, .dateModified?, .evi:contentChecksum?)' \
        "$OUT_DIR/cli/fixed_primary_only.json"   > "$OUT_DIR/cli/_fixed_primary_only.norm.json" 2>/dev/null || true
    jq 'del(.. | .["@id"]?, .dateCreated?, .dateModified?, .evi:contentChecksum?)' \
        "$OUT_DIR/cli/fixed_with_example_ref.json" > "$OUT_DIR/cli/_fixed_with_example_ref.norm.json" 2>/dev/null || true
    diff -u "$OUT_DIR/cli/_fixed_primary_only.norm.json" \
            "$OUT_DIR/cli/_fixed_with_example_ref.norm.json" \
        > "$OUT_DIR/cli/_fixed_ref_vs_noref.diff" || true
    note "diff written to $OUT_DIR/cli/_fixed_ref_vs_noref.diff (empty = no structural change from adding the reference)"
else
    warn "jq not installed; skipping structural-diff helpers"
fi

#
# Server path — only runs when RUN_SERVER=1 and auth is supplied.
# You validate responses; this just orchestrates the calls.
#
if [ "${RUN_SERVER:-0}" = "1" ]; then
    : "${FAIRSCAPE_URL:?set FAIRSCAPE_URL, e.g. https://fairscape.example.com/api}"
    : "${FAIRSCAPE_TOKEN:?set FAIRSCAPE_TOKEN (never echoed)}"
    AUTH="Authorization: Bearer $FAIRSCAPE_TOKEN"

    server_run_one() {
        # server_run_one <label> <zip_path>
        local label="$1"; local zip="$2"
        local dir="$OUT_DIR/server/$label"
        mkdir -p "$dir"
        banner "SERVER — $label"
        note "upload:  $zip"

        # Step 1: upload crate → returns a job guid
        curl -sS -X POST "$FAIRSCAPE_URL/rocrate/upload-async" \
            -H "$AUTH" \
            -F "crate=@$zip" \
            -o "$dir/01_upload.json"
        note "upload resp: $dir/01_upload.json"
        local job_guid
        job_guid=$(jq -r '.guid // empty' "$dir/01_upload.json" 2>/dev/null || true)
        [ -n "$job_guid" ] || { warn "no job guid returned — inspect $dir/01_upload.json"; return; }
        note "upload job: $job_guid"

        # Step 2: poll upload status until terminal
        for i in 1 2 3 4 5 6 7 8 9 10; do
            sleep 3
            curl -sS "$FAIRSCAPE_URL/rocrate/upload/status/$job_guid" \
                -H "$AUTH" -o "$dir/02_upload_status_${i}.json"
            local st
            st=$(jq -r '.status // empty' "$dir/02_upload_status_${i}.json" 2>/dev/null)
            note "upload status[$i]: $st"
            case "$st" in SUCCESS|FAILURE|success|failure|finished|complete) break;; esac
        done

        # The upload response or status should carry the minted ROCrate ARK.
        # Adjust this jq selector if your mds_python returns it under a
        # different key — common candidates are .rocrateGUID, .ark, .guid.
        local crate_ark
        crate_ark=$(jq -r '.rocrateGUID // .ark // .guid // empty' \
            "$dir"/02_upload_status_*.json "$dir/01_upload.json" 2>/dev/null \
            | grep -m1 '^ark:' || true)
        [ -n "$crate_ark" ] || { warn "could not find rocrate ARK in upload responses — inspect $dir/"; return; }
        note "rocrate: $crate_ark"

        # Step 3: trigger interpretation (matches router signature —
        # persona/force defaults match mds_python defaults).
        curl -sS -X POST \
            "$FAIRSCAPE_URL/interpretation/$crate_ark?llm_model=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$LLM_MODEL")&temperature=$TEMPERATURE&force=true" \
            -H "$AUTH" \
            -o "$dir/03_trigger.json"
        local task_id
        task_id=$(jq -r '.task_id // empty' "$dir/03_trigger.json")
        [ -n "$task_id" ] || { warn "no task_id — inspect $dir/03_trigger.json"; return; }
        note "task: $task_id"

        # Step 4: poll status. Interpretation over either fixture is
        # usually <2 min; bump the loop if your LLM is slow.
        for i in $(seq 1 60); do
            sleep 5
            curl -sS "$FAIRSCAPE_URL/interpretation/status/$task_id" \
                -H "$AUTH" -o "$dir/04_status_${i}.json"
            local st step done_n total_n
            st=$(jq -r '.status // empty' "$dir/04_status_${i}.json")
            step=$(jq -r '.current_step // empty' "$dir/04_status_${i}.json")
            done_n=$(jq -r '.completed_computations // 0' "$dir/04_status_${i}.json")
            total_n=$(jq -r '.total_computations // 0' "$dir/04_status_${i}.json")
            note "status[$i]: $st ($step) $done_n/$total_n"
            case "$st" in SUCCESS|FAILURE) break;; esac
        done

        # Step 5: fetch result
        curl -sS "$FAIRSCAPE_URL/interpretation/result/$crate_ark" \
            -H "$AUTH" -o "$dir/05_result.json"
        note "server AEG: $dir/05_result.json"
    }

    server_run_one example_splits "$REPO_ROOT/Old/interpret_example_splits.zip"
    server_run_one fixed_splits   "$REPO_ROOT/Old/interpret_fixed_splits.zip"

    #
    # Cross-consistency (the Phase 2 deferred item). Both sides should
    # produce the same logical AnnotatedEvidenceGraph; wrappers differ
    # (server wraps in StoredIdentifier + back-pointers).
    #
    if command -v jq >/dev/null 2>&1; then
        banner "Cross-consistency: CLI vs server (example_splits)"
        jq '.metadata // .' "$OUT_DIR/server/example_splits/05_result.json" \
            > "$OUT_DIR/server/example_splits/_metadata.json" 2>/dev/null || true
        jq '.metadata // .' "$OUT_DIR/cli/example_primary_only.json" \
            > "$OUT_DIR/cli/_example_primary_only.metadata.json" 2>/dev/null || true
        diff -u "$OUT_DIR/cli/_example_primary_only.metadata.json" \
                "$OUT_DIR/server/example_splits/_metadata.json" \
            > "$OUT_DIR/_cli_vs_server.example.diff" || true
        note "diff → $OUT_DIR/_cli_vs_server.example.diff (expect only wrapper / id / timestamp noise)"
    fi
else
    banner "SERVER path skipped"
    note "set RUN_SERVER=1 + FAIRSCAPE_URL + FAIRSCAPE_TOKEN to exercise the server"
fi

banner "Done"
note "all artifacts under $OUT_DIR"
