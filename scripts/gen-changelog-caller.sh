#!/usr/bin/env bash
# Generate a consumer's changelog contract adoption files.
#
# Generated, not hand-written, because the two halves must agree on one commit
# and nothing fails loudly when they don't: the renderer keeps rendering, the
# workflow keeps validating, and local output silently stops predicting CI.
# Hand-writing this three times already produced three shapes.
#
# Usage:
#   scripts/gen-changelog-caller.sh workflow <sha> > .github/workflows/changelog.yml  # canonical compatibility alias
#   scripts/gen-changelog-caller.sh generated-artifacts <sha> > .github/workflows/changelog.yml
#   scripts/gen-changelog-caller.sh generated-artifacts-with-adr-index <sha> > .github/workflows/changelog.yml
#   scripts/gen-changelog-caller.sh renovate-attribution <sha> > .github/workflows/renovate-changelog.yml
#   scripts/gen-changelog-caller.sh adr-index-generator <sha> > scripts/gen-adr-index.sh
#   scripts/gen-changelog-caller.sh adr-index-test <sha> > scripts/gen-adr-index.test.sh
#   scripts/gen-changelog-caller.sh renderer <sha> > scripts/render-next.sh
#   scripts/gen-changelog-caller.sh contract-test <sha> [--scope <scope>] [--node-version <version>] > scripts/changelog-contract.test.sh
#   scripts/gen-changelog-caller.sh codeowners <sha> > .github/CODEOWNERS
#   scripts/gen-changelog-caller.sh pr-gate <sha> [--untrusted-runner <label>[,<label>...]] > .github/workflows/changelog-contract.yml
#   scripts/gen-changelog-caller.sh release-node <sha> [--scope <scope>] [--node-version <version>] [--default-prefix <prefix> --default-component <component>] [--release-asset <path>]... > .github/workflows/release.yml
#   scripts/gen-changelog-caller.sh release-artifact <sha> --build-runner <selector>... [--approved-internal-package <@verjson/name>]... [--scope <scope>] [--node-version <version>] [--default-prefix <prefix> --default-component <component>] > .github/workflows/release.yml
#   scripts/gen-changelog-caller.sh release-snapshot <sha> [--scope <scope>] [--node-version <version>] [--default-prefix <prefix> --default-component <component>] [--package-dir <relative-dir>]... [--only-package-dir <relative-dir>]... > .github/workflows/release.yml
#   scripts/gen-changelog-caller.sh release-propose <sha> --autonomy {propose|dispatch} > .github/workflows/release-propose.yml
#
# Every changelog-enabled workflow mode publishes the organization ruleset's
# required check: changelog / validate.
#
# `pr-gate` is generated separately from repository-specific CI so the required
# changelog-contract job cannot miss runner-safety fixes when adopters repin.
# It defaults to GitHub-hosted `ubuntu-24.04`: the job runs `pull_request`,
# so `actions/checkout` resolves the PR's own ref and the job then executes
# PR-authored `scripts/changelog-contract.test.sh` from that checkout. A
# persistent self-hosted runner turns that into arbitrary code execution from
# any PR author; an ephemeral hosted runner does not. Adopters with a genuinely
# isolated, ephemeral, per-job untrusted-PR lane may opt into it explicitly
# with `--untrusted-runner` (#935) — never default to self-hosted here.
# `release-node` is the fifth output. It was added because the
# release caller was the one adopter file still hand-copied from a sibling, and
# every defect in the copied shape propagated to every migrated repository at
# once: verification running after the irreversible snapshot (#463, #464),
# `npm ci` installing with GITHUB_TOKEN (#465), and the two halves of one release
# landing on two runner pools (#465). Adopters with nothing to publish keep
# having no release caller at all; that is still a supported shape.
#
# `release-artifact` (#975) is for an adopter with nothing to publish to a
# registry but that does ship GitHub Release assets — an Electron desktop app's
# OS installers, concretely. It shares release-node's immutable
# verify -> snapshot boundary unchanged and replaces publish's delegation to
# node-release.yml with a caller-declared --build-runner matrix plus an inlined,
# restart-safe GitHub Release publication step.
#
# `release-snapshot` (#1206) is for an adopter that publishes NOTHING from the
# release workflow — a repository whose container images ship from a separate,
# independently triggered workflow — but that still cuts versioned releases and
# therefore still accumulates NEXT/ fragments needing a snapshot. "No release
# caller at all" is correct only for an adopter that cuts no releases; for this
# one it means NEXT/ is never consumed, because changelog-release.yml is
# workflow_call and both other callers bolt on a publication stage it cannot
# satisfy. It keeps verify -> snapshot unchanged and reduces publish to the tag's
# GitHub Release, created from the immutable snapshot with no artifacts attached.
#
# Consumers pin an immutable commit (docs/changelog/README.md) rather than a
# branch: the contract defines their release history's shape, so it must not
# move under them between a local render and the CI run that gates the PR.
#
# The contract test is generated for a second reason on top of pin agreement: it
# is the only adopter file that encodes assumptions about repository *state*, and
# every hand-copied version so far asserted a pre-release tree — named fragment
# titles, hashed released entries, "no CHANGELOG.md yet" — which the first real
# release deletes. Consumers wire it into `npm test`, which release workflows run
# before publishing, so that shape aborts the release it is supposed to protect.
# See #309.
set -euo pipefail

usage() {
  echo "usage: $(basename "$0") {workflow|generated-artifacts|generated-artifacts-with-adr-index|renovate-attribution|adr-index-generator|adr-index-test|codeowners|renderer|contract-test|pr-gate|release-node|release-artifact|release-snapshot|release-propose} <40-hex-commit> [--scope <npm-scope>] [--node-version <version>] [--default-prefix <prefix> --default-component <component>] [--package-dir <relative-dir>]... [--only-package-dir <relative-dir>]... [--release-asset <path>]... [--build-runner <selector>]... [--approved-internal-package <@verjson/name>]... [--autonomy {propose|dispatch}] [--untrusted-runner <label>[,<label>...]]" >&2
  echo "required check: changelog / validate" >&2
  exit 2
}

[ "$#" -ge 2 ] || usage
mode="$1"
ref="$2"
shift 2

release_scope="@verjson"
release_node_version="24"
release_scope_set=false
release_node_version_set=false
release_default_prefix="v"
release_default_component=""
release_default_prefix_set=false
release_default_component_set=false
release_package_dirs=(".")
release_package_dirs_set=false
release_package_dirs_exact=false
release_assets=()
release_build_runners=()
release_approved_internal_packages=()
release_autonomy=""
pr_gate_untrusted_runner=""
pr_gate_untrusted_runner_set=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --scope)
      [ "$#" -ge 2 ] && [ "$release_scope_set" = false ] || usage
      release_scope="$2"
      release_scope_set=true
      shift 2
      ;;
    --node-version)
      [ "$#" -ge 2 ] && [ "$release_node_version_set" = false ] || usage
      release_node_version="$2"
      release_node_version_set=true
      shift 2
      ;;
    --default-prefix)
      [ "$#" -ge 2 ] && [ "$release_default_prefix_set" = false ] || usage
      release_default_prefix="$2"
      release_default_prefix_set=true
      shift 2
      ;;
    --default-component)
      [ "$#" -ge 2 ] && [ "$release_default_component_set" = false ] || usage
      release_default_component="$2"
      release_default_component_set=true
      shift 2
      ;;
    --package-dir)
      [ "$#" -ge 2 ] && [ "$release_package_dirs_exact" = false ] || usage
      release_package_dirs+=("$2")
      release_package_dirs_set=true
      shift 2
      ;;
    --only-package-dir)
      [ "$#" -ge 2 ] || usage
      if [ "$release_package_dirs_exact" = false ]; then
        [ "$release_package_dirs_set" = false ] || usage
        release_package_dirs=()
        release_package_dirs_exact=true
      fi
      release_package_dirs+=("$2")
      release_package_dirs_set=true
      shift 2
      ;;
    --release-asset)
      [ "$#" -ge 2 ] || usage
      release_assets+=("$2")
      shift 2
      ;;
    --build-runner)
      [ "$#" -ge 2 ] || usage
      release_build_runners+=("$2")
      shift 2
      ;;
    --approved-internal-package)
      [ "$#" -ge 2 ] || usage
      release_approved_internal_packages+=("$2")
      shift 2
      ;;
    --autonomy)
      [ "$#" -ge 2 ] && [ -z "$release_autonomy" ] || usage
      release_autonomy="$2"
      shift 2
      ;;
    --untrusted-runner)
      [ "$#" -ge 2 ] && [ "$pr_gate_untrusted_runner_set" = false ] || usage
      pr_gate_untrusted_runner="$2"
      pr_gate_untrusted_runner_set=true
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

if { [ "$release_scope_set" = true ] || [ "$release_node_version_set" = true ] \
  || [ "$release_package_dirs_set" = true ]; } \
  && [ "$mode" != release-node ] && [ "$mode" != release-artifact ] \
  && [ "$mode" != release-snapshot ] && [ "$mode" != contract-test ]; then
  echo "$(basename "$0"): release parameters are accepted only by release-node, release-artifact, release-snapshot and contract-test" >&2
  exit 2
fi
if { [ "$release_default_prefix_set" = true ] || [ "$release_default_component_set" = true ]; } \
  && [ "$mode" != release-node ] && [ "$mode" != release-artifact ] \
  && [ "$mode" != release-snapshot ]; then
  echo "$(basename "$0"): release defaults are accepted only by release-node, release-artifact and release-snapshot" >&2
  exit 2
fi
if [ "${#release_build_runners[@]}" -gt 0 ] \
  && [ "$mode" != release-artifact ] && [ "$mode" != contract-test ]; then
  echo "$(basename "$0"): --build-runner is accepted only by release-artifact and contract-test" >&2
  exit 2
fi
if [ "${#release_approved_internal_packages[@]}" -gt 0 ] \
  && [ "$mode" != release-artifact ] && [ "$mode" != contract-test ]; then
  echo "$(basename "$0"): --approved-internal-package is accepted only by release-artifact and contract-test" >&2
  exit 2
fi
if [ "${#release_assets[@]}" -gt 0 ] && [ "$mode" != release-node ] && [ "$mode" != contract-test ]; then
  echo "$(basename "$0"): --release-asset is accepted only by release-node and contract-test" >&2
  exit 2
fi
if [ "${#release_assets[@]}" -gt 16 ]; then
  echo "$(basename "$0"): at most 16 --release-asset paths are accepted" >&2
  exit 2
fi
release_asset_names=()
release_assets_seen=()
for release_asset in "${release_assets[@]}"; do
  [[ "$release_asset" =~ ^[A-Za-z0-9._][A-Za-z0-9._-]*(/[A-Za-z0-9._][A-Za-z0-9._-]*)*$ ]] \
    && [[ "/$release_asset/" != */./* ]] && [[ "/$release_asset/" != */../* ]] || {
    echo "$(basename "$0"): --release-asset must be a normalized repository-relative path" >&2
    exit 2
  }
  release_asset_name="${release_asset##*/}"
  for existing_asset in "${release_assets_seen[@]}"; do
    [ "$existing_asset" != "$release_asset" ] || {
      echo "$(basename "$0"): release asset paths must be unique" >&2
      exit 2
    }
  done
  release_assets_seen+=("$release_asset")
  for existing_name in "${release_asset_names[@]}"; do
    [ "$existing_name" != "$release_asset_name" ] || {
      echo "$(basename "$0"): release asset basenames must be unique" >&2
      exit 2
    }
  done
  release_asset_names+=("$release_asset_name")
done
if [ "$mode" = release-artifact ] && [ "${#release_build_runners[@]}" -eq 0 ]; then
  echo "$(basename "$0"): release-artifact requires at least one --build-runner" >&2
  exit 2
fi
for release_build_runner in "${release_build_runners[@]}"; do
  [[ "$release_build_runner" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    && [[ ! "$release_build_runner" =~ ^(vars|inputs|matrix|needs|github|env|secrets)\. ]] \
    && [[ ! "$release_build_runner" =~ ^(macos|windows)- ]] \
    || [[ "$release_build_runner" =~ ^\$\{\{[[:space:]]fromJSON\(vars\.CI_LANE_TRUSTED_(MACOS|WINDOWS)\)[[:space:]]\}\}$ ]] || {
    echo "$(basename "$0"): --build-runner must be a literal label or an exact ADR 0103 fromJSON(vars.CI_LANE_TRUSTED_MACOS|WINDOWS) expression" >&2
    exit 2
  }
done
release_approved_packages_seen=()
for release_approved_package in "${release_approved_internal_packages[@]}"; do
  [[ "$release_approved_package" =~ ^@verjson/[a-z0-9][a-z0-9._-]*$ ]] || {
    echo "$(basename "$0"): --approved-internal-package must be an exact lowercase @verjson package name" >&2
    exit 2
  }
  for existing_package in "${release_approved_packages_seen[@]}"; do
    [ "$existing_package" != "$release_approved_package" ] || {
      echo "$(basename "$0"): approved internal packages must be unique" >&2
      exit 2
    }
  done
  release_approved_packages_seen+=("$release_approved_package")
done
if [ -n "$release_autonomy" ] && [ "$mode" != release-propose ]; then
  echo "$(basename "$0"): --autonomy is accepted only by release-propose" >&2
  exit 2
fi
if [ "$pr_gate_untrusted_runner_set" = true ] && [ "$mode" != pr-gate ]; then
  echo "$(basename "$0"): --untrusted-runner is accepted only by pr-gate" >&2
  exit 2
fi
pr_gate_runs_on="ubuntu-24.04"
if [ "$pr_gate_untrusted_runner_set" = true ]; then
  [[ "$pr_gate_untrusted_runner" =~ ^[a-z0-9][a-z0-9_-]*(,[a-z0-9][a-z0-9_-]*)*$ ]] || {
    echo "$(basename "$0"): --untrusted-runner must be a comma-separated list of runner labels" >&2
    exit 2
  }
  IFS=',' read -ra _pr_gate_labels <<<"$pr_gate_untrusted_runner"
  _pr_gate_joined=""
  for _pr_gate_label in "${_pr_gate_labels[@]}"; do
    _pr_gate_joined="${_pr_gate_joined:+$_pr_gate_joined, }$_pr_gate_label"
  done
  pr_gate_runs_on="[$_pr_gate_joined]"
fi
if [ "$mode" = release-propose ]; then
  [ "$release_autonomy" = propose ] || [ "$release_autonomy" = dispatch ] || {
    echo "$(basename "$0"): release-propose requires --autonomy propose or --autonomy dispatch" >&2
    exit 2
  }
elif [ -n "$release_autonomy" ]; then
  usage
fi
[[ "$release_scope" =~ ^@[a-z0-9][a-z0-9._~-]*$ ]] \
  && [ "${#release_scope}" -le 214 ] || {
  echo "$(basename "$0"): scope must be a lowercase npm scope such as @verjson" >&2
  exit 2
}
[[ "$release_node_version" =~ ^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){0,2}$ ]] || {
  echo "$(basename "$0"): node version must be a numeric major, major.minor, or major.minor.patch" >&2
  exit 2
}
if [ "$release_default_prefix_set" != "$release_default_component_set" ]; then
  echo "$(basename "$0"): --default-prefix and --default-component must be provided together" >&2
  exit 2
fi
if [ "$release_default_prefix_set" = true ]; then
  [[ "$release_default_prefix" =~ ^[a-z0-9][a-z0-9._-]*-v$ ]] || {
    echo "$(basename "$0"): --default-prefix must be a lowercase stream name followed by -v" >&2
    exit 2
  }
  [[ "$release_default_component" =~ ^[a-z0-9]$|^[a-z0-9][a-z0-9._-]{0,62}[a-z0-9]$ ]] || {
    echo "$(basename "$0"): --default-component must be a 1-64 character lowercase component identifier" >&2
    exit 2
  }
fi
release_default_prefix_yaml="$release_default_prefix"
release_default_component_yaml="''"
if [ "$release_default_prefix_set" = true ]; then
  release_default_prefix_yaml="'$release_default_prefix'"
  release_default_component_yaml="'$release_default_component'"
fi
seen_package_dirs=()
for package_dir in "${release_package_dirs[@]}"; do
  for seen_package_dir in "${seen_package_dirs[@]}"; do
    [ "$seen_package_dir" != "$package_dir" ] || {
      echo "$(basename "$0"): duplicate package dir: $package_dir" >&2
      exit 2
    }
  done
  seen_package_dirs+=("$package_dir")
  [ "$package_dir" = . ] && continue
  [[ "$package_dir" =~ ^[A-Za-z0-9._][A-Za-z0-9._-]*(/[A-Za-z0-9._][A-Za-z0-9._-]*)*$ ]] || {
    echo "$(basename "$0"): package dir must be a normalized repository-relative path" >&2
    exit 2
  }
  IFS=/ read -r -a package_dir_segments <<<"$package_dir"
  for segment in "${package_dir_segments[@]}"; do
    [ "$segment" != . ] && [ "$segment" != .. ] || {
      echo "$(basename "$0"): package dir may not contain . or .. segments" >&2
      exit 2
    }
  done
done

# One validated selection drives stamping, publication and contract expectations.
selected_package_dirs_json='['
selected_package_dir_args=''
package_dir_separator=''
for package_dir in "${release_package_dirs[@]}"; do
  selected_package_dirs_json="$selected_package_dirs_json$package_dir_separator\"$package_dir\""
  package_dir_separator=,
  if [ "$release_package_dirs_exact" = true ]; then
    selected_package_dir_args="$selected_package_dir_args --only-package-dir $package_dir"
  elif [ "$package_dir" != . ]; then
    selected_package_dir_args="$selected_package_dir_args --package-dir $package_dir"
  fi
done
selected_package_dirs_json="$selected_package_dirs_json]"

# Strictly validated, not merely quoted. Both outputs interpolate this value —
# one into YAML, one into a shell assignment — so anything other than a bare
# commit is an injection vector. The sibling gen-privileged-merge-caller.sh
# documents a live instance of exactly that class.
[[ "$ref" =~ ^[0-9a-f]{40}$ ]] || {
  echo "$(basename "$0"): ref must be a 40-character lowercase commit SHA" >&2
  exit 2
}

# The digest of the engine at the pinned commit. Resolved here, once, so every
# generated script can verify what it is about to execute instead of trusting a
# path. Local object first (the usual case: generating from a checkout that has
# the ref), then the same URL the generated scripts use. No digest, no output —
# emitting an unverifiable contract would be worse than emitting nothing.
# Piped, never captured. `$(...)` strips trailing newlines, so hashing a captured
# copy digests content the file does not have — every honest override would then
# be rejected as divergent. Caught by the byte-identical-copy case in
# changelog-contract-resolution.test.sh, which is why that case exists.
resolve_contract_digest() {
  local out
  if out="$(git -C "$(dirname "$0")/.." show "$ref:scripts/changelog.py" 2>/dev/null | digest_of)" \
    && [ -n "$out" ]; then
    printf '%s' "$out"
    return 0
  fi
  if out="$(curl -fsSL "https://raw.githubusercontent.com/verJSON/.github/$ref/scripts/changelog.py" 2>/dev/null | digest_of)" \
    && [ -n "$out" ]; then
    printf '%s' "$out"
    return 0
  fi
  return 1
}

# sha256sum on Linux, shasum on macOS. A host with neither cannot verify, and an
# unverified contract is not a contract.
digest_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | cut -d' ' -f1
  else
    return 1
  fi
}

contract_sha256="$(resolve_contract_digest)" || {
  echo "$(basename "$0"): cannot resolve the contract digest at $ref" >&2
  exit 1
}
[[ "$contract_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "$(basename "$0"): resolved digest is not a sha256: $contract_sha256" >&2
  exit 1
}

# The one place that decides which implementation runs. Emitted verbatim into
# both the renderer and the contract test: two copies of this logic is the drift
# #304 was filed about, one level down.
#
# Callers define contract_fail() and have $CONTRACT_REF and $CONTRACT_SHA256 in
# scope; this sets $contract.
emit_contract_resolution() {
  cat <<'EOF'

contract_digest_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum <"$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 <"$1" | cut -d' ' -f1
  else
    return 1
  fi
}

# Identity, not existence. The cache path is keyed by commit, which reads as
# content-addressed but is not: nothing stops another tool, a restored CI cache,
# or an interrupted write from leaving different bytes there, and they would then
# be executed as the contract on every run.
contract_is_pinned() {
  [ -f "$1" ] || return 1
  local got
  got="$(contract_digest_of "$1")" || return 1
  [ "$got" = "$CONTRACT_SHA256" ]
}

# CHANGELOG_CONTRACT_PATH selects WHERE the engine comes from — a vendored copy,
# an offline mirror, a warmed CI cache — and cannot select WHAT runs, because the
# override is held to the digest pinned at $CONTRACT_REF. So it stays useful to
# an air-gapped or cache-restoring consumer while the guarantee the renderer is
# sold on ("the same code CI validates with") holds unconditionally (#304).
if [ -n "${CHANGELOG_CONTRACT_PATH:-}" ]; then
  contract="$CHANGELOG_CONTRACT_PATH"
  [ -e "$contract" ] \
    || contract_fail "CHANGELOG_CONTRACT_PATH is $contract, which does not exist"
  contract_is_pinned "$contract" \
    || contract_fail "CHANGELOG_CONTRACT_PATH ($contract) is not the contract pinned at $CONTRACT_REF"
else
  # Stable runner/bootstrap contract: preload
  #   $VERJSON_CHANGELOG_TOOL_CACHE/<commit>/changelog.py
  # or leave the variable unset for the per-user cache. The commit selects the
  # location; CONTRACT_SHA256 still decides whether those bytes may execute.
  cache_root="${VERJSON_CHANGELOG_TOOL_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/verjson-changelog}"
  cache_dir="$cache_root/$CONTRACT_REF"
  contract="$cache_dir/changelog.py"
  if ! contract_is_pinned "$contract"; then
    mkdir -p "$cache_dir" \
      || contract_fail "cannot create changelog tool cache directory $cache_dir"
    # mktemp, not a fixed name: concurrent runs share this cache directory.
    tmp="$(mktemp "$cache_dir/.changelog.XXXXXX")" \
      || contract_fail "cannot create a temporary changelog contract in $cache_dir"
    if ! curl -fsSL \
      "https://raw.githubusercontent.com/verJSON/.github/$CONTRACT_REF/scripts/changelog.py" \
      -o "$tmp"; then
      rm -f "$tmp"
      contract_fail "cannot fetch the changelog contract at $CONTRACT_REF and no verified cache entry is available at $contract; preload SHA-256 $CONTRACT_SHA256 and set VERJSON_CHANGELOG_TOOL_CACHE=$cache_root"
    fi
    # Verify before publishing into the cache, so a bad fetch is never persisted
    # for the next run to trust.
    if ! contract_is_pinned "$tmp"; then
      rm -f "$tmp"
      contract_fail "fetched contract does not match the digest pinned at $CONTRACT_REF"
    fi
    mv "$tmp" "$contract" \
      || contract_fail "cannot publish the verified changelog contract to $contract"
  fi
fi
EOF
}

emit_workflow() {
  emit_generated_artifacts false
}

emit_generated_artifacts() {
  local with_adr_index="$1" adr_input=""
  if [ "$with_adr_index" = true ]; then
    adr_input="      adr-index: true"
  fi
  cat <<EOF
name: generated artifacts

# Generated by verJSON/.github scripts/gen-changelog-caller.sh ${mode} ${ref}
# — do not edit by hand. Regenerate it with the renderer and contract test when
# the pinned contract moves.
on:
  pull_request:

permissions:
  contents: read

jobs:
  changelog:
    uses: verJSON/.github/.github/workflows/generated-artifacts.yml@${ref}
    with:
      changelog: true
      contract_ref: ${ref}
${adr_input}
EOF
}

emit_renovate_attribution() {
  cat <<EOF
name: Renovate changelog attribution

# Generated by verJSON/.github scripts/gen-changelog-caller.sh renovate-attribution ${ref}
# — do not edit by hand. This pull_request_target caller executes only the
# immutable trusted workflow below and never checks out pull-request code.
on:
  pull_request_target:
    types: [opened, reopened, synchronize]

permissions:
  actions: read
  contents: read
  pull-requests: read

jobs:
  renovate-changelog:
    if: >-
      github.event.pull_request.head.repo.full_name == github.repository &&
      (github.event.pull_request.user.login == 'app/renovate' ||
       github.event.pull_request.user.login == 'renovate[bot]') &&
      startsWith(github.event.pull_request.head.ref, 'renovate/')
    uses: verJSON/.github/.github/workflows/renovate-changelog.yml@${ref}
    secrets: inherit
    with:
      contract_ref: ${ref}
      release_app_client_id: \${{ vars.RELEASE_APP_CLIENT_ID }}
      release_environment: release-app
EOF
}

emit_pr_gate() {
  cat <<EOF
name: changelog contract

# Generated by verJSON/.github scripts/gen-changelog-caller.sh pr-gate ${ref}
# — do not edit by hand. Regenerate all changelog contract artifacts together.
on:
  pull_request:

permissions:
  contents: read

jobs:
  changelog-contract:
    runs-on: ${pr_gate_runs_on}
    timeout-minutes: 10
    steps:
      - name: Check out the pull request under validation
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          # The next step runs PR-authored scripts/changelog-contract.test.sh out
          # of this workspace, and nothing here fetches or pushes afterwards — so
          # leaving actions/checkout's default on would hand the job's
          # GITHUB_TOKEN to arbitrary PR code in .git/config for no gain (#959).
          persist-credentials: false
      - name: Prepare job-scoped changelog tool cache
        run: echo "VERJSON_CHANGELOG_TOOL_CACHE=\$RUNNER_TEMP/verjson-changelog-tools" >> "\$GITHUB_ENV"
      - name: Validate the changelog contract
        run: bash scripts/changelog-contract.test.sh
EOF
}

resolve_adr_index_generator() {
  git -C "$(dirname "$0")/.." show "$ref:scripts/gen-adr-index.sh" 2>/dev/null \
    || curl -fsSL \
      "https://raw.githubusercontent.com/verJSON/.github/$ref/scripts/gen-adr-index.sh"
}

# The canonical .github/CODEOWNERS bytes. They are embedded here rather than
# resolved at $ref because the required-checks audit materializes ONLY this
# generator at a pin (no checkout, no network, no interpreter) and must still
# emit the same contract test; the generator itself is the pinned artifact, so
# its embedded copy IS the bytes at $ref. config/codeowners/CODEOWNERS is the
# reviewed source of truth and scripts/codeowners_test.py refuses drift between
# the two (ADR 0210).
codeowners_bytes() {
  cat <<'CODEOWNERS'
# Generated by verJSON/.github; do not edit by hand.
# Regenerate from a reviewed immutable contract checkout:
# scripts/gen-changelog-caller.sh codeowners <contract-sha> > .github/CODEOWNERS
* @verJSON/devs
CODEOWNERS
}
codeowners_digest_at_ref() {
  codeowners_bytes | digest_of
}
resolve_adr_index_test() {
  git -C "$(dirname "$0")/.." show "$ref:scripts/ci-gate/gen-adr-index.test.sh" 2>/dev/null \
    || curl -fsSL \
      "https://raw.githubusercontent.com/verJSON/.github/$ref/scripts/ci-gate/gen-adr-index.test.sh"
}

# The hub keeps its suite at scripts/ci-gate/gen-adr-index.test.sh and never
# distributed it, so ~95 adopters each re-derived one for a file the contract
# generates for them. A test whose fixtures the generator rejects passes
# indefinitely as long as every case ahead of the first one asserts a rejection,
# which is how one stayed green for months (#1380). Ship the canonical suite
# instead of asking each repository to reinvent it.
#
# One line differs: the canonical copy walks two directories back to the
# repository root because it sits in scripts/ci-gate/, and an adopter's copy
# sits in scripts/. Rewriting exactly that line keeps a single source of truth —
# maintaining a second adopter-shaped body here is the drift this fixes. If the
# canonical suite ever computes its root differently, refuse to emit rather than
# ship a test that silently looks for the generator outside the repository.
# Both ADR-index modes normalize the canonical bytes to exactly one trailing
# newline on the way out, so the pin has to be taken from that same emitted form.
# Digesting the resolver's raw bytes instead would agree only as long as the
# canonical file happens to end in exactly one newline — and the day it did not,
# every adopter's contract test would fail at once against a file it had just
# regenerated correctly.
emit_adr_index_generator() {
  local canonical
  canonical="$(resolve_adr_index_generator)" || return 1
  # A resolver can succeed and still yield nothing — an empty blob at the ref, or
  # a 200 with an empty body. `printf` would turn that into a single newline,
  # which is non-empty enough to satisfy every downstream guard and would pin a
  # real-looking digest over a one-byte generator. The diagnostic is distinct
  # from the resolver's own failure so that "resolved, but empty" can never be
  # mistaken for "never resolved" — by a reader or by a test.
  [ -n "$canonical" ] || {
    echo "$(basename "$0"): the canonical scripts/gen-adr-index.sh is empty at $ref" >&2
    return 1
  }
  printf '%s\n' "${canonical%%$'\n'*}"
  printf '# Generated by verJSON/.github scripts/gen-changelog-caller.sh adr-index-generator %s\n' "$ref"
  printf '%s\n' "${canonical#*$'\n'}"
}

adr_index_test_hub_root='repo_root="$(cd "$here/../.." && pwd)"'
adr_index_test_adopter_root='repo_root="$(cd "$here/.." && pwd)"'

emit_adr_index_test() {
  local canonical rewritten
  canonical="$(resolve_adr_index_test)" || return 1
  rewritten="$(printf '%s\n' "$canonical" | tail -n +2 | awk \
    -v hub="$adr_index_test_hub_root" -v adopter="$adr_index_test_adopter_root" '
      $0 == hub { print adopter; found = 1; next }
      { print }
      END { exit(found ? 0 : 1) }
    ')" || {
    echo "internal error: the canonical gen-adr-index.test.sh at $ref no longer resolves its repository root as expected; refusing to emit a test that would look for the generator in the wrong place" >&2
    return 3
  }
  cat <<EOF
#!/usr/bin/env bash
# Fixture-based unit tests for scripts/gen-adr-index.sh.
#
# Generated by verJSON/.github scripts/gen-changelog-caller.sh adr-index-test ${ref}
# — do not edit by hand. Regenerate it whenever the pinned contract
# commit moves, together with
# .github/workflows/changelog.yml, scripts/render-next.sh,
# scripts/gen-adr-index.sh and scripts/changelog-contract.test.sh.
# scripts/changelog-contract.test.sh asserts this file's digest at the pin, so a
# partial regeneration fails there rather than drifting quietly.
#
# CONTRACT_REF=${ref}
EOF
  printf '%s\n' "$rewritten"
}

# One runner expression, emitted verbatim into every position that routes a job
# in the release caller. #465(2): a caller that omits the optional `runner:`
# input lets changelog-release.yml route the snapshot through
# CI_RUNNER_OVERFLOW (hosted for a private verJSON repository) while the
# caller's own jobs hardcode an expression resolving to CI_RUNNER_DEFAULT
# (self-hosted). One release then runs its two halves on two pools, and on a
# private repository without hosted minutes the snapshot half — the half that
# mutates ruleset-protected `main` — queues silently: no check run, no error, no
# signal. Substituted from a single variable so the three occurrences cannot
# drift; the generated contract test asserts they are still identical.
release_runner_expr="github.repository_owner == 'verJSON' && (vars.CI_RUNNER_DEFAULT || '[\"self-hosted\",\"general\"]') || '[\"ubuntu-24.04\"]'"

# The audited action commits this repository pins everywhere else
# (scripts/node-workflow-pins.test.sh).
release_checkout='actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7'
release_setup_node='actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7'
release_upload_artifact='actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1'
release_download_artifact='actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1'
release_cache_save='actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0'
release_cache_restore='actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0'

release_plan_step=$(cat <<'EOF'
      - name: Resolve the release selection and version
        id: release-version
        env:
          COMPONENT: ${{ inputs.component }}
          EXPECTED_SELECTOR_DIGEST: ${{ inputs.selector_digest }}
          FRAGMENTS: ${{ inputs.fragments }}
          INPUT_VERSION: ${{ inputs.version }}
          PREFIX: ${{ inputs.prefix }}
        run: |
          set -euo pipefail
          args=(release-plan --repo-root "$GITHUB_WORKSPACE" --prefix "$PREFIX" --version "$INPUT_VERSION")
          [ -z "$COMPONENT" ] || args+=(--component "$COMPONENT")
          while IFS= read -r fragment; do
            [ -z "$fragment" ] || args+=(--fragment "$fragment")
          done <<<"$FRAGMENTS"
          plan_file="$RUNNER_TEMP/verjson-release-plan.json"
          python3 .changelog-contract/scripts/changelog.py "${args[@]}" >"$plan_file"
          actual_selector_digest="$(python3 - "$plan_file" <<'PY'
          import json
          import sys

          with open(sys.argv[1], encoding="utf-8") as plan_file:
              print(json.load(plan_file).get("selection_digest") or "")
          PY
          )"
          if [ -n "$EXPECTED_SELECTOR_DIGEST" ] &&
            [ "$actual_selector_digest" != "$EXPECTED_SELECTOR_DIGEST" ]; then
            echo "::error::Selected fragments resolve to $actual_selector_digest, not proposer receipt $EXPECTED_SELECTOR_DIGEST."
            exit 1
          fi
          python3 - "$plan_file" "$GITHUB_OUTPUT" "$GITHUB_STEP_SUMMARY" "$GITHUB_SHA" <<'PY'
          import json
          import sys
          from pathlib import Path

          plan_path, output_path, summary_path, source_sha = sys.argv[1:]
          plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
          selected = bool(plan["selected"])
          version = str(plan["version"] or "") if selected else ""
          prefix = str(plan["prefix"])
          package_version = version[len(prefix):] if version else ""

          with open(output_path, "a", encoding="utf-8") as output:
              for name, value in {
                  "selected": "true" if selected else "false",
                  "version": version,
                  "package-version": package_version,
                  "selection-digest": plan.get("selection_digest") or "",
              }.items():
                  output.write(f"{name}={value}\n")

          summary = [
              "## Release resolution",
              "",
              f"- Component stream: `{plan['component'] or 'unscoped'}`",
              f"- Source commit: `{source_sha}`",
          ]
          if not selected:
              summary.append("No unreleased fragments are selected; no release, tag, snapshot, or publication will occur.")
          else:
              previous = plan.get("previous_release") or "none (bootstrap)"
              summary.extend(
                  [
                      f"- Resolved version: `{version}`",
                      f"- Previous release: `{previous}`",
                      f"- Bump rationale: {plan['bump_rationale']}",
                      f"- Selection digest: `{plan['selection_digest']}`",
                      "- Selected fragments:",
                  ]
              )
              summary.extend(f"  - `{name}`" for name in plan["fragments"])
              summary.extend(["", "### Assembled release notes", "", plan["preview"].rstrip(), ""])
          with open(summary_path, "a", encoding="utf-8") as summary_file:
              summary_file.write("\n".join(summary) + "\n")
          PY
EOF
)

emit_release_node() {
  local generation_command="release-node ${ref}"
  local package_dirs_json="$selected_package_dirs_json"
  local package_dirs_shell=''
  local release_assets_json='[' release_asset_sep=''
  [ "$release_scope" = "@verjson" ] \
    || generation_command="$generation_command --scope $release_scope"
  [ "$release_node_version" = "24" ] \
    || generation_command="$generation_command --node-version $release_node_version"
  [ "$release_default_prefix_set" = false ] \
    || generation_command="$generation_command --default-prefix $release_default_prefix --default-component $release_default_component"
  generation_command="$generation_command$selected_package_dir_args"
  for release_asset in "${release_assets[@]}"; do
    generation_command="$generation_command --release-asset $release_asset"
    release_assets_json="$release_assets_json$release_asset_sep\"$release_asset\""
    release_asset_sep=,
  done
  release_assets_json="$release_assets_json]"
  printf -v package_dirs_shell '%q ' "${release_package_dirs[@]}"
  package_dirs_shell="${package_dirs_shell% }"
  cat <<EOF
name: Release
run-name: Release \${{ inputs.version || 'auto' }} \${{ inputs.selector_digest || 'manual' }}

concurrency:
  group: release-\${{ github.repository }}
  cancel-in-progress: false

# Generated by verJSON/.github scripts/gen-changelog-caller.sh ${generation_command}
# — do not edit by hand. Regenerate it whenever the pinned contract commit moves,
# together with .github/workflows/changelog.yml, scripts/render-next.sh and
# scripts/changelog-contract.test.sh. A partial regeneration is the divergence
# the generator exists to prevent.
#
# WHY THE JOB ORDER IS THE POINT OF THIS FILE (verJSON/.github #463, #464, #465)
#
# \`snapshot\` calls changelog-release.yml, which consumes the NEXT/ fragments,
# writes an immutable CHANGELOG/<version>.md, commits, tags, and pushes all of it
# to the default branch in ONE atomic push. Nothing after that push can be undone
# by re-running: the same version is refused because the tag exists, and a higher
# version is refused with "release selected no fragments" because NEXT/ was
# already consumed. Recovery is manual surgery on a ruleset-protected branch.
#
# So every check that can say "no" runs in \`verify\`, which \`snapshot\` declares in
# \`needs:\` — dispatched-from-the-default-branch, version format, tag absence, and
# the repository's full suite.
#
# \`verify\` checks out \`github.sha\` — the dispatch commit — and
# changelog-release.yml checks out that same commit instead of re-resolving the
# branch name at snapshot time. Both halves must pin it: pinning only one leaves
# the window in which anything merged mid-run is tagged without ever being
# verified. Because the snapshot is taken from the dispatch commit, its final
# --atomic push is non-fast-forward if the default branch has moved since, so a
# concurrent merge fails the release with no tag pushed and every NEXT/ fragment
# still unconsumed — re-dispatch from the new head.
#
# What \`verify\` cannot check is the snapshot commit itself: it does not exist
# yet. Verifying the dispatch commit stands in for it because of what that commit
# contains — a clean-checkout run of the pinned scripts/changelog.py release
# produces a commit whose diff is exactly CHANGELOG.md, CHANGELOG/<version>.md
# and the consumed NEXT/ fragments, touching no source, no config and no
# dependency. So the tree \`publish\` builds from the tag is byte-for-byte the tree
# \`verify\` proved, minus the changelog.
#
# HOW TO CONFIGURE THE SUITE WITHOUT EDITING THIS FILE
#
# If your suite is not \`npm test\`, commit an executable \`scripts/release-verify.sh\`
# and \`verify\` runs it instead of the default Node sequence. The escape hatch is a
# separate file you own precisely so that no adopter has to edit a generated
# artifact — an artifact adopters must edit is the defect this generator removes,
# not a compromise it makes.
#
# The verification suite runs after package.json has been stamped to the
# dispatched version. Its expected version must be read dynamically from
# package.json; never assert a hardcoded version literal. This order is
# intentional: the suite verifies the exact package metadata that will ship.
#
# The operator still explicitly dispatches publication, but may leave the version
# blank. In that case the canonical release plan derives it from the selected
# fragments on this exact source commit; no push trigger infers a version from
# commit subjects (ADR 0038, ADR 0060).

on:
  workflow_dispatch:
    inputs:
      version:
        description: Optional exact SemVer tag; blank derives the next version from selected fragments
        required: false
        type: string
        default: ''
      prefix:
        description: Exact version namespace prefix; independent from component
        required: false
        type: string
        default: ${release_default_prefix_yaml}
      expected_head:
        description: Optional exact default-branch head derived by release-propose
        required: false
        type: string
        default: ''
      selector_digest:
        description: Optional canonical selection digest derived by release-propose
        required: false
        type: string
        default: ''
      fragments:
        description: Newline-separated NEXT fragment filenames; empty selects the requested component stream
        required: false
        type: string
        default: ''
      component:
        description: Optional component stream; empty selects only unscoped fragments
        required: false
        type: string
        default: ${release_default_component_yaml}

permissions:
  contents: read

jobs:
  verify:
    name: Verify the tree the snapshot will tag
    runs-on: \${{ fromJSON(${release_runner_expr}) }}
    timeout-minutes: 30
    permissions:
      contents: read
    outputs:
      selected: \${{ steps.release-version.outputs.selected }}
      version: \${{ steps.release-version.outputs.version }}
      selection-digest: \${{ steps.release-version.outputs.selection-digest }}
      snapshot-exists: \${{ steps.release-state.outputs.snapshot-exists }}
    steps:
      - name: Prepare job-scoped changelog tool cache
        run: echo "VERJSON_CHANGELOG_TOOL_CACHE=\$RUNNER_TEMP/verjson-changelog-tools" >> "\$GITHUB_ENV"
      # changelog-release.yml carries this guard too, but there it fires inside
      # \`snapshot\` — after \`verify\` has already spent a full suite run on a ref
      # whose tree will never be tagged. It is asserted here first because
      # verifying any ref other than the default branch proves nothing about the
      # content the snapshot takes (#466).
      - name: Release only from the default branch
        env:
          DISPATCH_REF: \${{ github.ref }}
          DEFAULT_BRANCH: \${{ github.event.repository.default_branch }}
        run: |
          if [ "\$DISPATCH_REF" != "refs/heads/\$DEFAULT_BRANCH" ]; then
            echo "::error::A release must be dispatched from \$DEFAULT_BRANCH, but this run was dispatched from '\$DISPATCH_REF'. The snapshot is always taken from \$DEFAULT_BRANCH, so verifying any other ref proves nothing about the tree that would be tagged."
            exit 1
          fi
          echo "Dispatched from \$DEFAULT_BRANCH; verifying its head."
      - name: Bind a proposer dispatch to its exact derived head
        env:
          EXPECTED_HEAD: \${{ inputs.expected_head }}
          SELECTOR_DIGEST: \${{ inputs.selector_digest }}
        run: |
          if [ -z "\$EXPECTED_HEAD" ] && [ -z "\$SELECTOR_DIGEST" ]; then
            echo "Manual dispatch has no proposer receipt to bind."
          elif [[ ! "\$EXPECTED_HEAD" =~ ^[0-9a-f]{40}\$ ]] ||
            [[ ! "\$SELECTOR_DIGEST" =~ ^[0-9a-f]{64}\$ ]]; then
            echo "::error::expected_head and selector_digest must either both be empty or both be canonical lowercase digests."
            exit 1
          elif [ "\$GITHUB_SHA" != "\$EXPECTED_HEAD" ]; then
            echo "::error::The default branch advanced from derived head \$EXPECTED_HEAD to dispatch head \$GITHUB_SHA. Derive the proposal again; this run will not verify, snapshot, or publish."
            exit 1
          else
            echo "Dispatch is bound to derived head \$EXPECTED_HEAD and selector \$SELECTOR_DIGEST."
          fi
      - name: Check out the tree that will be released
        uses: ${release_checkout}
        with:
          # github.sha is the default branch head at dispatch time, and
          # changelog-release.yml checks out that same commit rather than
          # re-resolving the branch name at snapshot time — so the tree verified
          # here is the tree that gets tagged, even if something merges to the
          # default branch while this job is running. Both halves must agree:
          # pinning only one of them reintroduces the window (#463, #464).
          ref: \${{ github.sha }}
          fetch-depth: 0
      - name: Check out the canonical selection contract
        uses: ${release_checkout}
        with:
          repository: verJSON/.github
          ref: ${ref}
          path: .changelog-contract
          persist-credentials: false
${release_plan_step}
      - name: Resolve restart-safe release state
        id: release-state
        if: steps.release-version.outputs.selected == 'true'
        env:
          VERSION: \${{ steps.release-version.outputs.version }}
        run: |
          if git ls-remote --exit-code --tags origin "refs/tags/\$VERSION" >/dev/null 2>&1; then
            git fetch --force origin "refs/tags/\$VERSION:refs/tags/\$VERSION"
            if [ ! -f "CHANGELOG/\$VERSION.md" ] ||
              ! git cat-file -e "\$VERSION:CHANGELOG/\$VERSION.md" ||
              ! git merge-base --is-ancestor "\$VERSION" HEAD ||
              ! git diff --quiet "\$VERSION" HEAD -- "CHANGELOG/\$VERSION.md"; then
              echo "::error::Tag \$VERSION exists but is not the immutable release snapshot reachable from this default-branch head. Refusing to resume a conflicting release."
              exit 1
            fi
            echo "snapshot-exists=true" >> "\$GITHUB_OUTPUT"
            echo "\$VERSION already has its immutable snapshot; verifying current release inputs before resuming publication."
          elif [ -e "CHANGELOG/\$VERSION.md" ]; then
            echo "::error::CHANGELOG/\$VERSION.md already exists, and a released snapshot is immutable (ADR 0059). Cut the next version instead."
            exit 1
          else
            echo "snapshot-exists=false" >> "\$GITHUB_OUTPUT"
            echo "\$VERSION is unused."
          fi
      - name: Check out the existing snapshot for resumed verification
        if: steps.release-version.outputs.selected == 'true' && steps.release-state.outputs.snapshot-exists == 'true'
        uses: ${release_checkout}
        with:
          ref: \${{ steps.release-version.outputs.version }}
          fetch-depth: 0
          persist-credentials: false
      - uses: ${release_setup_node}
        if: steps.release-version.outputs.selected == 'true'
        with:
          # Keep the literal inside an expression so Renovate's uses-with
          # extractor leaves it alone while setup-node receives the same value.
          node-version: \${{ '${release_node_version}' }}
          registry-url: https://npm.pkg.github.com
          scope: '${release_scope}'
          package-manager-cache: false
      # \`publish\` delegates to node-release.yml, which runs npm publish — but it
      # only ever runs AFTER \`snapshot\` has consumed NEXT/, written the immutable
      # CHANGELOG/<version>.md, committed, tagged and pushed. A package npm can
      # never publish therefore fails over a release that already completed and
      # cannot be re-cut, so the refusal is asserted here, while it is still a
      # no-op (#1206).
      - name: Refuse a package this release can never publish
        if: steps.release-version.outputs.selected == 'true'
        run: |
          package_dirs=(${package_dirs_shell})
          for package_dir in "\${package_dirs[@]}"; do
            # A directory with no package.json publishes nothing, and the
            # install and version-stamping steps below fail on it regardless.
            [ -f "\$package_dir/package.json" ] || continue
            is_private="\$(node -p 'JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")).private === true' "\$package_dir/package.json")"
            if [ "\$is_private" = true ]; then
              echo "::error::package.json in '\$package_dir' is marked \"private\": true, so npm publish can never succeed and this release would fail after its snapshot is already tagged. To publish nothing from the release workflow, regenerate this caller as scripts/gen-changelog-caller.sh release-snapshot ${ref} > .github/workflows/release.yml"
              exit 1
            fi
          done
      - name: Install dependencies
        if: steps.release-version.outputs.selected == 'true'
        run: npm ci
        env:
          # NOT GITHUB_TOKEN (#465). A repository-scoped GITHUB_TOKEN cannot read
          # a private GitHub Packages package owned by a DIFFERENT repository, so
          # an adopter with a private @verjson devDependency 401s here. Canonical
          # node-ci.yml states the same requirement for the same reason.
          NODE_AUTH_TOKEN: \${{ secrets.NODE_AUTH_TOKEN }}
      - name: Prepare release package metadata
        if: steps.release-version.outputs.selected == 'true'
        env:
          PACKAGE_VERSION: \${{ steps.release-version.outputs.package-version }}
        run: |
          if [ -e scripts/release-prepare-packages.sh ] && [ ! -x scripts/release-prepare-packages.sh ]; then
            echo "::error::scripts/release-prepare-packages.sh exists but is not executable."
            exit 1
          fi
          if [ -x scripts/release-prepare-packages.sh ]; then
            scripts/release-prepare-packages.sh "\$PACKAGE_VERSION"
          fi
      - name: Stamp the dispatched package versions
        if: steps.release-version.outputs.selected == 'true'
        env:
          PACKAGE_VERSION: \${{ steps.release-version.outputs.package-version }}
        run: |
          package_dirs=(${package_dirs_shell})
          for package_dir in "\${package_dirs[@]}"; do
            npm version --prefix "\$package_dir" "\$PACKAGE_VERSION" --no-git-tag-version --ignore-scripts --allow-same-version
          done
      - name: Run the release verification suite
        if: steps.release-version.outputs.selected == 'true'
        env:
          NODE_AUTH_TOKEN: \${{ secrets.NODE_AUTH_TOKEN }}
          PACKAGE_VERSION: \${{ steps.release-version.outputs.package-version }}
        run: |
          # Existence and executability are checked separately on purpose. A
          # single \`-x\` test reads a hook committed without the executable bit
          # as "no hook here" and quietly runs the Node default instead — so an
          # adopter who deliberately replaced their suite watches a green
          # release verified by the suite they replaced.
          if [ -e scripts/release-verify.sh ] && [ ! -x scripts/release-verify.sh ]; then
            echo "::error::scripts/release-verify.sh exists but is not executable, so this release would silently fall back to the default Node suite. Run: chmod +x scripts/release-verify.sh && git update-index --chmod=+x scripts/release-verify.sh"
            exit 1
          fi
          if [ -x scripts/release-verify.sh ]; then
            echo "Running this repository's scripts/release-verify.sh"
            verification_status=0
            scripts/release-verify.sh || verification_status=\$?
          else
            verification_status=0
            npm run build --if-present &&
              npm run typecheck --if-present &&
              npm run lint --if-present &&
              npm test || verification_status=\$?
          fi
          if [ "\$verification_status" -ne 0 ]; then
            echo "::error::Release verification failed against stamped dispatch version \$PACKAGE_VERSION. Check for the hardcoded-version footgun: version assertions can pass in pull requests and local runs, then fail only here; read the expected version dynamically from package.json."
            exit "\$verification_status"
          fi

  snapshot:
    # The irreversible act, and the only job that may not run first.
    needs: verify
    if: needs.verify.outputs.selected == 'true' && needs.verify.outputs.snapshot-exists != 'true'
    uses: verJSON/.github/.github/workflows/changelog-release.yml@${ref}
    secrets: inherit
    permissions:
      actions: read
      # The reusable workflow pushes with its separately minted release App
      # token. This caller grant caps only its read-only GITHUB_TOKEN (#784).
      contents: read
    with:
      contract_ref: ${ref}
      version: \${{ needs.verify.outputs.version }}
      fragments: \${{ inputs.fragments }}
      component: \${{ inputs.component }}
      prefix: \${{ inputs.prefix }}
      selection_digest: \${{ needs.verify.outputs.selection-digest }}
      # v3 recommends the App client ID and deprecates its legacy numeric ID.
      release_app_client_id: \${{ vars.RELEASE_APP_CLIENT_ID }}
      release_environment: release-app
      # Explicit, so both halves of one release share one pool (#465).
      runner: \${{ ${release_runner_expr} }}

  publish:
    name: Publish the released snapshot
    needs: [verify, snapshot]
    if: always() && needs.verify.result == 'success' && needs.verify.outputs.selected == 'true' && (needs.snapshot.result == 'success' || needs.snapshot.result == 'skipped')
    uses: verJSON/.github/.github/workflows/node-release.yml@${ref}
    permissions:
      contents: write
      packages: write
    with:
      version: \${{ needs.verify.outputs.version }}
      prefix: \${{ inputs.prefix }}
      contract-ref: ${ref}
      runner: \${{ ${release_runner_expr} }}
      # Keep this byte-coupled input Renovate-inert for the same reason as the
      # setup-node input in verify (#700).
      node-version: \${{ '${release_node_version}' }}
      scope: '${release_scope}'
      package-dirs: '${package_dirs_json}'
      release-assets: '${release_assets_json}'
    secrets:
      NODE_AUTH_TOKEN: \${{ secrets.NODE_AUTH_TOKEN }}
EOF
}

emit_release_artifact() {
  local generation_command="release-artifact ${ref}"
  local package_dirs_shell=''
  local build_runners_yaml='' approved_packages_csv='' private_acquisition_job='' restore_dependency_step=''
  local required_lane_names='' required_lane_env='' required_lane_validation_step='' runner_index=0
  local build_needs='[verify, snapshot]' build_condition="always() && needs.verify.result == 'success' && needs.verify.outputs.selected == 'true' && (needs.snapshot.result == 'success' || needs.snapshot.result == 'skipped')"
  [ "$release_scope" = "@verjson" ] \
    || generation_command="$generation_command --scope $release_scope"
  [ "$release_node_version" = "24" ] \
    || generation_command="$generation_command --node-version $release_node_version"
  [ "$release_default_prefix_set" = false ] \
    || generation_command="$generation_command --default-prefix $release_default_prefix --default-component $release_default_component"
  generation_command="$generation_command$selected_package_dir_args"
  for build_runner in "${release_build_runners[@]}"; do
    printf -v quoted_build_runner '%q' "$build_runner"
    generation_command="$generation_command --build-runner $quoted_build_runner"
    if [[ "$build_runner" == \$\{\{* ]]; then
      yaml_build_runner="$build_runner"
      lane_name="${build_runner#*vars.}"
      lane_name="${lane_name%%)*}"
      if [[ ",$required_lane_names," != *",$lane_name,"* ]]; then
        required_lane_names="${required_lane_names:+$required_lane_names,}$lane_name"
        required_lane_env="${required_lane_env}          $lane_name: \${{ vars.$lane_name }}
"
      fi
    else
      yaml_build_runner="'$build_runner'"
    fi
    build_runners_yaml="${build_runners_yaml}          - build-runner: ${yaml_build_runner}
            dependency-index: ${runner_index}
"
    runner_index=$((runner_index + 1))
  done
  for approved_package in "${release_approved_internal_packages[@]}"; do
    generation_command="$generation_command --approved-internal-package $approved_package"
    approved_packages_csv="${approved_packages_csv:+$approved_packages_csv,}$approved_package"
  done
  printf -v package_dirs_shell '%q ' "${release_package_dirs[@]}"
  package_dirs_shell="${package_dirs_shell% }"
  if [ -n "$required_lane_names" ]; then
    required_lane_validation_step="$(cat <<EOF
      - name: Validate required OS-scoped build lanes
        if: steps.release-version.outputs.selected == 'true'
        shell: bash
        env:
          REQUIRED_BUILD_LANES: '${required_lane_names}'
${required_lane_env%$'\n'}
        run: |
          set -euo pipefail
          IFS=',' read -ra lane_names <<<"\$REQUIRED_BUILD_LANES"
          for lane_name in "\${lane_names[@]}"; do
            lane_value="\${!lane_name:-}"
            LANE_NAME="\$lane_name" LANE_VALUE="\$lane_value" node <<'NODE'
          const name = process.env.LANE_NAME;
          let value;
          try { value = JSON.parse(process.env.LANE_VALUE); } catch { throw new Error(name + ' must be a non-empty JSON runner-label array'); }
          if (!Array.isArray(value) || value.length === 0 || value.some(label => typeof label !== 'string' || !label)) throw new Error(name + ' must be a non-empty JSON runner-label array');
          NODE
          done
EOF
)"
  fi
  if [ "${#release_approved_internal_packages[@]}" -gt 0 ]; then
    build_needs='[verify, snapshot, acquire-private-dependencies]'
    build_condition="$build_condition && needs.acquire-private-dependencies.result == 'success'"
    private_acquisition_job="$(cat <<EOF
  acquire-private-dependencies:
    name: Acquire approved private dependencies (\${{ matrix.build-runner }})
    needs: [verify, snapshot]
    if: always() && needs.verify.result == 'success' && needs.verify.outputs.selected == 'true' && (needs.snapshot.result == 'success' || needs.snapshot.result == 'skipped')
    strategy:
      fail-fast: false
      matrix:
        include:
${build_runners_yaml%$'\n'}
    runs-on: \${{ matrix.build-runner }}
    timeout-minutes: 45
    permissions:
      contents: read
      packages: read
    steps:
      - name: Check out the tagged release tree without credentials
        uses: ${release_checkout}
        with:
          ref: \${{ needs.verify.outputs.version }}
          persist-credentials: false
      - uses: ${release_setup_node}
        with:
          node-version: '${release_node_version}'
          package-manager-cache: false
      - name: Validate the approved private dependency lock
        shell: bash
        env:
          APPROVED_INTERNAL_PACKAGES: '${approved_packages_csv}'
        run: |
          set -euo pipefail
          [ -f package-lock.json ] || { echo "::error::private release acquisition requires package-lock.json"; exit 1; }
          [ -z "\$(find . -name .npmrc -print -quit)" ] || { echo "::error::private release acquisition rejects repository-controlled npm configuration"; exit 1; }
          node <<'NODE'
          const fs = require('fs');
          const approved = new Set(process.env.APPROVED_INTERNAL_PACKAGES.split(',').filter(Boolean));
          const lock = JSON.parse(fs.readFileSync('package-lock.json', 'utf8'));
          if (![2, 3].includes(lock.lockfileVersion) || !lock.packages || Array.isArray(lock.packages)) throw new Error('private release acquisition requires package-lock lockfileVersion 2 or 3');
          const found = new Set();
          for (const [path, entry] of Object.entries(lock.packages)) {
            if (!path || !path.includes('node_modules/')) continue;
            const name = path.slice(path.lastIndexOf('node_modules/') + 13);
            if (!name.startsWith('@verjson/')) continue;
            if (!approved.has(name)) throw new Error('unapproved internal dependency: ' + name);
            if (entry.name && entry.name !== name) throw new Error('internal dependency aliases unexpected package: ' + name);
            const url = new URL(entry.resolved);
            const parts = url.pathname.split('/');
            if (url.protocol !== 'https:' || url.host !== 'npm.pkg.github.com' || url.username || url.password || url.search || url.hash || url.pathname.includes('\\') || decodeURIComponent(url.pathname) !== url.pathname || parts.length !== 6 || parts[1] !== 'download' || parts[2] + '/' + parts[3] !== name || !parts[4] || !parts[5]) throw new Error('internal dependency is not pinned to its exact GitHub Packages download URL: ' + name);
            if (typeof entry.integrity !== 'string' || !/^sha512-[A-Za-z0-9+/]{86}==$/.test(entry.integrity)) throw new Error('internal dependency requires exact sha512 integrity: ' + name);
            found.add(name);
          }
          for (const name of approved) if (!found.has(name)) throw new Error('approved internal dependency absent from lock: ' + name);
          NODE
      - name: Acquire dependencies without lifecycle execution
        shell: bash
        env:
          NODE_AUTH_TOKEN: \${{ secrets.NODE_AUTH_TOKEN }}
          NPM_CONFIG_GLOBALCONFIG: \${{ runner.temp }}/release-empty-global.npmrc
          NPM_CONFIG_USERCONFIG: \${{ runner.temp }}/release-acquisition.npmrc
        run: |
          set -euo pipefail
          umask 077
          [ -n "\$NODE_AUTH_TOKEN" ] || { echo "::error::private release acquisition requires NODE_AUTH_TOKEN"; exit 1; }
          : > "\$NPM_CONFIG_GLOBALCONFIG"
          printf '%s\n' 'registry=https://registry.npmjs.org/' '@verjson:registry=https://npm.pkg.github.com/' '//npm.pkg.github.com/:_authToken=\${NODE_AUTH_TOKEN}' > "\$NPM_CONFIG_USERCONFIG"
          npm ci --ignore-scripts --audit=false --fund=false
          [ -d node_modules ] || { echo "::error::npm produced no dependency tree"; exit 1; }
          if grep -R -a -F -q -- "\$NODE_AUTH_TOKEN" node_modules; then echo "::error::dependency tree contains the acquisition credential"; exit 1; fi
          [ "\$(du -sk node_modules | awk '{print \$1}')" -le 2097152 ] || { echo "::error::dependency transfer exceeds 2 GiB"; exit 1; }
          rm -f "\$NPM_CONFIG_USERCONFIG" "\$NPM_CONFIG_GLOBALCONFIG"
      - name: Save exact-attempt credentialless dependencies
        uses: ${release_cache_save}
        with:
          path: node_modules
          key: release-dependencies-\${{ github.run_id }}-\${{ github.run_attempt }}-\${{ matrix.dependency-index }}
      - name: Remove acquisition state
        if: always()
        shell: bash
        run: rm -rf node_modules "\$RUNNER_TEMP/release-acquisition.npmrc" "\$RUNNER_TEMP/release-empty-global.npmrc"

EOF
)"
    restore_dependency_step="$(cat <<EOF
      - name: Restore exact-attempt credentialless dependencies
        uses: ${release_cache_restore}
        with:
          path: node_modules
          key: release-dependencies-\${{ github.run_id }}-\${{ github.run_attempt }}-\${{ matrix.dependency-index }}
          fail-on-cache-miss: true
EOF
)"
  fi
  cat <<EOF
name: Release
run-name: Release \${{ inputs.version || 'auto' }} \${{ inputs.selector_digest || 'manual' }}

concurrency:
  group: release-\${{ github.repository }}
  cancel-in-progress: false

# Generated by verJSON/.github scripts/gen-changelog-caller.sh ${generation_command}
# — do not edit by hand. Regenerate it whenever the pinned contract commit moves,
# together with .github/workflows/changelog.yml, scripts/render-next.sh and
# scripts/changelog-contract.test.sh. A partial regeneration is the divergence
# the generator exists to prevent.
#
# WHY THE JOB ORDER IS THE POINT OF THIS FILE (verJSON/.github #463, #464, #465, #975)
#
# \`verify\` and \`snapshot\` are byte-identical in spirit to release-node's: the
# irreversible changelog-release.yml snapshot may never be the first job to run,
# and \`verify\`/\`snapshot\` must share one runner pool (see release-node's header
# for the full rationale — it applies unchanged here).
#
# This mode exists for adopters with nothing to publish to a package registry —
# concretely, an Electron desktop app that ships OS installers as GitHub Release
# assets (#975). \`build\` replaces \`publish\`'s call to node-release.yml: it is a
# caller-declared matrix of runner selectors (one per --build-runner passed at
# generation time), each running an adopter-owned, fail-closed
# \`scripts/release-build.sh <version> <output-dir>\` hook that must leave every
# artifact for that runner inside the given directory. \`publish\` downloads every
# runner's artifacts and attaches them to the tag's GitHub Release, using the
# immutable CHANGELOG/<version>.md as the release notes — the same restart-safe
# release-notes logic node-release.yml uses, inlined here because there is no
# separate reusable publication workflow for artifact releases.
#
# HOW TO CONFIGURE THE SUITE WITHOUT EDITING THIS FILE
#
# If your suite is not \`npm test\`, commit an executable \`scripts/release-verify.sh\`
# and \`verify\` runs it instead of the default Node sequence. The escape hatch is a
# separate file you own precisely so that no adopter has to edit a generated
# artifact — an artifact adopters must edit is the defect this generator removes,
# not a compromise it makes. Each build leg requires its own executable
# \`scripts/release-build.sh\`; it receives the exact dispatched version and an
# empty output directory and must exit non-zero on failure.
# Callers with private @verjson dependencies name each package through
# --approved-internal-package. A lifecycle-disabled acquisition matrix receives
# the package credential and hands an exact-attempt dependency tree to the
# credentialless build matrix; the build hook never receives that credential.
#
# The verification suite runs after package.json has been stamped to the
# dispatched version. Its expected version must be read dynamically from
# package.json; never assert a hardcoded version literal. This order is
# intentional: the suite verifies the exact package metadata that will ship.
#
# The operator still explicitly dispatches publication, but may leave the version
# blank. In that case the canonical release plan derives it from the selected
# fragments on this exact source commit; no push trigger infers a version from
# commit subjects (ADR 0038, ADR 0060).

on:
  workflow_dispatch:
    inputs:
      version:
        description: Optional exact SemVer tag; blank derives the next version from selected fragments
        required: false
        type: string
        default: ''
      prefix:
        description: Exact version namespace prefix; independent from component
        required: false
        type: string
        default: ${release_default_prefix_yaml}
      expected_head:
        description: Optional exact default-branch head derived by release-propose
        required: false
        type: string
        default: ''
      selector_digest:
        description: Optional canonical selection digest derived by release-propose
        required: false
        type: string
        default: ''
      fragments:
        description: Newline-separated NEXT fragment filenames; empty selects the requested component stream
        required: false
        type: string
        default: ''
      component:
        description: Optional component stream; empty selects only unscoped fragments
        required: false
        type: string
        default: ${release_default_component_yaml}

permissions:
  contents: read

jobs:
  verify:
    name: Verify the tree the snapshot will tag
    runs-on: \${{ fromJSON(${release_runner_expr}) }}
    timeout-minutes: 30
    permissions:
      contents: read
    outputs:
      selected: \${{ steps.release-version.outputs.selected }}
      version: \${{ steps.release-version.outputs.version }}
      selection-digest: \${{ steps.release-version.outputs.selection-digest }}
      snapshot-exists: \${{ steps.release-state.outputs.snapshot-exists }}
    steps:
      - name: Prepare job-scoped changelog tool cache
        run: echo "VERJSON_CHANGELOG_TOOL_CACHE=\$RUNNER_TEMP/verjson-changelog-tools" >> "\$GITHUB_ENV"
      # changelog-release.yml carries this guard too, but there it fires inside
      # \`snapshot\` — after \`verify\` has already spent a full suite run on a ref
      # whose tree will never be tagged. It is asserted here first because
      # verifying any ref other than the default branch proves nothing about the
      # content the snapshot takes (#466).
      - name: Release only from the default branch
        env:
          DISPATCH_REF: \${{ github.ref }}
          DEFAULT_BRANCH: \${{ github.event.repository.default_branch }}
        run: |
          if [ "\$DISPATCH_REF" != "refs/heads/\$DEFAULT_BRANCH" ]; then
            echo "::error::A release must be dispatched from \$DEFAULT_BRANCH, but this run was dispatched from '\$DISPATCH_REF'. The snapshot is always taken from \$DEFAULT_BRANCH, so verifying any other ref proves nothing about the tree that would be tagged."
            exit 1
          fi
          echo "Dispatched from \$DEFAULT_BRANCH; verifying its head."
      - name: Bind a proposer dispatch to its exact derived head
        env:
          EXPECTED_HEAD: \${{ inputs.expected_head }}
          SELECTOR_DIGEST: \${{ inputs.selector_digest }}
        run: |
          if [ -z "\$EXPECTED_HEAD" ] && [ -z "\$SELECTOR_DIGEST" ]; then
            echo "Manual dispatch has no proposer receipt to bind."
          elif [[ ! "\$EXPECTED_HEAD" =~ ^[0-9a-f]{40}\$ ]] ||
            [[ ! "\$SELECTOR_DIGEST" =~ ^[0-9a-f]{64}\$ ]]; then
            echo "::error::expected_head and selector_digest must either both be empty or both be canonical lowercase digests."
            exit 1
          elif [ "\$GITHUB_SHA" != "\$EXPECTED_HEAD" ]; then
            echo "::error::The default branch advanced from derived head \$EXPECTED_HEAD to dispatch head \$GITHUB_SHA. Derive the proposal again; this run will not verify, snapshot, or publish."
            exit 1
          else
            echo "Dispatch is bound to derived head \$EXPECTED_HEAD and selector \$SELECTOR_DIGEST."
          fi
      - name: Check out the tree that will be released
        uses: ${release_checkout}
        with:
          # github.sha is the default branch head at dispatch time, and
          # changelog-release.yml checks out that same commit rather than
          # re-resolving the branch name at snapshot time — so the tree verified
          # here is the tree that gets tagged, even if something merges to the
          # default branch while this job is running. Both halves must agree:
          # pinning only one of them reintroduces the window (#463, #464).
          ref: \${{ github.sha }}
          fetch-depth: 0
      - name: Check out the canonical selection contract
        uses: ${release_checkout}
        with:
          repository: verJSON/.github
          ref: ${ref}
          path: .changelog-contract
          persist-credentials: false
${release_plan_step}
${required_lane_validation_step}
      - name: Resolve restart-safe release state
        id: release-state
        env:
          VERSION: \${{ steps.release-version.outputs.version }}
        run: |
          if git ls-remote --exit-code --tags origin "refs/tags/\$VERSION" >/dev/null 2>&1; then
            git fetch --force origin "refs/tags/\$VERSION:refs/tags/\$VERSION"
            if [ ! -f "CHANGELOG/\$VERSION.md" ] ||
              ! git cat-file -e "\$VERSION:CHANGELOG/\$VERSION.md" ||
              ! git merge-base --is-ancestor "\$VERSION" HEAD ||
              ! git diff --quiet "\$VERSION" HEAD -- "CHANGELOG/\$VERSION.md"; then
              echo "::error::Tag \$VERSION exists but is not the immutable release snapshot reachable from this default-branch head. Refusing to resume a conflicting release."
              exit 1
            fi
            echo "snapshot-exists=true" >> "\$GITHUB_OUTPUT"
            echo "\$VERSION already has its immutable snapshot; verifying current release inputs before resuming publication."
          elif [ -e "CHANGELOG/\$VERSION.md" ]; then
            echo "::error::CHANGELOG/\$VERSION.md already exists, and a released snapshot is immutable (ADR 0059). Cut the next version instead."
            exit 1
          else
            echo "snapshot-exists=false" >> "\$GITHUB_OUTPUT"
            echo "\$VERSION is unused."
          fi
      - name: Check out the existing snapshot for resumed verification
        if: steps.release-version.outputs.selected == 'true' && steps.release-state.outputs.snapshot-exists == 'true'
        uses: ${release_checkout}
        with:
          ref: \${{ steps.release-version.outputs.version }}
          fetch-depth: 0
          persist-credentials: false
      - uses: ${release_setup_node}
        if: steps.release-version.outputs.selected == 'true'
        with:
          # Keep the literal inside an expression so Renovate's uses-with
          # extractor leaves it alone while setup-node receives the same value.
          node-version: \${{ '${release_node_version}' }}
          registry-url: https://npm.pkg.github.com
          scope: '${release_scope}'
          package-manager-cache: false
      - name: Install dependencies
        if: steps.release-version.outputs.selected == 'true'
        run: npm ci
        env:
          # NOT GITHUB_TOKEN (#465). A repository-scoped GITHUB_TOKEN cannot read
          # a private GitHub Packages package owned by a DIFFERENT repository, so
          # an adopter with a private @verjson devDependency 401s here. Canonical
          # node-ci.yml states the same requirement for the same reason.
          NODE_AUTH_TOKEN: \${{ secrets.NODE_AUTH_TOKEN }}
      - name: Prepare release package metadata
        if: steps.release-version.outputs.selected == 'true'
        env:
          PACKAGE_VERSION: \${{ steps.release-version.outputs.package-version }}
        run: |
          if [ -e scripts/release-prepare-packages.sh ] && [ ! -x scripts/release-prepare-packages.sh ]; then
            echo "::error::scripts/release-prepare-packages.sh exists but is not executable."
            exit 1
          fi
          if [ -x scripts/release-prepare-packages.sh ]; then
            scripts/release-prepare-packages.sh "\$PACKAGE_VERSION"
          fi
      - name: Stamp the dispatched package versions
        if: steps.release-version.outputs.selected == 'true'
        env:
          PACKAGE_VERSION: \${{ steps.release-version.outputs.package-version }}
        run: |
          package_dirs=(${package_dirs_shell})
          for package_dir in "\${package_dirs[@]}"; do
            npm version --prefix "\$package_dir" "\$PACKAGE_VERSION" --no-git-tag-version --ignore-scripts --allow-same-version
          done
      - name: Run the release verification suite
        if: steps.release-version.outputs.selected == 'true'
        env:
          NODE_AUTH_TOKEN: \${{ secrets.NODE_AUTH_TOKEN }}
          PACKAGE_VERSION: \${{ steps.release-version.outputs.package-version }}
        run: |
          # Existence and executability are checked separately on purpose. A
          # single \`-x\` test reads a hook committed without the executable bit
          # as "no hook here" and quietly runs the Node default instead — so an
          # adopter who deliberately replaced their suite watches a green
          # release verified by the suite they replaced.
          if [ -e scripts/release-verify.sh ] && [ ! -x scripts/release-verify.sh ]; then
            echo "::error::scripts/release-verify.sh exists but is not executable, so this release would silently fall back to the default Node suite. Run: chmod +x scripts/release-verify.sh && git update-index --chmod=+x scripts/release-verify.sh"
            exit 1
          fi
          if [ -x scripts/release-verify.sh ]; then
            echo "Running this repository's scripts/release-verify.sh"
            verification_status=0
            scripts/release-verify.sh || verification_status=\$?
          else
            verification_status=0
            npm run build --if-present &&
              npm run typecheck --if-present &&
              npm run lint --if-present &&
              npm test || verification_status=\$?
          fi
          if [ "\$verification_status" -ne 0 ]; then
            echo "::error::Release verification failed against stamped dispatch version \$PACKAGE_VERSION. Check for the hardcoded-version footgun: version assertions can pass in pull requests and local runs, then fail only here; read the expected version dynamically from package.json."
            exit "\$verification_status"
          fi

  snapshot:
    # The irreversible act, and the only job that may not run first.
    needs: verify
    if: needs.verify.outputs.selected == 'true' && needs.verify.outputs.snapshot-exists != 'true'
    uses: verJSON/.github/.github/workflows/changelog-release.yml@${ref}
    secrets: inherit
    permissions:
      actions: read
      # The reusable workflow pushes with its separately minted release App
      # token. This caller grant caps only its read-only GITHUB_TOKEN (#784).
      contents: read
    with:
      contract_ref: ${ref}
      version: \${{ needs.verify.outputs.version }}
      fragments: \${{ inputs.fragments }}
      component: \${{ inputs.component }}
      prefix: \${{ inputs.prefix }}
      selection_digest: \${{ needs.verify.outputs.selection-digest }}
      # v3 recommends the App client ID and deprecates its legacy numeric ID.
      release_app_client_id: \${{ vars.RELEASE_APP_CLIENT_ID }}
      release_environment: release-app
      # Explicit, so both halves of one release share one pool (#465).
      runner: \${{ ${release_runner_expr} }}

${private_acquisition_job}
  build:
    name: Build release artifacts (\${{ matrix.build-runner }})
    needs: ${build_needs}
    if: ${build_condition}
    strategy:
      fail-fast: false
      matrix:
        include:
${build_runners_yaml%$'\n'}
    runs-on: \${{ matrix.build-runner }}
    timeout-minutes: 45
    permissions:
      contents: read
    steps:
      - name: Check out the tagged release tree
        uses: ${release_checkout}
        with:
          ref: \${{ needs.verify.outputs.version }}
          fetch-depth: 0
          persist-credentials: false
${restore_dependency_step}
      - name: Require the release build hook
        shell: bash
        run: |
          if [ -e scripts/release-build.sh ] && [ ! -x scripts/release-build.sh ]; then
            echo "::error::scripts/release-build.sh exists but is not executable. Run: chmod +x scripts/release-build.sh && git update-index --chmod=+x scripts/release-build.sh"
            exit 1
          fi
          if [ ! -x scripts/release-build.sh ]; then
            echo "::error::release-artifact requires an executable scripts/release-build.sh. It receives the released version and an empty output directory and must leave every artifact for this runner inside that directory, or exit non-zero."
            exit 1
          fi
      - name: Build this runner's release artifacts
        shell: bash
        env:
          ACTIONS_ID_TOKEN_REQUEST_TOKEN: ''
          ACTIONS_ID_TOKEN_REQUEST_URL: ''
          AWS_ACCESS_KEY_ID: ''
          AWS_SECRET_ACCESS_KEY: ''
          AWS_SESSION_TOKEN: ''
          AZURE_CREDENTIALS: ''
          GH_TOKEN: ''
          GITHUB_TOKEN: ''
          GOOGLE_APPLICATION_CREDENTIALS: ''
          NODE_AUTH_TOKEN: ''
          NPM_TOKEN: ''
          RELEASE_VERSION: \${{ needs.verify.outputs.version }}
        run: |
          mkdir -p release-artifacts
          scripts/release-build.sh "\$RELEASE_VERSION" release-artifacts
          if [ -z "\$(find release-artifacts -type f -print -quit)" ]; then
            echo "::error::scripts/release-build.sh produced no files in release-artifacts/"
            exit 1
          fi
      - name: Upload this runner's release artifacts
        uses: ${release_upload_artifact}
        with:
          name: release-artifacts-\${{ strategy.job-index }}
          path: release-artifacts/*
          if-no-files-found: error
          retention-days: 1
      - name: Remove restored dependencies and build outputs
        if: always()
        shell: bash
        run: rm -rf node_modules release-artifacts

  publish:
    name: Publish the released snapshot
    needs: [verify, snapshot, build]
    if: always() && needs.verify.result == 'success' && needs.verify.outputs.selected == 'true' && (needs.snapshot.result == 'success' || needs.snapshot.result == 'skipped') && needs.build.result == 'success'
    runs-on: \${{ fromJSON(${release_runner_expr}) }}
    timeout-minutes: 15
    permissions:
      contents: write
    steps:
      - uses: ${release_checkout}
        with:
          ref: \${{ needs.verify.outputs.version }}
          fetch-depth: 0
          persist-credentials: false
      - name: Verify the checked-out tag and immutable release note
        env:
          VERSION: \${{ needs.verify.outputs.version }}
        run: |
          test "\$(git describe --tags --exact-match HEAD)" = "\$VERSION"
          test -f "CHANGELOG/\$VERSION.md"
      - name: Download every runner's release artifacts
        uses: ${release_download_artifact}
        with:
          pattern: release-artifacts-*
          path: release-artifacts
          merge-multiple: true
      - name: Require at least one produced artifact
        run: |
          [ -n "\$(find release-artifacts -type f -print -quit)" ] \\
            || { echo "::error::no release artifacts were produced by the build matrix"; exit 1; }
      - name: Publish the snapshot release and artifacts
        env:
          GH_TOKEN: \${{ secrets.GITHUB_TOKEN }}
          VERSION: \${{ needs.verify.outputs.version }}
        run: |
          # RESTART_SAFE_GH_RELEASE_BEGIN
          snapshot="CHANGELOG/\$VERSION.md"
          notes="\$snapshot"
          notes_limit=125000
          if [ "\$(wc -c <"\$snapshot")" -gt "\$notes_limit" ]; then
            temp_root="\${RUNNER_TEMP:-/tmp}"
            notes="\$(mktemp "\$temp_root/release-notes.XXXXXX")"
            cleanup_release_notes() { rm -f -- "\$notes"; }
            trap cleanup_release_notes EXIT
            head -c 120000 "\$snapshot" | sed '\$d' >"\$notes"
            {
              printf "\n\n---\n\n"
              printf "_These notes were truncated at GitHub's 125,000-character limit. "
              printf "The complete and immutable snapshot for this release is "
              printf "[\\\`%s\\\`](%s/%s/blob/%s/%s)._\n" \\
                "\$snapshot" "\$GITHUB_SERVER_URL" "\$GITHUB_REPOSITORY" "\$VERSION" "\$snapshot"
            } >>"\$notes"
            [ "\$(wc -c <"\$notes")" -le "\$notes_limit" ] \\
              || { echo "::error::Bounded GitHub Release notes exceed \$notes_limit bytes."; exit 1; }
            echo "::notice::Release notes were truncated by bytes; the full immutable snapshot is \$snapshot."
          fi
          existing_tag="\$(gh release view "\$VERSION" --json tagName --jq .tagName 2>/dev/null || true)"
          if [ -n "\$existing_tag" ]; then
            [ "\$existing_tag" = "\$VERSION" ] \\
              || { echo "::error::GitHub Release lookup returned unexpected tag '\$existing_tag'."; exit 1; }
            gh release edit "\$VERSION" --notes-file "\$notes"
          else
            gh release create "\$VERSION" --verify-tag --notes-file "\$notes"
          fi
          gh release upload "\$VERSION" release-artifacts/* --clobber
          # RESTART_SAFE_GH_RELEASE_END
EOF
}

emit_release_snapshot() {
  local generation_command="release-snapshot ${ref}"
  local package_dirs_shell=''
  [ "$release_scope" = "@verjson" ] \
    || generation_command="$generation_command --scope $release_scope"
  [ "$release_node_version" = "24" ] \
    || generation_command="$generation_command --node-version $release_node_version"
  [ "$release_default_prefix_set" = false ] \
    || generation_command="$generation_command --default-prefix $release_default_prefix --default-component $release_default_component"
  generation_command="$generation_command$selected_package_dir_args"
  printf -v package_dirs_shell '%q ' "${release_package_dirs[@]}"
  package_dirs_shell="${package_dirs_shell% }"
  cat <<EOF
name: Release
run-name: Release \${{ inputs.version || 'auto' }} \${{ inputs.selector_digest || 'manual' }}

concurrency:
  group: release-\${{ github.repository }}
  cancel-in-progress: false

# Generated by verJSON/.github scripts/gen-changelog-caller.sh ${generation_command}
# — do not edit by hand. Regenerate it whenever the pinned contract commit moves,
# together with .github/workflows/changelog.yml, scripts/render-next.sh and
# scripts/changelog-contract.test.sh. A partial regeneration is the divergence
# the generator exists to prevent.
#
# WHY THE JOB ORDER IS THE POINT OF THIS FILE (verJSON/.github #463, #464, #465, #1206)
#
# \`verify\` and \`snapshot\` are byte-identical in spirit to release-node's: the
# irreversible changelog-release.yml snapshot may never be the first job to run,
# and \`verify\`/\`snapshot\` must share one runner pool (see release-node's header
# for the full rationale — it applies unchanged here).
#
# This mode exists for adopters that publish NOTHING from the release workflow —
# concretely, a repository whose container images are published by a separate,
# already-working workflow triggered independently of the release (#1206). They
# still cut versioned releases, so their NEXT/ fragments still have to be
# consumed into an immutable CHANGELOG/<version>.md; before this mode their only
# supported shape was "no release caller at all", which left NEXT/ permanently
# unconsumed. \`publish\` therefore carries no build matrix, no package
# publication and no artifact upload: it creates or updates the tag's GitHub
# Release from the immutable snapshot alone, using the same restart-safe
# release-notes logic release-node and release-artifact use.
#
# Nothing here may grow a publication stage. An adopter that gains something to
# publish regenerates as release-node (a package registry) or release-artifact
# (GitHub Release assets) instead; bolting a stage onto this mode by hand is the
# hand-edited divergence this generator exists to prevent.
#
# HOW TO CONFIGURE THE SUITE WITHOUT EDITING THIS FILE
#
# If your suite is not \`npm test\`, commit an executable \`scripts/release-verify.sh\`
# and \`verify\` runs it instead of the default Node sequence. The escape hatch is a
# separate file you own precisely so that no adopter has to edit a generated
# artifact — an artifact adopters must edit is the defect this generator removes,
# not a compromise it makes.
#
# The verification suite runs after package.json has been stamped to the
# dispatched version. Its expected version must be read dynamically from
# package.json; never assert a hardcoded version literal. This order is
# intentional: the suite verifies the exact package metadata that will ship.
#
# The operator still explicitly dispatches publication, but may leave the version
# blank. In that case the canonical release plan derives it from the selected
# fragments on this exact source commit; no push trigger infers a version from
# commit subjects (ADR 0038, ADR 0060).

on:
  workflow_dispatch:
    inputs:
      version:
        description: Optional exact SemVer tag; blank derives the next version from selected fragments
        required: false
        type: string
        default: ''
      prefix:
        description: Exact version namespace prefix; independent from component
        required: false
        type: string
        default: ${release_default_prefix_yaml}
      expected_head:
        description: Optional exact default-branch head derived by release-propose
        required: false
        type: string
        default: ''
      selector_digest:
        description: Optional canonical selection digest derived by release-propose
        required: false
        type: string
        default: ''
      fragments:
        description: Newline-separated NEXT fragment filenames; empty selects the requested component stream
        required: false
        type: string
        default: ''
      component:
        description: Optional component stream; empty selects only unscoped fragments
        required: false
        type: string
        default: ${release_default_component_yaml}

permissions:
  contents: read

jobs:
  verify:
    name: Verify the tree the snapshot will tag
    runs-on: \${{ fromJSON(${release_runner_expr}) }}
    timeout-minutes: 30
    permissions:
      contents: read
    outputs:
      selected: \${{ steps.release-version.outputs.selected }}
      version: \${{ steps.release-version.outputs.version }}
      selection-digest: \${{ steps.release-version.outputs.selection-digest }}
      snapshot-exists: \${{ steps.release-state.outputs.snapshot-exists }}
    steps:
      - name: Prepare job-scoped changelog tool cache
        run: echo "VERJSON_CHANGELOG_TOOL_CACHE=\$RUNNER_TEMP/verjson-changelog-tools" >> "\$GITHUB_ENV"
      # changelog-release.yml carries this guard too, but there it fires inside
      # \`snapshot\` — after \`verify\` has already spent a full suite run on a ref
      # whose tree will never be tagged. It is asserted here first because
      # verifying any ref other than the default branch proves nothing about the
      # content the snapshot takes (#466).
      - name: Release only from the default branch
        env:
          DISPATCH_REF: \${{ github.ref }}
          DEFAULT_BRANCH: \${{ github.event.repository.default_branch }}
        run: |
          if [ "\$DISPATCH_REF" != "refs/heads/\$DEFAULT_BRANCH" ]; then
            echo "::error::A release must be dispatched from \$DEFAULT_BRANCH, but this run was dispatched from '\$DISPATCH_REF'. The snapshot is always taken from \$DEFAULT_BRANCH, so verifying any other ref proves nothing about the tree that would be tagged."
            exit 1
          fi
          echo "Dispatched from \$DEFAULT_BRANCH; verifying its head."
      - name: Bind a proposer dispatch to its exact derived head
        env:
          EXPECTED_HEAD: \${{ inputs.expected_head }}
          SELECTOR_DIGEST: \${{ inputs.selector_digest }}
        run: |
          if [ -z "\$EXPECTED_HEAD" ] && [ -z "\$SELECTOR_DIGEST" ]; then
            echo "Manual dispatch has no proposer receipt to bind."
          elif [[ ! "\$EXPECTED_HEAD" =~ ^[0-9a-f]{40}\$ ]] ||
            [[ ! "\$SELECTOR_DIGEST" =~ ^[0-9a-f]{64}\$ ]]; then
            echo "::error::expected_head and selector_digest must either both be empty or both be canonical lowercase digests."
            exit 1
          elif [ "\$GITHUB_SHA" != "\$EXPECTED_HEAD" ]; then
            echo "::error::The default branch advanced from derived head \$EXPECTED_HEAD to dispatch head \$GITHUB_SHA. Derive the proposal again; this run will not verify, snapshot, or publish."
            exit 1
          else
            echo "Dispatch is bound to derived head \$EXPECTED_HEAD and selector \$SELECTOR_DIGEST."
          fi
      - name: Check out the tree that will be released
        uses: ${release_checkout}
        with:
          # github.sha is the default branch head at dispatch time, and
          # changelog-release.yml checks out that same commit rather than
          # re-resolving the branch name at snapshot time — so the tree verified
          # here is the tree that gets tagged, even if something merges to the
          # default branch while this job is running. Both halves must agree:
          # pinning only one of them reintroduces the window (#463, #464).
          ref: \${{ github.sha }}
          fetch-depth: 0
      - name: Check out the canonical selection contract
        uses: ${release_checkout}
        with:
          repository: verJSON/.github
          ref: ${ref}
          path: .changelog-contract
          persist-credentials: false
${release_plan_step}
      - name: Resolve restart-safe release state
        id: release-state
        env:
          VERSION: \${{ steps.release-version.outputs.version }}
        run: |
          if git ls-remote --exit-code --tags origin "refs/tags/\$VERSION" >/dev/null 2>&1; then
            git fetch --force origin "refs/tags/\$VERSION:refs/tags/\$VERSION"
            if [ ! -f "CHANGELOG/\$VERSION.md" ] ||
              ! git cat-file -e "\$VERSION:CHANGELOG/\$VERSION.md" ||
              ! git merge-base --is-ancestor "\$VERSION" HEAD ||
              ! git diff --quiet "\$VERSION" HEAD -- "CHANGELOG/\$VERSION.md"; then
              echo "::error::Tag \$VERSION exists but is not the immutable release snapshot reachable from this default-branch head. Refusing to resume a conflicting release."
              exit 1
            fi
            echo "snapshot-exists=true" >> "\$GITHUB_OUTPUT"
            echo "\$VERSION already has its immutable snapshot; verifying current release inputs before resuming publication."
          elif [ -e "CHANGELOG/\$VERSION.md" ]; then
            echo "::error::CHANGELOG/\$VERSION.md already exists, and a released snapshot is immutable (ADR 0059). Cut the next version instead."
            exit 1
          else
            echo "snapshot-exists=false" >> "\$GITHUB_OUTPUT"
            echo "\$VERSION is unused."
          fi
      - name: Check out the existing snapshot for resumed verification
        if: steps.release-version.outputs.selected == 'true' && steps.release-state.outputs.snapshot-exists == 'true'
        uses: ${release_checkout}
        with:
          ref: \${{ steps.release-version.outputs.version }}
          fetch-depth: 0
          persist-credentials: false
      # A snapshot-only adopter publishes nothing and need not be a Node project
      # (#1206): the Node steps run only when a package.json exists, and a
      # repository without one must verify through its own verify hook below.
      - uses: ${release_setup_node}
        if: steps.release-version.outputs.selected == 'true' && hashFiles('package.json') != ''
        with:
          # Keep the literal inside an expression so Renovate's uses-with
          # extractor leaves it alone while setup-node receives the same value.
          node-version: \${{ '${release_node_version}' }}
          registry-url: https://npm.pkg.github.com
          scope: '${release_scope}'
          package-manager-cache: false
      - name: Install dependencies
        if: steps.release-version.outputs.selected == 'true' && hashFiles('package.json') != ''
        run: npm ci
        env:
          # NOT GITHUB_TOKEN (#465). A repository-scoped GITHUB_TOKEN cannot read
          # a private GitHub Packages package owned by a DIFFERENT repository, so
          # an adopter with a private @verjson devDependency 401s here. Canonical
          # node-ci.yml states the same requirement for the same reason.
          NODE_AUTH_TOKEN: \${{ secrets.NODE_AUTH_TOKEN }}
      - name: Prepare release package metadata
        if: steps.release-version.outputs.selected == 'true'
        env:
          PACKAGE_VERSION: \${{ steps.release-version.outputs.package-version }}
        run: |
          if [ -e scripts/release-prepare-packages.sh ] && [ ! -x scripts/release-prepare-packages.sh ]; then
            echo "::error::scripts/release-prepare-packages.sh exists but is not executable."
            exit 1
          fi
          if [ -x scripts/release-prepare-packages.sh ]; then
            scripts/release-prepare-packages.sh "\$PACKAGE_VERSION"
          fi
      - name: Stamp the dispatched package versions
        if: steps.release-version.outputs.selected == 'true' && hashFiles('package.json') != ''
        env:
          PACKAGE_VERSION: \${{ steps.release-version.outputs.package-version }}
        run: |
          package_dirs=(${package_dirs_shell})
          for package_dir in "\${package_dirs[@]}"; do
            npm version --prefix "\$package_dir" "\$PACKAGE_VERSION" --no-git-tag-version --ignore-scripts --allow-same-version
          done
      - name: Run the release verification suite
        if: steps.release-version.outputs.selected == 'true'
        env:
          NODE_AUTH_TOKEN: \${{ secrets.NODE_AUTH_TOKEN }}
          PACKAGE_VERSION: \${{ steps.release-version.outputs.package-version }}
        run: |
          # Existence and executability are checked separately on purpose. A
          # single \`-x\` test reads a hook committed without the executable bit
          # as "no hook here" and quietly runs the Node default instead — so an
          # adopter who deliberately replaced their suite watches a green
          # release verified by the suite they replaced.
          if [ -e scripts/release-verify.sh ] && [ ! -x scripts/release-verify.sh ]; then
            echo "::error::scripts/release-verify.sh exists but is not executable, so this release would silently fall back to the default Node suite. Run: chmod +x scripts/release-verify.sh && git update-index --chmod=+x scripts/release-verify.sh"
            exit 1
          fi
          if [ -x scripts/release-verify.sh ]; then
            echo "Running this repository's scripts/release-verify.sh"
            verification_status=0
            scripts/release-verify.sh || verification_status=\$?
          elif [ ! -f package.json ]; then
            echo "::error::No package.json and no executable scripts/release-verify.sh: nothing verifies this tree before the snapshot. Commit scripts/release-verify.sh (#1206)."
            exit 1
          else
            verification_status=0
            npm run build --if-present &&
              npm run typecheck --if-present &&
              npm run lint --if-present &&
              npm test || verification_status=\$?
          fi
          if [ "\$verification_status" -ne 0 ]; then
            echo "::error::Release verification failed against stamped dispatch version \$PACKAGE_VERSION. Check for the hardcoded-version footgun: version assertions can pass in pull requests and local runs, then fail only here; read the expected version dynamically from package.json."
            exit "\$verification_status"
          fi

  snapshot:
    # The irreversible act, and the only job that may not run first.
    needs: verify
    if: needs.verify.outputs.selected == 'true' && needs.verify.outputs.snapshot-exists != 'true'
    uses: verJSON/.github/.github/workflows/changelog-release.yml@${ref}
    secrets: inherit
    permissions:
      actions: read
      # The reusable workflow pushes with its separately minted release App
      # token. This caller grant caps only its read-only GITHUB_TOKEN (#784).
      contents: read
    with:
      contract_ref: ${ref}
      version: \${{ needs.verify.outputs.version }}
      fragments: \${{ inputs.fragments }}
      component: \${{ inputs.component }}
      prefix: \${{ inputs.prefix }}
      selection_digest: \${{ needs.verify.outputs.selection-digest }}
      # v3 recommends the App client ID and deprecates its legacy numeric ID.
      release_app_client_id: \${{ vars.RELEASE_APP_CLIENT_ID }}
      release_environment: release-app
      # Explicit, so both halves of one release share one pool (#465).
      runner: \${{ ${release_runner_expr} }}

  publish:
    name: Publish the released snapshot
    needs: [verify, snapshot]
    if: always() && needs.verify.result == 'success' && needs.verify.outputs.selected == 'true' && (needs.snapshot.result == 'success' || needs.snapshot.result == 'skipped')
    runs-on: \${{ fromJSON(${release_runner_expr}) }}
    timeout-minutes: 15
    permissions:
      contents: write
    steps:
      - uses: ${release_checkout}
        with:
          ref: \${{ needs.verify.outputs.version }}
          fetch-depth: 0
          persist-credentials: false
      - name: Verify the checked-out tag and immutable release note
        env:
          VERSION: \${{ needs.verify.outputs.version }}
        run: |
          test "\$(git describe --tags --exact-match HEAD)" = "\$VERSION"
          test -f "CHANGELOG/\$VERSION.md"
      - name: Publish the snapshot release notes
        env:
          GH_TOKEN: \${{ secrets.GITHUB_TOKEN }}
          VERSION: \${{ needs.verify.outputs.version }}
        run: |
          # RESTART_SAFE_GH_RELEASE_BEGIN
          snapshot="CHANGELOG/\$VERSION.md"
          notes="\$snapshot"
          notes_limit=125000
          if [ "\$(wc -c <"\$snapshot")" -gt "\$notes_limit" ]; then
            temp_root="\${RUNNER_TEMP:-/tmp}"
            notes="\$(mktemp "\$temp_root/release-notes.XXXXXX")"
            cleanup_release_notes() { rm -f -- "\$notes"; }
            trap cleanup_release_notes EXIT
            head -c 120000 "\$snapshot" | sed '\$d' >"\$notes"
            {
              printf "\n\n---\n\n"
              printf "_These notes were truncated at GitHub's 125,000-character limit. "
              printf "The complete and immutable snapshot for this release is "
              printf "[\\\`%s\\\`](%s/%s/blob/%s/%s)._\n" \\
                "\$snapshot" "\$GITHUB_SERVER_URL" "\$GITHUB_REPOSITORY" "\$VERSION" "\$snapshot"
            } >>"\$notes"
            [ "\$(wc -c <"\$notes")" -le "\$notes_limit" ] \\
              || { echo "::error::Bounded GitHub Release notes exceed \$notes_limit bytes."; exit 1; }
            echo "::notice::Release notes were truncated by bytes; the full immutable snapshot is \$snapshot."
          fi
          existing_tag="\$(gh release view "\$VERSION" --json tagName --jq .tagName 2>/dev/null || true)"
          if [ -n "\$existing_tag" ]; then
            [ "\$existing_tag" = "\$VERSION" ] \\
              || { echo "::error::GitHub Release lookup returned unexpected tag '\$existing_tag'."; exit 1; }
            gh release edit "\$VERSION" --notes-file "\$notes"
          else
            gh release create "\$VERSION" --verify-tag --notes-file "\$notes"
          fi
          # RESTART_SAFE_GH_RELEASE_END
EOF
}

emit_release_propose() {
  local write_permission reusable_workflow
  if [ "$release_autonomy" = propose ]; then
    write_permission="      issues: write"
    reusable_workflow="release-propose.yml"
  else
    write_permission="      actions: write"
    reusable_workflow="release-dispatch.yml"
  fi
  cat <<EOF
name: Release proposal

# Generated by verJSON/.github scripts/gen-changelog-caller.sh release-propose ${ref} --autonomy ${release_autonomy}
# — do not edit by hand. The autonomy is fixed in source so a scheduled run
# cannot acquire a different write permission through an event input.
on:
  schedule:
    - cron: '17 9 * * *'
  workflow_dispatch:
    inputs:
      fragments:
        description: Newline-separated NEXT fragment filenames; empty selects the component stream
        required: false
        type: string
        default: ''
      component:
        description: Optional component stream; empty selects only unscoped fragments
        required: false
        type: string
        default: ''
      prefix:
        description: Release tag prefix used by next-version
        required: false
        type: string
        default: v

permissions:
  contents: read

jobs:
  release-propose:
    permissions:
      contents: read
${write_permission}
    uses: verJSON/.github/.github/workflows/${reusable_workflow}@${ref}
    with:
      contract_ref: ${ref}
      fragments: \${{ inputs.fragments }}
      component: \${{ inputs.component }}
      prefix: \${{ inputs.prefix || 'v' }}
      runner: \${{ github.repository_owner == 'verJSON' && (vars.CI_RUNNER_DEFAULT || '["self-hosted","general"]') || '["ubuntu-24.04"]' }}
EOF
}

emit_renderer() {
  cat <<EOF
#!/usr/bin/env bash
# Prints the running log from the NEXT/ changelog fragments, newest first.
#
# Generated by verJSON/.github scripts/gen-changelog-caller.sh renderer ${ref}
# — do not edit by hand. Rendering is not implemented here: the canonical
# changelog contract
# lives in verJSON/.github (ADR 0038) and this repository pins one immutable
# commit of it, shared with .github/workflows/changelog.yml so that what you
# render locally is what CI validates.
set -euo pipefail

CONTRACT_REF="${ref}"
CONTRACT_SHA256="${contract_sha256}"

# --as-released is the only flag that passes through. It shows what a release
# would write into CHANGELOG/<version>.md, which under ADR 0059 can never be
# edited afterwards — so reading it before merge is the one review step the
# contract asks of a fragment author, and it has to be reachable from the tool
# they are given (#443). Everything else is still refused: this is a renderer,
# not a general front end to a pinned engine.
as_released=
component=
while [ "\$#" -gt 0 ]; do
  case "\$1" in
    --as-released)
      [ -z "\$as_released" ] || { echo "render-next: --as-released was repeated" >&2; exit 2; }
      as_released=--as-released
      shift
      ;;
    --component)
      [ "\$#" -ge 2 ] && [ -n "\$2" ] \
        || { echo "render-next: --component requires a value" >&2; exit 2; }
      [ -z "\$component" ] \
        || { echo "render-next: --component was repeated" >&2; exit 2; }
      component="\$2"
      shift 2
      ;;
    *)
      echo "render-next: unexpected argument '\$1'" >&2
      exit 2
      ;;
  esac
done

root="\$(cd "\$(dirname "\$0")/.." && pwd)"

contract_fail() { echo "render-next: \$1" >&2; exit 1; }
EOF
  emit_contract_resolution
  cat <<EOF

args=(render-next --repo-root "\$root")
[ -z "\$component" ] || args+=(--component "\$component")
[ -z "\$as_released" ] || args+=(--as-released)
exec python3 "\$contract" "\${args[@]}"
EOF
}

# Digests what a resolver actually produced, or nothing at all.
#
# `sha256sum` will happily digest an empty stream, and the empty-string digest
# is a real-looking pin that no adopter file can ever match — so a resolver that
# failed used to yield a contract test which was unsatisfiable rather than one
# that reported the missing canonical source. That also made the `[ -n ... ]`
# guards in the emitted `validate_adr_generator` vacuously true. A temp file,
# rather than a pipeline, because command substitution strips trailing newlines
# and the pin has to match the bytes the adopter writes to disk.
digest_of_resolved() { # "$@" = the resolver command
  local tmp err digest=''
  tmp="$(mktemp)" || {
    echo "$(basename "$0"): cannot create a temporary file to digest $1" >&2
    return 1
  }
  err="$(mktemp)" || {
    echo "$(basename "$0"): cannot create a temporary file to capture $1's diagnostics" >&2
    rm -f "$tmp"
    return 1
  }
  # `-s` is unreachable from the callers this function has today: both emit_*
  # resolvers already refuse an empty canonical file with their own diagnostic,
  # so nothing reaches here having exited zero with nothing written. It is kept
  # deliberately, as the last line of defense rather than a covered branch --
  # removing it alone leaves the suite green, but removing it together with the
  # pin-source unification reproduces the original defect exactly, pinning the
  # empty-string hash. Read it as belt-and-braces for a future resolver that
  # does not carry its own guard, not as a claim the suite is asserting.
  if "$@" >"$tmp" 2>"$err" && [ -s "$tmp" ]; then
    digest="$(digest_of <"$tmp")" || digest=''
  fi
  if [ -z "$digest" ]; then
    # The resolver's own explanation, not a silent empty pin: without it the
    # adopter-side message blames the pin for a hub-side resolution failure.
    echo "$(basename "$0"): $1 produced no digestible output at $ref; the emitted contract test will report the missing canonical source" >&2
    [ -s "$err" ] && sed 's/^/  /' "$err" >&2
  fi
  rm -f "$tmp" "$err"
  [ -n "$digest" ] || return 1
  printf '%s' "$digest"
}

emit_contract_test() {
  local adr_index_sha256="" adr_index_test_sha256="" codeowners_sha256=""
  codeowners_sha256="$(codeowners_digest_at_ref)" || {
    echo "$(basename "$0"): cannot digest the embedded CODEOWNERS member" >&2
    return 1
  }
  adr_index_sha256="$(digest_of_resolved emit_adr_index_generator)" || adr_index_sha256=""
  # The adopter's copy of the suite is the rewritten form, not the canonical
  # bytes, so its digest has to be taken from what `adr-index-test` emits.
  adr_index_test_sha256="$(digest_of_resolved emit_adr_index_test)" || adr_index_test_sha256=""
  # The interpolated preamble is kept deliberately small: everything below it is
  # a quoted heredoc, so the body cannot accidentally expand a generator-side
  # variable into an adopter's test.
  local release_package_dirs_json="$selected_package_dirs_json"
  local release_package_dirs_shell=''
  local release_assets_json='[' release_asset_sep=''
  local release_approved_packages_csv='' release_approved_package=''
  local release_lane_names='' release_lane_env='' release_lane_preflight='' release_lane_preflight_sha256=''
  printf -v release_package_dirs_shell '%q ' "${release_package_dirs[@]}"
  release_package_dirs_shell="${release_package_dirs_shell% }"
  for release_asset in "${release_assets[@]}"; do
    release_assets_json="$release_assets_json$release_asset_sep\"$release_asset\""
    release_asset_sep=,
  done
  release_assets_json="$release_assets_json]"
  for release_approved_package in "${release_approved_internal_packages[@]}"; do
    release_approved_packages_csv="${release_approved_packages_csv:+$release_approved_packages_csv,}$release_approved_package"
  done
  for release_build_runner in "${release_build_runners[@]}"; do
    if [[ "$release_build_runner" =~ vars\.(CI_LANE_TRUSTED_(MACOS|WINDOWS)) ]]; then
      release_lane_name="${BASH_REMATCH[1]}"
      if [[ ",$release_lane_names," != *",$release_lane_name,"* ]]; then
        release_lane_names="${release_lane_names:+$release_lane_names,}$release_lane_name"
        release_lane_env="${release_lane_env}          $release_lane_name: \${{ vars.$release_lane_name }}
"
      fi
    fi
  done
  if [ -n "$release_lane_names" ]; then
    release_lane_preflight="$(cat <<EOF
      - name: Validate required OS-scoped build lanes
        if: steps.release-version.outputs.selected == 'true'
        shell: bash
        env:
          REQUIRED_BUILD_LANES: '${release_lane_names}'
${release_lane_env%$'\n'}
        run: |
          set -euo pipefail
          IFS=',' read -ra lane_names <<<"\$REQUIRED_BUILD_LANES"
          for lane_name in "\${lane_names[@]}"; do
            lane_value="\${!lane_name:-}"
            LANE_NAME="\$lane_name" LANE_VALUE="\$lane_value" node <<'NODE'
          const name = process.env.LANE_NAME;
          let value;
          try { value = JSON.parse(process.env.LANE_VALUE); } catch { throw new Error(name + ' must be a non-empty JSON runner-label array'); }
          if (!Array.isArray(value) || value.length === 0 || value.some(label => typeof label !== 'string' || !label)) throw new Error(name + ' must be a non-empty JSON runner-label array');
          NODE
          done
EOF
)"
    release_lane_preflight_sha256="$(printf '%s' "$release_lane_preflight" | digest_of)"
  fi
  cat <<EOF
#!/usr/bin/env bash
# Asserts that this repository still satisfies the canonical verJSON changelog
# contract (verJSON/.github ADR 0038) rather than a local re-implementation of it.
#
# Generated by verJSON/.github scripts/gen-changelog-caller.sh contract-test ${ref}
# — do not edit by hand. Regenerate it whenever the pinned contract
# commit moves, together with
# .github/workflows/changelog.yml, scripts/render-next.sh and — wherever
# \`adr-index: true\` is in effect — scripts/gen-adr-index.sh and
# scripts/gen-adr-index.test.sh. A partial regeneration is the divergence this
# generator exists to prevent.
#
# Every assertion here holds both BEFORE and AFTER a release. \`release\` consumes
# NEXT/, writes CHANGELOG/<version>.md and generates the root CHANGELOG.md, so an
# assertion phrased as "nothing has been released yet" is a time bomb: consumers
# wire this suite into \`npm test\`, which release workflows run before publishing,
# so it would abort the release it exists to protect. Assertions about repository
# content are therefore derived from the tree, never named inline.
set -euo pipefail

CONTRACT_REF="${ref}"
CONTRACT_SHA256="${contract_sha256}"
ADR_INDEX_SHA256="${adr_index_sha256}"
ADR_INDEX_TEST_SHA256="${adr_index_test_sha256}"
EXPECTED_CODEOWNERS_SHA256="${codeowners_sha256}"
EXPECTED_RELEASE_SCOPE="${release_scope}"
EXPECTED_RELEASE_NODE_VERSION="${release_node_version}"
EXPECTED_RELEASE_PACKAGE_DIRS_JSON='${release_package_dirs_json}'
EXPECTED_RELEASE_PACKAGE_DIRS_SHELL='${release_package_dirs_shell}'
EXPECTED_RELEASE_ASSETS_JSON='${release_assets_json}'
EXPECTED_RELEASE_APPROVED_INTERNAL_PACKAGES='${release_approved_packages_csv}'
EXPECTED_RELEASE_LANE_PREFLIGHT_SHA256='${release_lane_preflight_sha256}'
EXPECTED_RELEASE_UPLOAD_ARTIFACT='${release_upload_artifact}'
EXPECTED_RELEASE_DOWNLOAD_ARTIFACT='${release_download_artifact}'
EXPECTED_RELEASE_CACHE_SAVE='${release_cache_save}'
EXPECTED_RELEASE_CACHE_RESTORE='${release_cache_restore}'
EOF
  cat <<'EOF'

root="$(cd "$(dirname "$0")/.." && pwd)"
renderer="$root/scripts/render-next.sh"
validation_workflow="$root/.github/workflows/changelog.yml"
renovate_attribution_workflow="$root/.github/workflows/renovate-changelog.yml"
release_propose_workflow="$root/.github/workflows/release-propose.yml"
pr_gate_workflow="$root/.github/workflows/changelog-contract.yml"

fail() { echo "FAIL - $1" >&2; exit 1; }

contract_fail() { fail "$1"; }
EOF
  emit_contract_resolution
  cat <<'EOF' 

work="$(mktemp -d)"
fixture_root="$(mktemp -d)"
trap 'rm -rf "$work" "$fixture_root"' EXIT

python3 "$contract" validate --repo-root "$root"
echo "ok - canonical validation accepts this repository"

# --- the generated set moves as one commit (verJSON/.github #1369) ---------
#
# This suite, the renderer, the changelog caller, the PR gate and the release
# callers are one artifact spread across several files, all generated at one
# contract commit. Regenerating a strict subset leaves the rest behind. Until
# #1369 that invariant was carried only by prose in the generated headers and
# by a Renovate grouping rule; neither reddens when a human regenerates half
# the set by hand, and the per-member assertions further down stop at whichever
# divergence they reach first.
#
# SECURITY BOUNDARY: a member's header is adopter-controlled text. It is read
# here as a CLAIM about which commit produced the file, constrained by these
# patterns to 40 lowercase hex characters, and compared as a string. It is
# never evaluated, sourced, expanded, or used to build a command. The remedy
# printed below is composed from this file's own constants only. ~95 adopter
# repositories call into the hub with this suite; letting an adopter's header
# name what the checker runs would be a privilege escalation across all of them.
#
# Every arm reports POSITIVE evidence. Missing, unreadable, unparsable and
# empty extractions are all divergence findings, never a silent skip: a check
# that reads a malformed member as conformance is the defect class this exists
# to close.
#
# The header states which generator MODE produced the file, and that is part of
# the claim, not decoration: a changelog.yml carrying a `pr-gate` header at the
# right commit is not the changelog caller. Each member below names the modes
# that write its path, so this verdict -- which is advertised as authoritative
# and printed before every per-member assertion -- never agrees with a file it
# has not identified. The declared mode is constrained to lowercase letters and
# hyphens and is PRINTED in a finding; like the pin, it is never evaluated,
# sourced, expanded, or used to build a command.
generated_set_header_pin='^# Generated by verJSON/\.github scripts/gen-changelog-caller\.sh [a-z][a-z-]* ([0-9a-f]{40})( .*)?$'
generated_set_header_mode='^# Generated by verJSON/\.github scripts/gen-changelog-caller\.sh ([a-z][a-z-]*) [0-9a-f]{40}( .*)?$'
generated_set_assign_pin='^CONTRACT_REF="([0-9a-f]{40})"$'
generated_set_comment_pin='^# CONTRACT_REF=([0-9a-f]{40})$'

generated_set_problems=''
generated_set_note() {
  generated_set_problems="$generated_set_problems
  - $1"
}

generated_set_symlink_component() { # generated_set_symlink_component <relative-path>
  local current="$root" part
  local -a parts
  IFS='/' read -ra parts <<< "$1"
  for part in "${parts[@]}"; do
    current="$current/$part"
    if [ -L "$current" ]; then
      printf '%s' "${current#"$root"/}"
      return 0
    fi
  done
  return 1
}

# The remedy in every finding is composed from $mode, a literal in THIS file,
# and $CONTRACT_REF, this file's own constant. Nothing in it is derived from the
# member being reported on: $declared is used only after grep -qxE has matched it
# EXACTLY against $accepted, an alternation that is itself a literal here, so the
# value substituted is one of this file's own constants and not adopter text.
#
# Repair guidance is deliberately prose, never a shell command. The checker
# cannot recover every caller-specific option from a workflow, and a direct `>`
# redirection truncates its target before generation succeeds. The guidance
# names a mode hint when available, asks the operator to inspect the
# existing artifacts for the full mode and options, and directs a complete
# regeneration into a temporary checkout for review before replacing the set.
generated_set_remedy() { # generated_set_remedy <one-mode|alternation> <rel> <note>
  local mode_hint
  case "$1" in
    *[\|{}]*)
      mode_hint="its mode cannot be established by this check; possible modes are $(printf '%s' "$1" | tr -d '{}' | sed 's/|/, /g')"
      ;;
    *)
      mode_hint="mode hint: $1; verify it against existing artifacts"
      ;;
  esac
  printf '%s' "Regenerate the complete generated set at one immutable contract commit. Affected member: $2; $mode_hint. Inspect the existing caller and related generated artifacts to derive the exact mode and all custom generator options; preserve them. Generate into a clean temporary checkout, review the full diff, then replace the committed set together. This diagnostic intentionally prints no single-file command.$3"
}

# The INTERNAL SHAPE of this function is pinned by the hub's
# scripts/ci-gate/changelog-caller-contract.test.sh: $remedy is assigned only by
# the two generated_set_remedy compositions below, and the remedy-safety cases
# drive one arm per member rather than every arm, standing in for the rest
# only while that holds. Any line here that *names* $remedy and is not one of
# those two compositions reddens that pin -- a third composition, an append, a
# rewrite at a use site. Its reach is the name: an indirect write that never
# spells `remedy` (building the variable name in another parameter and using
# `printf -v`, `eval`, `declare -g` or a `local -n` alias) is classified as a
# read and passes. That is a known limit, not a guarantee.
generated_set_check() { # generated_set_check <relative-path> <required|optional> <pin-pattern> <remedy-mode> <accepted-modes|''> [remedy-note]
  local rel="$1" required="$2" pattern="$3" mode="$4" accepted="$5" note="${6:-}"
  local abs="$root/$rel" claims claim count remedy modes mode_count declared symlink_component repair_command quoted_path parent_dir quoted_parent
  remedy="$(generated_set_remedy "$mode" "$rel" "$note")"
  if symlink_component="$(generated_set_symlink_component "$rel")"; then
    printf -v quoted_path '%q' "$symlink_component"
    parent_dir="${rel%/*}"
    printf -v quoted_parent '%q' "$parent_dir"
    repair_command="if [ -L $quoted_path ]; then rm -f -- $quoted_path; else printf '%s\\n' 'Refusing to remove a path that is no longer a symlink: $quoted_path' >&2; exit 1; fi && mkdir -p -- $quoted_parent"
    generated_set_note "$rel traverses symlinked path component $symlink_component, so its pin cannot be compared with $CONTRACT_REF. Safe path repair preserves the symlink target: \`$repair_command\`. Run it before regenerating the whole set; do not redirect into a symlink. $remedy"
    return 0
  fi
  if [ ! -e "$abs" ]; then
    if [ "$required" = required ]; then
      generated_set_note "$rel is absent, so nothing establishes that member at $CONTRACT_REF. $remedy"
    fi
    return 0
  fi
  if [ ! -f "$abs" ] || [ ! -r "$abs" ]; then
    generated_set_note "$rel is present but not a readable regular file, so its pin cannot be compared with $CONTRACT_REF. $remedy"
    return 0
  fi
  if [ -n "$accepted" ]; then
    modes="$(sed -nE "s|$generated_set_header_mode|\1|p" "$abs" 2>/dev/null)" || modes=''
    mode_count=0
    if [ -n "$modes" ]; then
      mode_count="$(printf '%s\n' "$modes" | wc -l | tr -d ' ')"
    fi
    if [ "$mode_count" -ne 1 ]; then
      generated_set_note "$rel declares $mode_count generator-mode headers; a member states exactly one, so nothing in it identifies which mode produced it and its pin claims nothing about this member. $remedy"
      return 0
    fi
    declared="$modes"
    if ! printf '%s\n' "$declared" | grep -qxE "$accepted"; then
      generated_set_note "$rel declares generator mode '$declared', which does not write this path, so its pin claims nothing about this member. $remedy"
      return 0
    fi
    # $declared matched $accepted exactly, so it is one of this file's own mode
    # literals. Passing it through makes the prose identify the mode declared by
    # this member; custom generator options still have to be inspected in the
    # existing artifacts before regeneration.
    remedy="$(generated_set_remedy "$declared" "$rel" "$note")"
  fi
  if ! claims="$(sed -nE "s|$pattern|\1|p" "$abs" 2>/dev/null)"; then
    generated_set_note "$rel could not be scanned for a pin declaration, so it cannot be compared with $CONTRACT_REF. $remedy"
    return 0
  fi
  count=0
  if [ -n "$claims" ]; then
    count="$(printf '%s\n' "$claims" | wc -l | tr -d ' ')"
  fi
  if [ "$count" -eq 0 ]; then
    generated_set_note "$rel declares no contract pin this suite can read, so nothing in it claims $CONTRACT_REF. $remedy"
    return 0
  fi
  if [ "$count" -gt 1 ]; then
    generated_set_note "$rel declares $count contract pins ($(printf '%s' "$claims" | tr '\n' ' ')); a member states exactly one, and $CONTRACT_REF cannot be matched against several. $remedy"
    return 0
  fi
  claim="$claims"
  if [ "$claim" != "$CONTRACT_REF" ]; then
    generated_set_note "$rel is still at $claim while this suite was generated at $CONTRACT_REF. $remedy"
  fi
  return 0
}

# Where several modes write one path, the remedy names all of them and says how
# they differ. A remedy hardcoded to one mode is worse than no remedy on ~95
# repositories: following `generated-artifacts` on a repository that adopted
# `generated-artifacts-with-adr-index` silently drops `adr-index: true`, the
# pinned scripts/gen-adr-index.sh, and the validate_adr_generator path with it.
generated_set_check .github/workflows/changelog.yml           required "$generated_set_header_pin" \
  '{generated-artifacts|generated-artifacts-with-adr-index|workflow}' \
  'generated-artifacts|generated-artifacts-with-adr-index|workflow' \
  " Name the mode this repository already adopted: generated-artifacts-with-adr-index also wires adr-index: true and the pinned scripts/gen-adr-index.sh, and workflow is the compatibility alias."
generated_set_check .github/workflows/changelog-contract.yml  required "$generated_set_header_pin" pr-gate pr-gate
generated_set_check scripts/render-next.sh                    required "$generated_set_assign_pin" renderer renderer
# This suite is itself a member of the set it checks, so its pin claim is read
# out of the file the comparison value was assigned from and the "still at" arm
# cannot fire for it. It stays enumerated because SOMETHING has to fire for it:
# repinning the suite by adding a second CONTRACT_REF line rather than
# regenerating leaves a file that is half-old and half-new, which is precisely
# the partial regeneration this check exists to catch, and dropping the member
# from the enumeration loses that entirely.
#
# What fires is a narrower claim than it looks. The multiplicity arm below is
# not load-bearing for DETECTION here: with two CONTRACT_REF lines the extracted
# claim is a two-line string, so the trailing mismatch arm reports the member in
# any case. The multiplicity arm decides only which MESSAGE the reader gets --
# "declares 2 contract pins", naming the actual fault, rather than a mismatch
# against a value that is itself one of the two. The covering suite therefore
# asserts that wording, because an assertion on the member alone stays green
# with the arm deleted.
generated_set_check scripts/changelog-contract.test.sh        required "$generated_set_assign_pin" contract-test contract-test
# Optional members: a repository that never adopted Renovate attribution, a
# release caller, a release proposer or the ADR index legitimately does not
# carry the file. Once present, it is held to the same pin as everything else.
generated_set_check .github/workflows/renovate-changelog.yml  optional "$generated_set_header_pin" renovate-attribution renovate-attribution
generated_set_check .github/workflows/release.yml             optional "$generated_set_header_pin" \
  '{release-node|release-artifact|release-snapshot}' \
  'release-node|release-artifact|release-snapshot' \
  " Name the mode this repository already adopted: release-artifact publishes GitHub Release assets, and release-snapshot publishes nothing from the release workflow."
generated_set_check .github/workflows/release-propose.yml     optional "$generated_set_header_pin" release-propose release-propose
# The ADR index test is held to the pin here even when the caller does not set
# `adr-index: true`. validate_adr_generator's stronger digest comparison runs
# only under that key, so a repository that regenerated changelog.yml WITHOUT
# adr-index and left these files behind -- a partial regeneration -- has no
# other check on them.
generated_set_check scripts/gen-adr-index.sh                  optional "$generated_set_header_pin" adr-index-generator adr-index-generator
generated_set_check scripts/gen-adr-index.test.sh             optional "$generated_set_comment_pin" adr-index-test adr-index-test

if [ -n "$generated_set_problems" ]; then
  fail "the generated adopter set is not atomic. Regenerating a subset is the divergence scripts/gen-changelog-caller.sh exists to prevent: regenerate every member at one commit. This suite is pinned at $CONTRACT_REF and these members disagree:$generated_set_problems"
fi
echo "ok - every generated member of the adopter set pins $CONTRACT_REF"
# .github/CODEOWNERS is a required member of the adopter set (ADR 0210): the
# organization ruleset main-protection requires code-owner review, and that
# rule asks for nothing in a repository without a CODEOWNERS file. The file is
# generated, so it is held to the exact bytes the contract renders at
# $CONTRACT_REF; a competing CODEOWNERS location would take precedence over the
# canonical one and is refused as well.
codeowners_remedy="Generate it with: scripts/gen-changelog-caller.sh codeowners $CONTRACT_REF > .github/CODEOWNERS (regenerate the whole adopter set at one commit)"
[ -n "$EXPECTED_CODEOWNERS_SHA256" ] \
  || fail "this suite carries no CODEOWNERS pin; regenerate it with scripts/gen-changelog-caller.sh contract-test $CONTRACT_REF"
[ ! -L "$root/.github" ] && [ ! -L "$root/.github/CODEOWNERS" ] \
  || fail ".github/CODEOWNERS must be a regular repository file, not reached through a symlink. $codeowners_remedy"
[ -f "$root/.github/CODEOWNERS" ] \
  || fail ".github/CODEOWNERS is absent, so main-protection's code-owner review requirement asks for nothing here. $codeowners_remedy"
[ "$(contract_digest_of "$root/.github/CODEOWNERS")" = "$EXPECTED_CODEOWNERS_SHA256" ] \
  || fail ".github/CODEOWNERS is not the generated artifact at $CONTRACT_REF (hand-edited, stale, or from another pin). $codeowners_remedy"
for competing in CODEOWNERS docs/CODEOWNERS; do
  [ ! -e "$root/$competing" ] && [ ! -L "$root/$competing" ] \
    || fail "$competing competes with .github/CODEOWNERS; GitHub reads only the first location it finds, so remove it after preserving its ownership intent in the canonical file"
done
echo "ok - .github/CODEOWNERS is the generated artifact at $CONTRACT_REF"

[ -f "$pr_gate_workflow" ] \
  || fail "$pr_gate_workflow is missing. Generate it with: scripts/gen-changelog-caller.sh pr-gate $CONTRACT_REF > .github/workflows/changelog-contract.yml"
grep -qE "^# Generated by verJSON/\.github scripts/gen-changelog-caller\.sh pr-gate $CONTRACT_REF$" "$pr_gate_workflow" \
  || fail "$pr_gate_workflow is not the generated PR gate at $CONTRACT_REF"
[ "$(grep -Ec '^  changelog-contract:$' "$pr_gate_workflow")" = 1 ] \
  || fail "$pr_gate_workflow must publish exactly one changelog-contract job"
grep -qF 'VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools' "$pr_gate_workflow" \
  || fail "$pr_gate_workflow does not prepare a job-writable changelog cache (#822)"
cache_line="$(grep -nF 'VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools' "$pr_gate_workflow" | cut -d: -f1)"
test_line="$(grep -nF 'bash scripts/changelog-contract.test.sh' "$pr_gate_workflow" | cut -d: -f1)"
[ -n "$test_line" ] && [ "$cache_line" -lt "$test_line" ] \
  || fail "$pr_gate_workflow must prepare its cache before contract validation (#822)"

# One pin, shared by every generated artifact: local rendering must predict the
# CI run that gates the PR, and the release must write the shape both assumed.
[ "$(grep -Ec '^  changelog:$' "$validation_workflow")" = 1 ] \
  || fail "$validation_workflow does not publish the required changelog / validate context"
validation_job="$(awk '
  /^jobs:$/ { in_jobs = 1; next }
  in_jobs && /^[^ ]/ { exit }
  in_jobs && /^  changelog:$/ { capture = 1; print; next }
  capture && /^  [^ ]/ { exit }
  capture { print }
' "$validation_workflow")"
[ "$(grep -Ec '^    [^[:space:]#]' <<<"$validation_job")" = 2 ] \
  || fail "$validation_workflow changelog job may contain only the canonical uses and with fields; name, strategy, matrix, and other check-shaping fields are forbidden"
[ "$(grep -Ec '^    with:$' <<<"$validation_job")" = 1 ] \
  || fail "$validation_workflow changelog inputs must be nested under exactly one canonical with mapping"
[ "$(grep -Ec '^    uses: verJSON/\.github/\.github/workflows/generated-artifacts\.yml@[0-9a-f]{40}$' <<<"$validation_job")" = 1 ] \
  && grep -qE "^    uses: verJSON/\\.github/\\.github/workflows/generated-artifacts\\.yml@$CONTRACT_REF$" <<<"$validation_job" \
  || fail "$validation_workflow does not call generated-artifacts.yml at the shared pin"
[ "$(grep -Ec '^      changelog: true$' <<<"$validation_job")" = 1 ] \
  || fail "$validation_workflow does not enable changelog validation"
[ "$(grep -Ec '^      contract_ref: [0-9a-f]{40}$' <<<"$validation_job")" = 1 ] \
  && grep -qE "^      contract_ref: $CONTRACT_REF$" <<<"$validation_job" \
  || fail "$validation_workflow does not pass the pinned contract_ref"
expected_input_count=2
if grep -qE '^      adr-index: true$' <<<"$validation_job"; then
  expected_input_count=3
fi
[ "$(grep -Ec '^      [^[:space:]#][^:]*:' <<<"$validation_job")" = "$expected_input_count" ] \
  || fail "$validation_workflow changelog inputs must be exactly changelog and contract_ref, plus optional canonical adr-index"
validate_optional_adr_artifacts() {
  local adr_generator="$root/scripts/gen-adr-index.sh"
  local adr_generator_test="$root/scripts/gen-adr-index.test.sh"
  if [ -L "$adr_generator" ]; then
    fail "$adr_generator must be a regular file, not a symlink"
  elif [ -e "$adr_generator" ]; then
    [ -n "$ADR_INDEX_SHA256" ] \
      || fail "$adr_generator is present but there is no canonical generator at $CONTRACT_REF"
    [ -f "$adr_generator" ] && [ -r "$adr_generator" ] \
      || fail "$adr_generator is present but is not a readable regular file"
    [ "$(contract_digest_of "$adr_generator")" = "$ADR_INDEX_SHA256" ] \
      || fail "$adr_generator is not the generator pinned at $CONTRACT_REF. Regenerate the canonical ADR-index generator at $CONTRACT_REF in a clean temporary checkout, review it, and replace this file only after generation succeeds."
  fi
  if [ -e "$adr_generator_test" ] || [ -L "$adr_generator_test" ]; then
    [ ! -L "$adr_generator_test" ] \
      || fail "$adr_generator_test must be a regular file, not a symlink"
    [ -n "$ADR_INDEX_TEST_SHA256" ] \
      || fail "$adr_generator_test is present but there is no canonical test at $CONTRACT_REF"
    [ -f "$adr_generator_test" ] && [ -r "$adr_generator_test" ] \
      || fail "$adr_generator_test is present but is not a readable regular file"
    [ "$(contract_digest_of "$adr_generator_test")" = "$ADR_INDEX_TEST_SHA256" ] \
      || fail "$adr_generator_test is not the test pinned at $CONTRACT_REF. Replace it with the canonical ADR-index test generated at $CONTRACT_REF in a clean temporary checkout after reviewing it."
  fi
}

validate_adr_generator() {
  adr_generator="$root/scripts/gen-adr-index.sh"
  [ -n "$ADR_INDEX_SHA256" ] \
    || fail "adr-index: true has no canonical generator at $CONTRACT_REF"
  [ -x "$adr_generator" ] \
    || fail "adr-index: true requires the pinned scripts/gen-adr-index.sh. Generate the canonical ADR-index generator at $CONTRACT_REF in a clean temporary checkout, review it, then add it and mark it executable only after generation succeeds."
  # The generator ships with the suite that covers it. A hand-written local copy
  # is not equivalent: one asserted only rejections, so every fixture being
  # malformed kept it green for months (#1380). Executability is deliberately
  # not required — adopters invoke it as `bash scripts/gen-adr-index.test.sh`.
  adr_generator_test="$root/scripts/gen-adr-index.test.sh"
  [ -f "$adr_generator_test" ] \
    || fail "adr-index: true requires the pinned scripts/gen-adr-index.test.sh. Generate the canonical ADR-index test at $CONTRACT_REF in a clean temporary checkout, review it, then add it only after generation succeeds."
}
validate_optional_adr_artifacts
if grep -qE '^ +adr-index: true$' "$validation_workflow"; then
  validate_adr_generator
fi
changelog_caller_count=0
for candidate in "$root"/.github/workflows/*.yml "$root"/.github/workflows/*.yaml; do
  [ -f "$candidate" ] || continue
  capable_calls="$(grep -Ec "^    uses:[[:space:]]*['\"]?(verJSON/\.github/\.github/workflows/(generated-artifacts|changelog-validate)\.yml@|\./\.github/workflows/changelog-validate\.yml)" "$candidate" || true)"
  changelog_caller_count=$((changelog_caller_count + capable_calls))
done
[ "$changelog_caller_count" -eq 1 ] \
  || fail "workflow set contains $changelog_caller_count reusable callers capable of publishing changelog / validate; keep only .github/workflows/changelog.yml"
if [ -e "$renovate_attribution_workflow" ]; then
  [ "$(grep -Ec "^# Generated by verJSON/\.github scripts/gen-changelog-caller\.sh renovate-attribution $CONTRACT_REF$" "$renovate_attribution_workflow")" = 1 ] \
    || fail "$renovate_attribution_workflow is not the generated Renovate attribution caller at $CONTRACT_REF"
  [ "$(grep -Ec '^  pull_request_target:$' "$renovate_attribution_workflow")" = 1 ] \
    && grep -qE '^    types: \[opened, reopened, synchronize\]$' "$renovate_attribution_workflow" \
    || fail "$renovate_attribution_workflow is not limited to the reviewed pull_request_target events"
  ! grep -qE '^  (pull_request|push|workflow_dispatch|workflow_run|schedule):' "$renovate_attribution_workflow" \
    || fail "$renovate_attribution_workflow exposes an unreviewed trigger"
  [ "$(grep -Ec '^  renovate-changelog:[[:space:]]*$' "$renovate_attribution_workflow")" = 1 ] \
    || fail "$renovate_attribution_workflow must contain exactly one Renovate attribution job"
  renovate_attribution_job="$(awk '
    /^  renovate-changelog:[[:space:]]*$/ { capture = 1; next }
    capture && /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ { exit }
    capture { print }
  ' "$renovate_attribution_workflow")"
  renovate_admission="$(awk '
    /^    if: >-[[:space:]]*$/ { capture = 1 }
    capture && /^    uses:/ { exit }
    capture { print }
  ' <<<"$renovate_attribution_job")"
  expected_renovate_admission="$(cat <<'RENOVATE_ADMISSION'
    if: >-
      github.event.pull_request.head.repo.full_name == github.repository &&
      (github.event.pull_request.user.login == 'app/renovate' ||
       github.event.pull_request.user.login == 'renovate[bot]') &&
      startsWith(github.event.pull_request.head.ref, 'renovate/')
RENOVATE_ADMISSION
  )"
  [ "$(grep -Ec '^    if:' <<<"$renovate_attribution_job")" = 1 ] \
    && [ "$renovate_admission" = "$expected_renovate_admission" ] \
    || fail "$renovate_attribution_workflow does not preserve the exact same-repository Renovate admission gate"
  grep -qE '^    secrets: inherit$' "$renovate_attribution_workflow" \
    || fail "$renovate_attribution_workflow lacks inherited environment context"
  [ "$(grep -Ec '^ +uses: verJSON/\.github/\.github/workflows/renovate-changelog\.yml@[0-9a-f]{40}$' "$renovate_attribution_workflow")" = 1 ] \
    && grep -qE "^ +uses: verJSON/\\.github/\\.github/workflows/renovate-changelog\\.yml@$CONTRACT_REF$" "$renovate_attribution_workflow" \
    || fail "$renovate_attribution_workflow does not call the trusted attribution workflow at the shared pin"
  [ "$(grep -Ec '^ +contract_ref: [0-9a-f]{40}$' "$renovate_attribution_workflow")" = 1 ] \
    && grep -qE "^ +contract_ref: $CONTRACT_REF$" "$renovate_attribution_workflow" \
    || fail "$renovate_attribution_workflow does not pass the shared pinned contract_ref"
  grep -qE '^ +release_app_client_id: \$\{\{ vars\.RELEASE_APP_CLIENT_ID \}\}$' "$renovate_attribution_workflow" \
    && grep -qE '^ +release_environment: release-app$' "$renovate_attribution_workflow" \
    || fail "$renovate_attribution_workflow does not select the dedicated release App environment"
  grep -qE '^  contents: read$' "$renovate_attribution_workflow" \
    && grep -qE '^  pull-requests: read$' "$renovate_attribution_workflow" \
    && ! grep -qE 'contents: write|ORG_ADMIN_TOKEN|GITHUB_TOKEN|github\.token|^[[:space:]]+(steps|runs-on):' "$renovate_attribution_workflow" \
    || fail "$renovate_attribution_workflow is not a thin read-only caller"
fi
if [ -e "$release_propose_workflow" ]; then
  [ -f "$root/.github/workflows/release.yml" ] \
    || fail "$release_propose_workflow requires the generated .github/workflows/release.yml dispatch target"
  provenance="$(grep -E "^# Generated by verJSON/\.github scripts/gen-changelog-caller\.sh release-propose $CONTRACT_REF --autonomy (propose|dispatch)$" "$release_propose_workflow" || true)"
  [ "$(printf '%s\n' "$provenance" | grep -c .)" -eq 1 ] \
    || fail "$release_propose_workflow is not the generated release-propose caller at $CONTRACT_REF"
  autonomy="${provenance##*--autonomy }"
  if [ "$autonomy" = propose ]; then
    reusable_workflow="release-propose.yml"
  else
    reusable_workflow="release-dispatch.yml"
  fi
  [ "$(grep -Ec '^ +uses: verJSON/\.github/\.github/workflows/release-(propose|dispatch)\.yml@[0-9a-f]{40}$' "$release_propose_workflow")" -eq 1 ] \
    && grep -qE "^ +uses: verJSON/\.github/\.github/workflows/$reusable_workflow@$CONTRACT_REF$" "$release_propose_workflow" \
    || fail "$release_propose_workflow does not call $reusable_workflow at the shared pin"
  [ "$(grep -Ec '^ +contract_ref: [0-9a-f]{40}$' "$release_propose_workflow")" -eq 1 ] \
    && grep -qE "^ +contract_ref: $CONTRACT_REF$" "$release_propose_workflow" \
    || fail "$release_propose_workflow does not pass the shared pinned contract_ref"
  ! grep -qE '^ +autonomy:' "$release_propose_workflow" \
    || fail "$release_propose_workflow must select autonomy through its reusable workflow path"
  grep -qE '^  schedule:$' "$release_propose_workflow" \
    && grep -qE '^  workflow_dispatch:$' "$release_propose_workflow" \
    && ! grep -qE '^  (push|pull_request|pull_request_target):' "$release_propose_workflow" \
    || fail "$release_propose_workflow must be schedule/operator triggered, never release-on-merge"
  proposer_job="$(awk '
    /^  release-propose:[[:space:]]*$/ { capture = 1 }
    capture && /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ && $0 !~ /^  release-propose:/ { exit }
    capture { print }
  ' "$release_propose_workflow")"
  grep -qE '^ +contents: read$' <<<"$proposer_job" \
    || fail "$release_propose_workflow proposer job lacks contents-read"
  if [ "$autonomy" = propose ]; then
    grep -qE '^ +issues: write$' <<<"$proposer_job" \
      && ! grep -qE '^ +actions: write$' <<<"$proposer_job" \
      || fail "$release_propose_workflow propose mode must grant issues-write and not actions-write"
  else
    grep -qE '^ +actions: write$' <<<"$proposer_job" \
      && ! grep -qE '^ +issues: write$' <<<"$proposer_job" \
      || fail "$release_propose_workflow dispatch mode must grant actions-write and not issues-write"
  fi
fi
grep -q "CONTRACT_REF=\"$CONTRACT_REF\"" "$renderer" \
  || fail "$renderer does not pin the same contract commit"
cat >"$work/release-shape.py" <<'RELEASE_SHAPE_PY'
"""Structural checks on a release caller, on a bare python3.

Only two properties live here, both of which a line-oriented grep gets wrong in
ways that report green:

  * the trigger set must be EXACTLY {workflow_dispatch}. A blocklist accepts
    every trigger nobody listed, and an anchor on a bare `on:` line never sees
    the flow spelling `on: {workflow_dispatch: {...}, push: {...}}`.
  * a GITHUB_TOKEN bound to NODE_AUTH_TOKEN is legitimate only inside the step
    that runs `npm publish`. Checking the install step alone misses the same
    credential inherited from a job-level or workflow-level `env:`.

Anything this parser cannot read confidently is an error, never a pass.
"""
import re
import shlex
import sys

path = sys.argv[1]
problems = []

with open(path, encoding="utf-8") as handle:
    raw_lines = handle.read().splitlines()


def strip_comment(line):
    """Drop a trailing comment without touching a `#` inside a quoted scalar."""
    out = []
    quote = None
    for index, char in enumerate(line):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            continue
        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            break
        out.append(char)
    return "".join(out).rstrip()


lines = [strip_comment(line) for line in raw_lines]


def mapping_entry(text):
    """Return a strict scalar mapping key/value; reject YAML ambiguity."""
    quote = None
    colon = None
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char == ":":
            colon = index
            break
        elif char in "{}[]&*!":
            raise ValueError("flow collections, tags, and aliases are unsupported")
    if quote or colon is None:
        raise ValueError("malformed mapping entry")
    raw_key = text[:colon].strip()
    if not raw_key or raw_key == "<<":
        raise ValueError("empty or merged mapping key")
    quoted = len(raw_key) >= 2 and raw_key[0] == raw_key[-1] and raw_key[0] in "'\""
    if quoted:
        key = raw_key[1:-1]
        if raw_key[0] in key or "\\" in key:
            raise ValueError("escaped mapping keys are unsupported")
    else:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", raw_key):
            raise ValueError("non-plain mapping key")
        key = raw_key
    return key, quoted, text[colon + 1:].strip()


def trigger_identity(key, quoted):
    if quoted:
        return "on" if key == "on" else None
    return "on" if key.casefold() in {"y", "yes", "true", "on"} else None


def release_defaults():
    provenance_prefix = (
        "# Generated by verJSON/.github scripts/gen-changelog-caller.sh "
    )
    provenance = [line for line in raw_lines if line.startswith(provenance_prefix)]
    if len(provenance) != 1:
        problems.append("requires exactly one canonical generator provenance line")
        return "v", ""
    try:
        tokens = shlex.split(provenance[0][len(provenance_prefix):])
    except ValueError as error:
        problems.append(f"has malformed generator provenance: {error}")
        return "v", ""

    def values(name):
        found = []
        for index, token in enumerate(tokens):
            if token != name:
                continue
            if index + 1 >= len(tokens):
                problems.append(f"has {name} without a value in generator provenance")
                continue
            found.append(tokens[index + 1])
        return found

    prefixes = values("--default-prefix")
    components = values("--default-component")
    if not prefixes and not components:
        return "v", ""
    if len(prefixes) != 1 or len(components) != 1:
        problems.append(
            "must declare --default-prefix and --default-component exactly once together"
        )
        return "v", ""
    prefix = prefixes[0]
    component = components[0]
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]*-v", prefix) is None:
        problems.append("has an invalid component release default prefix")
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?", component) is None:
        problems.append("has an invalid component release default component")
    return prefix, component


release_default_prefix, release_default_component = release_defaults()
release_default_prefix_yaml = (
    "v" if release_default_prefix == "v" else f"'{release_default_prefix}'"
)
release_default_component_yaml = (
    "''" if not release_default_component else f"'{release_default_component}'"
)


EXPECTED_TRIGGER_BLOCK = (
    (2, "workflow_dispatch:"),
    (4, "inputs:"),
    (6, "version:"),
    (8, "description: Optional exact SemVer tag; blank derives the next version from selected fragments"),
    (8, "required: false"),
    (8, "type: string"),
    (8, "default: ''"),
    (6, "prefix:"),
    (8, "description: Exact version namespace prefix; independent from component"),
    (8, "required: false"),
    (8, "type: string"),
    (8, f"default: {release_default_prefix_yaml}"),
    (6, "expected_head:"),
    (8, "description: Optional exact default-branch head derived by release-propose"),
    (8, "required: false"),
    (8, "type: string"),
    (8, "default: ''"),
    (6, "selector_digest:"),
    (8, "description: Optional canonical selection digest derived by release-propose"),
    (8, "required: false"),
    (8, "type: string"),
    (8, "default: ''"),
    (6, "fragments:"),
    (8, "description: Newline-separated NEXT fragment filenames; empty selects the requested component stream"),
    (8, "required: false"),
    (8, "type: string"),
    (8, "default: ''"),
    (6, "component:"),
    (8, "description: Optional component stream; empty selects only unscoped fragments"),
    (8, "required: false"),
    (8, "type: string"),
    (8, f"default: {release_default_component_yaml}"),
)


def trigger_names():
    top_keys = set()
    trigger_entries = []
    try:
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            if "\t" in line[: len(line) - len(line.lstrip())]:
                raise ValueError("tab indentation")
            if line[:1].isspace():
                continue
            if line.startswith(("---", "...", "%", "- ")):
                raise ValueError("unsupported top-level YAML form")
            key, quoted, value = mapping_entry(line)
            identity = trigger_identity(key, quoted) or key
            if identity in top_keys:
                raise ValueError("duplicate YAML-equivalent top-level key")
            top_keys.add(identity)
            if trigger_identity(key, quoted):
                trigger_entries.append((index, value))
        if len(trigger_entries) != 1:
            raise ValueError("missing or duplicate YAML-equivalent trigger key")
        index, inline = trigger_entries[0]
        if inline:
            raise ValueError("trigger mapping must use canonical block form")
        block = []
        for following in raw_lines[index + 1:]:
            if not following.strip():
                continue
            indent = len(following) - len(following.lstrip())
            if indent == 0:
                break
            block.append((indent, following.strip()))
        if tuple(block) != EXPECTED_TRIGGER_BLOCK:
            raise ValueError("workflow_dispatch input schema differs from the generated contract")
        return ["workflow_dispatch"]
    except ValueError as error:
        problems.append("has an ambiguous or malformed top-level trigger mapping: %s" % error)
        return None


triggers = trigger_names()
if triggers is None:
    problems.append(
        "declares no readable top-level `on:` trigger. A release states the "
        "version it cuts, so it must be a workflow_dispatch and nothing else "
        "(ADR 0038, ADR 0060)"
    )
elif set(triggers) != {"workflow_dispatch"}:
    problems.append(
        "is triggered by %s. A release is dispatched with the version it cuts, "
        "never derived from repository activity, and never exposed as a "
        "reusable workflow another caller can fire (ADR 0038, ADR 0060)"
        % ", ".join(sorted(set(triggers)) or ["nothing"])
    )

raw = "\n".join(raw_lines)
header, trigger_separator, _ = raw.partition("\non:")
header_warning = """# The verification suite runs after package.json has been stamped to the
# dispatched version. Its expected version must be read dynamically from
# package.json; never assert a hardcoded version literal. This order is
# intentional: the suite verifies the exact package metadata that will ship."""
if not trigger_separator or header_warning not in header:
    problems.append(
        "does not carry the stamped-version warning inside the generated header "
        "before `on:` (#862)"
    )

GITHUB_TOKEN = re.compile(
    r"\$\{\{\s*(secrets\.GITHUB_TOKEN|github\.token)\s*\}\}", re.IGNORECASE
)
PRIVATE_NODE_TOKEN = re.compile(
    r"\$\{\{\s*secrets\.NODE_AUTH_TOKEN\s*\}\}", re.IGNORECASE
)
LIST_ITEM = re.compile(r"^(\s*)-\s")


def enclosing_step(index):
    """The list-item block containing `index`, or None if it is not in one."""
    cursor = index
    while cursor >= 0:
        match = LIST_ITEM.match(lines[cursor])
        if match:
            indent = len(match.group(1))
            end = cursor + 1
            while end < len(lines):
                current = lines[end]
                if current.strip() and len(current) - len(current.lstrip()) <= indent:
                    break
                end += 1
            if cursor <= index < end:
                return lines[cursor:end]
            return None
        cursor -= 1
    return None


for index, line in enumerate(lines):
    if "NODE_AUTH_TOKEN" not in line or not GITHUB_TOKEN.search(line):
        continue
    step = enclosing_step(index)
    if step is None or not any("npm publish" in entry for entry in step):
        problems.append(
            "binds NODE_AUTH_TOKEN to GITHUB_TOKEN at line %d, outside the "
            "`npm publish` step. A repository-scoped GITHUB_TOKEN cannot read a "
            "private @verjson package owned by another repository, so the "
            "install 401s after the tag has already been pushed. Install with "
            "NODE_AUTH_TOKEN and keep GITHUB_TOKEN for npm publish (#465)"
            % (index + 1)
        )

verification_steps = [
    index for index, line in enumerate(lines)
    if line.strip() == "- name: Run the release verification suite"
]
if len(verification_steps) != 1:
    problems.append(
        "must contain exactly one named release verification suite step (#569)"
    )
else:
    verification_step = enclosing_step(verification_steps[0])
    if verification_step is None or not any(
        "NODE_AUTH_TOKEN" in entry and PRIVATE_NODE_TOKEN.search(entry)
        for entry in verification_step
    ):
        problems.append(
            "does not expose secrets.NODE_AUTH_TOKEN to the release verification "
            "hook/default suite step (#569)"
        )
    if verification_step is None or not any(
        "PACKAGE_VERSION" in entry
        and "steps.release-version.outputs.package-version" in entry
        for entry in verification_step
    ):
        problems.append(
            "cannot diagnose the stamped dispatch version when verification fails (#862)"
        )
for index, line in enumerate(lines):
    if "NODE_AUTH_TOKEN" not in line or not PRIVATE_NODE_TOKEN.search(line):
        continue
    step = enclosing_step(index)
    if step is None:
        indent = len(line) - len(line.lstrip())
        parent = next(
            (
                entry.strip()
                for entry in reversed(lines[:index])
                if entry.strip()
                and len(entry) - len(entry.lstrip()) < indent
            ),
            "",
        )
        if parent == "secrets:":
            continue
        problems.append(
            "exposes secrets.NODE_AUTH_TOKEN outside a step-scoped environment (#569)"
        )
        continue
    is_install = any(re.search(r"\bnpm ci\b", entry) for entry in step)
    is_verification = any(
        entry.strip() == "- name: Run the release verification suite"
        for entry in step
    )
    if not (is_install or is_verification):
        problems.append(
            "exposes secrets.NODE_AUTH_TOKEN to an unrelated release step (#569)"
        )
for problem in problems:
    sys.stderr.write("FAIL - %s %s\n" % (path, problem))
sys.exit(1 if problems else 0)
RELEASE_SHAPE_PY

# Every workflow that calls changelog-release.yml is a release caller, whatever
# it happens to be named. Keying these checks on one filename let a caller named
# anything else — publish.yml, release-package.yml, a second caller kept beside
# the first — collect zero checks and report green, which is the failure mode
# this whole file exists to remove.
release_workflows=""
for candidate in "$root"/.github/workflows/*.yml "$root"/.github/workflows/*.yaml; do
  [ -f "$candidate" ] || continue
  if grep -q 'changelog-release\.yml@' "$candidate"; then
    release_workflows="$release_workflows$candidate
"
  fi
done

while IFS= read -r release_workflow; do
  [ -n "$release_workflow" ] || continue
  grep -q "changelog-release.yml@$CONTRACT_REF" "$release_workflow" \
    || fail "$release_workflow does not call the release workflow at the pin"
  # The release caller was the last adopter file still hand-copied from a
  # sibling, so one ordering bug propagated to every migrated repository at once
  # (#463, #464, #465). Provenance is asserted first because it is the only check
  # that also catches the defects nobody has named yet. A caller is conformant
  # only if it is the BYTE-IDENTICAL output of one of the generator's own release
  # modes — the mode name in its own provenance comment selects which shape the
  # rest of this loop enforces; nothing here infers the mode from file content.
  release_mode=""
  if grep -q "gen-changelog-caller.sh release-node $CONTRACT_REF" "$release_workflow"; then
    release_mode=release-node
  elif grep -q "gen-changelog-caller.sh release-artifact $CONTRACT_REF" "$release_workflow"; then
    release_mode=release-artifact
  elif grep -q "gen-changelog-caller.sh release-snapshot $CONTRACT_REF" "$release_workflow"; then
    release_mode=release-snapshot
  else
    fail "$release_workflow is not a generated release caller at $CONTRACT_REF. Inspect the existing release workflow and repository configuration to determine its mode and all custom generator options. Regenerate the complete caller set at $CONTRACT_REF in a clean temporary checkout, review the full diff, then replace the committed set together. Supported release modes are release-node, release-artifact for GitHub Release assets, and release-snapshot when the release workflow publishes nothing."
  fi
  workflow_package_dirs_json=""
  workflow_package_dirs_shell=""
  if [ "$release_mode" = release-node ]; then
    workflow_package_dirs_json="$(sed -n -E "s/^[[:space:]]+package-dirs: '([^']+)'$/\1/p" "$release_workflow" | head -n 1)"
    [ -n "$workflow_package_dirs_json" ] \
      || fail "$release_workflow does not declare package-dirs in its node release caller"
    workflow_package_dirs_shell="$(python3 - "$workflow_package_dirs_json" <<'PY'
import json
import shlex
import sys

directories = json.loads(sys.argv[1])
if (
    not isinstance(directories, list)
    or not directories
    or any(not isinstance(directory, str) or not directory for directory in directories)
):
    raise SystemExit("package-dirs must be a non-empty JSON array of non-empty strings")
print(" ".join(shlex.quote(directory) for directory in directories))
PY
    )" \
      || fail "$release_workflow has invalid package-dirs JSON"
  else
    workflow_package_dirs_shell="$(sed -n -E 's/^[[:space:]]+package_dirs=\((.*)\)$/\1/p' "$release_workflow" | head -n 1)"
    [ -n "$workflow_package_dirs_shell" ] \
      || fail "$release_workflow does not declare package_dirs for version stamping"
  fi
  grep -qF "run-name: Release \${{ inputs.version || 'auto' }} \${{ inputs.selector_digest || 'manual' }}" "$release_workflow" \
    || fail "$release_workflow lacks the resolved-version run title required for idempotent dispatch"

  # Comments stripped before structural matching so a migration note naming a
  # retired token does not read as live credential wiring.
  sed 's/#.*//' "$release_workflow" >"$work/release-stripped.yml"

  # Renovate's GitHub Actions manager does not implement `# renovate: ignore`
  # for setup-node's uses-with fields. A literal expression remains the same
  # runtime string but is intentionally dynamic to Renovate (#700). release-node
  # stamps this in both its verify and publish jobs (node-release.yml receives
  # it too); release-artifact and release-snapshot have no publish-side Node
  # job, so it appears only once, in verify's setup-node.
  release_node_occurrences=2
  { [ "$release_mode" = release-artifact ] || [ "$release_mode" = release-snapshot ]; } \
    && release_node_occurrences=1
  printf -v expected_node_version "node-version: \${{ '%s' }}" "$EXPECTED_RELEASE_NODE_VERSION"
  [ "$(awk -v expected="$expected_node_version" '
      { line = $0; sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+$/, "", line) }
      line == expected { count++ }
      END { print count + 0 }
    ' "$work/release-stripped.yml")" -eq "$release_node_occurrences" ] \
    || fail "$release_workflow does not use Node $EXPECTED_RELEASE_NODE_VERSION consistently; regenerate $release_mode and contract-test with the same --node-version (#520)"
  [ "$(awk -v expected="scope: '$EXPECTED_RELEASE_SCOPE'" '
      { line = $0; sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+$/, "", line) }
      line == expected { count++ }
      END { print count + 0 }
    ' "$work/release-stripped.yml")" -eq "$release_node_occurrences" ] \
    || fail "$release_workflow does not use npm scope $EXPECTED_RELEASE_SCOPE consistently; regenerate $release_mode and contract-test with the same --scope (#520)"

  # The job that calls changelog-release.yml, isolated as a block rather than
  # grepped for. `needs:` and `runner:` are ordinary keys that also appear under
  # other jobs, so a guard matching them anywhere in the file passes on exactly
  # the shape it exists to reject.
  snapshot_job="$(awk '
    /^jobs:[[:space:]]*$/ { in_jobs = 1; next }
    !in_jobs { next }
    /^[^[:space:]]/ { in_jobs = 0; next }
    /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ {
      if (block ~ /changelog-release\.yml@/) { printf "%s", block; done = 1; exit }
      block = ""
    }
    { block = block $0 "\n" }
    END { if (!done && block ~ /changelog-release\.yml@/) printf "%s", block }
  ' "$release_workflow")"

  grep -qE '^[[:space:]]+release_app_client_id:[[:space:]]*\$\{\{[[:space:]]*vars\.RELEASE_APP_CLIENT_ID[[:space:]]*\}\}[[:space:]]*$' \
    <<<"$snapshot_job" \
    || fail "$release_workflow does not pass the organization RELEASE_APP_CLIENT_ID variable to the release workflow"
  grep -qE '^[[:space:]]+release_environment:[[:space:]]+release-app[[:space:]]*$' \
    <<<"$snapshot_job" \
    || fail "$release_workflow does not select the release-app environment"
  grep -qE '^    secrets: inherit$' <<<"$snapshot_job" \
    || fail "$release_workflow snapshot lacks inherited environment context"
  ! sed 's/#.*//' <<<"$snapshot_job" \
    | grep -qE 'PRIVATE_KEY|ORG_ADMIN_TOKEN|push_token|github\.token' \
    || fail "$release_workflow snapshot forwards credentials instead of selecting its environment"
  snapshot_permissions="$(awk '
    /^    permissions:[[:space:]]*$/ { in_permissions = 1; next }
    in_permissions && /^    [^[:space:]]/ { exit }
    in_permissions { print }
  ' <<<"$snapshot_job")"
  snapshot_permissions_effective="$(sed 's/#.*//' <<<"$snapshot_permissions" | sed '/^[[:space:]]*$/d')"
  [ "$(grep -c . <<<"$snapshot_permissions_effective")" -eq 2 ] \
    && grep -qE '^[[:space:]]+contents:[[:space:]]+read[[:space:]]*$' <<<"$snapshot_permissions_effective" \
    && grep -qE '^[[:space:]]+actions:[[:space:]]+read[[:space:]]*$' <<<"$snapshot_permissions_effective" \
    || fail "$release_workflow snapshot must grant only contents-read and environment-policy actions-read"

  # The reusable workflow and its engine are one contract. Checking only the
  # uses: ref lets a caller execute workflow A with contract_ref B (#349).
  grep -qE "^[[:space:]]+contract_ref:[[:space:]]*${CONTRACT_REF}[[:space:]]*$" \
    <<<"$snapshot_job" \
    || fail "$release_workflow passes a contract_ref that differs from its changelog-release.yml pin"
  grep -qF 'prefix: ${{ inputs.prefix }}' <<<"$snapshot_job" \
    || fail "$release_workflow does not pass the selected release namespace to changelog-release.yml"
  grep -qF 'selection_digest: ${{ needs.verify.outputs.selection-digest }}' <<<"$snapshot_job" \
    || fail "$release_workflow does not pass the resolved selection digest to changelog-release.yml"

  # #463/#464. changelog-release.yml consumes NEXT/, writes an immutable
  # CHANGELOG/<version>.md, commits, tags and pushes to the default branch in one
  # atomic push. Nothing after that is recoverable by re-dispatch: the same
  # version is refused because the tag exists, a higher one because NEXT/ was
  # already consumed. So the snapshot may never be the first job to run.
  grep -qE '^[[:space:]]+needs:[[:space:]]*[^[:space:]]' <<<"$snapshot_job" \
    || fail "$release_workflow runs the irreversible snapshot with no needs:, so a red tree is discovered only after the tag has been pushed (#463, #464)"

  # #465. Omitting the optional runner input lets changelog-release.yml route the
  # snapshot by its own default while the caller's jobs route by another. On a
  # private repository the snapshot half — the half that mutates protected main —
  # then queues on hosted runners with no check run and no error.
  grep -qE '^[[:space:]]+runner:[[:space:]]*[^[:space:]]' <<<"$snapshot_job" \
    || fail "$release_workflow passes no explicit runner:, so the snapshot and the publish half can land on different runner pools (#465)"

  # #519. Verification must see the version selected by workflow_dispatch. The
  # stamp is deliberately uncommitted: verify applies it to the dispatch tree,
  # while the immutable node-release reusable workflow reapplies it to the tag
  # before its build. The reusable workflow owns publication and restart safety;
  # duplicating either concern into every generated caller recreates #455.
  verify_job="$(awk '
    /^  verify:[[:space:]]*$/ { in_job = 1 }
    in_job && /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ && $0 !~ /^  verify:/ { exit }
    in_job { print }
  ' "$release_workflow")"
  publish_job="$(awk '
    /^  publish:[[:space:]]*$/ { in_job = 1 }
    in_job && /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ && $0 !~ /^  publish:/ { exit }
    in_job { print }
  ' "$release_workflow")"
  build_job="$(awk '
    /^  build:[[:space:]]*$/ { in_job = 1 }
    in_job && /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ && $0 !~ /^  build:/ { exit }
    in_job { print }
  ' "$release_workflow")"
  acquisition_job="$(awk '
    /^  acquire-private-dependencies:[[:space:]]*$/ { in_job = 1 }
    in_job && /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ && $0 !~ /^  acquire-private-dependencies:/ { exit }
    in_job { print }
  ' "$release_workflow")"
  stamp_before() {
    local job="$1" consumer_pattern="$2" stamp_line consumer_line
    stamp_line="$(grep -n -m1 \
      'npm version .*--no-git-tag-version --ignore-scripts --allow-same-version' \
      <<<"$job" | cut -d: -f1)"
    consumer_line="$(grep -n -m1 -E "$consumer_pattern" <<<"$job" | cut -d: -f1)"
    [ -n "$stamp_line" ] && [ -n "$consumer_line" ] && [ "$stamp_line" -lt "$consumer_line" ]
  }
  stamp_before "$verify_job" 'scripts/release-verify\.sh|npm run build|npm run typecheck|npm run lint|npm test' \
    || fail "$release_workflow does not stamp the dispatched package version before the verification build or suite (#519)"
  grep -qF "package_dirs=($workflow_package_dirs_shell)" <<<"$verify_job" \
    || fail "$release_workflow does not stamp every package directory selected for publication (#557)"
  prepare_line="$(grep -n -m1 'scripts/release-prepare-packages\.sh "\$PACKAGE_VERSION"' \
    <<<"$verify_job" | cut -d: -f1)"
  stamp_line="$(grep -n -m1 \
    'npm version .*--no-git-tag-version --ignore-scripts --allow-same-version' \
    <<<"$verify_job" | cut -d: -f1)"
  [ -n "$prepare_line" ] && [ -n "$stamp_line" ] && [ "$prepare_line" -lt "$stamp_line" ] \
    || fail "$release_workflow does not prepare package metadata before stamping and verifying the release tree (#550)"
  if [ "$release_mode" = release-node ]; then
    grep -qF "uses: verJSON/.github/.github/workflows/node-release.yml@$CONTRACT_REF" \
      <<<"$publish_job" \
      || fail "$release_workflow does not delegate publication to node-release.yml at the immutable contract pin (#455)"
    grep -qF 'needs: [verify, snapshot]' <<<"$publish_job" \
      || fail "$release_workflow does not gate publication on both verification and snapshot state"
    grep -qF "if: always() && needs.verify.result == 'success' && needs.verify.outputs.selected == 'true' && (needs.snapshot.result == 'success' || needs.snapshot.result == 'skipped')" \
      <<<"$publish_job" \
      || fail "$release_workflow cannot safely resume publication after reusing an immutable snapshot"
    # node-release.yml cannot refuse an unpublishable package in time: it only
    # ever runs as `publish`, after `snapshot` has already pushed the immutable
    # tag. `verify` is the last stage that still precedes that push (#1206).
    private_guard_step="$(awk '
      /^      - name: Refuse a package this release can never publish$/ { found = 1; next }
      found && /^      - / { exit }
      found { print }
    ' <<<"$verify_job")"
    grep -qF 'private === true' <<<"$private_guard_step" \
      && grep -qF 'exit 1' <<<"$private_guard_step" \
      || fail "$release_workflow does not refuse a private, unpublishable package before the snapshot is tagged (#1206)"
  elif [ "$release_mode" = release-snapshot ]; then
    # release-snapshot: the adopter publishes NOTHING from the release workflow
    # (#1206). Its whole reason to exist is reaching changelog-release.yml, so
    # verify -> snapshot is asserted exactly as for the other two modes above and
    # publish is reduced to creating the tag's GitHub Release from the immutable
    # snapshot. The assertions below are therefore mostly NEGATIVE: any build
    # matrix, private-dependency acquisition, artifact attachment or reusable
    # publication delegation appearing here is a hand edit, not this mode.
    [ -z "$build_job" ] \
      || fail "$release_workflow has a build job; release-snapshot publishes nothing and must be regenerated as release-artifact if it now ships assets (#1206)"
    [ -z "$acquisition_job" ] \
      || fail "$release_workflow adds private dependency acquisition absent from the generated contract"
    ! grep -qF 'uses: verJSON/.github/.github/workflows/node-release.yml' <<<"$publish_job" \
      || fail "$release_workflow delegates publication to node-release.yml; regenerate it as release-node instead of hand-editing a snapshot-only caller (#1206)"
    grep -qF 'needs: [verify, snapshot]' <<<"$publish_job" \
      || fail "$release_workflow does not gate publication on both verification and snapshot state"
    grep -qF "if: always() && needs.verify.result == 'success' && needs.verify.outputs.selected == 'true' && (needs.snapshot.result == 'success' || needs.snapshot.result == 'skipped')" \
      <<<"$publish_job" \
      || fail "$release_workflow cannot safely resume publication after reusing an immutable snapshot"
    grep -qF 'ref: ${{ needs.verify.outputs.version }}' <<<"$publish_job" \
      || fail "$release_workflow publishes from a ref other than the tagged snapshot"
    grep -qF 'test -f "CHANGELOG/$VERSION.md"' <<<"$publish_job" \
      || fail "$release_workflow publish job does not verify the immutable release note before publishing"
    grep -qF '# RESTART_SAFE_GH_RELEASE_BEGIN' <<<"$publish_job" \
      && grep -qF '# RESTART_SAFE_GH_RELEASE_END' <<<"$publish_job" \
      || fail "$release_workflow publish job does not use the restart-safe GitHub Release publication shape (#862)"
    ! grep -qE 'gh release upload|actions/(download|upload)-artifact' <<<"$publish_job" \
      || fail "$release_workflow publish job attaches release artifacts; release-snapshot attaches none, so regenerate it as release-artifact (#1206)"
    # The one job holding contents: write in this mode. It runs no adopter-owned
    # hook, so nothing here needs a secret beyond the job's own GITHUB_TOKEN.
    publish_permissions="$(awk '
      /^    permissions:[[:space:]]*$/ { in_permissions = 1; next }
      in_permissions && /^    [^[:space:]]/ { exit }
      in_permissions { print }
    ' <<<"$publish_job")"
    publish_permissions_effective="$(sed 's/#.*//' <<<"$publish_permissions" | sed '/^[[:space:]]*$/d')"
    [ "$(grep -c . <<<"$publish_permissions_effective")" -eq 1 ] \
      && grep -qE '^[[:space:]]+contents:[[:space:]]+write[[:space:]]*$' <<<"$publish_permissions_effective" \
      || fail "$release_workflow publish job grants more than contents-write; a snapshot-only release publishes nothing else (#1206)"
    [ "$(sed 's/#.*//' <<<"$publish_job" | grep -Ec 'secrets\b')" -eq 1 ] \
      && grep -qF 'GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}' <<<"$publish_job" \
      || fail "$release_workflow publish job references a secret other than the job's own GITHUB_TOKEN; a snapshot-only release needs no publication credential (#1206)"
  else
    # release-artifact: publication is not a reusable-workflow delegation —
    # there is no artifact-release.yml counterpart to node-release.yml — so the
    # caller must instead run a caller-declared build matrix and inline the
    # restart-safe GitHub Release logic itself (#975).
    [ -n "$build_job" ] \
      || fail "$release_workflow has no build job; release-artifact requires a caller-declared build matrix (#975)"
    if [ -n "$EXPECTED_RELEASE_APPROVED_INTERNAL_PACKAGES" ]; then
      [ -n "$acquisition_job" ] \
        || fail "$release_workflow omits the independently authorized private dependency acquisition"
      [ "$(grep -cF "APPROVED_INTERNAL_PACKAGES: '$EXPECTED_RELEASE_APPROVED_INTERNAL_PACKAGES'" <<<"$acquisition_job")" -eq 1 ] \
        || fail "$release_workflow private package allowlist differs from the generated contract"
      python3 - "$root/package-lock.json" "$EXPECTED_RELEASE_APPROVED_INTERNAL_PACKAGES" <<'PY' \
        || fail "$release_workflow repository lock differs from the generated private package authorization"
import base64
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

lock = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
approved = set(filter(None, sys.argv[2].split(",")))
if lock.get("lockfileVersion") not in (2, 3) or not isinstance(lock.get("packages"), dict):
    raise SystemExit(1)
found = set()
for path, entry in lock["packages"].items():
    if not path or "node_modules/" not in path:
        continue
    name = path.rsplit("node_modules/", 1)[1]
    if not name.startswith("@verjson/"):
        continue
    if name not in approved or (entry.get("name") not in (None, name)):
        raise SystemExit(1)
    resolved = entry.get("resolved", "")
    parsed = urlparse(resolved)
    resolved_path = unquote(parsed.path)
    parts = resolved_path.split("/")
    integrity = entry.get("integrity")
    if (parsed.scheme != "https" or parsed.netloc != "npm.pkg.github.com"
            or parsed.query or parsed.fragment or resolved != f"https://npm.pkg.github.com{resolved_path}"
            or "\\" in resolved_path or len(parts) != 6 or parts[1] != "download"
            or f"{parts[2]}/{parts[3]}" != name or not parts[4] or not parts[5]
            or not isinstance(integrity, str)
            or re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", integrity) is None):
        raise SystemExit(1)
    try:
        digest = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
    except ValueError:
        raise SystemExit(1)
    if len(digest) != 64:
        raise SystemExit(1)
    found.add(name)
if found != approved:
    raise SystemExit(1)
PY
    else
      [ -z "$acquisition_job" ] \
        || fail "$release_workflow adds private dependency acquisition absent from the generated contract"
    fi
    grep -qE '^[[:space:]]+strategy:[[:space:]]*$' <<<"$build_job" \
      && grep -qE '^[[:space:]]+include:[[:space:]]*$' <<<"$build_job" \
      && grep -qE '^[[:space:]]+- build-runner:[[:space:]]*[^[:space:]].*$' <<<"$build_job" \
      || fail "$release_workflow build job has no non-empty build-runner matrix"
    while IFS= read -r runner_selector; do
      runner_selector="${runner_selector#*: }"
      [[ "$runner_selector" =~ ^\'[A-Za-z0-9][A-Za-z0-9._-]*\'$ ]] \
        && [[ ! "${runner_selector:1:${#runner_selector}-2}" =~ ^(vars|inputs|matrix|needs|github|env|secrets)\. ]] \
        || [[ "$runner_selector" =~ ^\$\{\{[[:space:]]fromJSON\(vars\.CI_LANE_TRUSTED_(MACOS|WINDOWS)\)[[:space:]]\}\}$ ]] \
        || fail "$release_workflow build matrix contains an unreviewed runner selector: $runner_selector (ADR 0103)"
      if [[ "$runner_selector" =~ ^\'.*\'$ ]] \
        && [[ "${runner_selector:1:${#runner_selector}-2}" =~ ^(macos|windows)- ]]; then
        fail "$release_workflow uses a literal metered OS selector forbidden by ADR 0103: $runner_selector"
      fi
      if [[ "$runner_selector" =~ vars\.(CI_LANE_TRUSTED_(MACOS|WINDOWS)) ]]; then
        lane_name="${BASH_REMATCH[1]}"
        grep -qF "$lane_name: \${{ vars.$lane_name }}" <<<"$verify_job" \
          && grep -qF 'Validate required OS-scoped build lanes' <<<"$verify_job" \
          && grep -qF 'must be a non-empty JSON runner-label array' <<<"$verify_job" \
          || fail "$release_workflow does not fail loudly before snapshot when $lane_name is unset or malformed"
      fi
    done < <(grep -E '^[[:space:]]+- build-runner:' <<<"$build_job")
    if [ -n "$EXPECTED_RELEASE_LANE_PREFLIGHT_SHA256" ]; then
      lane_preflight="$(awk '
        /^      - name: Validate required OS-scoped build lanes$/ { found = 1 }
        found && /^      - name:/ && !/Validate required OS-scoped build lanes$/ { exit }
        found { print }
      ' <<<"$verify_job")"
      if command -v sha256sum >/dev/null 2>&1; then
        lane_preflight_sha256="$(printf '%s' "$lane_preflight" | sha256sum | cut -d' ' -f1)"
      elif command -v shasum >/dev/null 2>&1; then
        lane_preflight_sha256="$(printf '%s' "$lane_preflight" | shasum -a 256 | cut -d' ' -f1)"
      else
        fail "cannot verify the provenance-authorized OS lane preflight without a SHA-256 tool"
      fi
      [ "$lane_preflight_sha256" = "$EXPECTED_RELEASE_LANE_PREFLIGHT_SHA256" ] \
        || fail "$release_workflow OS lane preflight logic differs from the provenance-authorized contract"
    fi
    if [ -n "$acquisition_job" ]; then
      grep -qF 'timeout-minutes: 45' <<<"$acquisition_job" \
        || fail "$release_workflow acquisition matrix exceeds ADR 0103's 45-minute bound"
      grep -qF 'needs: [verify, snapshot, acquire-private-dependencies]' <<<"$build_job" \
        || fail "$release_workflow private build matrix is not gated on credentialed acquisition"
    grep -qF "if: always() && needs.verify.result == 'success' && needs.verify.outputs.selected == 'true' && (needs.snapshot.result == 'success' || needs.snapshot.result == 'skipped') && needs.acquire-private-dependencies.result == 'success'" \
        <<<"$build_job" \
        || fail "$release_workflow private build matrix can run without successful acquisition"
      acquisition_permissions="$(awk '/^    permissions:/{seen=1;next} seen && /^    [^ ]/{exit} seen{print}' <<<"$acquisition_job" | sed 's/#.*//' | sed '/^[[:space:]]*$/d')"
      [ "$(grep -c . <<<"$acquisition_permissions")" -eq 2 ] \
        && grep -qE '^[[:space:]]+contents:[[:space:]]+read[[:space:]]*$' <<<"$acquisition_permissions" \
        && grep -qE '^[[:space:]]+packages:[[:space:]]+read[[:space:]]*$' <<<"$acquisition_permissions" \
        || fail "$release_workflow private acquisition must have exactly contents-read and packages-read"
      grep -qF 'ref: ${{ needs.verify.outputs.version }}' <<<"$acquisition_job" \
        && grep -qF 'persist-credentials: false' <<<"$acquisition_job" \
        && grep -qF 'npm ci --ignore-scripts --audit=false --fund=false' <<<"$acquisition_job" \
        && grep -qF 'NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}' <<<"$acquisition_job" \
        && grep -qF "uses: $EXPECTED_RELEASE_CACHE_SAVE" <<<"$acquisition_job" \
        || fail "$release_workflow private acquisition weakened its credentialless handoff"
      [ "$(sed 's/#.*//' <<<"$acquisition_job" | grep -Ec 'secrets\b')" -eq 1 ] \
        && ! grep -qE 'scripts/release-|npm (run|test|exec)' <<<"$acquisition_job" \
        || fail "$release_workflow private acquisition exposes credentials to repository execution or another secret"
      grep -qF "uses: $EXPECTED_RELEASE_CACHE_RESTORE" <<<"$build_job" \
        && grep -qF 'fail-on-cache-miss: true' <<<"$build_job" \
        && grep -qF "NODE_AUTH_TOKEN: ''" <<<"$build_job" \
        || fail "$release_workflow private build does not restore dependencies with credentials blanked"
      acquisition_selectors="$(grep -E '^[[:space:]]+- build-runner:' <<<"$acquisition_job" | sed 's/^[[:space:]]*//')"
      build_selectors="$(grep -E '^[[:space:]]+- build-runner:' <<<"$build_job" | sed 's/^[[:space:]]*//')"
      [ "$acquisition_selectors" = "$build_selectors" ] \
        || fail "$release_workflow private acquisition and credentialless build runner matrices differ"
      acquisition_indices="$(grep -E '^[[:space:]]+dependency-index:' <<<"$acquisition_job" | sed 's/^[[:space:]]*//')"
      build_indices="$(grep -E '^[[:space:]]+dependency-index:' <<<"$build_job" | sed 's/^[[:space:]]*//')"
      [ "$acquisition_indices" = "$build_indices" ] \
        || fail "$release_workflow private acquisition and credentialless build dependency indices differ"
      expected_index=0
      while IFS= read -r dependency_index; do
        [ "$dependency_index" = "dependency-index: $expected_index" ] \
          || fail "$release_workflow dependency indices must be unique canonical matrix positions"
        expected_index=$((expected_index + 1))
      done <<<"$build_indices"
      [ "$expected_index" -gt 0 ] \
        || fail "$release_workflow private dependency matrix has no bound index"
      while IFS= read -r runner_selector; do
        runner_selector="${runner_selector#*: }"
        [[ "$runner_selector" =~ ^\'[A-Za-z0-9][A-Za-z0-9._-]*\'$ ]] \
          && [[ ! "${runner_selector:1:${#runner_selector}-2}" =~ ^(vars|inputs|matrix|needs|github|env|secrets)\. ]] \
          || [[ "$runner_selector" =~ ^\$\{\{[[:space:]]fromJSON\(vars\.CI_LANE_TRUSTED_(MACOS|WINDOWS)\)[[:space:]]\}\}$ ]] \
          || fail "$release_workflow acquisition matrix contains an unreviewed runner selector: $runner_selector (ADR 0103)"
        if [[ "$runner_selector" =~ ^\'.*\'$ ]] \
          && [[ "${runner_selector:1:${#runner_selector}-2}" =~ ^(macos|windows)- ]]; then
          fail "$release_workflow acquisition uses a literal metered OS selector forbidden by ADR 0103: $runner_selector"
        fi
      done < <(grep -E '^[[:space:]]+- build-runner:' <<<"$acquisition_job")
      [ "$(grep -cF 'key: release-dependencies-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.dependency-index }}' <<<"$acquisition_job")" -eq 1 ] \
        && [ "$(grep -cF 'key: release-dependencies-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.dependency-index }}' <<<"$build_job")" -eq 1 ] \
        || fail "$release_workflow dependency cache keys are not bound identically to run, attempt, and matrix OS index"
    else
      grep -qF 'needs: [verify, snapshot]' <<<"$build_job" \
        || fail "$release_workflow does not gate the build matrix on both verification and snapshot state"
    grep -qF "if: always() && needs.verify.result == 'success' && needs.verify.outputs.selected == 'true' && (needs.snapshot.result == 'success' || needs.snapshot.result == 'skipped')" \
        <<<"$build_job" \
        || fail "$release_workflow cannot safely resume the build matrix after reusing an immutable snapshot"
    fi
    grep -qF 'ref: ${{ needs.verify.outputs.version }}' <<<"$build_job" \
      || fail "$release_workflow builds artifacts from a ref other than the tagged snapshot"
    grep -qF 'timeout-minutes: 45' <<<"$build_job" \
      || fail "$release_workflow build matrix exceeds ADR 0103's 45-minute bound"
    grep -qF -- '-x scripts/release-build.sh' <<<"$build_job" \
      || fail "$release_workflow does not require an executable scripts/release-build.sh build hook"
    grep -qF "uses: $EXPECTED_RELEASE_UPLOAD_ARTIFACT" <<<"$build_job" \
      || fail "$release_workflow build job does not upload artifacts at the pinned actions/upload-artifact commit"
    grep -qF 'name: release-artifacts-${{ strategy.job-index }}' <<<"$build_job" \
      || fail "$release_workflow build job does not key each runner's upload uniquely by strategy.job-index"
    # #975 (security). The build matrix runs adopter-owned scripts/release-build.sh
    # — potentially third-party build tooling (e.g. Electron/native code-signing
    # dependencies) — on caller-chosen runners. It must never be able to push, and
    # it must never see the release App credential that mints main-protection-bypass
    # tokens (that credential belongs only to snapshot's changelog-release.yml
    # delegation, checked above). A denylist of secret names would need updating
    # every time a new sensitive secret is added elsewhere in this contract, so this
    # bans the whole secrets context instead: scripts/release-build.sh receives only
    # RELEASE_VERSION as a plain input, never a secret.
    build_permissions="$(awk '
      /^    permissions:[[:space:]]*$/ { in_permissions = 1; next }
      in_permissions && /^    [^[:space:]]/ { exit }
      in_permissions { print }
    ' <<<"$build_job")"
    build_permissions_effective="$(sed 's/#.*//' <<<"$build_permissions" | sed '/^[[:space:]]*$/d')"
    [ "$(grep -c . <<<"$build_permissions_effective")" -eq 1 ] \
      && grep -qE '^[[:space:]]+contents:[[:space:]]+read[[:space:]]*$' <<<"$build_permissions_effective" \
      || fail "$release_workflow build job grants more than contents-read; the build matrix runs adopter-owned scripts/release-build.sh on caller-chosen runners and must never receive write access (#975)"
    # Match the bare word, not just the dot-accessor form: ${{ secrets['NAME'] }}
    # and ${{ toJSON(secrets) }} both expose secret material without the literal
    # substring "secrets." ever appearing, and both must be caught too.
    ! sed 's/#.*//' <<<"$build_job" | grep -qE 'secrets\b' \
      || fail "$release_workflow build job references a secrets context; the build matrix runs adopter-owned scripts/release-build.sh on caller-chosen runners and must never receive any secret, especially not the release App credential that mints main-protection-bypass tokens (#975)"
    grep -qF 'needs: [verify, snapshot, build]' <<<"$publish_job" \
      || fail "$release_workflow does not gate publication on verification, snapshot, and build state"
    grep -qF "if: always() && needs.verify.result == 'success' && needs.verify.outputs.selected == 'true' && (needs.snapshot.result == 'success' || needs.snapshot.result == 'skipped') && needs.build.result == 'success'" \
      <<<"$publish_job" \
      || fail "$release_workflow cannot safely resume publication after reusing an immutable snapshot and build matrix"
    grep -qF "uses: $EXPECTED_RELEASE_DOWNLOAD_ARTIFACT" <<<"$publish_job" \
      || fail "$release_workflow publish job does not download artifacts at the pinned actions/download-artifact commit"
    grep -qF 'pattern: release-artifacts-*' <<<"$publish_job" \
      && grep -qF 'merge-multiple: true' <<<"$publish_job" \
      || fail "$release_workflow publish job does not merge every runner's release-artifacts-* upload"
    grep -qF 'test -f "CHANGELOG/$VERSION.md"' <<<"$publish_job" \
      || fail "$release_workflow publish job does not verify the immutable release note before publishing"
    grep -qF '# RESTART_SAFE_GH_RELEASE_BEGIN' <<<"$publish_job" \
      && grep -qF '# RESTART_SAFE_GH_RELEASE_END' <<<"$publish_job" \
      || fail "$release_workflow publish job does not use the restart-safe GitHub Release publication shape (#862)"
    grep -qF 'gh release upload "$VERSION" release-artifacts/* --clobber' <<<"$publish_job" \
      || fail "$release_workflow publish job does not idempotently attach every downloaded artifact to the release"
  fi
  # #975 (security). A workflow-level env: block (0-indent, a sibling of jobs:)
  # could smuggle a secret past the per-job secrets scan above: ${{ env.NAME }}
  # inside the build job never contains the literal word "secrets", even though
  # the workflow-level env: entry that defines NAME does. The generator's own
  # template never emits a workflow-level env: block, so rejecting one outright
  # is a zero-false-positive tightening rather than scanning and folding it in.
  ! grep -qE '^env:[[:space:]]*$' "$release_workflow" \
    || fail "$release_workflow declares a workflow-level env: block; a job could read a secret placed there via \${{ env.NAME }} without the literal secrets context ever appearing inside the job itself, bypassing the build job's secrets scan (#975)"
  grep -qF 'group: release-${{ github.repository }}' "$release_workflow" \
    && grep -qF 'cancel-in-progress: false' "$release_workflow" \
    || fail "$release_workflow does not serialize destructive package cleanup across release versions (#889)"
  grep -qF "if: needs.verify.outputs.selected == 'true' && needs.verify.outputs.snapshot-exists != 'true'" <<<"$snapshot_job" \
    || fail "$release_workflow does not skip empty selections or resume an existing immutable snapshot"
  grep -qF 'snapshot-exists: ${{ steps.release-state.outputs.snapshot-exists }}' \
    <<<"$verify_job" \
    || fail "$release_workflow does not propagate verified snapshot state"
  grep -qF 'selected: ${{ steps.release-version.outputs.selected }}' \
    <<<"$verify_job" \
    || fail "$release_workflow does not propagate the resolved selection state"
  grep -qF 'version: ${{ steps.release-version.outputs.version }}' \
    <<<"$verify_job" \
    || fail "$release_workflow does not propagate the resolved release version"
  grep -qF 'selection-digest: ${{ steps.release-version.outputs.selection-digest }}' \
    <<<"$verify_job" \
    || fail "$release_workflow does not propagate the resolved selection digest"
  grep -qF 'release-plan --repo-root "$GITHUB_WORKSPACE"' \
    <<<"$verify_job" \
    || fail "$release_workflow does not resolve the version with the pinned release-plan engine"
  grep -qF 'GITHUB_STEP_SUMMARY' <<<"$verify_job" \
    || fail "$release_workflow does not expose the release resolution summary"
  grep -qF 'echo "VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools" >> "$GITHUB_ENV"' \
    <<<"$verify_job" \
    || fail "$release_workflow does not give repository verification hooks a job-writable changelog cache beneath runner.temp (#630)"
  first_verify_step="$(awk '/^[[:space:]]+- name:/ { print; exit }' <<<"$verify_job")"
  grep -qF -- '- name: Prepare job-scoped changelog tool cache' <<<"$first_verify_step" \
    || fail "$release_workflow does not prepare the writable changelog cache before repository verification steps (#630)"
  grep -qF "if: steps.release-version.outputs.selected == 'true' && steps.release-state.outputs.snapshot-exists == 'true'" <<<"$verify_job" \
    || fail "$release_workflow does not condition resumed verification on an existing snapshot"
  grep -qF 'ref: ${{ steps.release-version.outputs.version }}' <<<"$verify_job" \
    || fail "$release_workflow verifies the later dispatch tree instead of the existing tagged snapshot"
  if [ "$release_mode" = release-node ]; then
    for publish_input in \
      'version: ${{ needs.verify.outputs.version }}' \
      'prefix: ${{ inputs.prefix }}' \
      "contract-ref: $CONTRACT_REF" \
      "$expected_node_version" \
      "scope: '$EXPECTED_RELEASE_SCOPE'" \
      "package-dirs: '$workflow_package_dirs_json'" \
      "release-assets: '$EXPECTED_RELEASE_ASSETS_JSON'" \
      'runner: ${{'; do
      grep -qF "$publish_input" <<<"$publish_job" \
        || fail "$release_workflow does not pass '$publish_input' to node-release.yml"
    done
    grep -qF 'NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}' <<<"$publish_job" \
      || fail "$release_workflow does not pass the private-dependency token to node-release.yml"
  fi

  # The trigger surface and the install credential are checked structurally,
  # because both were shipped here as line-oriented greps first and both were
  # trivially evadable: an `on:` blocklist accepts every trigger nobody thought
  # to list (`workflow_call`, `release`, `workflow_run`), and a `^on:$` anchor
  # never sees `on: {workflow_dispatch: ..., push: ...}` written in flow style.
  # The rules below are allowlists over a parsed trigger set, and the parser
  # refuses anything it cannot read rather than passing it.
  #
  # PyYAML is deliberately not used: the canonical contract runs on a bare
  # python3 with no third-party dependency, and a "use it if importable"
  # fallback would put every adopter without it on the untested path.
  python3 "$work/release-shape.py" "$release_workflow" \
    || fail "$release_workflow: see above"

  # Text presence is not behavior: a no-op shell command can carry the entire
  # diagnostic and emit nothing. Execute the generated failure path with a
  # failing repository hook and require its original status and safe output.
  verification_fixture="$(mktemp -d "$work/release-verification.XXXXXX")"
  verification_script="$verification_fixture/verify.sh"
  awk '
    /^      - name: Run the release verification suite$/ { found = 1; next }
    found && /^        run: \|$/ { capture = 1; next }
    capture && /^      - / { exit }
    capture { sub(/^          /, ""); print }
  ' "$release_workflow" >"$verification_script"
  [ -s "$verification_script" ] \
    || fail "$release_workflow has no executable release verification body"
  mkdir -p "$verification_fixture/scripts"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 23' \
    >"$verification_fixture/scripts/release-verify.sh"
  chmod +x "$verification_fixture/scripts/release-verify.sh"
  verification_rc=0
  (
    cd "$verification_fixture"
    PACKAGE_VERSION=9.8.7 NODE_AUTH_TOKEN=do-not-print-this \
      bash -eo pipefail "$verification_script"
  ) >"$verification_fixture/output" 2>&1 || verification_rc=$?
  [ "$verification_rc" -eq 23 ] \
    || fail "$release_workflow does not preserve a failing suite's exit status (#862)"
  grep -qF 'Release verification failed against stamped dispatch version 9.8.7. Check for the hardcoded-version footgun:' \
    "$verification_fixture/output" \
    || fail "$release_workflow does not emit the stamped-version failure diagnostic (#862)"
  ! grep -qF 'do-not-print-this' "$verification_fixture/output" \
    || fail "$release_workflow exposes the verification credential in its failure diagnostic (#862)"
done <<RELEASE_WORKFLOWS
$release_workflows
RELEASE_WORKFLOWS
echo "ok - render, validation and release automation share one immutable pin"

# The regression this file exists to prevent was a hand-written local renderer
# that kept working while silently diverging from the contract.
grep -q 'gen-changelog-caller.sh' "$renderer" \
  || fail "$renderer is not the generated renderer; regenerate it"
echo "ok - the renderer delegates to the contract instead of reimplementing it"

# A non-executable script fails CI with exit 126 long after the diff looks fine.
for file in "$renderer" "$0"; do
  [ -x "$file" ] || fail "$file is not executable"
done
echo "ok - contract scripts are executable"

# Guarded, because render-next exits non-zero on an empty NEXT/ — which is
# exactly the state a release leaves behind. The final fixture proves this guard
# is still load-bearing rather than dead code.
#
# The tolerated cause is decided from the TREE, not from the exit status (#399,
# duplicate #419). Keyed on the status alone, every renderer failure reported
# `ok - no unreleased fragments to render`: an unreachable contract fetch, a
# digest mismatch, a malformed fragment, a missing python3, the #398 argv
# ceiling. Each of those is a broken adopter announcing a clean release, and
# `2>/dev/null` threw away the only sentence that said which.
#
# So: an emptied NEXT/ is the one state that excuses a non-zero exit, and it is
# observable directly. A failure with fragments still present is a failure, and
# the captured stderr is printed rather than discarded.
#
# The rendered log travels through a file, never through a variable handed to
# execve. A single argv or environment string is capped at MAX_ARG_STRLEN — a
# fixed 128 KiB, unrelated to the far larger ARG_MAX that a check would read —
# so an adopter whose unreleased NEXT/ crossed that line died here with a bare
# "Argument list too long" and exit 126, naming neither the changelog nor the
# fragment count (#398). NEXT/ is per-change and never batched, so it grows past
# 128 KiB in the ordinary course of a busy release cycle; releasing consumes it,
# but the release path runs this suite, so the failure gated its own remedy.
render_rc=0
"$renderer" >"$work/rendered" 2>"$work/render-err" || render_rc=$?
# README.md and 0000-archive.md are excluded by name here for the same reason the
# python block skips them: neither is a renderable fragment, so a NEXT/ holding
# only those is "emptied" as far as the renderer is concerned.
renderable_left="$(python3 - "$root/NEXT" <<'PY'
import sys
from pathlib import Path

count = 0
for path in Path(sys.argv[1]).glob("*.md"):
    if path.name in {"README.md", "0000-archive.md"}:
        continue
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        count += 1
        continue
    try:
        end = lines.index("---", 1)
    except ValueError:
        count += 1
        continue
    if not any(line.partition(":")[0].strip() == "component" for line in lines[1:end]):
        count += 1
print(count)
PY
)"
if [ "$render_rc" -ne 0 ] && [ "${renderable_left:-0}" -gt 0 ]; then
  echo "the renderer exited $render_rc with $renderable_left unreleased fragment(s) still in NEXT/." >&2
  echo "This is not the post-release empty-NEXT/ case; the renderer itself is broken." >&2
  echo "--- renderer stderr ---" >&2
  cat "$work/render-err" >&2 || true
  echo "--- end renderer stderr ---" >&2
  fail "render-next failed for a reason other than an emptied NEXT/"
fi
if [ "$render_rc" -eq 0 ]; then
  ROOT="$root" RENDERED_PATH="$work/rendered" python3 - <<'PY'
import os
import re
import sys
from pathlib import Path

root = Path(os.environ["ROOT"])
rendered = Path(os.environ["RENDERED_PATH"]).read_text(encoding="utf-8")
# 0000-archive.md is special-cased by name and is not rendered in strict mode.
skip = {"README.md", "0000-archive.md"}
fragments = sorted(p for p in (root / "NEXT").glob("*.md") if p.name not in skip)
fragments = [
    path
    for path in fragments
    if not any(
        line.partition(":")[0].strip() == "component"
        for line in path.read_text(encoding="utf-8").split("---", 2)[1].splitlines()
    )
]
if not fragments:
    sys.exit("NEXT/ holds no renderable fragments but the renderer produced output")

def unquote(value):
    """The text a YAML-quoted scalar denotes.

    Reimplemented rather than imported, deliberately: this file exists to check
    the engine's output, so it must not borrow the engine's reading of the
    input. But it does have to read the same subset. YAML *requires* a quoted
    scalar wherever a value contains `: `, which is the shape of every
    conventional-commit title, so a parser that keeps the quotes as literal text
    rejects the one spelling a YAML parser accepts and reports it as a missing
    title. A value that merely opens and closes with a quote is not a quoted
    scalar and is returned untouched.
    """
    if len(value) < 2 or value[0] not in "'\"" or value[-1] != value[0]:
        return value
    quote, inner = value[0], value[1:-1]
    index = 0
    while index < len(inner):
        if quote == '"' and inner[index] == "\\":
            index += 2
            continue
        if inner[index] == quote:
            if quote == "'" and inner[index : index + 2] == "''":
                index += 2
                continue
            return value
        index += 1
    if quote == "'":
        return inner.replace("''", "'")
    return inner.replace('\\"', '"').replace("\\\\", "\\")


for path in fragments:
    front = path.read_text(encoding="utf-8").split("---", 2)[1]
    meta = {}
    for line in front.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = unquote(value.strip())
    if f"## {meta['title']}" not in rendered:
        sys.exit(f"{path.name}: title missing from the rendered log")
    # Identity is not decoration: only issue-form entries render a `#n`
    # back-link, so a fragment demoted to `id` silently loses release linkage
    # and no validation error is raised.
    #
    # The trailing group is the `; refs #a, #b` an entry renders when it links
    # issues it does not own (#316). Anchoring `_$` straight after the back-link
    # rejected that combination even though validation accepts it and the engine
    # renders it — and the test is generated, so the adopter had no legal way out
    # (#461). It stays a spelled-out shape rather than `.*`, because the anchor is
    # what makes a truncated or embellished back-link fail — and the group is
    # required, not optional, once the fragment declares `refs`, so a linkage the
    # render drops is caught by the same assertion that the missing back-link is.
    # Whether the numbers are the declared ones is the engine's business; this
    # file checks the shape it emits, never re-derives the input.
    if "issue" in meta:
        refs_group = r"(?:; refs #\d+(?:, #\d+)*)"
        if not meta.get("refs", "").strip():
            refs_group += "?"
        pattern = (
            rf"^_Date: {re.escape(meta['date'])}; issue #{re.escape(meta['issue'])}"
            rf"{refs_group}_$"
        )
        if not re.search(pattern, rendered, re.MULTILINE):
            sys.exit(f"{path.name}: issue back-link missing from the rendered log")

# Metadata, not filename allocation, orders the log.
dates = re.findall(r"^_Date: (\d{4}-\d{2}-\d{2});", rendered, re.MULTILINE)
if dates != sorted(dates, reverse=True):
    sys.exit("rendered log is not newest-first by metadata date")
PY
  echo "ok - every unreleased fragment renders with its metadata linkage, newest first"
else
  echo "ok - no unreleased fragments to render (a release consumed them)"
fi

# CHANGELOG.md is generated by `release`, never authored. Asserting it absent
# fails the moment the contract works as intended, so assert instead that it is
# exactly what the released snapshots render to.
if [ -e "$root/CHANGELOG.md" ]; then
  python3 "$contract" render-released --repo-root "$root" >"$work/released"
  diff -q "$work/released" "$root/CHANGELOG.md" >/dev/null \
    || fail "CHANGELOG.md was hand-edited; it must equal the rendered released snapshots"
  echo "ok - CHANGELOG.md is generated from released snapshots, not authored"
else
  echo "ok - no aggregate changelog yet; nothing has been released"
fi

# A root NEXT.md may survive as a pointer, but never as a second running log:
# entries written there are invisible to validation, rendering and release.
if [ -e "$root/NEXT.md" ] && grep -q '^## ' "$root/NEXT.md"; then
  fail "NEXT.md still holds log entries; NEXT/ is the only unreleased store"
fi
echo "ok - NEXT/ is the only unreleased store"

# Releases are the only writer of released history. A stray .releaserc.json
# silently reintroduces release-on-merge, which never consumes a fragment.
[ ! -e "$root/.releaserc.json" ] \
  || fail ".releaserc.json reintroduces semantic-release outside the contract"
# Every discovered release caller was parsed and restricted to workflow_dispatch
# inside the loop above. Do not inspect the loop variable here: after the loop it
# identifies only the last caller and silently drops coverage for every earlier one.
echo "ok - every release caller is dispatched explicitly, not derived from pushes to main"

new_fixture() {
  rm -rf "$fixture_root/case"
  mkdir -p "$fixture_root/case/NEXT"
}

write_fragment() {
  # write_fragment <relative-path> <date> <identity-line> <title> [impact]
  local impact="${5:-}"
  {
    echo "---"
    echo "date: $2"
    echo "$3"
    [ -z "$impact" ] || echo "impact: $impact"
    echo "title: $4"
    echo "---"
    echo
    echo "Body."
  } >"$fixture_root/case/$1"
}

init_fixture_repo() {
  git -C "$fixture_root/case" init -q
  git -C "$fixture_root/case" config user.name Test
  git -C "$fixture_root/case" config user.email test@example.com
}

# The rules this repository relies on, exercised against the pinned contract so
# that re-pinning to a revision that dropped one fails here rather than in a
# release six weeks later.
new_fixture
for slug in first second; do
  write_fragment "NEXT/2026-08-01-issue-43-$slug.md" 2026-08-01 "issue: 43" Duplicate
done
if python3 "$contract" validate --repo-root "$fixture_root/case" 2>"$fixture_root/error"; then
  fail "duplicate issue identity was accepted"
fi
grep -q 'duplicate identity issue:43' "$fixture_root/error"
echo "ok - duplicate issue identities are rejected"

new_fixture
write_fragment NEXT/2026-08-01-issue-43-wrong-date.md 2026-07-31 "issue: 43" "Wrong date"
if python3 "$contract" validate --repo-root "$fixture_root/case" 2>"$fixture_root/error"; then
  fail "mismatched filename metadata was accepted"
fi
grep -q 'does not match' "$fixture_root/error"
echo "ok - filename and metadata must agree"

new_fixture
printf '# Legacy entry\n\nBody.\n' >"$fixture_root/case/NEXT/2026-08-01-legacy.md"
if python3 "$contract" validate --repo-root "$fixture_root/case" 2>"$fixture_root/error"; then
  fail "a pre-contract fragment name was accepted"
fi
grep -q 'does not follow the canonical contract' "$fixture_root/error"
echo "ok - pre-contract fragment names are rejected"

new_fixture
write_fragment NEXT/2026-07-31-issue-99-zzz.md 2026-07-31 "issue: 99" Older
write_fragment NEXT/2026-08-01-issue-1-aaa.md 2026-08-01 "issue: 1" Newer
ordered="$(python3 "$contract" render-next --repo-root "$fixture_root/case")"
[ "$(grep -n '^## Newer$' <<<"$ordered" | cut -d: -f1)" \
  -lt "$(grep -n '^## Older$' <<<"$ordered" | cut -d: -f1)" ] \
  || fail "rendering order followed slug allocation instead of metadata"
echo "ok - rendering order follows metadata instead of slug allocation"

# Issue-less work keeps the literal -issue- filename segment; only the identity
# varies. A -id- filename is rejected even though the metadata key is `id`.
new_fixture
write_fragment NEXT/2026-08-01-issue-20260801T184500Z-timestamped.md \
  2026-08-01 "id: 20260801T184500Z" "Issue-less work"
python3 "$contract" validate --repo-root "$fixture_root/case"
echo "ok - issue-less work may use a UTC timestamp identity"

# New fragments must state release intent at review time. Existing unreleased
# fragments keep their patch fallback, so adopting this contract never rewrites
# an old NEXT/ entry or an immutable CHANGELOG/ snapshot (#800).
new_fixture
init_fixture_repo
printf 'base\n' >"$fixture_root/case/README.md"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
write_fragment NEXT/2026-08-15-issue-800-missing-impact.md \
  2026-08-15 "issue: 800" "Missing impact"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "new fragment without impact"
if python3 "$contract" validate --repo-root "$fixture_root/case" \
  --base "$base" --head HEAD 2>"$fixture_root/error"; then
  fail "a new fragment without explicit impact was accepted"
fi
grep -q 'impact is required.*major, minor, or patch' "$fixture_root/error"
python3 "$contract" validate --repo-root "$fixture_root/case" \
  --base "$base" --head HEAD --allow-missing-impact-through 9999-12-31
echo "ok - new fragments require explicit impact after the bounded migration window"

new_fixture
init_fixture_repo
write_fragment NEXT/2026-08-01-issue-43-legacy-impact.md \
  2026-08-01 "issue: 43" "Legacy implicit patch"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
write_fragment NEXT/2026-08-15-issue-800-explicit-impact.md \
  2026-08-15 "issue: 800" "Explicit impact" minor
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "new fragment with impact"
python3 "$contract" validate --repo-root "$fixture_root/case" \
  --base "$base" --head HEAD
echo "ok - legacy implicit-patch fragments remain valid while new fragments declare impact"

new_fixture
init_fixture_repo
write_fragment NEXT/2026-08-01-issue-43-legacy-rename.md \
  2026-08-01 "issue: 43" "Renamed identity"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
mv "$fixture_root/case/NEXT/2026-08-01-issue-43-legacy-rename.md" \
  "$fixture_root/case/NEXT/2026-08-01-issue-800-renamed-identity.md"
python3 - "$fixture_root/case/NEXT/2026-08-01-issue-800-renamed-identity.md" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(
    path.read_text(encoding="utf-8").replace("issue: 43", "issue: 800"),
    encoding="utf-8",
)
PY
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "rename to a different identity"
git -C "$fixture_root/case" diff --find-renames --name-status "$base...HEAD" \
  | grep -q '^R' || fail "identity-change fixture was not classified as a rename"
if python3 "$contract" validate --repo-root "$fixture_root/case" \
  --base "$base" --head HEAD 2>"$fixture_root/error"; then
  fail "a NEXT rename to a different identity bypassed explicit impact"
fi
grep -q 'impact is required.*major, minor, or patch' "$fixture_root/error"
echo "ok - NEXT renames to a different identity require explicit impact"

# ADR 0017's check-pr rule, both halves: an ordinary pull request may neither
# write released history nor consume a fragment.
new_fixture
init_fixture_repo
write_fragment NEXT/2026-08-01-issue-43-base.md 2026-08-01 "issue: 43" Base
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
mkdir -p "$fixture_root/case/CHANGELOG"
printf 'snapshot\n' >"$fixture_root/case/CHANGELOG/v9.9.9.md"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "write released history"
if python3 "$contract" check-pr --repo-root "$fixture_root/case" \
  --base "$base" --head HEAD 2>"$fixture_root/error"; then
  fail "a pull request writing released history was accepted"
fi
grep -q 'released snapshots' "$fixture_root/error"
echo "ok - pull requests cannot write released history"

new_fixture
init_fixture_repo
write_fragment NEXT/2026-08-01-issue-43-base.md 2026-08-01 "issue: 43" Base
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
git -C "$fixture_root/case" rm -q "NEXT/2026-08-01-issue-43-base.md"
git -C "$fixture_root/case" commit -qm "consume a fragment"
if python3 "$contract" check-pr --repo-root "$fixture_root/case" \
  --base "$base" --head HEAD 2>"$fixture_root/error"; then
  fail "a pull request deleting a NEXT/ fragment was accepted"
fi
grep -q 'NEXT' "$fixture_root/error"
echo "ok - pull requests cannot consume NEXT/ fragments"

# Dependency-only bot updates are still observable changes. The policy is keyed
# exclusively to the changed-file boundary, not actor identity, so Renovate and
# human-authored updates receive the same requirement.
for dependency_path in \
  package.json apps/api/package-lock.json pnpm-lock.yaml web/yarn.lock \
  pyproject.toml requirements.txt requirements-dev.txt poetry.lock Pipfile \
  Pipfile.lock uv.lock services/worker/go.mod services/worker/go.sum \
  crates/core/Cargo.toml Cargo.lock infra/.terraform.lock.hcl; do
  new_fixture
  init_fixture_repo
  printf 'base\n' >"$fixture_root/case/README.md"
  git -C "$fixture_root/case" add .
  git -C "$fixture_root/case" commit -qm base
  base="$(git -C "$fixture_root/case" rev-parse HEAD)"
  mkdir -p "$(dirname "$fixture_root/case/$dependency_path")"
  printf 'dependency update\n' >"$fixture_root/case/$dependency_path"
  git -C "$fixture_root/case" add .
  git -C "$fixture_root/case" commit -qm "renovate dependency update"
  if python3 "$contract" check-pr --repo-root "$fixture_root/case" \
    --base "$base" --head HEAD 2>"$fixture_root/error"; then
    fail "dependency change without a fragment was accepted: $dependency_path"
  fi
  grep -q 'dependency manifests or lockfiles require a new NEXT fragment' \
    "$fixture_root/error"
done
echo "ok - dependency manifests and lockfiles require a new fragment for every actor (#524)"

new_fixture
init_fixture_repo
printf '{"version":"1.0.0"}\n' >"$fixture_root/case/package.json"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
printf '{"version":"1.0.1"}\n' >"$fixture_root/case/package.json"
write_fragment NEXT/2026-08-01-issue-524-dependency.md \
  2026-08-01 "issue: 524" "Dependency update"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "dependency update with fragment"
python3 "$contract" check-pr --repo-root "$fixture_root/case" --base "$base" --head HEAD
echo "ok - a dependency change with a new valid fragment is accepted"

# The generator can reproduce older immutable contracts for audits. Only run
# this mutation suite once the pinned engine advertises the #1455 boundary.
if grep -q '^def is_release_relevant_change' "$contract"; then
# Behavior, configuration, code, documentation, and pins need release context
# even when no dependency manifest is involved. The decision is based on the
# final base-to-head tree, so adding a fragment and deleting it later cannot
# leave an undocumented change behind (#1455).
new_fixture
init_fixture_repo
printf 'base\n' >"$fixture_root/case/README.md"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
mkdir -p "$fixture_root/case/docs"
printf 'documented behavior\n' >"$fixture_root/case/docs/runbook.md"
write_fragment NEXT/2026-09-23-issue-1455-release-context.md \
  2026-09-23 "issue: 1455" "Require release context" patch
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "document behavior with fragment"
python3 "$contract" check-pr --repo-root "$fixture_root/case" --base "$base" --head HEAD
echo "ok - non-dependency change with a new valid fragment is accepted"

git -C "$fixture_root/case" rm -q NEXT/2026-09-23-issue-1455-release-context.md
git -C "$fixture_root/case" commit -qm "remove fragment"
if python3 "$contract" check-pr --repo-root "$fixture_root/case" \
  --base "$base" --head HEAD 2>"$fixture_root/error"; then
  fail "non-dependency change accepted after its fragment was deleted"
fi
grep -q 'release-relevant changes require a new NEXT fragment' "$fixture_root/error"
echo "ok - deleting a branch fragment cannot hide non-dependency release context"

new_fixture
init_fixture_repo
printf 'base\n' >"$fixture_root/case/README.md"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
mkdir -p "$fixture_root/case/docs"
printf 'undocumented behavior\n' >"$fixture_root/case/docs/runbook.md"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "document behavior without fragment"
if python3 "$contract" check-pr --repo-root "$fixture_root/case" \
  --base "$base" --head HEAD 2>"$fixture_root/error"; then
  fail "non-dependency change without a fragment was accepted"
fi
grep -q 'release-relevant changes require a new NEXT fragment' "$fixture_root/error"
echo "ok - non-dependency changes require a new fragment"

for executable_path in \
  .github/workflows/ci.test.yml \
  .github/workflows/release.spec.yaml \
  .github/actions/test/action.yml \
  tests/action.yml \
  packages/foo/tests/action.yaml; do
  new_fixture
  init_fixture_repo
  printf 'base\n' >"$fixture_root/case/README.md"
  git -C "$fixture_root/case" add .
  git -C "$fixture_root/case" commit -qm base
  base="$(git -C "$fixture_root/case" rev-parse HEAD)"
  mkdir -p "$fixture_root/case/$(dirname "$executable_path")"
  printf 'name: executable\n' >"$fixture_root/case/$executable_path"
  git -C "$fixture_root/case" add .
  git -C "$fixture_root/case" commit -qm "add executable github yaml"
  if python3 "$contract" check-pr --repo-root "$fixture_root/case" \
    --base "$base" --head HEAD 2>"$fixture_root/error"; then
    fail "executable GitHub YAML escaped release context: $executable_path"
  fi
  grep -q 'release-relevant changes require a new NEXT fragment' "$fixture_root/error"
done
echo "ok - executable GitHub YAML takes precedence over test exemptions"
fi

new_fixture
init_fixture_repo
printf 'base\n' >"$fixture_root/case/README.md"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
printf '{"version":"1.0.0"}\n' >"$fixture_root/case/package.json"
printf '# NEXT fragments\n' >"$fixture_root/case/NEXT/README.md"
write_fragment NEXT/2026-08-26-issue-1116-adoption.md \
  2026-08-26 "issue: 1116" "Adopt changelog contract"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "adopt contract with dependency manifest"
python3 "$contract" check-pr --repo-root "$fixture_root/case" --base "$base" --head HEAD
echo "ok - NEXT README is not mistaken for an invalid fragment during adoption (#1116)"

base="$(git -C "$fixture_root/case" rev-parse HEAD)"
git -C "$fixture_root/case" rm -q NEXT/README.md
git -C "$fixture_root/case" commit -qm "remove changelog documentation"
python3 "$contract" check-pr --repo-root "$fixture_root/case" --base "$base" --head HEAD
echo "ok - removing NEXT README does not consume a fragment (#1116)"

printf '# NEXT fragments\n' >"$fixture_root/case/NEXT/README.md"
git -C "$fixture_root/case" add NEXT/README.md
git -C "$fixture_root/case" commit -qm "restore changelog documentation"
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
mkdir -p "$fixture_root/case/docs"
git -C "$fixture_root/case" mv NEXT/README.md docs/changelog-fragments.md
write_fragment NEXT/2026-08-26-issue-20260826T120000Z-move-docs.md \
  2026-08-26 "id: 20260826T120000Z" "Move changelog documentation" patch
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "move changelog documentation"
python3 "$contract" check-pr --repo-root "$fixture_root/case" --base "$base" --head HEAD
echo "ok - moving NEXT README does not consume a fragment (#1116)"

new_fixture
init_fixture_repo
printf '{"version":"1.0.0"}\n' >"$fixture_root/case/package.json"
write_fragment NEXT/2026-08-01-issue-524-existing.md \
  2026-08-01 "issue: 524" "Existing entry"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
printf '{"version":"1.0.1"}\n' >"$fixture_root/case/package.json"
printf '\nMore.\n' >>"$fixture_root/case/NEXT/2026-08-01-issue-524-existing.md"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "dependency update reusing fragment"
if python3 "$contract" check-pr --repo-root "$fixture_root/case" \
  --base "$base" --head HEAD 2>"$fixture_root/error"; then
  fail "dependency change reused an existing fragment"
fi
grep -q 'new NEXT fragment' "$fixture_root/error"
echo "ok - dependency updates cannot reuse an unrelated existing fragment"

new_fixture
init_fixture_repo
printf 'base\n' >"$fixture_root/case/README.md"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm base
base="$(git -C "$fixture_root/case" rev-parse HEAD)"
for unrelated in package.json.md package-lock.yaml requirements.md \
  Cargo.lock.backup terraform.lock.hcl docs/go.mod.md; do
  mkdir -p "$(dirname "$fixture_root/case/$unrelated")"
  printf 'not a dependency boundary\n' >"$fixture_root/case/$unrelated"
done
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm "similarly named files"
if grep -q '^def is_release_relevant_change' "$contract"; then
  if python3 "$contract" check-pr --repo-root "$fixture_root/case" \
    --base "$base" --head HEAD 2>"$fixture_root/error"; then
    fail "similarly named non-dependency files escaped release context"
  fi
  grep -q 'release-relevant changes require a new NEXT fragment' "$fixture_root/error"
  if grep -q 'dependency manifests or lockfiles' "$fixture_root/error"; then
    fail "similarly named files were misclassified as dependency manifests"
  fi
  echo "ok - similarly named files remain non-dependencies but require release context"
else
  python3 "$contract" check-pr --repo-root "$fixture_root/case" --base "$base" --head HEAD
  echo "ok - similarly named non-dependency files do not trigger the fragment rule"
fi

if python3 "$contract" check-pr --repo-root "$fixture_root/case" \
  --base malformed-api-sha --head HEAD 2>"$fixture_root/error"; then
  fail "malformed pull-request revision input was accepted"
fi
grep -q 'malformed-api-sha' "$fixture_root/error"
echo "ok - malformed pull-request revision input fails closed"

new_fixture
init_fixture_repo
mkdir -p "$fixture_root/case/CHANGELOG"
write_fragment NEXT/2026-08-01-issue-43-release.md 2026-08-01 "issue: 43" Release
printf 'immutable\n' >"$fixture_root/case/CHANGELOG/v1.0.0.md"
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm initial
if python3 "$contract" release --repo-root "$fixture_root/case" --version v1.0.0 \
  2>"$fixture_root/error"; then
  fail "a released snapshot overwrite was accepted"
fi
grep -q 'already exists' "$fixture_root/error"
[ "$(cat "$fixture_root/case/CHANGELOG/v1.0.0.md")" = 'immutable' ] \
  || fail "a rejected release still mutated the snapshot"
echo "ok - released snapshots cannot be overwritten"

new_fixture
init_fixture_repo
mkdir -p "$fixture_root/case/CHANGELOG"
printf 'baseline\n' >"$fixture_root/case/CHANGELOG/v1.0.0.md"
write_fragment NEXT/2026-08-01-issue-43-impact.md \
  2026-08-01 "issue: 43" "Minor release" minor
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm initial
before="$(git -C "$fixture_root/case" status --porcelain)"
next_version="$(python3 "$contract" next-version --repo-root "$fixture_root/case")"
[ "$next_version" = v1.1.0 ] \
  || fail "next-version derived '$next_version' instead of v1.1.0"
[ "$(git -C "$fixture_root/case" status --porcelain)" = "$before" ] \
  || fail "next-version mutated the release tree"
if python3 "$contract" release --repo-root "$fixture_root/case" --version v1.0.1 \
  2>"$fixture_root/error"; then
  fail "a release smaller than the selected impact was accepted"
fi
grep -q 'require a minor bump' "$fixture_root/error"
[ "$(git -C "$fixture_root/case" status --porcelain)" = "$before" ] \
  || fail "a rejected impact mismatch mutated the release tree"
rendered="$(python3 "$contract" render-next --repo-root "$fixture_root/case")"
[[ "$rendered" != *"impact:"* ]] \
  || fail "release impact leaked into rendered changelog text"
python3 "$contract" release --repo-root "$fixture_root/case" \
  --version "$next_version" >/dev/null
[ -f "$fixture_root/case/CHANGELOG/v1.1.0.md" ] \
  || fail "the required impact bump wrote no snapshot"
echo "ok - next-version matches release enforcement without mutating the tree"

# The regression this file exists to prevent: prove that the repository-level
# assertions above survive a real release, instead of asserting a pre-release
# state that the first tag destroys. Every branch taken above is taken again
# here against a released tree.
new_fixture
init_fixture_repo
write_fragment NEXT/2026-08-01-issue-43-released.md 2026-08-01 "issue: 43" Released
git -C "$fixture_root/case" add .
git -C "$fixture_root/case" commit -qm initial
python3 "$contract" release --repo-root "$fixture_root/case" --version v1.0.0 >/dev/null
[ -f "$fixture_root/case/CHANGELOG/v1.0.0.md" ] || fail "release wrote no snapshot"
[ -e "$fixture_root/case/CHANGELOG.md" ] || fail "release generated no aggregate changelog"
[ -z "$(ls -1 "$fixture_root/case/NEXT" 2>/dev/null)" ] \
  || fail "release left fragments behind in NEXT/"
python3 "$contract" render-released --repo-root "$fixture_root/case" >"$work/after"
diff -q "$work/after" "$fixture_root/case/CHANGELOG.md" >/dev/null \
  || fail "the generated CHANGELOG.md does not equal the rendered released snapshots"
# If this ever succeeds, the guard around the render block above is dead code and
# a future edit could reintroduce the unguarded form without any test failing.
if python3 "$contract" render-next --repo-root "$fixture_root/case" >/dev/null 2>&1; then
  fail "render-next succeeded on an emptied NEXT/; the guard above is now dead code"
fi
echo "ok - a real release produces exactly the state asserted above"
EOF
}

# Never hand an operator a file that does not parse. The premise of generating
# these at all is that they should not be able to receive a silent footgun.
case "$mode" in
  codeowners)
    out="$(codeowners_bytes)"
    ;;
  workflow)
    out="$(emit_workflow)"
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)' 2>/dev/null \
        || { echo "internal error: generated workflow is not valid YAML; refusing to emit" >&2; exit 3; }
    fi
    ;;
  generated-artifacts)
    out="$(emit_generated_artifacts false)"
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)' 2>/dev/null \
        || { echo "internal error: generated workflow is not valid YAML; refusing to emit" >&2; exit 3; }
    fi
    ;;
  pr-gate)
    out="$(emit_pr_gate)"
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)' 2>/dev/null \
        || { echo "internal error: generated PR gate is not valid YAML; refusing to emit" >&2; exit 3; }
    fi
    ;;
  generated-artifacts-with-adr-index)
    out="$(emit_generated_artifacts true)"
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)' 2>/dev/null \
        || { echo "internal error: generated workflow is not valid YAML; refusing to emit" >&2; exit 3; }
    fi
    ;;
  renovate-attribution)
    out="$(emit_renovate_attribution)"
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)' 2>/dev/null \
        || { echo "internal error: generated Renovate caller is not valid YAML; refusing to emit" >&2; exit 3; }
    fi
    ;;
  adr-index-generator)
    # The same emitter the pin digests, so the mode and `ADR_INDEX_SHA256` cannot
    # disagree about what an adopter is supposed to have on disk.
    out="$(emit_adr_index_generator)" \
      || { echo "$(basename "$0"): cannot resolve gen-adr-index.sh at $ref" >&2; exit 1; }
    printf '%s\n' "$out" | bash -n 2>/dev/null \
      || { echo "internal error: generated ADR index generator is not valid bash; refusing to emit" >&2; exit 3; }
    ;;
  adr-index-test)
    # `$?` is captured on the failure branch itself: `if ! cmd` would invert the
    # status being read and turn a refusal into a silent exit 0.
    emit_status=0
    out="$(emit_adr_index_test)" || emit_status=$?
    if [ "$emit_status" != 0 ]; then
      # 3 means the canonical suite resolved but no longer carries the line this
      # mode rewrites; emit_adr_index_test has already said so on stderr.
      [ "$emit_status" = 3 ] \
        || echo "$(basename "$0"): cannot resolve gen-adr-index.test.sh at $ref" >&2
      exit "$emit_status"
    fi
    printf '%s\n' "$out" | bash -n 2>/dev/null \
      || { echo "internal error: generated ADR index test is not valid bash; refusing to emit" >&2; exit 3; }
    ;;
  release-node)
    out="$(emit_release_node)"
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)' 2>/dev/null \
        || { echo "internal error: generated release caller is not valid YAML; refusing to emit" >&2; exit 3; }
    fi
    # The verify job's guards are shell, and a release caller that cannot parse
    # fails at the step that was supposed to protect the release.
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c '
import sys, yaml
doc = yaml.safe_load(sys.stdin)
for job in doc["jobs"].values():
    for step in job.get("steps", []):
        if "run" in step:
            print(step["run"])
            print("")
' 2>/dev/null | bash -n 2>/dev/null \
        || { echo "internal error: generated release caller contains invalid bash; refusing to emit" >&2; exit 3; }
    fi
    ;;
  release-artifact)
    out="$(emit_release_artifact)"
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)' 2>/dev/null \
        || { echo "internal error: generated release caller is not valid YAML; refusing to emit" >&2; exit 3; }
    fi
    # The verify/build job guards are shell, and a release caller that cannot
    # parse fails at the step that was supposed to protect the release.
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c '
import sys, yaml
doc = yaml.safe_load(sys.stdin)
for job in doc["jobs"].values():
    for step in job.get("steps", []):
        if "run" in step:
            print(step["run"])
            print("")
' 2>/dev/null | bash -n 2>/dev/null \
        || { echo "internal error: generated release caller contains invalid bash; refusing to emit" >&2; exit 3; }
    fi
    ;;
  release-snapshot)
    out="$(emit_release_snapshot)"
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)' 2>/dev/null \
        || { echo "internal error: generated release caller is not valid YAML; refusing to emit" >&2; exit 3; }
    fi
    # The verify job's guards are shell, and a release caller that cannot parse
    # fails at the step that was supposed to protect the release.
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c '
import sys, yaml
doc = yaml.safe_load(sys.stdin)
for job in doc["jobs"].values():
    for step in job.get("steps", []):
        if "run" in step:
            print(step["run"])
            print("")
' 2>/dev/null | bash -n 2>/dev/null \
        || { echo "internal error: generated release caller contains invalid bash; refusing to emit" >&2; exit 3; }
    fi
    ;;
  release-propose)
    out="$(emit_release_propose)"
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
      printf '%s\n' "$out" | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)' 2>/dev/null \
        || { echo "internal error: generated release proposer is not valid YAML; refusing to emit" >&2; exit 3; }
    fi
    ;;
  renderer)
    out="$(emit_renderer)"
    printf '%s\n' "$out" | bash -n 2>/dev/null \
      || { echo "internal error: generated renderer is not valid bash; refusing to emit" >&2; exit 3; }
    ;;
  contract-test)
    out="$(emit_contract_test)"
    # Do not pipe a captured generated program into a validator that may exit
    # before the producer finishes. Under pipefail that turns the intended
    # syntax diagnostic into printf's SIGPIPE. A file-backed stdin preserves
    # the validator result independently of output size and read-ahead.
    syntax_input="$(mktemp)"
    printf '%s\n' "$out" >"$syntax_input"
    if ! bash -n <"$syntax_input" 2>/dev/null; then
      rm -f "$syntax_input"
      echo "internal error: generated contract test is not valid bash; refusing to emit" >&2
      exit 3
    fi
    rm -f "$syntax_input"
    ;;
  *)
    usage
    ;;
esac
printf '%s\n' "$out"
