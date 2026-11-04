#!/usr/bin/env bash
set -Eeuo pipefail
# `set -e` ends the run on a failed bare assertion without saying which one, so
# name it. `-E` carries the trap into functions; a subshell keeps its own
# errexit, and its failure is reported where the parent sees it.
report_failed_assertion() {
  local status=$?
  if [ "$BASH_SUBSHELL" -eq 0 ]; then
    printf 'FAIL - %s line %s: %s (exit %s)\n' "${0##*/}" "$1" "$2" "$status" >&2
    exit "$status"
  fi
}
trap 'report_failed_assertion "$LINENO" "$BASH_COMMAND"' ERR

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

awk '$0=="      - name: Prepare failed DeepSeek pass 1 replay"{f=1;next} f&&$0=="        run: |"{r=1;next} r{if($0~/^      - name:/)exit;sub(/^          /,"");print}' \
  "$workflow" >"$tmp/handoff.sh"
[ -s "$tmp/handoff.sh" ] && bash -n "$tmp/handoff.sh" \
  || { echo "FAIL - could not extract the DeepSeek pass 1 replay handoff from $workflow" >&2; exit 1; }

cat >"$tmp/prepare-deepseek-replay.py" <<'PY'
import json
import os
import sys
from pathlib import Path

Path(os.environ["CAPTURE"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
    output.write("available=false\n")
PY

export RUNNER_TEMP="$tmp" GITHUB_OUTPUT="$tmp/output" CAPTURE="$tmp/args.json"
export TRANSPORT=success USABLE=false PUBLICATION=success
export EXPECTED_MODEL=deepseek-v4-pro EXPECTED_HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export EXPECTED_SENSITIVE=false EXPECTED_TRUSTED_REVIEW_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
export AUTHORIZATION_CHECK_ID=9001 TARGET_REPO=Verjson/example PR_NUMBER=7

diagnostic_json='{"path":"findings[0].evidence","expected":"exact fragment","observed":"fragment mismatch"}'
DIAGNOSTIC="$diagnostic_json" bash "$tmp/handoff.sh"
python3 - "$tmp/args.json" "$diagnostic_json" <<'PY'
import json
import sys

args = json.load(open(sys.argv[1], encoding="utf-8"))
index = args.index("--diagnostic")
assert args[index + 1] == sys.argv[2]
assert json.loads(args[index + 1])["observed"] == "fragment mismatch"
PY

DIAGNOSTIC='' bash "$tmp/handoff.sh"
python3 - "$tmp/args.json" <<'PY'
import json
import sys

args = json.load(open(sys.argv[1], encoding="utf-8"))
assert args[args.index("--diagnostic") + 1] == "{}"
PY

# A missing helper after successful admission must remain a real preparation error.
rm "$tmp/prepare-deepseek-replay.py"
if bash "$tmp/handoff.sh" >"$tmp/missing-helper.log" 2>&1; then
  echo "missing replay helper unexpectedly succeeded" >&2
  exit 1
fi
grep -q 'prepare-deepseek-replay.py' "$tmp/missing-helper.log" \
  || { echo "FAIL - a missing replay helper failed without naming it" >&2; cat "$tmp/missing-helper.log" >&2; exit 1; }

echo "DeepSeek replay shell handoff: ok"
