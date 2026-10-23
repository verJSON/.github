"""The one place a reusable workflow's `runs-on` lane expression is spelled.

ADR 0240 separates the portable contract from the installation policy. The
contract is the *shape* of each selector: which term wins, in what order, and
which tier is fail-safe. The installation policy is who the installation is,
named by the organization variable `CI_PROFILE_OWNER`. It defaults to the
built-in `Verjson` profile, so an unset variable resolves exactly as the
literal `'Verjson'` did before.

`runs-on` cannot call an action, so this catalog is a generator input rather
than a runtime job: `scripts/gen-runner-lane.py` stamps each marked `runs-on:`
line from `expression()`, and CI fails on any hand-edited or stale line.

Security shape (ADR 0240, adversarial review of 2026-10-22): non-built-in
profiles never let public or unclassified events reach CI_LANE_FALLBACK
through lane variables. In the normal routing tail, an explicit caller runner
still wins first, as in every tier of ADR 0033. secretless-first adds its
eligibility-gated untrusted lane and literal hosted fallback before that tail.

secretless-first-legacy-tail is a migration shape for node-ci: it adds the
same fail-closed secretless terms while preserving node-ci's current
non-secretless selector byte-for-byte. Do not use it for new callers; the
remaining node-ci profile rollout is tracked by ADR 0240.
"""

from __future__ import annotations

BUILTIN_PROFILE = "Verjson"
HOSTED = "ubuntu-24.04"

# Evaluated in the caller's context: a reusable workflow reads the caller's
# `vars`. Unset or empty falls back to the built-in profile.
PROFILE_OWNER = f"(vars.CI_PROFILE_OWNER || '{BUILTIN_PROFILE}')"
FOREIGN_OWNER = f"github.repository_owner != {PROFILE_OWNER}"
CUSTOM_PROFILE = f"{PROFILE_OWNER} != '{BUILTIN_PROFILE}'"

_HOSTED_JSON = f"'[\"{HOSTED}\"]'"
_TRUSTED = f"fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || {_HOSTED_JSON})"
_UNTRUSTED_STRICT = f"fromJSON(vars.CI_LANE_UNTRUSTED || {_HOSTED_JSON})"
_UNTRUSTED_LEGACY = (
    f"fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || {_HOSTED_JSON})"
)
_CALLER_RUNNER_PROFILE_TAIL = (
    "inputs.runner != '' && fromJSON(inputs.runner)"
    f" || {FOREIGN_OWNER} && '{HOSTED}'"
    f" || github.event.repository.private == true && {_TRUSTED}"
    f" || {CUSTOM_PROFILE} && {_UNTRUSTED_STRICT}"
    f" || {_UNTRUSTED_LEGACY}"
)
_SECRETLESS_LEGACY_TAIL = (
    "inputs.runner != '' && fromJSON(inputs.runner)"
    f" || github.repository_owner != '{BUILTIN_PROFILE}' && '{HOSTED}'"
    f" || github.event.repository.private == true && {_TRUSTED}"
    f" || {_UNTRUSTED_LEGACY}"
)

# The secretless term precedes every caller-supplied runner (ADR 0234, 0238).
SECRETLESS = "(inputs.secretless-pr || inputs.secretless-trusted-ref)"
SECRETLESS_ELIGIBLE_UNTRUSTED = (
    f"{SECRETLESS} && needs.eligibility.result == 'success' && {_UNTRUSTED_STRICT}"
)
SECRETLESS_HOSTED_FALLBACK = f"{SECRETLESS} && '{HOSTED}'"

MARKER = "runner-lane:"
SHAPES = {
    # An explicit caller `runner` wins; a foreign owner is hosted; a private
    # repository gets the trusted lane; everything else is untrusted.
    "caller-runner": _CALLER_RUNNER_PROFILE_TAIL,
    # Profile-aware secretless shape for the remaining ADR 0240 rollout.
    "secretless-first": (
        f"{SECRETLESS_ELIGIBLE_UNTRUSTED}"
        f" || {SECRETLESS_HOSTED_FALLBACK}"
        f" || {_CALLER_RUNNER_PROFILE_TAIL}"
    ),
    # node-ci `build-test` keeps its current non-secretless policy until its
    # profile-aware tail is rolled out separately under ADR 0240.
    "secretless-first-legacy-tail": (
        f"{SECRETLESS_ELIGIBLE_UNTRUSTED}"
        f" || {SECRETLESS_HOSTED_FALLBACK}"
        f" || {_SECRETLESS_LEGACY_TAIL}"
    ),
}


def expression(shape: str) -> str:
    return SHAPES[shape]


def runs_on_line(shape: str, indent: str) -> str:
    return f"{indent}runs-on: ${{{{ {expression(shape)} }}}}"
