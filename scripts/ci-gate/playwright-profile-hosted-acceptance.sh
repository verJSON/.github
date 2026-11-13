#!/usr/bin/env bash
set -euo pipefail

if [[ "${RUNNER_OS:-}" != Linux || "${RUNNER_ENVIRONMENT:-}" != github-hosted ]]; then
  echo "profile acceptance requires a GitHub-hosted Linux runner" >&2
  exit 1
fi

source /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
  echo "profile acceptance requires Ubuntu 24.04" >&2
  exit 1
fi

if [[ ! "${GITHUB_RUN_ID:-}" =~ ^[0-9]{1,20}$ || ! "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]{1,6}$ ]]; then
  echo "profile acceptance requires GitHub run identity" >&2
  exit 1
fi

profile='playwright-chromium-1.63.0-ubuntu-24.04-amd64'
state_file="/run/verjson-playwright-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-hosted-acceptance.json"
receipt_identity=""
helper='scripts/ci_playwright_system_libraries.py'

run_helper() {
  sudo /usr/bin/env -i \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    HOME=/nonexistent \
    DEBIAN_FRONTEND=noninteractive \
    LC_ALL=C \
    APT_CONFIG=/dev/null \
    RUNNER_ENVIRONMENT=github-hosted \
    RUNNER_OS=Linux \
  "RUNNER_IMAGE_OS=${ImageOS:-}" \
  "RUNNER_IMAGE_VERSION=${ImageVersion:-}" \
    VERJSON_CI_SECRETLESS_MODE=true \
    /usr/bin/python3 -I "$helper" "$@"
}

cleanup_receipt() {
  if [[ -n "$receipt_identity" ]] && { sudo test -e "$state_file" || sudo test -L "$state_file"; }; then
    run_helper cleanup --state-file "$state_file" --expected-identity "$receipt_identity"
  fi
}
trap cleanup_receipt EXIT

if sudo test -e "$state_file" || sudo test -L "$state_file"; then
  echo "profile acceptance receipt path already exists" >&2
  exit 1
fi

run_helper provision \
  --profile "$profile" \
  --state-file "$state_file" \
  --runner-environment "$RUNNER_ENVIRONMENT" \
  --runner-os "$RUNNER_OS" \
  --require-nonempty-plan
receipt_identity="$(sudo /usr/bin/stat -c '%d:%i' -- "$state_file")"
[[ "$receipt_identity" =~ ^[0-9]+:[0-9]+$ ]]
run_helper verify --profile "$profile" --state-file "$state_file"
run_helper cleanup --state-file "$state_file" --expected-identity "$receipt_identity"
if sudo test -e "$state_file" || sudo test -L "$state_file"; then
  echo "profile acceptance receipt was not removed" >&2
  exit 1
fi
trap - EXIT
