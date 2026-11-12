#!/usr/bin/env bash
# Exercises scripts/ci-gate/merge-admission-plan.sh, which lists the pull-request heads one
# merge-admission producer run evaluates (ADR 0260, #1990, #2031), against a stubbed `gh`.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
plan="$here/merge-admission-plan.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

REPO=Verjson/example
SHA_A=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
SHA_B=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
SHA_C=cccccccccccccccccccccccccccccccccccccccc

mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -uo pipefail
printf 'gh %s\n' "$*" >>"$CALLS"
case "$*" in
  *"repos/"*"/pulls?state=open&per_page=100"*)
    [ "${FAIL_LIST:-0}" -eq 0 ] || { echo "gh: Server Error (HTTP 502)" >&2; exit 1; }
    printf '%s\n' "$PULLS_FIXTURE" ;;
  *) echo "gh: unstubbed call: $*" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls"

pull() { # number head-sha draft
  printf '{"number":%s,"draft":%s,"head":{"sha":"%s"}}' "$1" "$3" "$2"
}
reset_env() {
  : >"$CALLS"
  unset FAIL_LIST
  export PULLS_FIXTURE
  PULLS_FIXTURE="[$(pull 7 "$SHA_A" false),$(pull 8 "$SHA_B" true),$(pull 9 "$SHA_C" false)]"
}
run() {
  OUT="$(bash "$plan" "$@" 2>"$tmp/err")"
  RC=$?
  ERR="$(cat "$tmp/err")"
}

# --- sweep -------------------------------------------------------------------------------------
# A draft cannot be merged, and marking it ready is itself a trigger, so the sweep skips it.
reset_env
run "$REPO"
if [ "$RC" -eq 0 ] && [ "$OUT" = "[{\"pr\":7,\"head_sha\":\"$SHA_A\"},{\"pr\":9,\"head_sha\":\"$SHA_C\"}]" ]; then
  pass "a sweep plans every open pull request that is not a draft, by number and head"
else
  fail "a sweep plans every open pull request that is not a draft, by number and head (rc=$RC, output: $OUT, stderr: $ERR)"
fi

# --- one event head -----------------------------------------------------------------------------
# A completed workflow or check suite names one head; only pull requests at that head are
# evaluated, since the event changed nothing on any other.
reset_env
run "$REPO" "$SHA_C"
if [ "$RC" -eq 0 ] && [ "$OUT" = "[{\"pr\":9,\"head_sha\":\"$SHA_C\"}]" ]; then
  pass "an event head plans only the open pull requests at that head"
else
  fail "an event head plans only the open pull requests at that head (rc=$RC, output: $OUT, stderr: $ERR)"
fi

# --- an unreadable listing plans nothing, and the producer succeeds -----------------------------
# ADR 0260 precondition (a): the producer's own checks conclude success on error; the next
# trigger or sweep evaluates again.
reset_env
export FAIL_LIST=1
run "$REPO"
if [ "$RC" -eq 0 ] && [ "$OUT" = "[]" ] \
  && grep -qF "::warning::merge-admission could not list the open pull requests of $REPO, so this run evaluates none" <<<"$ERR"; then
  pass "an unreadable pull-request listing plans nothing, names why, and succeeds"
else
  fail "an unreadable pull-request listing plans nothing, names why, and succeeds (rc=$RC, output: $OUT, stderr: $ERR)"
fi

# --- only well-formed entries reach the evaluation ----------------------------------------------
# The evaluation refuses a malformed number or head as a usage error, which would fail the
# producer's own job; the plan drops such an entry instead, and says so.
reset_env
PULLS_FIXTURE="[$(pull '"7"' "$SHA_A" false),$(pull 8 "${SHA_B^^}" false),$(pull 9 "$SHA_C" false)]"
run "$REPO"
if [ "$RC" -eq 0 ] && [ "$OUT" = "[{\"pr\":9,\"head_sha\":\"$SHA_C\"}]" ] \
  && grep -qF "::warning::merge-admission skipped 2 open pull request(s) of $REPO whose number or head SHA is malformed" <<<"$ERR"; then
  pass "an open pull request with a malformed number or head is skipped and named"
else
  fail "an open pull request with a malformed number or head is skipped and named (rc=$RC, output: $OUT, stderr: $ERR)"
fi

# A page that is not an array, such as an error body, is no listing at all.
reset_env
PULLS_FIXTURE='{"message":"Bad credentials","status":"401"}'
run "$REPO"
if [ "$RC" -eq 0 ] && [ "$OUT" = "[]" ] \
  && grep -qF "::warning::merge-admission could not list the open pull requests of $REPO, so this run evaluates none" <<<"$ERR"; then
  pass "a listing page that is not an array plans nothing, names why, and succeeds"
else
  fail "a listing page that is not an array plans nothing, names why, and succeeds (rc=$RC, output: $OUT, stderr: $ERR)"
fi

# --- pagination -------------------------------------------------------------------------------
# `gh api --paginate` prints one array per page, back to back. A pull request on a later page
# that is never planned never gets a superseding check, so it keeps whatever result it had.
reset_env
PULLS_FIXTURE="[$(pull 7 "$SHA_A" false)]
[$(pull 9 "$SHA_C" false)]"
run "$REPO"
if [ "$RC" -eq 0 ] && [ "$OUT" = "[{\"pr\":7,\"head_sha\":\"$SHA_A\"},{\"pr\":9,\"head_sha\":\"$SHA_C\"}]" ] && [ -z "$ERR" ]; then
  pass "a paginated listing plans the open pull requests of every page"
else
  fail "a paginated listing plans the open pull requests of every page (rc=$RC, output: $OUT, stderr: $ERR)"
fi

if [ "$fails" -ne 0 ]; then
  printf '%s merge-admission plan case(s) failed\n' "$fails" >&2
  exit 1
fi
echo "merge-admission plan: all cases passed"
