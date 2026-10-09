#!/usr/bin/env bash
# Section map for scripts/ci-gate/changelog-caller-contract.test.sh.
# An id that is not listed here runs every section. The coverage test
# fails on that id, so a new mutation cannot skip assertions by omission.
export CHANGELOG_CONTRACT_SECTION_NAMES='generated-set workflow-callers release-workflows renderer fixtures'
CHANGELOG_CALLER_CONTRACT_SHARD_WIDTH=4

changelog_caller_shard_name_ok() {
  case "${1:-all}" in
    all|1|2|3|4) return 0 ;;
    *) return 1 ;;
  esac
}

changelog_caller_case_selected() {
  caller_case_count=$((caller_case_count + 1))
  local shard="${CHANGELOG_CALLER_CONTRACT_SHARD:-all}"
  local width="$CHANGELOG_CALLER_CONTRACT_SHARD_WIDTH"
  local slot
  [ "$shard" = all ] && { caller_case_ran=$((caller_case_ran + 1)); return 0; }
  slot=$(( (caller_case_count - 1) % width + 1 ))
  if [ "$slot" = "$shard" ]; then
    caller_case_ran=$((caller_case_ran + 1))
    return 0
  fi
  return 1
}

changelog_contract_sections_for() {
  case "$1" in
    'adopter') printf '%s\n' 'all' ;;
    'adopter-stale-renovate-caller') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-overprivileged-renovate-caller') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-wrong-event-renovate-caller') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-missing-renovate-gate') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-stale-proposer') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-overprivileged-proposer') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-event-selected-proposer') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-nested-only-release') printf '%s\n' 'all' ;;
    'adopter-multi-release') printf '%s\n' 'all' ;;
    'adopter-custom-release') printf '%s\n' 'all' ;;
    'adopter-omitted-secondary-stamp') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'adopter-generated-artifacts') printf '%s\n' 'all' ;;
    'adopter-retired-changelog-workflow') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-cross-job-generated-artifacts-caller') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-named-changelog-job') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'adopter-matrix-changelog-job') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'adopter-secrets-changelog-job') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'adopter-typo-changelog-job') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'adopter-extra-changelog-input') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'adopter-split-generated-artifacts') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-renamed-duplicate-changelog') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-legacy-duplicate-changelog') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-generated-artifacts-adr') printf '%s\n' 'all' ;;
    'adopter-adr-without-suite') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-handwritten-adr-suite') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-optional-adr-generator') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-optional-adr-test') printf '%s\n' 'generated-set workflow-callers' ;;
    'adopter-broken-renderer') printf '%s\n' 'generated-set renderer' ;;
    'adopter-released-broken') printf '%s\n' 'all' ;;
    'adopter-norelease') printf '%s\n' 'all' ;;
    'adopter-oversize') printf '%s\n' 'all' ;;
    'adopter-quoted-checkout') printf '%s\n' 'all' ;;
    'adopter-inline-checkout') printf '%s\n' 'all' ;;
    'adopter-reindented-jobs') printf '%s\n' 'all' ;;
    'adopter-legacy-release') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'adopter-commented') printf '%s\n' 'all' ;;
    'adopter-quoted') printf '%s\n' 'all' ;;
    'adopter-refs') printf '%s\n' 'all' ;;
    'adopter-refs-released') printf '%s\n' 'all' ;;
    'adopter-refs-multi') printf '%s\n' 'all' ;;
    'adopter-edited') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'adopter-artifact') printf '%s\n' 'all' ;;
    'adopter-artifact-private') printf '%s\n' 'all' ;;
    'adopter-artifact-unbound-build') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-allowlist') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-lock') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-static-cache') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-mismatched-cache') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-lane-preflight') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-noop-lane-preflight') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-timeout') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-acquisition-timeout') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-lifecycle') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-extra-secret') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-acquisition-github-token') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-selector') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-broken-build-gate') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-escalated-permissions') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-leaked-secret') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-bracket-secret') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-tojson-secret') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-github-token-build') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-github-context-build') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-toplevel-env-secret') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-snapshot') printf '%s\n' 'all' ;;
    'adopter-unrecognized-release') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'adopter-stripped-private-guard') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'adopter-remedy-custom-modes') printf '%s\n' 'generated-set' ;;
    'adopter-remedy-paste-modes') printf '%s\n' 'generated-set' ;;
    'adopter-remedy-paste-self') printf '%s\n' 'generated-set' ;;
    'adopter-atomic-set') printf '%s\n' 'all' ;;
    'adopter-header-injection') printf '%s\n' 'generated-set' ;;
    'a renderer pinned to a different commit') printf '%s\n' 'generated-set renderer' ;;
    'an adopter without .github/CODEOWNERS') printf '%s\n' 'generated-set' ;;
    'a hand-edited .github/CODEOWNERS') printf '%s\n' 'generated-set' ;;
    'a competing root CODEOWNERS beside the canonical file') printf '%s\n' 'generated-set' ;;
    'a symlinked .github/CODEOWNERS') printf '%s\n' 'generated-set' ;;
    'a symlinked .github directory in front of CODEOWNERS') printf '%s\n' 'generated-set' ;;
    'a competing docs/CODEOWNERS beside the canonical file') printf '%s\n' 'generated-set' ;;
    'a directory where .github/CODEOWNERS must be a file') printf '%s\n' 'generated-set' ;;
    'a hand-written renderer that bypasses the contract') printf '%s\n' 'generated-set renderer' ;;
    'a .releaserc.json that reintroduces release-on-merge') printf '%s\n' 'generated-set renderer' ;;
    'a second authored running log in NEXT.md') printf '%s\n' 'generated-set renderer' ;;
    'a fragment whose filename is not canonical') printf '%s\n' 'generated-set renderer' ;;
    'a non-executable renderer') printf '%s\n' 'generated-set renderer' ;;
    'a release caller without RELEASE_APP_CLIENT_ID') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release caller without its release environment') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release caller restoring ORG_ADMIN_TOKEN') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release caller wiring GITHUB_TOKEN') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a snapshot caller granting GITHUB_TOKEN contents-write (#784)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a snapshot job that verifies nothing first (#463, #464)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a snapshot job with no explicit runner (#465)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a resumed release that verifies the later dispatch tree instead of its tagged snapshot (#591)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an npm ci installing with GITHUB_TOKEN (#465)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a verification job without package metadata preparation (#550)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a verification suite with no dispatched version stamp (#519)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'version stamps that can run package lifecycle scripts (#519)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a first release whose scaffold version already matches the dispatch (#579)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release verification suite receiving package credentials (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a credentialed install step with an extra command (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a credentialed install step without a repository npm configuration guard (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a credentialed install working-directory override (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'canonical selection checkout uses an unpinned branch (#1717)') printf '%s\n' 'generated-set' ;;
    'an escaped YAML explicit key setting inherited working-directory defaults (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a multiline escaped YAML explicit key setting inherited working-directory defaults (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a verification job inheriting GITHUB_TOKEN (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a verification suite receiving a wrapped package token (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a verification suite receiving a folded wrapped package token (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a verification suite serializing the secrets context (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a verification suite serializing the GitHub context (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a verification suite reading github['\''token'\''] (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a verification suite inheriting a GitHub token through YAML aliases (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release state step writing its token directly into .git/config (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release state step forwarding its token through GITHUB_ENV (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release state step loading repository code through quoted BASH_ENV (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release state step selecting custom shell (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a verify job setting custom shell through quoted defaults (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a workflow enabling Bash xtrace through quoted SHELLOPTS (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a workflow preloading library in credentialed steps (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a workflow adding dynamic loader audit library (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a workflow changing dynamic library search paths (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a workflow logging Git authorization values through curl and Trace2 (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a workflow redirecting Git remote helpers (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a workflow selecting hostile Git config sources (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a workflow overriding Git config parameters (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a lifecycle step that only prints npm rebuild (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release verification job granting contents-write (#1712)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release verification job that continues after failure (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release verification step that continues after failure (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release verification step skipped by its condition (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release verification step replaced with a no-op (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'release verification failure handler removed (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an inline checkout mapping without disabled credentials (#1717)') printf '%s\n' 'generated-set' ;;
    'checkout reference uses case-variant owner and repository (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'checkout reference uses a folded YAML scalar (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'release job hides steps in a flow-style sequence (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'release verifier uses PATH modified by dependency lifecycle scripts (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'release verification PATH capture overrides runner PATH (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'verify job overrides runner PATH (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'release verification PATH is captured after dependency lifecycle code (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an escaped flow-style environment key selects runner PATH (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a tagged flow-style environment key selects Bash startup script (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an explicit-key BASH_ENV mapping selects Bash startup script (#1717)') printf '%s\n' 'generated-set' ;;
    'an inserted verify step writes Bash startup script through GITHUB_ENV (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an existing release-plan step writes Bash startup script through GITHUB_ENV (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an existing release-plan step injects PYTHONPATH (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'the stamped-version step changes generator-declared package directories (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'the version-stamp step changes its command (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'the version-stamp step changes its condition (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'the version-stamp step changes its environment (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'generator provenance alone uses an unsafe or duplicate package directory: --package-dir ../compat (#1717)') printf '%s\n' 'generated-set' ;;
    'generator provenance alone uses an unsafe or duplicate package directory: --only-package-dir compat --only-package-dir compat (#1717)') printf '%s\n' 'generated-set' ;;
    'generator provenance changes the contract-pinned package set (#1717)') printf '%s\n' 'generated-set' ;;
    'credentialed dependency install preloads repository Node code (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'release verifier overrides npm script shell (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'release verifier inherits Bash startup script (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'stamped-version warning text shadowed outside generated header (#862)') printf '%s\n' 'generated-set' ;;
    'a modified release-verification failure diagnostic (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an unrelated release step exposed to private-package auth (#569)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release caller reachable by a push to main') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release caller on a mutable reusable ref') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release caller whose contract_ref drifts from its uses pin') printf '%s\n' 'generated-set' ;;
    'a release caller whose Node version became Renovate-visible') printf '%s\n' 'generated-set workflow-callers' ;;
    'a hand-written release caller with no generator provenance') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a push: trigger hidden in a flow-style on:') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a duplicate YAML-equivalent on trigger key (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a duplicate YAML-equivalent "on" trigger key (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a duplicate YAML-equivalent true trigger key (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a duplicate YAML-equivalent True trigger key (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a duplicate YAML-equivalent TRUE trigger key (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a duplicate YAML-equivalent yes trigger key (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a duplicate YAML-equivalent Yes trigger key (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a duplicate YAML-equivalent YES trigger key (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an aliased release trigger mapping (#1070)') printf '%s\n' 'generated-set' ;;
    'a merged release trigger mapping (#1070)') printf '%s\n' 'generated-set' ;;
    'duplicate quoted and plain workflow_dispatch keys (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a workflow_dispatch value alias (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an anchored workflow_dispatch mapping (#1070)') printf '%s\n' 'generated-set' ;;
    'a flow-style workflow_dispatch input mapping (#1070)') printf '%s\n' 'generated-set' ;;
    'an explicitly tagged workflow_dispatch mapping (#1070)') printf '%s\n' 'generated-set' ;;
    'a duplicate nested dispatch input key (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an unexpected dispatch input (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a changed required dispatch-input shape (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a YAML directive (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a second YAML document (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'malformed nested trigger indentation (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a scalar changed through a trailing comment (#1070)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a malformed top-level release trigger mapping (#1070)') printf '%s\n' 'generated-set' ;;
    'a release caller exposed as a reusable workflow_call') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release caller fired by a release: event') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'an install credential inherited from a job-level env:') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release caller under any other filename (#463, #464)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release-artifact release-plan step writes Bash startup script through GITHUB_ENV (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release-snapshot release-plan step writes Bash startup script through GITHUB_ENV (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release-artifact release-plan step injects PYTHONPATH (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a release-snapshot release-plan step injects PYTHONPATH (#1717)') printf '%s\n' 'generated-set workflow-callers release-workflows renderer' ;;
    'a required member deleted outright') printf '%s\n' 'generated-set' ;;
    'a required member that cannot be read') printf '%s\n' 'generated-set' ;;
    'a required member replaced by a directory') printf '%s\n' 'generated-set' ;;
    'an optional member replaced by dangling symlink') printf '%s\n' 'generated-set' ;;
    'an optional member replaced by a live symlink') printf '%s\n' 'generated-set' ;;
    'a required member emptied to zero bytes') printf '%s\n' 'generated-set' ;;
    'a pin declaration that no longer parses') printf '%s\n' 'generated-set' ;;
    'a member declaring two different pins') printf '%s\n' 'generated-set' ;;
    'a caller header stripped of its pin') printf '%s\n' 'generated-set' ;;
    'the suite itself declaring two different pins') printf '%s\n' 'generated-set' ;;
    'adopter-artifact-unbound-acquisition') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-artifact-private-upper-scope') printf '%s\n' 'generated-set workflow-callers release-workflows' ;;
    'adopter-changelog-remedy') printf '%s\n' 'generated-set' ;;
    'adopter-changelog-remedy-unknown') printf '%s\n' 'generated-set' ;;
    'adopter-remedy-paste-safety') printf '%s\n' 'generated-set' ;;
    'adopter-forged-mode-header') printf '%s\n' 'generated-set' ;;
    'adopter-two-stale-members') printf '%s\n' 'generated-set' ;;
    *) return 2 ;;
  esac
}

changelog_contract_apply_sections() {
  local id="$1" sections="" status=0
  sections="$(changelog_contract_sections_for "$id")" || status=$?
  if [ "$status" -ne 0 ] || [ "$sections" = all ]; then
    unset CHANGELOG_CONTRACT_SECTIONS
    if [ "$status" -ne 0 ]; then
      printf '%s\n' "$id" >>"${CHANGELOG_CONTRACT_UNMAPPED_FILE:-/dev/null}"
    fi
    return 0
  fi
  export CHANGELOG_CONTRACT_SECTIONS="$sections"
}
