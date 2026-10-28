"""Canonical reusable-workflow runner-lane expressions (ADR 0240, 0234, 0238).

The built-in Verjson profile is the default; CI_PROFILE_OWNER selects a custom
profile. Custom profiles are fail-closed for public and unclassified events:
their untrusted lane never falls through to CI_LANE_FALLBACK. The built-in
profile keeps its historical fallback. The secretless-first shape adds a
fail-closed, eligibility-gated untrusted prefix and a literal hosted fallback
ahead of the profile-aware caller-runner tail, where an explicit caller runner
still wins first under ADR 0033.

secretless-first-legacy-tail remains a migration-compatibility shape that
preserves the pre-profile built-in routing tail. Current Node CI uses
secretless-first; new callers should use that profile-aware shape.
"""

from __future__ import annotations

BUILTIN_PROFILE = "Verjson"
HOSTED = "ubuntu-24.04"
PUBLIC_PR_EVENTS = (
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "merge_group",
    "pull_request_target",
)
PUBLIC_PR_HOSTED = (
    "(" + " || ".join(f"github.event_name == '{event}'" for event in PUBLIC_PR_EVENTS) + ") && "
    f"github.event.repository.private != true && '{HOSTED}'"
)

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
    # Jobs that deliberately use only the organization's untrusted lane.
    "untrusted-lane": (
        f"{PUBLIC_PR_HOSTED} || ({_UNTRUSTED_STRICT})"
    ),
    # Explicit caller override, foreign-owner hosted default, then owner
    # organization's trusted/private and strict-or-legacy untrusted routes.
    "caller-runner": f"{PUBLIC_PR_HOSTED} || ({_CALLER_RUNNER_PROFILE_TAIL})",
    # Profile-aware secretless shape for the remaining ADR 0240 rollout.
    "secretless-first": (
        f"{PUBLIC_PR_HOSTED} || ({SECRETLESS_ELIGIBLE_UNTRUSTED}"
        f" || {SECRETLESS_HOSTED_FALLBACK}"
        f" || {_CALLER_RUNNER_PROFILE_TAIL})"
    ),
    # Compatibility shape for callers still on the pre-profile routing tail.
    "secretless-first-legacy-tail": (
        f"{PUBLIC_PR_HOSTED} || ({SECRETLESS_ELIGIBLE_UNTRUSTED}"
        f" || {SECRETLESS_HOSTED_FALLBACK}"
        f" || {_SECRETLESS_LEGACY_TAIL})"
    ),
}


def expression(shape: str) -> str:
    return SHAPES[shape]


def runs_on_line(shape: str, indent: str) -> str:
    return f"{indent}runs-on: ${{{{ {expression(shape)} }}}}"
