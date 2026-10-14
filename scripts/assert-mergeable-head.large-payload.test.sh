#!/usr/bin/env bash
# Drives scripts/assert-mergeable-head.sh with API payloads far larger than Linux's
# per-argument limit (MAX_ARG_STRLEN, 128 KiB) and asserts the gate reaches the SAME
# verdict it reaches on the small-payload equivalent (#1784). verJSON/AiB#347 is the
# live case: its workflow-run listing passed to `jq --argjson` made the kernel refuse
# the exec, so the gate exited 1 "failed to evaluate" on exactly the heads that had
# been re-triggered.
#
# Every gate input is oversized here AFTER the script's own normalization, not merely
# in the raw response: the long strings sit in fields the gate reads (check, context,
# and workflow names), so neither the normalization nor any future cut-down of a
# response can shrink a value back under the limit and let a reintroduced `--argjson`
# pass unnoticed. Each case
# asserts a verdict reached only after the oversized value has been evaluated -- a
# superseded cancellation set aside, a pending run on the LAST page, a failing check
# in Gate C -- so a run that exits early cannot satisfy it.
#
# Fixtures are served from files, never environment variables: an environment string
# is subject to the same per-string limit, so an env-borne fixture would fail the
# stub's own exec and test nothing.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/assert-mergeable-head.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

HEAD_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
APP_ID=15368
WF_CI=315894159
MAX_ARG_STRLEN=131072

mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -uo pipefail
args="$*"
case "$args" in
  *"pr view "*"--json headRefOid,baseRefName"*) cat "$FIXTURES/pr.json" ;;
  *"/rules/branches/"*) cat "$FIXTURES/rules.json" ;;
  *"/check-runs?per_page"*) cat "$FIXTURES/check-runs.json" ;;
  *"/commits/"*"/status"*) cat "$FIXTURES/status.json" ;;
  *"/actions/runs?head_sha="*) cat "$FIXTURES/runs.json" ;;
  *"/annotations?per_page"*) printf '[]\n' ;;
  *) echo "gh: unstubbed call: $args" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"

# Writes one fixture set. Every required context is reported twice: CANCELLED by the
# draft-time run (9001, suite 501) and SUCCESS by the ready-time run (9002, suite 502)
# that superseded it -- the ADR 0226 shape, which is the only path through the
# `superseder` program. Unrelated finished runs pad the listing across several pages.
#
#   $1 directory  $2 context count  $3 name padding  $4 filler runs  $5 run-name padding
#   $6 last filler run's status ("completed" or an unfinished status)
#   $7 extra non-required check conclusion ("" for none)
build_fixtures() {
  local dir="$1"
  mkdir -p "$dir"
  printf '{"headRefOid":"%s","baseRefName":"main"}\n' "$HEAD_SHA" >"$dir/pr.json"
  jq -n --argjson n "$2" --argjson pad "$3" '
    def ctx($i): "ctx-\($i) / " + ("x" * $pad);
    def blob($i): { node_id: "N\($i)", description: ("d" * 400),
                    owner: { login: "verjson-bot", avatar_url: ("a" * 200) } };
    [range(0; $n)] as $ids
    | { rules: [ { type: "required_status_checks",
                   parameters: { required_status_checks:
                     [ $ids[] | { context: ctx(.), integration_id: '"$APP_ID"' } ] } } ],
        checks: [ $ids[] as $i
                  | ( { id: (10000 + $i), name: ctx($i), status: "completed",
                        conclusion: "cancelled", app: { id: '"$APP_ID"' },
                        check_suite: { id: 501 }, output: { text: ("o" * 600) },
                        repository: blob($i) },
                      { id: (20000 + $i), name: ctx($i), status: "completed",
                        conclusion: "success", app: { id: '"$APP_ID"' },
                        check_suite: { id: 502 }, output: { text: ("o" * 600) },
                        repository: blob($i) } ) ] }
  ' >"$dir/model.json"
  jq -c '.rules' "$dir/model.json" >"$dir/rules.json"
  jq -c --arg extra "$7" '
    .checks
    + (if $extra == "" then []
       else [ { id: 30000, name: ("advisory / " + ("y" * 3000)), status: "completed",
                conclusion: $extra, app: { id: 1 }, check_suite: { id: 503 } } ] end)
    | { total_count: length, check_runs: . }' "$dir/model.json" >"$dir/check-runs.json"
  printf '{"total_count":0,"statuses":[]}\n' >"$dir/status.json"
  # Pages of 100, as `gh api --paginate` concatenates them: every page states the
  # same total_count, and the unfinished run, when there is one, is on the LAST page.
  jq -nc --arg head "$HEAD_SHA" --argjson fill "$4" --argjson pad "$5" --arg last "$6" '
    def wf($id; $suite; $wf; $num; $status; $conclusion; $name):
      { id: $id, check_suite_id: $suite, workflow_id: $wf, run_number: $num,
        event: "pull_request", status: $status, conclusion: $conclusion,
        head_sha: $head, name: $name, path: ".github/workflows/x.yml",
        run_attempt: 1, created_at: "2026-10-14T00:00:00Z",
        html_url: "https://github.com/verJSON/AiB/actions/runs/\($id)",
        repository: { full_name: "verJSON/AiB", description: ("r" * 800) },
        head_repository: { full_name: "verJSON/AiB", description: ("r" * 800) },
        actor: { login: "verjson-bot", avatar_url: ("a" * 300) },
        head_commit: { message: ("m" * 600) } };
    ( [ wf(9001; 501; '"$WF_CI"'; 1; "completed"; "cancelled"; "ci"),
        wf(9002; 502; '"$WF_CI"'; 2; "completed"; "success"; "ci") ]
      + [ range(0; $fill) as $i
          | wf(40000 + $i; 600 + $i; 1000 + $i; 1;
               (if $i == $fill - 1 then $last else "completed" end);
               (if $i == $fill - 1 and $last != "completed" then null else "success" end);
               "filler-\($i) " + ("w" * $pad)) ] ) as $all
    | ($all | length) as $total
    | range(0; $total; 100) as $start
    | { total_count: $total, workflow_runs: $all[$start:$start + 100] }
  ' >"$dir/runs.json"
}

run_gate() { # fixture-dir
  OUT="$(FIXTURES="$1" "$script" verJSON/AiB 347 2>&1)"
  RC=$?
}

# A precondition, not a nicety: if the fixtures stop exceeding the limit in fields the
# gate keeps, every case below passes against the unfixed script and proves nothing.
assert_oversized() { # fixture-dir
  local check_names run_names context_names
  check_names="$(jq '[.check_runs[].name | length] | add' "$1/check-runs.json")"
  run_names="$(jq -s '[.[].workflow_runs[].name | length] | add' "$1/runs.json")"
  context_names="$(jq '[.[].parameters.required_status_checks[].context | length] | add' "$1/rules.json")"
  if [ "$check_names" -gt "$MAX_ARG_STRLEN" ] && [ "$run_names" -gt "$MAX_ARG_STRLEN" ] \
      && [ "$context_names" -gt "$MAX_ARG_STRLEN" ] \
      && [ "$(jq -s 'length' "$1/runs.json")" -gt 1 ] \
      && [ "$(wc -c <"$1/runs.json")" -gt $((200 * 1024)) ]; then
    pass "precondition: kept fields exceed MAX_ARG_STRLEN (checks $check_names, runs $run_names, contexts $context_names bytes of names) across several run pages"
  else
    fail "precondition: fixtures are not oversized in kept fields (checks $check_names, runs $run_names, contexts $context_names)"
  fi
}

# Same verdict, small and large: exit 0, one notice listing every set-aside
# cancellation, and `true` as the only line on stdout's tail.
# The notice alone can run to hundreds of kilobytes here, so a failure reports the lines
# that explain it -- faults and exec refusals, each cut short -- not the head of OUT.
diagnosis() {
  grep -E -- '^::error::|Argument list too long' <<<"$OUT" | cut -c1-300
}

expect_superseded_true() { # label context-count
  local dismissed
  dismissed="$(grep -oF -- 'superseded by run 9002' <<<"$OUT" | wc -l)"
  if [ "$RC" -eq 0 ] && [ "$(tail -n1 <<<"$OUT")" = "true" ] \
      && [ "$(grep -c '^::notice::' <<<"$OUT")" -eq 1 ] && [ "$dismissed" -eq "$2" ] \
      && ! grep -q '^::error::' <<<"$OUT"; then
    pass "$1"
  else
    fail "$1 (rc=$RC, dismissed=$dismissed, output: $(diagnosis))"
  fi
}

expect_fault() { # label rc needle
  if [ "$RC" -eq "$2" ] && grep -qF -- "$3" <<<"$OUT" \
      && ! grep -qF -- 'Argument list too long' <<<"$OUT"; then
    pass "$1"
  else
    fail "$1 (rc=$RC, output: $(diagnosis))"
  fi
}

LAST_FILLER=$((40000 + 449))

# --- passing head: same verdict small and large -------------------------------
build_fixtures "$tmp/small-pass" 2 8 3 8 completed ''
run_gate "$tmp/small-pass"
expect_superseded_true "small payload: a superseded-cancellation head is admitted" 2

build_fixtures "$tmp/large-pass" 60 3000 450 400 completed ''
assert_oversized "$tmp/large-pass"
run_gate "$tmp/large-pass"
expect_superseded_true "oversized payload: the same head shape is admitted, every cancellation set aside (#1784)" 60

# --- Gate A: a pending run on the last page -----------------------------------
build_fixtures "$tmp/small-pending" 2 8 3 8 queued ''
run_gate "$tmp/small-pending"
expect_fault "small payload: an unfinished run is pending under Gate A" 3 "pending: workflow run 40002 (QUEUED) filler-2"

build_fixtures "$tmp/large-pending" 60 3000 450 400 queued ''
run_gate "$tmp/large-pending"
expect_fault "oversized payload: an unfinished run on the last page is pending under Gate A (#1784)" 3 \
  "pending: workflow run $LAST_FILLER (QUEUED) filler-449"

# --- Gate C: a failing advisory check -----------------------------------------
build_fixtures "$tmp/small-gate-c" 2 8 3 8 completed failure
run_gate "$tmp/small-gate-c"
expect_fault "small payload: a failing advisory check fails Gate C" 5 "Gate C: verJSON/AiB#347 head $HEAD_SHA has pending or non-passing checks"

build_fixtures "$tmp/large-gate-c" 60 3000 450 400 completed failure
run_gate "$tmp/large-gate-c"
expect_fault "oversized payload: a failing advisory check fails Gate C, not the evaluation (#1784)" 5 \
  "Gate C: verJSON/AiB#347 head $HEAD_SHA has pending or non-passing checks"

# --- a malformed listing is refused by name, whatever its size ----------------
# Moving the listing through a file must not change where a malformed shape is
# refused: a run that is not an object fails run validation, and a page that is not an
# object fails the page check (#1786) before anything reads its runs.
malformed_listing() { # label runs-body expected-refusal
  build_fixtures "$tmp/shape" 2 8 0 8 completed ''
  printf '%s' "$2" >"$tmp/shape/runs.json"
  run_gate "$tmp/shape"
  expect_fault "$1" 1 "the workflow-run inventory for verJSON/AiB@$HEAD_SHA carries a malformed $3"
}
ok_run="$(jq -c '.workflow_runs[0]' <<<"$(head -n1 "$tmp/small-pass/runs.json")")"
malformed_listing "a run that is not an object is refused as a malformed record" \
  "{\"total_count\":2,\"workflow_runs\":[$ok_run,\"bogus\"]}" record
malformed_listing "a page that is not an object is refused as a malformed page (#1786)" \
  "{\"total_count\":1,\"workflow_runs\":[$ok_run]}[]" page

if [ "$fails" -ne 0 ]; then
  printf '\n%d assertion(s) failed\n' "$fails" >&2
  exit 1
fi
printf '\nall assertions passed\n'
