#!/usr/bin/env bash
set -euo pipefail

# One shellcheck process per core. The files are independent, and a single
# process is what made this command a minute of the platform shard (#1736).
shellcheck_jobs="$(nproc 2>/dev/null || echo 2)"
[ "$shellcheck_jobs" -gt 8 ] && shellcheck_jobs=8
[ "$shellcheck_jobs" -ge 1 ] || shellcheck_jobs=1
git ls-files -z -- '*.sh' \
  | xargs -0 -r -P "$shellcheck_jobs" shellcheck --severity=warning --
