#!/usr/bin/env bash
# Prove the release-artifact publish re-verify step refuses a tampered download (#2036).
set -euo pipefail
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

sha="$(git -C "$repo_root" rev-parse HEAD)"
bash "$repo_root/scripts/gen-changelog-caller.sh" release-artifact "$sha" \
  --build-runner ubuntu-24.04 >"$tmp/release.yml"

python3 - "$tmp/release.yml" "$tmp/reverify.sh" <<'PY'
import re
import sys
from pathlib import Path

workflow = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(
    r"- name: Re-verify each downloaded artifact against the build digests\n"
    r"        env:\n(?:          .*\n)*?"
    r"        run: \|\n((?:          .*\n)+)",
    workflow,
)
if match is None:
    raise SystemExit(
        "re-verify step missing from generated release-artifact publish job"
    )
body_lines = []
for line in match.group(1).splitlines():
    if line.startswith("          "):
        body_lines.append(line[10:])
    else:
        body_lines.append(line)
Path(sys.argv[2]).write_text(
    "#!/usr/bin/env bash\nset -euo pipefail\n" + "\n".join(body_lines) + "\n",
    encoding="utf-8",
)
PY
chmod +x "$tmp/reverify.sh"

setup_tree() {
  local root="$1"
  mkdir -p "$root/release-artifacts" "$root/release-artifact-digests"
  printf 'good\n' >"$root/release-artifacts/asset.bin"
  local digest
  digest="$(sha256sum "$root/release-artifacts/asset.bin" | cut -d' ' -f1)"
  {
    printf '%s\n' 'run_id=1'
    printf '%s\n' 'run_attempt=1'
    printf '%s\n' 'job_index=0'
    printf '%s\n' '---'
    printf 'asset.bin\t%s\n' "$digest"
  } >"$root/release-artifact-digests/digests-0.tsv"
}

good="$tmp/good"
setup_tree "$good"
( cd "$good" && RUN_ID=1 RUN_ATTEMPT=1 bash "$tmp/reverify.sh" ) \
  && pass "matching digests admit the downloaded artifacts" \
  || fail "matching digests were refused"

tampered="$tmp/tampered"
setup_tree "$tampered"
printf 'evil\n' >"$tampered/release-artifacts/asset.bin"
if ( cd "$tampered" && RUN_ID=1 RUN_ATTEMPT=1 bash "$tmp/reverify.sh" ) >/dev/null 2>&1; then
  fail "tampered artifact bytes were admitted"
else
  pass "tampered artifact bytes are refused"
fi

swapped="$tmp/swapped"
setup_tree "$swapped"
printf 'extra\n' >"$swapped/release-artifacts/extra.bin"
if ( cd "$swapped" && RUN_ID=1 RUN_ATTEMPT=1 bash "$tmp/reverify.sh" ) >/dev/null 2>&1; then
  fail "undigested extra artifact was admitted"
else
  pass "undigested extra artifact is refused"
fi

wrong_run="$tmp/wrong-run"
setup_tree "$wrong_run"
if ( cd "$wrong_run" && RUN_ID=9 RUN_ATTEMPT=1 bash "$tmp/reverify.sh" ) >/dev/null 2>&1; then
  fail "digest manifest from another run was admitted"
else
  pass "digest manifest from another run is refused"
fi

[ "$fails" -eq 0 ]
