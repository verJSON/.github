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

Security shape (ADR 0240, adversarial review of 2026-10-22): a profile other
than the built-in one never lets a public or unclassified event reach
`CI_LANE_FALLBACK` through the lane variables. An explicit caller `runner`
input still wins first, as in every tier of ADR 0033; that is caller policy. The built-in profile keeps its legacy tail
(`CI_LANE_UNTRUSTED || CI_LANE_FALLBACK`) byte-for-byte, because Verjson's
untrusted lane is reconciled as ephemeral (ADR 0147); an adopter's is not
known to be, so it may only use a lane it named as untrusted.
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

# The secretless term precedes every caller-supplied runner (ADR 0234, 0238).
SECRETLESS = "(inputs.secretless-pr || inputs.secretless-trusted-ref)"

MARKER = "runner-lane:"
SHAPES = {
    # An explicit caller `runner` wins; a foreign owner is hosted; a private
    # repository gets the trusted lane; everything else is untrusted.
    "caller-runner": (
        "inputs.runner != '' && fromJSON(inputs.runner)"
        f" || {FOREIGN_OWNER} && '{HOSTED}'"
        f" || github.event.repository.private == true && {_TRUSTED}"
        f" || {CUSTOM_PROFILE} && {_UNTRUSTED_STRICT}"
        f" || {_UNTRUSTED_LEGACY}"
    ),
    # node-ci `build-test`: both secretless modes take the hosted untrusted
    # lane ahead of the caller's `runner` input.
    "secretless-first": (
        f"{SECRETLESS} && {_UNTRUSTED_STRICT}"
        " || inputs.runner != '' && fromJSON(inputs.runner)"
        f" || {FOREIGN_OWNER} && '{HOSTED}'"
        f" || github.event.repository.private == true && {_TRUSTED}"
        f" || {CUSTOM_PROFILE} && {_UNTRUSTED_STRICT}"
        f" || {_UNTRUSTED_LEGACY}"
    ),
}


def expression(shape: str) -> str:
    return SHAPES[shape]


def runs_on_line(shape: str, indent: str) -> str:
    return f"{indent}runs-on: ${{{{ {expression(shape)} }}}}"
