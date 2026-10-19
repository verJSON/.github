#!/usr/bin/env bash
# Pin the review model's PR-head versus base-branch evidence boundary (#377).
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
workflow="$repo_root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

prompt="$tmp/review-prompt.txt"
awk '
  $0 == "            cat <<EOF" { capture = 1; next }
  capture && $0 == "          EOF" { exit }
  capture { print }
' "$workflow" >"$prompt"

grep -qF 'You are the autonomous merge gate' "$prompt" || {
  echo "FAIL - could not extract the active review prompt from $workflow"
  exit 1
}

grep -qF 'The checked-out workspace and git HEAD are the PR head, not the base or default branch.' "$prompt" \
  && pass "the prompt identifies HEAD as the untrusted PR head" \
  || fail "the prompt does not distinguish PR HEAD from the base branch"

grep -qF 'Never use HEAD, the current checkout, or matching blob hashes as evidence that content is already on the base branch.' "$prompt" \
  && pass "the prompt forbids the false duplicate evidence from PR #376" \
  || fail "the prompt permits PR-head evidence for base-branch claims"

grep -qF 'Do not block because you infer this PR submission is stale, duplicate, closed, or already merged; the deterministic API recheck owns PR lifecycle state.' "$prompt" \
  && pass "deterministic code, not the model, owns PR lifecycle state" \
  || fail "the model can still block on unverifiable PR lifecycle state"

grep -qF 'Still review duplicate-processing and idempotency defects in the proposed behavior normally.' "$prompt" \
  && pass "the lifecycle boundary preserves duplicate-behavior review" \
  || fail "the prompt can suppress duplicate-processing defects"

# #323/#394: exercise the shipped context-preparation block, including the
# bounded base-history fetch and a diff larger than GitHub's 300-file endpoint
# limit, against stateful gh/git stubs.
prep="$tmp/prep.sh"
awk '
  $0 == "      - name: Prepare bounded review context" { seen = 1 }
  seen && $0 == "        run: |" { cap = 1; next }
  cap && $0 ~ /^      - name:/ { exit }
  cap {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[[:space:]]*$/) { print ""; next }
    exit
  }
' "$workflow" >"$prep"
sed -i 's/${{ needs.preflight.outputs.head_sha }}/0123456789abcdef0123456789abcdef01234567/g' "$prep"
grep -q 'pr.full.diff' "$prep" || {
  echo "FAIL - could not extract the active context-preparation block"
  exit 1
}

mkdir -p "$tmp/bin" "$tmp/run" "$tmp/runner-temp"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
if [ "$1 $2" = "pr view" ]; then
  case "$*" in
    *headRefOid*) printf '%s\n' 0123456789abcdef0123456789abcdef01234567 ;;
    *) printf '%s\n' '{"title":"test","body":"","author":{"login":"author"},"labels":[],"baseRefName":"main","headRefName":"feature","additions":1,"deletions":1,"changedFiles":1}' ;;
  esac
  exit 0
fi
exit 2
GH
cat >"$tmp/bin/git" <<'GIT'
#!/usr/bin/env bash
case "$1" in
  fetch)
    count=$(cat "$FETCH_COUNT")
    count=$((count + 1))
    printf '%s\n' "$count" >"$FETCH_COUNT"
    if [ "$count" -le "${FETCH_FAILURES:-0}" ]; then
      echo "remote response-marker-must-stay-masked" >&2
      exit 1
    fi
    ;;
  merge-base)
    [ "${NO_MERGE_BASE:-false}" != true ] && printf '%040d\n' 1 || exit 1
    ;;
  rev-parse)
    printf '%s\n' true
    ;;
  diff)
    case " $* " in
      *" --raw "*" :(glob)"*) exit 0 ;;
      *" --name-status "*) for n in $(seq 1 301); do printf 'M\0src/file-%03d.ts\0' "$n"; done; exit 0 ;;
    esac
    for n in $(seq 1 301); do
      printf 'diff --git a/src/file-%03d.ts b/src/file-%03d.ts\n' "$n" "$n"
      printf '@@ -0,0 +1 @@\n+export const value%03d = true\n' "$n"
    done
    ;;
  *) exit 2 ;;
esac
GIT
chmod +x "$tmp/bin/gh" "$tmp/bin/git"

run_prep() {
  printf '0\n' >"$tmp/fetch-count"
  : >"$tmp/github-output.txt"
  (
    cd "$tmp/run" || exit
    PATH="$tmp/bin:$PATH" GH_TOKEN=token TARGET_REPO=Verjson/example PR_NUMBER=7 RUNNER_TEMP="$tmp/runner-temp" \
      DEPENDENCY_MAJOR=false FETCH_COUNT="$tmp/fetch-count" \
      GITHUB_OUTPUT="$tmp/github-output.txt" FETCH_FAILURES="${1-0}" NO_MERGE_BASE="${2-false}" \
      bash "$prep"
  ) >"$tmp/prep.out" 2>&1
}

run_prep 1 \
  && [ "$(cat "$tmp/fetch-count")" -eq 2 ] \
  && grep -q 'result=retry' "$tmp/prep.out" \
  && review_dir="$(sed -n 's/^review_dir=//p' "$tmp/github-output.txt")" \
  && [ -n "$review_dir" ] \
  && [ "$(grep -c '^diff --git ' "$review_dir/pr.full.diff")" -eq 301 ] \
  && pass "base fetch retries once and renders all 301 changed files locally" \
  || fail "large local diff did not recover through the bounded base fetch"

run_prep 9 \
  && fail "persistent base fetch failure was allowed through" \
  || {
    [ "$(cat "$tmp/fetch-count")" -eq 4 ] \
      && grep -q 'kind=infrastructure_unavailable' "$tmp/prep.out" \
      && grep -q '^review_input_failure=infrastructure_unavailable$' "$tmp/github-output.txt" \
      && ! grep -q 'response-marker-must-stay-masked' "$tmp/prep.out" \
      && pass "exhausted base fetch fails closed with typed infrastructure state" \
      || fail "exhausted base fetch lacks exponential backoff, masking, or typed failure evidence"
  }

run_prep 0 true \
  && fail "missing merge base was allowed through" \
  || {
    [ "$(cat "$tmp/fetch-count")" -eq 2 ] \
      && grep -q 'kind=merge_base_unavailable' "$tmp/prep.out" \
      && grep -q '^review_input_failure=merge_base_unavailable$' "$tmp/github-output.txt" \
      && pass "missing merge base retries with full history, then fails closed" \
      || fail "missing merge base skipped the unshallow fallback or typed failure"
  }

! grep -q 'gh pr diff' "$prep" \
  && pass "review input no longer depends on GitHub's whole-diff endpoint" \
  || fail "the 300-file-limited whole-diff endpoint remains on the review path"

# Exercise the exact shallow-history failure that a fully mocked git cannot
# represent: a feature branch whose fork point is deeper than fetch-depth 2.
real_git="$(command -v git)"
fixture="$tmp/shallow"
mkdir -p "$fixture"
"$real_git" init --bare --initial-branch=main "$fixture/remote.git" >/dev/null
"$real_git" init -b main "$fixture/seed" >/dev/null
(
  cd "$fixture/seed" || exit
  git config user.name test
  git config user.email test@example.com
  printf 'base\n' >file
  git add file
  git commit -m base >/dev/null
  git remote add origin "$fixture/remote.git"
  git push --quiet -u origin main
  git switch --quiet -c feature
  for n in 1 2 3; do
    printf '%s\n' "$n" >>file
    git commit -am "feature $n" >/dev/null
  done
  git push --quiet -u origin feature
)
"$real_git" clone --quiet --depth=2 --branch feature "file://$fixture/remote.git" "$fixture/checkout"
(
  cd "$fixture/checkout" || exit
  git fetch --no-tags --depth=100 origin \
    "+refs/heads/main:refs/remotes/origin/main" >/dev/null 2>&1
  [ -z "$(git merge-base origin/main HEAD 2>/dev/null || true)" ] \
    && git fetch --no-tags --unshallow origin \
      "+refs/heads/main:refs/remotes/origin/main" >/dev/null 2>&1 \
    && [ -n "$(git merge-base origin/main HEAD 2>/dev/null || true)" ]
) \
  && pass "unshallow fallback restores a merge base for a three-commit PR" \
  || fail "real fetch-depth 2 fixture cannot recover its merge base"

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
