#!/usr/bin/env bash
# Two writers deepening one shallow checkout must not abort each other
# (Verjson/.github#2003).
#
# `actions-ci-group.sh` runs a group's rows concurrently in one checkout, and more
# than one row deepens or fetches into it. Git reads `.git/shallow` when a fetch
# starts and refuses to commit its own update if another process rewrote the file
# in between: `fatal: shallow file has changed since we read it`. The hub gate
# then reddens a required check with no code cause.
#
# The interleaving is forced rather than hoped for. The first gate's fetch is held
# inside a fake upload-pack, after git has read the shallow file. The second gate
# then runs, and the first is released only once the second has either finished
# or is waiting on a lock. No network: the origin is a local bare repository.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
gate="$here/hub-changelog-validate.sh"
tmp="$(mktemp -d)"
first_pid=''
second_pid=''
cleanup() {
  : >"$tmp/hold/release" 2>/dev/null
  for pid in $first_pid $second_pid; do
    kill "$pid" 2>/dev/null
  done
  wait 2>/dev/null
  rm -rf "$tmp"
}
trap cleanup EXIT
unset GITHUB_BASE_REF

fail() {
  printf 'FAIL - %s\n' "$1"
  exit 1
}

[ -r /proc/locks ] \
  || fail "this test observes lock waiters through /proc/locks, which this host lacks"
command -v pgrep >/dev/null \
  || fail "this test walks the second writer's process tree with pgrep, which this host lacks"

write_fragment() {
  local dir="$1" file="$2" identity="$3" title="$4"
  mkdir -p "$dir/NEXT"
  printf -- '---\ndate: 2026-09-18\nissue: %s\nimpact: patch\ntitle: %s\n---\n\n%s release note.\n' \
    "$identity" "$title" "$title" >"$dir/NEXT/$file"
}

# A base with history beyond depth one, and a pull-request branch on top of it, so
# reaching the merge base genuinely requires deepening.
work="$tmp/work"
git init -q -b main "$work"
git -C "$work" config user.name test
git -C "$work" config user.email test@example.com
write_fragment "$work" 2026-09-18-issue-1000-base.md 1000 "Base entry"
git -C "$work" add -A
git -C "$work" commit -qm base
for n in 1 2 3; do
  git -C "$work" commit -q --allow-empty -m "main $n"
done
git -C "$work" switch -q -c feature
write_fragment "$work" 2026-09-18-issue-1001-branch.md 1001 "Branch entry"
git -C "$work" add -A
git -C "$work" commit -qm branch
origin="$tmp/origin.git"
git clone -q --bare "$work" "$origin"

# The shape actions-ci checks out: depth one, the pull-request branch only, no
# `refs/remotes/origin/main`.
checkout="$tmp/checkout"
git clone -q --depth 1 --branch feature "file://$origin" "$checkout" \
  || fail "cannot build the shallow checkout fixture"
[ "$(git -C "$checkout" rev-parse --is-shallow-repository)" = true ] \
  || fail "the checkout fixture is not shallow, so it cannot reproduce the race"

# Every fetch from this origin runs this upload-pack. The first one to arrive
# claims the hold and blocks until released; every later one passes straight
# through.
mkdir "$tmp/hold"
cat >"$tmp/held-upload-pack" <<SH
#!/usr/bin/env bash
if mkdir "$tmp/hold/claimed" 2>/dev/null; then
  : >"$tmp/hold/held"
  while [ ! -e "$tmp/hold/release" ]; do sleep 0.05; done
fi
exec git upload-pack "\$@"
SH
chmod +x "$tmp/held-upload-pack"
git -C "$checkout" config remote.origin.uploadpack "$tmp/held-upload-pack"

wait_for() {
  local description="$1"
  shift
  for _ in $(seq 1 600); do
    "$@" && return 0
    sleep 0.05
  done
  fail "timed out waiting until $description"
}

held() { [ -e "$tmp/hold/held" ]; }

descendants() {
  local child
  for child in $(pgrep -P "$1"); do
    printf '%s\n' "$child"
    descendants "$child"
  done
}

second_finished_or_waiting() {
  local pid
  kill -0 "$second_pid" 2>/dev/null || return 0
  for pid in $second_pid $(descendants "$second_pid"); do
    grep -E "^[0-9]+: -> [A-Z]+ +[A-Z]+ +[A-Z]+ +$pid " /proc/locks >/dev/null && return 0
  done
  return 1
}

(cd "$checkout" && exec bash "$gate" .) >"$tmp/first.log" 2>&1 &
first_pid=$!
wait_for "the first gate's fetch is held after reading the shallow file" held

(cd "$checkout" && exec bash "$gate" .) >"$tmp/second.log" 2>&1 &
second_pid=$!
wait_for "the second gate has finished or is waiting" second_finished_or_waiting

: >"$tmp/hold/release"
first_status=0
wait "$first_pid" || first_status=$?
second_status=0
wait "$second_pid" || second_status=$?
first_pid=''
second_pid=''

if [ "$first_status" -eq 0 ] && [ "$second_status" -eq 0 ]; then
  printf 'ok - two gates deepening one shallow checkout concurrently both validate\n'
else
  fail "concurrent deepens aborted a gate: first exited $first_status ($(cat "$tmp/first.log")); second exited $second_status ($(cat "$tmp/second.log"))"
fi
