#!/usr/bin/env bash
# Report which repositories do not yet emit their stack's CORE CHECK CONTRACT
# (ADR 0058, step 2). Read-only: it writes no ruleset and changes nothing.
#
# The contract is DECLARED here, not derived from what repositories happen to
# emit. Deriving it would make the current drift permanent — a stale repository's
# job naming would become organizational policy. The core set per stack lives in
# `core_contract_for`, and the check names it references are already pinned on
# the right-hand side by the org's reusable workflows (`node-ci` publishes
# `build-test` and `eligibility`, `helm-ci` publishes `lint-template`, and so
# on). The only free variable is the CALLER's job name, which is why the
# canonical caller names below are part of the contract and why generated thin
# callers, not convention, are what enforce them.
#
# WHY THIS EXISTS AS A SEPARATE, READ-ONLY STEP
#
# A required context that a repository never produces is not a warning — it is
# permanently pending, and that repository can never merge again until an admin
# removes the rule. At `~ALL` scope that is every repository in the org. So the
# question "does every repository actually emit its core set?" must be answered
# and driven to zero BEFORE any rule is written, and answering it must itself be
# incapable of breaking anything.
#
# The distinction that makes a context safe to require:
#
#   - A conditional JOB (`if:` false) emits a check run with conclusion
#     `skipped`, which SATISFIES a required check. Safe.
#   - A `paths:`-filtered WORKFLOW that does not match emits NO check run at
#     all, which is permanently pending. Fatal.
#
# So this audit reports a context as missing only when it is absent from every
# sampled PR head. A context that is present-but-skipped is conformant, and
# reporting it as missing would send people to "fix" the one shape that is
# already correct.
set -euo pipefail

fault() { echo "::error::phase=$1 result=$2 ${3:-}" >&2; exit 2; }

for tool in gh jq base64 python3 curl diff mktemp mkdir env; do
  command -v "$tool" >/dev/null 2>&1 ||
    fault startup toolchain-missing "tool=$tool — install it or this cannot run."
done

ORG="${RCA_ORG:?RCA_ORG must name the organization}"
script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
WORKFLOW_INSPECTOR="${RCA_WORKFLOW_INSPECTOR:-$script_dir/required-checks-workflow.py}"
[ -f "$WORKFLOW_INSPECTOR" ] && [ ! -L "$WORKFLOW_INSPECTOR" ] ||
  fault startup workflow-inspector-missing "path=$WORKFLOW_INSPECTOR"
CONTRACT_FILE="${RCA_CONTRACT_FILE:-$script_dir/../.github/required-check-contract.json}"
jq -e '
  .schema_version == 1 and
  .mode == "staged" and
  .mutation_authorized == true and
  (has("universal_contexts") | not) and
  (.stacks | type == "object") and
  # Every stack must declare a context list of literal check names. Without this
  # the ambiguous shapes reach the audit and read as "requires nothing": a
  # `contexts` of {} or [""] survives command substitution as no output at all,
  # which is indistinguishable from a deliberate empty list. Rejecting them here
  # is what lets `core_contract_for` stay a two-way decision.
  #
  # `length > 0` alone is not enough, because the reader below is a shell `read`.
  # A context of "   " is non-empty to jq and empty to `read`, so it would resolve
  # to a NON-empty contract that then checks nothing and reports conformant —
  # the same "verified nothing, called it a pass" outcome at per-context
  # granularity. Surrounding whitespace is rejected for the same reason: `read`
  # would silently compare a different string than the one declared. Tabs and
  # newlines are rejected because a context carrying one splits into two.
  (.stacks | to_entries | all(
    (.value | type == "object") and
    (.value.contexts | type == "array") and
    (.value.contexts | all(
      type == "string" and length > 0 and
      (test("^\\s") | not) and (test("\\s$") | not) and (test("[\\n\\t]") | not)
    ))
  )) and
  (.caller_job_names.stack | type == "string") and
  (.caller_job_names.changelog | type == "string")
' "$CONTRACT_FILE" >/dev/null 2>&1 ||
  fault contract declaration-invalid "file=$CONTRACT_FILE"

# How many recent merged PRs to look at per repository. One PR proves a context
# CAN report, never that the repository emits it as a matter of course, so the
# audit needs more than one head before it will call a context present.
SAMPLE="${RCA_SAMPLE_PRS:-5}"
[[ "$SAMPLE" =~ ^[0-9]+$ ]] ||
  fault startup sample-unparseable "RCA_SAMPLE_PRS='${SAMPLE}' is not a number."
[ "$SAMPLE" -ge 1 ] ||
  fault startup sample-too-small "RCA_SAMPLE_PRS=${SAMPLE} samples nothing."

# Whether "nothing was verified here" may count as a pass. It may for the
# org-wide report, whose question is "which repositories would be wedged?" — a
# stack that requires nothing cannot be. It may not for a caller that is about
# to *apply* a rule: `required-checks-rollout.sh` selects repositories by the
# ruleset's own properties rather than by `verjson-stack`, so a contextless
# stack can sit inside the set being gated, and a skip there means the audit
# never confirmed the repository emits the context it is about to be required
# to emit. Such a caller sets this and gets a skip as a failure.
# A positional flag, not an environment variable: an env var of this name can
# be exported by a parent shell or inherited across a CI step boundary without
# either side intending it, which would flip the org-wide read-only report to
# fail-closed with no caller ever having asked for that. A flag only takes
# effect when the invoking command line spells it out.
REQUIRE_VERIFIED=false
for arg in "$@"; do
  case "$arg" in
    --require-verified) REQUIRE_VERIFIED=true ;;
    *) fault startup unrecognized-argument "arg='$arg'" ;;
  esac
done

# --- the declared core contract ---------------------------------------------
# A stack the declaration does not mention is a fault; a stack it declares with
# an empty context list is a deliberate "require nothing here" and prints
# nothing. Keeping those two apart is why the declaration is consulted before
# the contexts are read: `.contexts[]` alone cannot tell them apart, and jq's
# `-e` reports "no output" for both.
core_contract_for() { # $1 = stack
  jq -e --arg stack "$1" '.stacks | has($stack)' "$CONTRACT_FILE" >/dev/null 2>&1 ||
    fault contract unknown-stack "stack='$1' has no declared core contract; classify the repository or extend $CONTRACT_FILE."
  # Unreachable given the schema validated at startup, and deliberately kept:
  # the file is re-read here, so this covers it being replaced between the two
  # reads. A `contexts` that stops being a list must never resolve to silence.
  jq -r --arg stack "$1" '.stacks[$stack].contexts[]' "$CONTRACT_FILE" ||
    fault contract contract-unreadable "stack='$1' — $CONTRACT_FILE did not yield a context list."
}

# `dispatch-merge` and `privileged_merge` PERFORM the merge, so a merge that
# waits for them waits for itself. They are never part of any contract; listed
# here so that a future edit to core_contract_for cannot quietly add one.
is_merge_machinery() {
  case "${1##*/ }" in
    dispatch-merge|privileged_merge) return 0 ;;
    *) return 1 ;;
  esac
}

# Stack classification comes from the org custom property `verjson-stack`.
# An unclassified repository is reported, never guessed: guessing is how a
# repository ends up required to emit a context its stack never produces.
stack_of() { # $1 = repo
  local v
  v=$(gh api "repos/$ORG/$1/properties/values" \
        --jq '.[] | select(.property_name == "verjson-stack") | .value' 2>/dev/null) ||
    return 1
  printf '%s' "$v"
}

repos() {
  if [ -n "${RCA_REPOS:-}" ]; then
    # shellcheck disable=SC2086  # Intentional whitespace-separated test override.
    printf '%s\n' $RCA_REPOS
  else
    gh api --paginate "orgs/$ORG/repos?per_page=100" --jq '.[] | select(.archived | not) | .name'
  fi
}

stack_workflow_for() {
  local value
  value="$(jq -er --arg stack "$1" '.stacks[$stack].reusable_workflow // empty' "$CONTRACT_FILE" 2>/dev/null)" ||
    return 1
  [ -n "$value" ] || return 1
  printf '%s' "$value"
}

caller_job_for() { # stdin = workflow source, $1 = reusable workflow filename
  awk -v target="/.github/workflows/$1@" '
    /^jobs:[[:space:]]*$/ { in_jobs = 1; next }
    in_jobs && /^[^[:space:]#]/ { in_jobs = 0 }
    in_jobs && /^  [^[:space:]#][^:]*:[[:space:]]*$/ {
      job = $0
      sub(/^  /, "", job); sub(/:[[:space:]]*$/, "", job)
    }
    in_jobs && index($0, target) { print job }
  '
}

head_for_repo() { # $1 = repo, optional RCA_HEADS_FILE = repo<TAB>branch<TAB>sha
  local repo="$1" record
  [ -n "${RCA_HEADS_FILE:-}" ] || return 1
  [ -r "$RCA_HEADS_FILE" ] || fault startup heads-file-unreadable "path=$RCA_HEADS_FILE"
  record="$(awk -F '\t' -v repo="$repo" '$1 == repo { print $3 }' "$RCA_HEADS_FILE")"
  [[ "$record" =~ ^[0-9a-f]{40}$ ]] || fault contract audited-head-missing "repo=$repo"
  printf '%s' "$record"
}

generated_contract_identity_for_repo() ( # $1 = repo, $2 = audited head or empty
  local repo="$1" head="$2" ref_query='' tmp mode pin params scope node package_dir
  local canonical_branch encoded_branch canonical_head ancestry
  # The audited head is a 40-hex commit SHA; re-assert that where it builds a query string,
  # so no future caller can route arbitrary text into one.
  [ -z "$head" ] || { [[ "$head" =~ ^[0-9a-f]{40}$ ]] || return 2; ref_query="?ref=$head"; }
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  mkdir -p "$tmp/actual" "$tmp/expected"

  local path name
  for path in \
    .github/workflows/changelog.yml \
    .github/workflows/release.yml \
    .github/workflows/changelog-contract.yml \
    scripts/render-next.sh \
    scripts/changelog-contract.test.sh; do
    name="${path##*/}"
    gh api "repos/$ORG/$repo/contents/$path$ref_query" --jq .content 2>/dev/null |
      tr -d '\n' | base64 --decode >"$tmp/actual/$name" 2>/dev/null || {
        echo "::error::phase=audit repo=$repo result=generated-contract-artifact-unreadable path=$path"
        return 1
      }
    # `base64 --decode` exits 0 on an empty stream, so a fetch that returned
    # nothing lands here as a zero-byte file that every check downstream reads
    # as content. The byte comparison then reports drift, and the parameter
    # extraction reports invalid parameters, for an artifact that was never
    # retrieved. An empty artifact is a fetch fault, named as one.
    [ -s "$tmp/actual/$name" ] || {
      echo "::error::phase=audit repo=$repo result=generated-contract-artifact-empty path=$path"
      return 1
    }
  done

  # Older immutable pins emitted Verjson; generated bytes at each pin still
  # decide whether that spelling is authentic.
  read -r mode pin < <(sed -nE \
    's/^# Generated by [Vv]er[Jj][Ss][Oo][Nn]\/\.github scripts\/gen-changelog-caller\.sh (workflow|generated-artifacts(-with-adr-index)?) ([0-9a-f]{40})$/\1 \3/p' \
    "$tmp/actual/changelog.yml")
  case "$mode" in workflow|generated-artifacts|generated-artifacts-with-adr-index) ;; *)
    echo "::error::phase=audit repo=$repo result=generated-contract-provenance-invalid path=.github/workflows/changelog.yml"
    return 1 ;;
  esac
  [[ "$pin" =~ ^[0-9a-f]{40}$ ]] || {
    echo "::error::phase=audit repo=$repo result=generated-contract-pin-invalid"
    return 1
  }

  canonical_branch="$(gh api "repos/$ORG/.github" --jq .default_branch 2>/dev/null)" || {
    echo "::error::phase=audit repo=$repo result=canonical-default-branch-unreadable"
    return 1
  }
  [ -n "$canonical_branch" ] || {
    echo "::error::phase=audit repo=$repo result=canonical-default-branch-invalid"
    return 1
  }
  encoded_branch="$(jq -rn --arg value "$canonical_branch" '$value|@uri')"
  canonical_head="$(gh api "repos/$ORG/.github/branches/$encoded_branch" --jq .commit.sha 2>/dev/null)" || {
    echo "::error::phase=audit repo=$repo result=canonical-default-head-unreadable branch=$canonical_branch"
    return 1
  }
  [[ "$canonical_head" =~ ^[0-9a-f]{40}$ ]] || {
    echo "::error::phase=audit repo=$repo result=canonical-default-head-invalid branch=$canonical_branch"
    return 1
  }
  ancestry="$(gh api "repos/$ORG/.github/compare/$pin...$canonical_head" --jq .status 2>/dev/null)" || {
    echo "::error::phase=audit repo=$repo result=generated-contract-pin-ancestry-unreadable pin=$pin canonical_head=$canonical_head"
    return 1
  }
  case "$ancestry" in ahead|identical) ;; *)
    echo "::error::phase=audit repo=$repo result=generated-contract-pin-not-on-default pin=$pin canonical_head=$canonical_head status=$ancestry"
    return 1 ;;
  esac

  params="$(python3 - "$tmp/actual/changelog-contract.test.sh" "$pin" <<'PY'
import json, re, sys
text = open(sys.argv[1], encoding="utf-8").read()
def value(name, quote='"'):
    match = re.search(rf"^{name}={re.escape(quote)}(.*){re.escape(quote)}$", text, re.MULTILINE)
    if not match:
        raise SystemExit(1)
    return match.group(1)
contract_ref = value("CONTRACT_REF")
if contract_ref != sys.argv[2]:
    raise SystemExit(1)
scope = value("EXPECTED_RELEASE_SCOPE")
node = value("EXPECTED_RELEASE_NODE_VERSION")
package_dir_flag = value("GENERATOR_RELEASE_PACKAGE_DIR_FLAG", "'")
if package_dir_flag not in ("--package-dir", "--only-package-dir"):
    raise SystemExit(1)
dirs_match = re.search(r"^GENERATOR_RELEASE_PACKAGE_DIRS_JSON='([^']*)'$", text, re.MULTILINE)
if not dirs_match:
    raise SystemExit(1)
dirs = json.loads(dirs_match.group(1))
if not isinstance(dirs, list) or not dirs or not all(isinstance(item, str) for item in dirs):
    raise SystemExit(1)
callers = json.loads(value("EXPECTED_RELEASE_CALLER_PACKAGE_DIRS_JSON", "'"))
if not isinstance(callers, dict) or callers.get(".github/workflows/release.yml") != dirs:
    raise SystemExit(1)
for path, package_dirs in callers.items():
    if (
        not isinstance(path, str)
        or re.fullmatch(r"\.github/workflows/[A-Za-z0-9._-]+\.ya?ml", path) is None
        or any(part in (".", "..") for part in path.split("/"))
        or not isinstance(package_dirs, list)
        or not package_dirs
        or any(
            not isinstance(item, str)
            or re.fullmatch(r"[A-Za-z0-9._][A-Za-z0-9._-]*(/[A-Za-z0-9._][A-Za-z0-9._-]*)*", item) is None
            or (item != "." and any(part in (".", "..") for part in item.split("/")))
            for item in package_dirs
        )
        or len(set(package_dirs)) != len(package_dirs)
    ):
        raise SystemExit(1)
print(json.dumps({"scope": scope, "node": node, "package_dir_flag": package_dir_flag, "package_dirs": dirs, "expected_release_callers": callers}, separators=(",", ":")))
PY
  )" || {
    echo "::error::phase=audit repo=$repo result=generated-contract-parameters-invalid"
    return 1
  }
  scope="$(jq -r .scope <<<"$params")"
  node="$(jq -r .node <<<"$params")"

  gh api "repos/$ORG/.github/contents/scripts/gen-changelog-caller.sh?ref=$pin" \
    -H 'Accept: application/vnd.github.raw+json' >"$tmp/gen-changelog-caller.sh" 2>/dev/null || {
    echo "::error::phase=audit repo=$repo result=canonical-generator-unreadable pin=$pin"
    return 1
  }
  chmod +x "$tmp/gen-changelog-caller.sh"
  mkdir -p "$tmp/home"

  local args=(--scope "$scope" --node-version "$node")
  package_dir_flag="$(jq -r .package_dir_flag <<<"$params")"
  while IFS= read -r package_dir; do
    if [ "$package_dir_flag" = --package-dir ] && [ "$package_dir" = . ]; then
      continue
    fi
    args+=("$package_dir_flag" "$package_dir")
  done < <(jq -r '.package_dirs[]' <<<"$params")
  local contract_test_args=("${args[@]}")
  while IFS=$'\t' read -r caller_path caller_dirs; do
    [ "$caller_path" = .github/workflows/release.yml ] && continue
    contract_test_args+=(--release-caller-package-dirs "$caller_path=$caller_dirs")
  done < <(jq -r '.expected_release_callers | to_entries[] | [.key, (.value | join(","))] | @tsv' <<<"$params")

  # `pr-gate` takes the pin plus an optional `--untrusted-runner` label list —
  # the one knob that changes its output. That knob is not recorded anywhere
  # the audit already reads (unlike scope/node/package-dirs, which live in the
  # contract test), so it has to be recovered from the artifact's own `runs-on:`
  # line. `emit_pr_gate` renders it as a bare (unquoted) `[label, label]` flow
  # sequence, not JSON — so the shape check below matches that exact rendering,
  # byte for byte, rather than a best-effort parse that would let a hand-edited
  # `runs-on:` regenerate to match itself.
  local pr_gate_args=() runner
  IFS= read -r runner < <(sed -n 's/^ *runs-on: //p' "$tmp/actual/changelog-contract.yml") || true
  case "$runner" in
    ubuntu-24.04) ;;
    '['*']')
      local inner labels
      inner="${runner#\[}"; inner="${inner%\]}"
      if [[ "$inner" =~ ^[a-z0-9][a-z0-9_-]*(,\ [a-z0-9][a-z0-9_-]*)*$ ]]; then
        labels="${inner//, /,}"
        pr_gate_args=(--untrusted-runner "$labels")
      else
        echo "::error::phase=audit repo=$repo result=generated-contract-runner-shape-invalid path=.github/workflows/changelog-contract.yml"
        return 1
      fi
      ;;
    *)
      echo "::error::phase=audit repo=$repo result=generated-contract-runner-shape-invalid path=.github/workflows/changelog-contract.yml"
      return 1
      ;;
  esac

  local generator_env=(env -i "PATH=$PATH" "HOME=$tmp/home" "LC_ALL=C" "REPO_ROOT=$repo_root")
  if ! "${generator_env[@]}" "$tmp/gen-changelog-caller.sh" "$mode" "$pin" >"$tmp/expected/changelog.yml" 2>/dev/null ||
    ! "${generator_env[@]}" "$tmp/gen-changelog-caller.sh" renderer "$pin" >"$tmp/expected/render-next.sh" 2>/dev/null ||
    ! "${generator_env[@]}" "$tmp/gen-changelog-caller.sh" contract-test "$pin" "${contract_test_args[@]}" >"$tmp/expected/changelog-contract.test.sh" 2>/dev/null ||
    ! "${generator_env[@]}" "$tmp/gen-changelog-caller.sh" release-node "$pin" "${args[@]}" >"$tmp/expected/release.yml" 2>/dev/null ||
    ! "${generator_env[@]}" "$tmp/gen-changelog-caller.sh" pr-gate "$pin" "${pr_gate_args[@]}" >"$tmp/expected/changelog-contract.yml" 2>/dev/null; then
    echo "::error::phase=audit repo=$repo result=canonical-generation-failed pin=$pin"
    return 1
  fi

  for name in changelog.yml render-next.sh changelog-contract.test.sh release.yml changelog-contract.yml; do
    cmp -s "$tmp/actual/$name" "$tmp/expected/$name" || {
      echo "::error::phase=audit repo=$repo result=generated-contract-byte-drift artifact=$name pin=$pin"
      return 1
    }
  done
)

source_contract_for_repo() { # $1 = repo, $2 = stack
  local repo="$1" stack="$2" listing listing_count paths path stack_workflow workflow_count head='' ref_query='' changelog_state
  local stack_callers='' canonical_callers=0 changelog_callers=0 changelog_caller_path='' changelog_contract_jobs=0 source_fault=0
  local source_bytes=0 remaining_bytes source_size
  local expected_stack_job expected_changelog_job inspection path_filter

  case "$stack" in
    actions|none) return 0 ;;
  esac
  if [ -n "${RCA_HEADS_FILE:-}" ]; then
    head="$(head_for_repo "$repo")" || return 2
  else
    head="$(gh api "repos/$ORG/$repo/commits?per_page=1" --jq '.[0].sha' 2>/dev/null)" || return 2
  fi
  # The audited head is a 40-hex commit SHA; re-assert that where it builds a query string,
  # so no future caller can route arbitrary text into one.
  [[ "$head" =~ ^[0-9a-f]{40}$ ]] || return 2
  ref_query="?ref=$head"
  stack_workflow="$(stack_workflow_for "$stack")" || return 2
  expected_stack_job="$(jq -r '.caller_job_names.stack' "$CONTRACT_FILE")"
  expected_changelog_job="$(jq -r '.caller_job_names.changelog' "$CONTRACT_FILE")"
  listing="$(gh api "repos/$ORG/$repo/contents/.github/workflows$ref_query" 2>/dev/null)" ||
    return 2
  jq -e 'type == "array"' <<<"$listing" >/dev/null 2>&1 || return 2
  listing_count="$(jq 'length' <<<"$listing")" || return 2
  path_filter='.[] | select(.type == "file") | .path as $path | select($path | strings) | select($path | explode | all(. >= 32 and . != 127)) | select($path | test("^\\.github/workflows/[^/]+\\.ya?ml$"))'
  workflow_count="$(jq "[${path_filter}] | length" <<<"$listing")" || return 2
  if [ "$listing_count" -ge "$MAX_WORKFLOW_DIRECTORY_ENTRIES" ] || [ "$workflow_count" -gt "$MAX_WORKFLOW_FILES_PER_REPO" ]; then
    source_fault=1
    paths=''
  else
    paths="$(jq -r "$path_filter | .path" <<<"$listing")" || return 2
  fi

  while IFS= read -r path; do
    [ -n "$path" ] || continue
    local source_file
    source_file="$(mktemp)" || {
      source_fault=1
      break
    }
    remaining_bytes=$((MAX_WORKFLOW_BYTES_PER_REPO - source_bytes))
    if [ "$remaining_bytes" -le 0 ] || ! workflow_source_to_file "$repo" "$path" "$ref_query" "$source_file" "$remaining_bytes"; then
      source_fault=1
      rm -f -- "$source_file"
      break
    fi
    source_size="$(wc -c <"$source_file")" || {
      source_fault=1
      rm -f -- "$source_file"
      break
    }
    source_size="${source_size//[[:space:]]/}"
    source_bytes=$((source_bytes + source_size))

    local found
    inspection="$(python3 -I "$WORKFLOW_INSPECTOR" "$expected_changelog_job" <"$source_file")" || {
      source_fault=1
      rm -f -- "$source_file"
      break
    }
    path_filter="$(jq -r '.path_filter' <<<"$inspection")" || {
      source_fault=1
      rm -f -- "$source_file"
      break
    }
    found="$(caller_job_for "$stack_workflow" <"$source_file")"
    rm -f -- "$source_file"
    if [ -n "$found" ]; then
      stack_callers="$stack_callers$found"$'\n'
      # Only the CANONICALLY NAMED caller publishes the required contexts, so
      # only it is in this contract. A repository may legitimately call the
      # same reusable workflow again under another job name — a Node floor
      # matrix, a downstream-package compatibility lane — and those callers
      # publish their own differently-prefixed contexts that no rule requires.
      # Reading them as a naming violation reported five conformant
      # repositories as nonconformant and would have held the rollout on work
      # that does not exist.
      if grep -Fxq "$expected_stack_job" <<<"$found"; then
        canonical_callers=$((canonical_callers + 1))
        if [ "$path_filter" = true ]; then
          echo "::error::phase=audit repo=$repo stack=$stack result=workflow-path-filter path=$path — a required context can be absent permanently."
          return 1
        fi
      fi
    fi

    if [ "$stack" = node ] || [ "$stack" = ui ] || [ "$stack" = helm ]; then
      changelog_state="$(jq -r '.generated_changelog' <<<"$inspection")"
      case "$changelog_state" in
        valid)
          changelog_callers=$((changelog_callers + 1))
          changelog_caller_path="$path"
          if [ "$(jq -r '.pull_request' <<<"$inspection")" != true ]; then
            echo "::error::phase=audit repo=$repo stack=$stack result=workflow-trigger-missing path=$path — changelog / validate must run on pull_request."
            return 1
          fi
          if [ "$path_filter" = true ]; then
            echo "::error::phase=audit repo=$repo stack=$stack result=workflow-path-filter path=$path — changelog / validate can be absent permanently."
            return 1
          fi
          ;;
        invalid)
          echo "::error::phase=audit repo=$repo stack=$stack result=changelog-caller-invalid path=$path — require one exact generated changelog job with no name or matrix override."
          return 1
          ;;
      esac
    fi

    if [ "$stack" = node ]; then
      local contract_job_state
      contract_job_state="$(jq -r '.changelog_contract' <<<"$inspection")"
      case "$contract_job_state" in
        valid)
          changelog_contract_jobs=$((changelog_contract_jobs + 1))
          if [ "$(jq -r '.pull_request' <<<"$inspection")" != true ]; then
            echo "::error::phase=audit repo=$repo stack=$stack result=workflow-trigger-missing path=$path — changelog-contract must run on pull_request."
            return 1
          fi
          if [ "$path_filter" = true ]; then
            echo "::error::phase=audit repo=$repo stack=$stack result=workflow-path-filter path=$path — changelog-contract can be absent permanently."
            return 1
          fi
          ;;
        invalid)
          echo "::error::phase=audit repo=$repo stack=$stack result=changelog-contract-job-invalid path=$path — require an unconditional job running the generated contract test."
          return 1
          ;;
      esac
    fi
  done <<<"$paths"

  [ "$source_fault" -eq 0 ] || return 2
  if [ -z "$stack_callers" ]; then
    echo "::error::phase=audit repo=$repo stack=$stack result=stack-caller-missing expected='ci / ${stack_workflow%.yml}'"
    return 1
  fi
  if [ "$canonical_callers" -eq 0 ]; then
    local observed_callers
    observed_callers="$(printf '%s' "$stack_callers" | tr '\n' ',')"
    echo "::error::phase=audit repo=$repo stack=$stack result=caller-job-name expected=$expected_stack_job actual=${observed_callers%,}"
    return 1
  fi
  if [ "$canonical_callers" -gt 1 ]; then
    # Two jobs named `ci` in two workflow files publish two check runs under
    # one context name. Which one satisfies the rule is not defined, so this
    # is nonconformant even though the name itself is right.
    echo "::error::phase=audit repo=$repo stack=$stack result=stack-caller-duplicate expected=1 actual=$canonical_callers — one required context cannot be published by two jobs."
    return 1
  fi
  if { [ "$stack" = node ] || [ "$stack" = ui ] || [ "$stack" = helm ]; } &&
    [ "$changelog_callers" -ne 1 ]; then
    echo "::error::phase=audit repo=$repo stack=$stack result=changelog-caller-missing expected=1 actual=$changelog_callers"
    return 1
  fi
  if { [ "$stack" = node ] || [ "$stack" = ui ] || [ "$stack" = helm ]; } &&
    [ "$changelog_caller_path" != .github/workflows/changelog.yml ]; then
    echo "::error::phase=audit repo=$repo stack=$stack result=changelog-caller-path expected=.github/workflows/changelog.yml actual=$changelog_caller_path"
    return 1
  fi
  if [ "$stack" = node ] && [ "$changelog_contract_jobs" -ne 1 ]; then
    echo "::error::phase=audit repo=$repo stack=$stack result=changelog-contract-job-count expected=1 actual=$changelog_contract_jobs"
    return 1
  fi
  if [ "$stack" = node ]; then
    generated_contract_identity_for_repo "$repo" "$head" || return 1
  fi
}

# Contexts observed on one head. A `skipped` conclusion counts as OBSERVED —
# the check run exists and satisfies a required check. Only a context with no
# check run at all is missing.
contexts_for_head() { # $1 = repo, $2 = sha
  local checks statuses
  checks=$(gh api --paginate "repos/$ORG/$1/commits/$2/check-runs?per_page=100" 2>/dev/null |
    jq -s -c '[.[].check_runs[]? | .name]') || return 1
  statuses=$(gh api --paginate "repos/$ORG/$1/commits/$2/status?per_page=100" 2>/dev/null |
    jq -s -c '[.[].statuses[]? | .context]') || return 1
  jq -n --argjson a "$checks" --argjson b "$statuses" '($a + $b) | unique | .[]' -r
}

conformant=0
nonconformant=0
unclassified=0
unaudited=0
skipped=0

readonly MAX_WORKFLOW_FILE_BYTES=$((5 * 1024 * 1024))
readonly MAX_WORKFLOW_BYTES_PER_REPO=$((10 * 1024 * 1024))
readonly MAX_WORKFLOW_FILES_PER_REPO=100
readonly MAX_WORKFLOW_DIRECTORY_ENTRIES=1000

workflow_source_to_file() {
  local repo="$1" path="$2" ref_query="$3" destination="$4" remaining_bytes="$5"
  local response encoding size encoded actual_size encoded_path

  encoded_path="$(jq -nr --arg path "$path" '$path | split("/") | map(@uri) | join("/")')" || return 1
  response="$(gh api "repos/$ORG/$repo/contents/$encoded_path$ref_query" 2>/dev/null)" || return 1
  encoding="$(jq -er '.encoding | select(. == "base64" or . == "none")' <<<"$response")" || return 1
  size="$(jq -er '.size | select(type == "number" and . >= 0 and floor == .)' <<<"$response")" || return 1
  [ "$size" -le "$MAX_WORKFLOW_FILE_BYTES" ] && [ "$size" -le "$remaining_bytes" ] || return 1

  case "$encoding" in
    base64)
      jq -e 'has("content") and (.content | type == "string")' <<<"$response" >/dev/null || return 1
      encoded="$(jq -r '.content' <<<"$response")" || return 1
      printf '%s' "$encoded" | tr -d '\n' | base64 --decode >"$destination" 2>/dev/null || return 1
      ;;
    none)
      [ "$size" -gt 1000000 ] || return 1
      gh api "repos/$ORG/$repo/contents/$encoded_path$ref_query" --header 'Accept: application/vnd.github.raw+json' \
        >"$destination" 2>/dev/null || return 1
      ;;
  esac

  actual_size="$(wc -c <"$destination")" || return 1
  actual_size="${actual_size//[[:space:]]/}"
  [ "$actual_size" = "$size" ]
}

audit_repo() {
  local repo="$1" stack heads sha seen='' n=0 missing=() contract

  stack="$(stack_of "$repo")" || {
    echo "::warning::phase=audit repo=$repo result=properties-unreadable — not audited."
    unaudited=$((unaudited + 1))
    return 0
  }
  if [ -z "$stack" ]; then
    echo "::warning::phase=audit repo=$repo result=unclassified — no 'verjson-stack' property; cannot know its core contract."
    unclassified=$((unclassified + 1))
    return 0
  fi

  # Resolve the contract BEFORE sampling, and in a plain command substitution so
  # that an unknown stack's `exit 2` actually reaches this shell. Resolving it
  # inside `< <(...)` instead would send the fault to a subshell the loop never
  # inspects: the contract would arrive empty, nothing would be reported
  # missing, and a repository nobody has classified would be called CONFORMANT —
  # the one wrong answer this audit exists to prevent.
  contract="$(core_contract_for "$stack")" || exit 2

  # A declared stack with no required contexts has nothing for this audit to
  # check. That is a selection outcome, not a fault: aborting here would end an
  # unscoped run at whichever such repository sorts first and leave every other
  # repository unreported (#1213).
  if [ -z "$contract" ]; then
    if [ "$REQUIRE_VERIFIED" = true ]; then
      echo "::error::phase=audit repo=$repo stack=$stack result=unverifiable — the declared contract requires no contexts, so nothing about $repo was verified; a caller about to require a context here cannot read that as a pass."
    else
      echo "::notice::phase=audit repo=$repo stack=$stack result=skipped — the declared contract requires no contexts."
    fi
    skipped=$((skipped + 1))
    return 0
  fi

  local source_rc=0
  source_contract_for_repo "$repo" "$stack" || source_rc=$?
  if [ "$source_rc" -eq 2 ]; then
    echo "::warning::phase=audit repo=$repo result=workflow-source-unreadable — not audited."
    unaudited=$((unaudited + 1))
    return 0
  elif [ "$source_rc" -ne 0 ]; then
    nonconformant=$((nonconformant + 1))
    return 0
  fi

  heads=$(gh api "repos/$ORG/$repo/pulls?state=closed&per_page=$SAMPLE&sort=updated&direction=desc" \
    --jq '.[] | select(.merged_at != null) | .head.sha' 2>/dev/null) || {
    echo "::warning::phase=audit repo=$repo result=pulls-unreadable — not audited."
    unaudited=$((unaudited + 1))
    return 0
  }

  while read -r sha; do
    [ -n "$sha" ] || continue
    local observed
    observed=$(contexts_for_head "$repo" "$sha") || {
      echo "::warning::phase=audit repo=$repo sha=$sha result=checks-unreadable — not audited."
      unaudited=$((unaudited + 1))
      return 0
    }
    seen="$seen$observed"$'\n'
    n=$((n + 1))
  done <<<"$heads"

  if [ "$n" -eq 0 ]; then
    echo "::notice::phase=audit repo=$repo stack=$stack result=no-merged-prs — nothing to audit yet."
    unaudited=$((unaudited + 1))
    return 0
  fi

  local ctx
  # `IFS=` so a context is compared exactly as declared. The schema already
  # rejects surrounding whitespace; this makes the reader incapable of
  # hiding one that ever got past it.
  while IFS= read -r ctx; do
    [ -n "$ctx" ] || continue
    if is_merge_machinery "$ctx"; then
      fault contract merge-machinery-in-contract "stack='$stack' lists '$ctx'; requiring a merge job deadlocks the merge."
    fi
    grep -Fxq "$ctx" <<<"$seen" || missing+=("$ctx")
  done <<<"$contract"

  if [ "${#missing[@]}" -eq 0 ]; then
    echo "::notice::phase=audit repo=$repo stack=$stack result=conformant sampled=$n"
    conformant=$((conformant + 1))
  else
    # Emitted as an error because this is the exact condition that would wedge
    # the repository if the rule were written today.
    echo "::error::phase=audit repo=$repo stack=$stack result=missing-core-contexts sampled=$n missing=$(printf '%s; ' "${missing[@]}")— requiring these today would leave $repo permanently pending."
    nonconformant=$((nonconformant + 1))
  fi
}

echo "::notice::phase=start org=$ORG sample=$SAMPLE mode=read-only"
repo_list="$(repos)" ||
  fault repository-list unreadable "org=$ORG — pagination or rate-limit failure."
while read -r repo; do
  [ -n "$repo" ] || continue
  audit_repo "$repo"
done <<<"$repo_list"

echo "::notice::phase=done conformant=$conformant nonconformant=$nonconformant unclassified=$unclassified unaudited=$unaudited skipped=$skipped"
# Non-zero while any repository would be wedged, so this can gate step 3 in CI
# rather than being a report somebody remembers to read.
#
# Every clause above is negative — they count what went wrong — so an audit that
# examined nothing at all satisfies all of them. That is an acceptable answer for
# the org-wide report, whose question is "which repositories would be wedged?".
# It is not an acceptable answer for a caller about to apply a rule, which needs
# the audit to have positively verified something, so REQUIRE_VERIFIED also
# demands at least one conformant repository.
[ "$nonconformant" -eq 0 ] && [ "$unclassified" -eq 0 ] && [ "$unaudited" -eq 0 ] &&
  { [ "$REQUIRE_VERIFIED" = false ] ||
    { [ "$skipped" -eq 0 ] && [ "$conformant" -gt 0 ]; }; }
