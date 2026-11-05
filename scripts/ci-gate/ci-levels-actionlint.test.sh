#!/usr/bin/env bash
# Every generated ci-levels caller golden must pass the organization's real
# actionlint run (#1989). This test executes actionlint.yml's own install and
# lint steps, so their pins (version and SHA-256 for actionlint and ShellCheck),
# checksum verification, ShellCheck version guard, and lint command are the
# workflow's, never a second copy. It downloads both pinned release binaries;
# without network it fails rather than skips, because a skipped lint proves
# nothing. A negative control proves the harness rejects an undeclared
# needs-output read, the defect class #1989 fixed.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
wf="$root/.github/workflows/actionlint.yml"
config="$root/.github/actionlint.yaml"
fixtures="$here/fixtures/ci-levels"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# Write the job's pinned env as NAME=value lines and each named step's run
# block to its own file, reading the workflow exactly as the runner does.
if ! python3 - "$wf" "$tmp" <<'PY'
import re
import sys
from pathlib import Path

import yaml

workflow, out = sys.argv[1], Path(sys.argv[2])
job = yaml.safe_load(Path(workflow).read_text())["jobs"]["actionlint"]
patterns = {
    "ACTIONLINT_VERSION": r"\d+\.\d+\.\d+",
    "ACTIONLINT_SHA256": r"[0-9a-f]{64}",
    "SHELLCHECK_VERSION": r"\d+\.\d+\.\d+",
    "SHELLCHECK_SHA256": r"[0-9a-f]{64}",
}
lines = []
for name, pattern in patterns.items():
    value = str(job["env"].get(name, ""))
    if not re.fullmatch(pattern, value):
        sys.exit(f"{workflow}: {name} is missing or malformed: {value!r}")
    lines.append(f"{name}={value}\n")
(out / "pins.env").write_text("".join(lines))
steps = {step.get("name"): step for step in job["steps"]}
for name, path, required in (
    ("Install actionlint (pinned release binary)", "install-actionlint.sh", "sha256sum --check --strict"),
    ("Install ShellCheck (pinned release binary)", "install-shellcheck.sh", "sha256sum --check --strict"),
    ("Run actionlint over all workflows", "lint.sh", "./actionlint"),
):
    run = steps.get(name, {}).get("run", "")
    if required not in run:
        sys.exit(f"{workflow}: step {name!r} is missing or no longer contains {required!r}")
    (out / path).write_text(run)
PY
then
  echo "FAIL - could not extract the pinned actionlint steps from $wf"
  exit 1
fi
while IFS='=' read -r name value; do
  export "$name=$value"
done <"$tmp/pins.env"
export ACTIONLINT_CONFIG_FILE="$config"

# The install steps write into the working directory, as on the runner; the
# ShellCheck step publishes its directory through GITHUB_PATH.
tools="$tmp/tools"
mkdir -p "$tools"
: >"$tmp/github_path"
if ! (cd "$tools" && bash -e "$tmp/install-actionlint.sh") >"$tmp/install.out" 2>&1 \
  || ! (cd "$tools" && GITHUB_PATH="$tmp/github_path" bash -e "$tmp/install-shellcheck.sh") \
    >>"$tmp/install.out" 2>&1; then
  echo "FAIL - pinned actionlint $ACTIONLINT_VERSION / ShellCheck $SHELLCHECK_VERSION did not download and verify (network required):"
  cat "$tmp/install.out"
  exit 1
fi
shellcheck_dir="$(cat "$tmp/github_path")"
pass "installed checksum-verified actionlint $ACTIONLINT_VERSION and ShellCheck $SHELLCHECK_VERSION"

# lint_tree <dir>: run the workflow's lint step over <dir>/.github/workflows.
# actionlint finds that directory from the repository root, hence git init.
lint_tree() {
  local dir="$1"
  git init -q "$dir"
  cp "$tools/actionlint" "$dir/actionlint"
  (cd "$dir" && PATH="$shellcheck_dir:$PATH" bash -e "$tmp/lint.sh") >"$dir.out" 2>&1
}

goldens="$tmp/goldens"
mkdir -p "$goldens/.github/workflows"
count=0
for golden in "$fixtures"/*.golden.yml; do
  [ -f "$golden" ] || continue
  cp "$golden" "$goldens/.github/workflows/"
  count=$((count + 1))
done
if [ "$count" -lt 4 ]; then
  fail "expected at least the 4 ci-levels goldens, found $count in $fixtures"
elif lint_tree "$goldens"; then
  pass "all $count ci-levels caller goldens pass the pinned actionlint and ShellCheck"
else
  fail "a ci-levels caller golden fails the pinned actionlint run:"
  cat "$goldens.out"
fi

control="$tmp/control"
mkdir -p "$control/.github/workflows"
cat >"$control/.github/workflows/undeclared-output.yml" <<'YAML'
name: undeclared needs output
on: push
jobs:
  preflight:
    runs-on: ubuntu-24.04
    outputs:
      declared: ${{ steps.plan.outputs.declared }}
    steps:
      - id: plan
        run: echo "declared=true" >>"$GITHUB_OUTPUT"
  reader:
    needs: preflight
    runs-on: ubuntu-24.04
    steps:
      - run: echo "${{ needs.preflight.outputs.undeclared }}"
YAML
if ! lint_tree "$control" && grep -q 'property "undeclared" is not defined' "$control.out"; then
  pass "the harness rejects a read of an undeclared needs output"
else
  fail "the harness accepted a read of an undeclared needs output:"
  cat "$control.out"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
