#!/usr/bin/env python3
"""Refuse literal GitHub-hosted runner selectors and unbounded hosted jobs.

    hosted-selector-policy.py --visibility public|private [--sanctioned NAME]... <workflow-dir>
    hosted-selector-policy.py --consumer-policy <workflow-dir>

Exit 0 is "scanned and clean", 1 is "policy violation", 2 is "undetermined".
The three are distinct because a sweep that scans nothing must not look like a
sweep that found nothing — the convention scripts/runner-selector-health.sh uses,
and the reason it exits 2 rather than 0 when it cannot decide.

Verjson/.github#814, ADR 0103.

WHY THIS IS A REAL YAML PARSER (#818 review)
--------------------------------------------
The first implementation matched `runs-on:` with line-oriented shell patterns.
Two review passes found five parser-level false negatives in shapes GitHub
accepts and the patterns did not: a TAB/IFS field collapse, a matrix indirection,
a flow-style job body (`mac: {runs-on: macos-latest, ...}`), a flow-mapping
`jobs:`, and `runs-on : macos-15` with a space before the colon. Each returned
exit 0 on a genuinely metered job.

That rate is the argument. This script is the single control between the
organization and metered spend, and a false negative here is silent and costs
money, so the parser must be one that understands the language rather than one
that recognises the shapes someone thought of. Every remaining unknown evasion in
the hand-rolled parser was an unknown; there is no such class left here, because
anything PyYAML cannot resolve is refused rather than guessed at.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys

import yaml

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

# R1 (Tier A) — the metered SKU families, refused outright.
#
# No allowlist, no parameter, no environment override, and deliberately NOT keyed
# on repository visibility. Visibility is a mutable organization-settings fact,
# and encoding it in workflow YAML is exactly the "stale by construction" defect
# ADR 0033 diagnosed and ADR 0040 watched regrow four times (#175, #182, #192,
# #203). A public repository flipped private turns a free `macos-latest` job into
# a 10x metered one with no commit, no review, and no signal.
#
# Preceded by a non-word character so `CI_LANE_TRUSTED_MACOS` — the lane
# variable NAME, governed by R5 — is not read as a macOS selector.
METERED_FAMILY = re.compile(r"(?:^|[^A-Za-z0-9_])(?:macos|windows)-[A-Za-z0-9]", re.I)
METERED_LATEST = re.compile(
    r"(?:^|[^A-Za-z0-9_])(?:ubuntu|macos|windows)-latest", re.I
)
LINUX_HOSTED_LITERAL = re.compile(r"(?:^|[^A-Za-z0-9_])ubuntu-[A-Za-z0-9]", re.I)

# The OS-scoped lanes of ADR 0103. Repository variables on the one desktop
# repository, never organization variables, so a workflow anywhere else resolves
# them to empty.
OS_LANE = re.compile(r"vars\.CI_LANE_TRUSTED_(?:MACOS|WINDOWS)")
OS_LANE_TEXT = re.compile(r"CI_LANE_TRUSTED_(?:MACOS|WINDOWS)")

# Any Verjson placement variable EXCEPT the OS lanes, which are governed by the
# inverted rule. Broadened from LANE_(TRUSTED|UNTRUSTED|PRIVILEGED) after review:
# a chain on `vars.CI_RUNNER_FASTLANE` or `CI_RUNNER_OVERFLOW` with no
# tail hits the #401 forever-queue mode just as squarely, and was unguarded.
PLACEMENT_VARIABLE = re.compile(
    r"vars\.CI_(?:LANE|RUNNER)_(?!TRUSTED_MACOS|TRUSTED_WINDOWS)[A-Z_]+"
)
TERMINAL_LANDING = re.compile(r"vars\.CI_LANE_FALLBACK|ubuntu-[A-Za-z0-9]", re.I)

# R3's ceiling. Not a presence check: `timeout-minutes: 360` satisfies "has a
# timeout" while being exactly the runaway the requirement exists to prevent —
# six hours at macOS's 10x multiplier is up to 3,600 billable minutes from one
# hung step. The lanes are configured at 45; 60 is the reviewed ceiling.
HOSTED_TIMEOUT_CEILING = 60

# R5's sanctioned desktop-release path, as an EXPLICIT constant rather than an
# implicit absence. In `Verjson/.github` the set is empty and that is the whole
# rule: no workflow here may name an OS lane variable at all. `.github`
# deliberately does not grow a cross-platform release workflow — ADR 0060 retired
# `node-release.yml`, and the canonical path is a dispatched
# `changelog-release.yml` that builds nothing.
#
# `--sanctioned` appends to this set, which is how #815 points the same script at
# a consumer checkout whose desktop-release workflow is legitimately sanctioned.
# That is a parameter and not a hole in the guarantee: R1 and R2 take no exception
# from it, and the OS lane variables are repository-scoped, so a repository that
# sanctions a filename it does not own still resolves them to empty. R5 is a
# legibility rule — one grep that answers "who can spend hosted minutes" — layered
# on top of that containment, not the containment itself. It is deliberately NOT
# `github.repository == 'Verjson/AiB'`: hardcoding the consumer's name here would
# make a second desktop repository a pull request in this repository instead of a
# variable edit, which is the property ADR 0041 exists to preserve.
SANCTIONED_OS_LANE_WORKFLOWS: tuple[str, ...] = ()

# A workflow file this large is not a workflow. The cap bounds the obvious
# resource exhaustion, but note what it does NOT bound: a YAML alias bomb is
# roughly a kilobyte on disk and expands without limit, so the size cap would let
# it straight through. Anchors are refused separately below, which is the guard
# that actually addresses that class.
MAX_WORKFLOW_BYTES = 1024 * 1024


class Undetermined(Exception):
    """The sweep could not decide. Never rendered as clean."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class _LinedDict(dict):
    """A mapping that remembers the source line of each of its keys."""

    __slots__ = ("lines",)


class PolicyLoader(yaml.SafeLoader):
    """SafeLoader, plus key line numbers, minus anchors.

    `SafeLoader` is not decoration: #815 points this script at consumer
    checkouts, so it parses workflow files authored by an untrusted pull request.
    `yaml.load` with the default loader would let a tag directive construct
    arbitrary Python objects on a runner. Only `safe_load` semantics are used
    here, and this subclass narrows them further rather than widening them.
    """


def _construct_lined_map(loader: PolicyLoader, node: yaml.MappingNode):
    data = _LinedDict()
    data.lines = {}
    yield data
    data.update(loader.construct_mapping(node))
    data.lines = {
        key.value: key.start_mark.line + 1
        for key, _ in node.value
        if isinstance(key, yaml.ScalarNode)
    }


PolicyLoader.add_constructor("tag:yaml.org,2002:map", _construct_lined_map)


def _refuse_alias(self, node):  # noqa: ANN001 - PyYAML hook signature
    raise yaml.YAMLError(
        "YAML anchors and aliases are refused: GitHub Actions does not support "
        "them in workflows, and their expansion is unbounded"
    )


PolicyLoader.compose_node_original = yaml.SafeLoader.compose_node


def _compose_node(self, parent, index):  # noqa: ANN001 - PyYAML hook signature
    if self.check_event(yaml.events.AliasEvent):
        _refuse_alias(self, None)
    return PolicyLoader.compose_node_original(self, parent, index)


PolicyLoader.compose_node = _compose_node


def load_workflow(path: str) -> _LinedDict:
    """Return the single workflow document, or raise Undetermined.

    Every anomaly below is refused rather than skipped. "I could not read this
    file" is not "this file is clean", and the whole value of this check is that
    the difference is visible in the exit code.
    """
    try:
        size = os.path.getsize(path)
    except OSError as error:
        raise Undetermined(f"{path}: cannot stat: {error}") from error
    if size > MAX_WORKFLOW_BYTES:
        raise Undetermined(
            f"{path}: {size} bytes exceeds the {MAX_WORKFLOW_BYTES}-byte ceiling"
        )
    try:
        with open(path, encoding="utf-8") as stream:
            documents = list(yaml.load_all(stream, Loader=PolicyLoader))
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as error:
        detail = " ".join(str(error).split())
        raise Undetermined(f"{path}: cannot parse: {detail}") from error

    if len(documents) != 1:
        # GitHub reads the first document. A reader that silently takes one of
        # several is guessing, and the metered selector can live in the other.
        raise Undetermined(
            f"{path}: expected exactly one YAML document, found {len(documents)}"
        )
    document = documents[0]
    if not isinstance(document, dict):
        raise Undetermined(f"{path}: top level is not a mapping")
    return document


def extract_jobs(path: str, document: dict) -> list[tuple[str, dict, int]]:
    """Return (name, body, line) per job, or raise Undetermined.

    Per FILE, deliberately. A global "did anything parse" counter only fires when
    NOTHING parses, so one unreadable file among readable siblings was silently
    ignored — the amplifier that turned an unscanned file into exit 0.
    """
    if "jobs" not in document:
        raise Undetermined(f"{path}: no 'jobs' key")
    jobs = document["jobs"]
    if not isinstance(jobs, dict):
        raise Undetermined(f"{path}: 'jobs' is not a mapping")
    if not jobs:
        raise Undetermined(f"{path}: 'jobs' is empty")
    lines = getattr(jobs, "lines", {})
    records = []
    for name, body in jobs.items():
        if not isinstance(body, dict):
            raise Undetermined(f"{path}: job '{name}' is not a mapping")
        records.append((str(name), body, lines.get(name, 0)))
    return records


def flatten(value) -> str:
    """Render any YAML value as searchable text.

    Structural rather than textual, so the shape a selector is written in stops
    mattering: `macos-15`, `[macos-15]`, a block sequence, and
    `{group: desktop, labels: [macos-15]}` — a real GitHub runner-group selector —
    all reduce to the same text.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(
            f"{flatten(key)} {flatten(item)}" for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return " ".join(flatten(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


EXPRESSION = re.compile(r"\$\{\{.*?\}\}", re.S)
FULL_EXPRESSION = re.compile(r"^\s*\$\{\{(.*)\}\}\s*$", re.S)
FUNCTION_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
QUOTED_EXPRESSION_STRING = re.compile(r"'(?:''|[^'])*'")
BRACKET_DEREFERENCE = re.compile(
    r"\b(vars|inputs|matrix|github|needs)\[(?:'([^']+)'|\"([^\"]+)\")\]"
)
CANONICAL_CONTAINER_CANDIDATE = "container-candidate.yml"
CANONICAL_CONTAINER_CANDIDATE_EXPRESSION = (
    "github.event_name != 'pull_request' && inputs.runner != '' && fromJSON(inputs.runner) "
    "|| github.repository_owner != 'Verjson' && 'ubuntu-24.04' "
    "|| github.event.repository.private == true && fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') "
    "|| fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')"
)
CANONICAL_CONTAINER_CANDIDATE_SELECTOR = "${{ " + CANONICAL_CONTAINER_CANDIDATE_EXPRESSION + " }}"

# Complete routing expressions reviewed by the organization contract. This is
# deliberately a full-expression allowlist, not a list of blessed reference
# names: accepting `inputs.runner` because one guarded canonical expression uses
# that input would also accept `${{ fromJSON(inputs.runner) }}` by itself. New
# routing shapes must therefore become visible code changes here.
REVIEWED_SELECTOR_EXPRESSIONS = frozenset(
    " ".join(spelling.split())
    for expression in (
        'fromJSON(github.repository_owner == \'Verjson\' && (vars.CI_RUNNER_DEFAULT || \'["self-hosted","general"]\') || \'["ubuntu-24.04"]\')',
        "(github.repository_owner != 'Verjson' || inputs.github-hosted-runner) && 'ubuntu-24.04' || github.event.repository.private == true && fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') || github.event.repository.visibility == 'public' && fromJSON(vars.CI_RUNNER_FASTLANE || vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') || fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "fromJSON(vars.CI_LANE_PRIVILEGED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]')",
        "fromJSON(vars.CI_RUNNER_FASTLANE || '[\"ubuntu-24.04\"]')",
        "fromJSON(vars.CI_RUNNER_FASTLANE || vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "github.repository_owner != 'Verjson' && 'ubuntu-24.04' || fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "github.repository_owner != 'Verjson' && 'ubuntu-24.04' || github.event.repository.private == true && fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') || fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "github.repository_owner != 'Verjson' && inputs.runner_labels && fromJSON(inputs.runner_labels) || github.repository_owner != 'Verjson' && 'ubuntu-24.04' || github.event.repository.visibility == 'public' && 'ubuntu-24.04' || fromJSON(inputs.privileged_lane)",
        "github.repository_owner == 'Verjson' && fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') || 'ubuntu-24.04'",
        "github.repository_owner == 'Verjson' && fromJSON(vars.CI_RUNNER_FASTLANE || vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') || 'ubuntu-24.04'",
        "inputs.runner != '' && fromJSON(inputs.runner) || github.repository_owner != 'Verjson' && 'ubuntu-24.04' || github.event.repository.private == true && fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') || fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "inputs.runner != '' && fromJSON(inputs.runner) || github.repository_owner != 'Verjson' && 'ubuntu-24.04' || github.event.repository.private == true && fromJSON(vars.CI_RUNNER_OVERFLOW || vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') || fromJSON(vars.CI_RUNNER_OVERFLOW || vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "inputs.runner_labels && fromJSON(inputs.runner_labels) || github.repository_owner != 'Verjson' && 'ubuntu-24.04' || github.event.repository.private == false && fromJSON(vars.CI_RUNNER_FASTLANE || '[\"ubuntu-24.04\"]') || fromJSON(vars.CI_RUNNER_OVERFLOW || vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "inputs.runner_labels && fromJSON(inputs.runner_labels) || github.repository_owner != 'Verjson' && 'ubuntu-24.04' || needs.preflight.outputs.target_private == 'false' && fromJSON(vars.CI_RUNNER_FASTLANE || '[\"ubuntu-24.04\"]') || fromJSON(vars.CI_LANE_PRIVILEGED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "inputs.runner_labels && fromJSON(inputs.runner_labels) || github.repository_owner != 'Verjson' && 'ubuntu-24.04' || needs.preflight.outputs.target_private == 'false' && fromJSON(vars.CI_RUNNER_FASTLANE || '[\"ubuntu-24.04\"]') || fromJSON(vars.CI_RUNNER_OVERFLOW || vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "inputs.secretless-pr && fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') || inputs.runner != '' && fromJSON(inputs.runner) || github.repository_owner != 'Verjson' && 'ubuntu-24.04' || github.event.repository.private == true && fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') || fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        # Known negative fixtures stay inside the grammar so R3/R4 can return a
        # definite policy violation instead of hiding behind undetermined.
        "fromJSON(vars.CI_LANE_TRUSTED)",
        "fromJSON(vars.CI_RUNNER_FASTLANE)",
        "fromJSON(vars.CI_RUNNER_OVERFLOW)",
        "fromJSON(vars.CI_LANE_TRUSTED_MACOS)",
        "fromJSON(vars.CI_LANE_TRUSTED_WINDOWS)",
        "fromJSON(vars.CI_LANE_TRUSTED_MACOS || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')",
        "fromJSON(vars.CI_LANE_TRUSTED_WINDOWS || vars.CI_LANE_FALLBACK)",
        "matrix.os",
        "fromJSON(matrix.lane)",
        # Static matrix source expressions are checked with the strategy block
        # whenever runs-on dereferences matrix.*.
        "vars.CI_LANE_TRUSTED",
        "vars.CI_LANE_TRUSTED_MACOS",
        "vars.CI_LANE_TRUSTED_WINDOWS",
        "vars.CI_LANE_TRUSTED_MACOS || vars.CI_LANE_FALLBACK",
    )
    for spelling in (expression, expression.replace("'Verjson'", "'verJSON'"))
)

# The only job-level reusable-workflow inputs in the organization contract that
# select the called workflow's runner. These are NOT arbitrary `with:` keys:
# step inputs and prose-bearing reusable inputs must not become policy text.
REUSABLE_RUNNER_INPUTS = ("runner", "runner_labels")

FASTLANE_REUSABLE_INPUT_EXPRESSION = (
    "vars.CI_RUNNER_FASTLANE || '[\"ubuntu-24.04\"]'"
)
POLICY_CONTRACT_SHA_ENV = "VERJSON_HOSTED_SELECTOR_POLICY_SHA"

# A reusable input receives selector JSON rather than a resolved runs-on value,
# so its reviewed grammar is intentionally smaller than runs-on's. The first
# expression is emitted by gen-changelog-caller.sh. Static matrix references are
# safe only because check_reusable_runner_inputs folds the complete strategy
# source into the metered-family verdict.
REVIEWED_REUSABLE_INPUT_EXPRESSIONS = frozenset(
    " ".join(spelling.split())
    for expression in (
        "github.repository_owner == 'Verjson' && (vars.CI_RUNNER_DEFAULT || '[\"self-hosted\",\"general\"]') || '[\"ubuntu-24.04\"]'",
        "matrix.os",
        "matrix.runner",
        "matrix.runner_labels",
    )
    for spelling in (expression, expression.replace("'Verjson'", "'verJSON'"))
)


def reviewed_reusable_input_expressions(
    body: dict,
    input_name: str,
    inputs: dict,
    guarded_fastlane_caller: bool,
) -> frozenset[str]:
    contract_sha = os.environ.get(POLICY_CONTRACT_SHA_ENV, "")
    if (
        guarded_fastlane_caller
        and input_name == "runner"
        and re.fullmatch(r"[0-9a-f]{40}", contract_sha)
        and body.get("uses")
        == f"Verjson/.github/.github/workflows/node-ci.yml@{contract_sha}"
        and " ".join(flatten(inputs.get("secretless-pr")).split())
        == "${{ github.event_name == 'pull_request' }}"
    ):
        return REVIEWED_REUSABLE_INPUT_EXPRESSIONS | {
            FASTLANE_REUSABLE_INPUT_EXPRESSION
        }
    return REVIEWED_REUSABLE_INPUT_EXPRESSIONS


def is_guarded_fastlane_caller(document: dict) -> bool:
    has_text_key = "on" in document
    has_yaml11_key = True in document
    if has_text_key == has_yaml11_key:
        return False
    trigger = document.get("on") if has_text_key else document.get(True)
    if not isinstance(trigger, dict) or set(trigger) != {"push", "pull_request"}:
        return False
    return (
        trigger["pull_request"] in (None, {})
        and trigger["push"] == {"branches": ["main"]}
    )
CANONICAL_RUNNER_CANARY = "runner-canary.yml"
CANONICAL_RUNNER_CANARY_SELECTOR = [
    "self-hosted",
    "${{ inputs.runner_label }}",
]
def is_canonical_changelog_pr_gate(document: dict) -> bool:
    # This PR-authored test must stay on a disposable hosted runner. Match every
    # executable field, not a filename or a forgeable generated-file comment.
    expected = {
        "name": "changelog contract",
        True: {"pull_request": None},
        "permissions": {"contents": "read"},
        "jobs": {
            "changelog-contract": {
                "runs-on": "ubuntu-24.04",
                "timeout-minutes": 10,
                "steps": [
                    {
                        "name": "Check out the pull request under validation",
                        "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                        "with": {"persist-credentials": False},
                    },
                    {
                        "name": "Prepare job-scoped changelog tool cache",
                        "run": 'echo "VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools" >> "$GITHUB_ENV"',
                    },
                    {
                        "name": "Validate the changelog contract",
                        "run": "bash scripts/changelog-contract.test.sh",
                    },
                ],
            },
        },
    }

    return (
        document == expected
        and any(key is True for key in document)
        and type(document["jobs"]["changelog-contract"]["timeout-minutes"]) is int
        and type(document["jobs"]["changelog-contract"]["steps"][0]["with"]["persist-credentials"]) is bool
    )


def canonical_runner_canary_jobs(
    report: Report,
    path: str,
    document: dict,
    jobs: list[tuple[str, dict, int]],
    consumer_policy: bool,
    canonical_path: str | None,
) -> frozenset[str]:
    """Return the one narrowly reviewed direct-input selector job, if exact."""
    if (
        consumer_policy
        or canonical_path is None
        or os.path.abspath(path) != canonical_path
    ):
        return frozenset()

    has_text_key = "on" in document
    has_yaml11_key = True in document
    trigger = document.get("on") if has_text_key else document.get(True)
    trigger_keys = set(trigger) if isinstance(trigger, dict) else set()
    valid_trigger = (
        has_text_key != has_yaml11_key
        and trigger_keys == {"workflow_dispatch"}
        and isinstance(trigger["workflow_dispatch"], (dict, type(None)))
    )
    if not valid_trigger:
        report.violation(
            path,
            getattr(document, "lines", {}).get("on", 0),
            "canonical runner canary must be workflow_dispatch-only",
        )

    if len(jobs) != 1 or jobs[0][0] != "canary":
        report.violation(path, 0, "canonical runner canary must contain only the canary job")
        return frozenset()
    name, body, line = jobs[0]
    if body.get("runs-on") != CANONICAL_RUNNER_CANARY_SELECTOR:
        report.violation(
            path,
            getattr(body, "lines", {}).get("runs-on", line),
            "canonical runner canary runs-on must exactly equal "
            "[self-hosted, ${{ inputs.runner_label }}]",
        )
        return frozenset()
    return frozenset({name}) if valid_trigger else frozenset()


def canonical_policy_workflow_path(
    workflow_dir: str, consumer_policy: bool, workflow_name: str
) -> str | None:
    """Resolve authority from the policy checkout, never the process cwd."""
    if consumer_policy:
        return None
    script_path = os.path.abspath(__file__)
    try:
        script_mode = os.lstat(script_path).st_mode
    except OSError as error:
        raise Undetermined(f"{script_path}: cannot lstat policy script: {error}") from error
    if not stat.S_ISREG(script_mode) or os.path.realpath(script_path) != script_path:
        raise Undetermined(
            f"{script_path}: policy script must be a regular non-symlink file at its checked-in location"
        )

    git_environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", ""),
    }

    def git(command: list[str], directory: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", directory, *command],
                capture_output=True,
                check=False,
                env=git_environment,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Undetermined(f"{directory}: cannot verify Git worktree identity: {error}") from error

    policy_root_result = git(["rev-parse", "--show-toplevel"], os.path.dirname(script_path))
    if policy_root_result.returncode != 0:
        raise Undetermined(f"{script_path}: policy script is not in a verified Git worktree")
    policy_root = os.path.realpath(policy_root_result.stdout.strip())
    direct_script = os.path.join("scripts", "ci-gate", "hosted-selector-policy.py")
    relative_policy_script = os.path.relpath(script_path, policy_root)
    if relative_policy_script != direct_script:
        raise Undetermined(
            f"{script_path}: policy script is not at an allowed checked-in location"
        )
    tracked_result = git(
        ["--literal-pathspecs", "ls-files", "--error-unmatch", "--", direct_script],
        policy_root,
    )
    if tracked_result.returncode != 0:
        raise Undetermined(f"{script_path}: policy script is not tracked by its Git worktree")

    if os.path.basename(policy_root) == ".verjson-actionlint-policy":
        repository_root = os.path.dirname(policy_root)
        repository_root_result = git(["rev-parse", "--show-toplevel"], repository_root)
        if (
            repository_root_result.returncode != 0
            or os.path.realpath(repository_root_result.stdout.strip()) != repository_root
        ):
            raise Undetermined(
                f"{script_path}: nested policy checkout has no verified outer Git worktree"
            )
    else:
        repository_root = policy_root
    expected_dir = os.path.join(repository_root, ".github", "workflows")
    supplied_dir = os.path.abspath(workflow_dir)
    if supplied_dir != expected_dir or os.path.realpath(supplied_dir) != expected_dir:
        return None
    candidate = os.path.join(expected_dir, workflow_name)
    if os.path.lexists(candidate):
        try:
            mode = os.lstat(candidate).st_mode
        except OSError as error:
            raise Undetermined(f"{candidate}: cannot lstat canonical workflow: {error}") from error
        if not stat.S_ISREG(mode):
            raise Undetermined(
                f"{candidate}: canonical workflow must be a regular non-symlink file"
            )
        tracked_candidate = git(
            [
                "--literal-pathspecs",
                "ls-files",
                "--error-unmatch",
                "--",
                os.path.join(".github", "workflows", workflow_name),
            ],
            repository_root,
        )
        if tracked_candidate.returncode != 0:
            raise Undetermined(
                f"{candidate}: canonical workflow is not tracked by its Git worktree"
            )
    return candidate


def canonical_runner_canary_path(workflow_dir: str, consumer_policy: bool) -> str | None:
    return canonical_policy_workflow_path(
        workflow_dir, consumer_policy, CANONICAL_RUNNER_CANARY
    )


def canonical_container_candidate_path(workflow_dir: str, consumer_policy: bool) -> str | None:
    return canonical_policy_workflow_path(
        workflow_dir, consumer_policy, CANONICAL_CONTAINER_CANDIDATE
    )


def canonical_container_candidate_jobs(
    report: Report,
    path: str,
    jobs: list[tuple[str, dict, int]],
    consumer_policy: bool,
    canonical_path: str | None,
) -> frozenset[str]:
    if consumer_policy or canonical_path is None or os.path.abspath(path) != canonical_path:
        return frozenset()
    expected = {"prepare", "skip-private-node-build", "pull-request-build"}
    if {name for name, _, _ in jobs} != expected:
        report.violation(path, 0, "canonical candidate workflow job set changed")
        return frozenset()
    valid = True
    for name, body, line in jobs:
        if body.get("runs-on") != CANONICAL_CONTAINER_CANDIDATE_SELECTOR:
            report.violation(
                path,
                getattr(body, "lines", {}).get("runs-on", line),
                f"canonical candidate job '{name}' must retain the pull-request runner guard",
            )
            valid = False
    return frozenset(expected) if valid else frozenset()


def normalize_dereferences(text: str) -> str:
    """Normalize both GitHub property syntaxes before applying policy rules."""
    return BRACKET_DEREFERENCE.sub(
        lambda match: f"{match.group(1)}.{match.group(2) or match.group(3)}",
        text,
    )


def _selector_strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _selector_strings(key)
            yield from _selector_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _selector_strings(item)
    elif isinstance(value, str):
        yield value


def validate_selector_expressions(
    value,
    reviewed_expressions: frozenset[str] = REVIEWED_SELECTOR_EXPRESSIONS,
) -> None:
    """Refuse selector expressions that construct labels dynamically.

    The accepted language is intentionally small: references, fixed quoted
    literals, boolean comparisons/chains, parentheses, and one-argument
    ``fromJSON`` calls. ``format``/``join`` and mixed literal-expression strings
    can synthesize a hosted label whose complete value never appears in source.
    """
    for text in _selector_strings(value):
        if "${{" not in text and "}}" not in text:
            continue
        match = FULL_EXPRESSION.fullmatch(text)
        if match is None:
            raise Undetermined(
                "selector contains a mixed or malformed expression"
            )
        expression = " ".join(normalize_dereferences(match.group(1)).split())
        scrubbed = QUOTED_EXPRESSION_STRING.sub("LITERAL", expression)
        functions = FUNCTION_CALL.findall(scrubbed)
        unsupported = sorted({name for name in functions if name != "fromJSON"})
        if unsupported:
            raise Undetermined(
                "selector uses unsupported construction function(s): "
                + ", ".join(unsupported)
            )
        # Brackets left after normalizing supported property dereferences, or
        # arithmetic/list punctuation outside a fixed string, are an expression
        # shape this policy does not evaluate. Commas would also imply more than
        # fromJSON's single accepted argument.
        if re.search(r"[\[\]+*/%,?:]", scrubbed):
            raise Undetermined(
                "selector uses unsupported dynamic expression syntax"
            )
        if expression not in reviewed_expressions:
            raise Undetermined(
                "selector uses an unreviewed routing expression source or shape"
            )


def outside_expressions(text: str) -> str:
    """Drop every `${{ … }}` span, leaving the literal text around them."""
    return EXPRESSION.sub(" ", text)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class Report:
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.anomalies: list[str] = []

    def violation(self, path: str, line: int, message: str) -> None:
        self.violations.append(f"{path}:{line}: {message}")

    def anomaly(self, message: str) -> None:
        self.anomalies.append(f"UNDETERMINED: {message}")


def check_reusable_runner_inputs(
    report: Report,
    path: str,
    name: str,
    body: dict,
    line: int,
    guarded_fastlane_caller: bool,
) -> None:
    """Apply R1 only to canonical job-level runner-routing inputs.

    Reusable jobs have no runs-on of their own, but canonical workflows consume
    `with.runner` or `with.runner_labels` and route on the supplied value. Scan
    exactly those names at job level; scanning every `with` value would turn
    descriptions and step inputs into false placement signals.
    """
    if "uses" not in body or "with" not in body:
        return

    body_lines = getattr(body, "lines", {})
    with_line = body_lines.get("with", line)
    inputs = body["with"]
    if not isinstance(inputs, dict):
        report.anomaly(
            f"{path}:{with_line}: reusable job '{name}': 'with' is not a mapping"
        )
        return

    input_lines = getattr(inputs, "lines", {})
    for input_name in REUSABLE_RUNNER_INPUTS:
        if input_name not in inputs:
            continue
        input_line = input_lines.get(input_name, with_line)
        value = inputs[input_name]
        if not isinstance(value, str):
            report.anomaly(
                f"{path}:{input_line}: reusable job '{name}' input "
                f"'{input_name}' is not a string selector"
            )
            continue

        raw_selector = flatten(value)
        normalized_selector = normalize_dereferences(raw_selector)
        selector_values = [value]
        if "matrix." in normalized_selector:
            selector_values.append(body.get("strategy"))
        try:
            for selector_value in selector_values:
                validate_selector_expressions(
                    selector_value,
                    reviewed_reusable_input_expressions(
                        body,
                        input_name,
                        inputs,
                        guarded_fastlane_caller,
                    ),
                )
        except Undetermined as error:
            report.anomaly(
                f"{path}:{input_line}: reusable job '{name}' input "
                f"'{input_name}': {error}"
            )
            continue

        selector = normalized_selector
        if "matrix." in normalized_selector:
            selector = normalize_dereferences(
                f"{normalized_selector} {flatten(body.get('strategy'))}"
            )
        if METERED_FAMILY.search(selector):
            report.violation(
                path,
                input_line,
                f"R1 metered hosted runner family in reusable job '{name}' "
                f"input '{input_name}': {raw_selector}",
            )
        if LINUX_HOSTED_LITERAL.search(outside_expressions(selector)):
            report.violation(
                path,
                input_line,
                f"TierB literal Linux hosted selector in reusable job '{name}' "
                f"input '{input_name}' — use a VERJSON lane variable: {raw_selector}",
            )


def check_job(report: Report, path: str, name: str, body: dict, line: int,
              visibility: str, consumer_policy: bool = False,
              canonical_canary: bool = False,
              canonical_container_candidate: bool = False,
              canonical_changelog_pr_gate: bool = False,
              guarded_fastlane_caller: bool = False) -> None:
    if consumer_policy:
        check_reusable_runner_inputs(
            report,
            path,
            name,
            body,
            line,
            guarded_fastlane_caller,
        )
    if "runs-on" not in body:
        # A job-level `uses:` calls a reusable workflow and declares no runner
        # of its own. Consumer mode checks the two canonical pass-through inputs
        # above; full local policy remains scoped to this repository's runs-on.
        return
    lines = getattr(body, "lines", {})
    runs_on_line = lines.get("runs-on", line)
    timeout_line = lines.get("timeout-minutes", runs_on_line)

    selector_values = [body["runs-on"]]
    raw_runs_on = flatten(body["runs-on"])
    if "matrix." in normalize_dereferences(raw_runs_on):
        selector_values.append(body.get("strategy"))
    try:
        if canonical_canary:
            reviewed_expressions = frozenset({"inputs.runner_label"})
        elif canonical_container_candidate:
            reviewed_expressions = REVIEWED_SELECTOR_EXPRESSIONS | frozenset(
                {CANONICAL_CONTAINER_CANDIDATE_EXPRESSION}
            )
        else:
            reviewed_expressions = REVIEWED_SELECTOR_EXPRESSIONS
        for value in selector_values:
            validate_selector_expressions(value, reviewed_expressions)
    except Undetermined as error:
        report.anomaly(f"{path}:{runs_on_line}: job '{name}': {error}")
        return

    runs_on = normalize_dereferences(raw_runs_on)

    # EVERY rule below judges `selector`, never `runs_on`. That divergence
    # produced a live false negative once already: with the lane variable in
    # `strategy.matrix` — the shape #810 proposes and AiB will adopt — the
    # OS-lane test read `runs-on` alone, matched nothing, and skipped R3 and R4
    # entirely on the one workflow they were written for.
    #
    # Known imprecision, chosen deliberately: the WHOLE strategy block is folded
    # in, not only the key the selector references, so a metered word in an
    # unreferenced cross-compile key is refused too. Resolving the reference
    # precisely means re-implementing matrix semantics, and a bug there would
    # fail OPEN. A false positive costs an argument and is fixed by renaming a
    # key; a false negative costs money. Pinned by `matrix-unreferenced-key`.
    selector = runs_on
    if "matrix." in runs_on:
        selector = normalize_dereferences(
            f"{runs_on} {flatten(body.get('strategy'))}"
        )

    if METERED_FAMILY.search(selector):
        report.violation(
            path, runs_on_line,
            f"R1 metered hosted runner family in job '{name}' runs-on: {runs_on}",
        )

    # Consumer mode exports the visibility-independent selector rules: metered
    # families and literal Linux hosted selectors. R3-R6 govern the one
    # sanctioned desktop release path rather than ordinary package consumers.
    # Parsing and job extraction still fail closed, expression construction
    # stays inside the reviewed grammar, and matrix sources are still folded in.
    if consumer_policy:
        if not canonical_changelog_pr_gate and LINUX_HOSTED_LITERAL.search(outside_expressions(selector)):
            report.violation(
                path,
                runs_on_line,
                f"TierB literal Linux hosted selector in consumer job '{name}' "
                f"runs-on — use a VERJSON lane variable: {runs_on}",
            )
        return

    # R2 — a rolling standard hosted image, independent of billing. R1 already
    # refuses the metered macOS/Windows families; R2 separately asks whether a
    # build environment can change without a commit, including free public
    # Linux minutes. Keeping the rules distinct stops `macos-latest` ->
    # `ubuntu-latest` reading as a fix.
    if METERED_LATEST.search(selector):
        report.violation(
            path, runs_on_line,
            f"R2 rolling -latest image in job '{name}' runs-on: {runs_on}",
        )

    # Tier B — literal Linux hosted selectors, keyed on repository visibility.
    #
    # Hosted minutes are FREE for a public repository (measured 2026-08-01; ADR
    # 0047 corrected ADR 0033's "unfunded" premise), so `ubuntu-latest` there
    # spends nothing. On a private repository the same line rides the spending
    # limit #810 is raising.
    #
    # Asked of the text OUTSIDE every expression span. Inside a `${{ … }}` chain,
    # `'["ubuntu-24.04"]'` is ADR 0040's portability tail, which appears in the
    # generated caller of every consumer and which runner-routing-policy.test.sh
    # already strips; firing on it would reject ~89 conforming repositories, and
    # a check nobody can keep switched on enforces nothing. But `${{ matrix.os }}`
    # with `os: [ubuntu-24.04]` IS a hardcoded selector wearing an indirection,
    # and stripping the expression rather than skipping the job is what catches
    # it. The public-repository half of this rule — a literal should still use
    # `vars.CI_RUNNER_FASTLANE`, a variable, so capacity moves stay
    # org-variable edits (ADR 0041) — is #816, deferred by decision.
    if visibility == "private" and LINUX_HOSTED_LITERAL.search(
        outside_expressions(selector)
    ):
        report.violation(
            path, runs_on_line,
            f"TierB literal Linux hosted selector on a private repository in job "
            f"'{name}' — use vars.CI_RUNNER_FASTLANE or a lane variable: {runs_on}",
        )

    if not OS_LANE.search(selector):
        # R4, the ordinary half. A placement chain must have a terminal landing —
        # `CI_LANE_FALLBACK`, or the portable `'["ubuntu-24.04"]'` tail ADR
        # 0040 put there for an organization with no lane variables at all. A
        # chain that stops at its own variable leaves an org that set only the
        # fallback with an EMPTY `runs-on`, and GitHub does not fail an
        # unplaceable job — it queues it forever with no diagnostic (#401).
        #
        # "Has a terminal landing" rather than "names FALLBACK", so node-ci.yml's
        # ADR 0086 secretless acquisition job — deliberately
        # `CI_LANE_UNTRUSTED || '["ubuntu-24.04"]'`, never the trusted
        # fallback — is covered by the rule instead of by a repository-specific
        # carve-out this script would have to carry into ~89 consumers.
        if PLACEMENT_VARIABLE.search(selector) and not TERMINAL_LANDING.search(selector):
            report.violation(
                path, runs_on_line,
                f"R4 lane selector with no terminal landing in job '{name}' — it "
                f"resolves to an empty runs-on when the variable is unset: {runs_on}",
            )
        return

    # R4 — the OS lanes are the exception, and it runs the other way. They must
    # NOT chain to CI_LANE_FALLBACK, and must not carry the portable
    # `'["ubuntu-…"]'` tail either: both degrade a macOS or Windows leg onto
    # Linux, producing a non-installable artifact behind a green check. Failing
    # closed is louder and cheaper than shipping the wrong binary.
    if TERMINAL_LANDING.search(selector):
        report.violation(
            path, runs_on_line,
            f"R4 OS lane carries a fallback tail in job '{name}' — an unset OS "
            f"lane must fail closed, never degrade to Linux: {runs_on}",
        )

    # R3 — bounded, not merely annotated. Missing key and over-ceiling value are
    # separate messages because they are separate mistakes: one is a job nobody
    # bounded, the other a bound somebody chose too high.
    timeout = body.get("timeout-minutes")
    if timeout is None:
        report.violation(
            path, runs_on_line,
            f"R3 OS-lane job declares no timeout-minutes in job '{name}'",
        )
    elif isinstance(timeout, bool) or not isinstance(timeout, int):
        # An expression or a non-integer cannot be compared against the ceiling,
        # so it is refused rather than skipped: an unevaluatable bound is not a
        # bound, and skipping it is how `timeout-minutes: ${{ inputs.timeout }}`
        # would buy back the whole six-hour default through a caller's value.
        report.violation(
            path, timeout_line,
            f"R3 OS-lane timeout-minutes is not a literal integer in job "
            f"'{name}': {timeout}",
        )
    elif timeout > HOSTED_TIMEOUT_CEILING:
        report.violation(
            path, timeout_line,
            f"R3 OS-lane timeout-minutes {timeout} exceeds the "
            f"{HOSTED_TIMEOUT_CEILING} ceiling in job '{name}'",
        )


def check_os_lane_references(report: Report, path: str, sanctioned: set[str]) -> bool:
    """R5 — a file-wide TEXT scan, deliberately independent of the parser.

    Every other rule depends on resolving the document; this one must hold even
    for a file the parser refuses, and it is the rule that answers "who can spend
    hosted minutes" with one grep. A reference in a comment counts: a
    commented-out selector is a copy-paste away from a live one, which is exactly
    how this defect class regrew four times (#175, #182, #192, #203).
    """
    found = False
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for number, text in enumerate(stream, start=1):
                if OS_LANE_TEXT.search(text):
                    found = True
                    if os.path.basename(path) not in sanctioned:
                        report.violation(
                            path, number,
                            "R5 off-path reference to an OS lane variable — only the "
                            "sanctioned desktop-release path may name "
                            "CI_LANE_TRUSTED_MACOS or CI_LANE_TRUSTED_WINDOWS",
                        )
    except OSError as error:
        report.anomaly(f"{path}: cannot read: {error}")
    return found


def check_os_lane_trigger(report: Report, path: str, document: dict) -> None:
    """R6 — every workflow that can select an OS lane is dispatch-only."""
    has_text_key = "on" in document
    has_yaml11_key = True in document
    if has_text_key and has_yaml11_key:
        report.anomaly(f"{path}: ambiguous duplicate 'on' trigger key")
        return
    if has_text_key:
        trigger = document["on"]
    elif has_yaml11_key:
        # PyYAML follows YAML 1.1 and resolves the unquoted key `on` to True.
        # GitHub treats it as the literal workflow trigger key.
        trigger = document[True]
    else:
        report.violation(path, 0, "R6 OS-lane workflow is not dispatch-only: no on key")
        return

    if isinstance(trigger, str):
        events = [trigger]
    elif isinstance(trigger, list) and all(isinstance(item, str) for item in trigger):
        events = trigger
    elif isinstance(trigger, dict) and all(isinstance(key, str) for key in trigger):
        events = list(trigger)
    else:
        report.anomaly(f"{path}: workflow trigger has an unsupported shape")
        return

    if events != ["workflow_dispatch"]:
        line = getattr(document, "lines", {}).get("on", 0)
        report.violation(
            path,
            line,
            "R6 OS-lane workflow is not dispatch-only; trigger set must be exactly "
            "workflow_dispatch",
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):  # noqa: D102 - argparse hook
        raise Undetermined(message)


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = _ArgumentParser(add_help=False)
    # No default. A caller that does not say what it is scanning gets no verdict,
    # because a permissive default here silently disables Tier B.
    parser.add_argument("--visibility", action="append")
    parser.add_argument(
        "--consumer-policy",
        "--metered-families-only",
        dest="consumer_policy",
        action="store_true",
    )
    # Behind a flag, not a trailing positional: in a script that fails closed
    # everywhere else, a stray argument must not quietly sanction a file and
    # narrow the sweep.
    parser.add_argument("--sanctioned", action="append", default=[])
    parser.add_argument("workflow_dir", nargs="?")
    parser.add_argument("extra", nargs="*")
    arguments = parser.parse_args(argv)

    if arguments.extra:
        raise Undetermined(
            f"unexpected argument(s): {' '.join(arguments.extra)} "
            "(sanctioned workflows go behind --sanctioned)"
        )
    visibility = arguments.visibility or []
    if len(visibility) > 1:
        # Last-wins on a repeated flag is how a caller typo silently changes the
        # policy tier being enforced.
        raise Undetermined(f"--visibility given {len(visibility)} times")
    arguments.visibility = visibility[0] if visibility else ""
    if arguments.consumer_policy:
        if arguments.visibility:
            raise Undetermined(
                "--consumer-policy and --visibility are mutually exclusive"
            )
        if arguments.sanctioned:
            raise Undetermined(
                "--sanctioned does not apply to --consumer-policy"
            )
    elif arguments.visibility not in ("public", "private"):
        raise Undetermined(
            "--visibility must be exactly 'public' or 'private' "
            f"(got '{arguments.visibility}')"
        )
    if not arguments.workflow_dir:
        raise Undetermined("no workflow directory given")
    return arguments


def collect_workflow_files(workflow_dir: str) -> list[str]:
    if not os.path.isdir(workflow_dir):
        raise Undetermined(f"not a directory: {workflow_dir}")
    # Both suffixes. GitHub runs `.yaml` and `.yml` identically, and a sweep that
    # globs `*.yml` alone is evaded by renaming the file (#401).
    files = sorted(
        os.path.join(workflow_dir, entry)
        for entry in os.listdir(workflow_dir)
        if entry.endswith((".yml", ".yaml"))
        and os.path.isfile(os.path.join(workflow_dir, entry))
    )
    if not files:
        # A sweep that scans nothing must not look like a sweep that found
        # nothing: a deleted directory, a renamed path in a consumer checkout, or
        # a typo in the #815 caller would otherwise report green.
        raise Undetermined(
            f"no .yml or .yaml workflow files found under {workflow_dir}"
        )
    return files


def main(argv: list[str]) -> int:
    try:
        arguments = parse_arguments(argv)
        canonical_canary_path = canonical_runner_canary_path(
            arguments.workflow_dir, arguments.consumer_policy
        )
        canonical_candidate_path = canonical_container_candidate_path(
            arguments.workflow_dir, arguments.consumer_policy
        )
        workflow_files = collect_workflow_files(arguments.workflow_dir)
    except Undetermined as error:
        print(f"UNDETERMINED: {error}", file=sys.stderr)
        return 2

    sanctioned = set(SANCTIONED_OS_LANE_WORKFLOWS) | set(arguments.sanctioned)
    report = Report()

    for path in workflow_files:
        references_os_lane = False
        if not arguments.consumer_policy:
            references_os_lane = check_os_lane_references(report, path, sanctioned)
        try:
            document = load_workflow(path)
            jobs = extract_jobs(path, document)
        except Undetermined as error:
            report.anomaly(str(error))
            continue
        if references_os_lane:
            check_os_lane_trigger(report, path, document)
        canonical_jobs = canonical_runner_canary_jobs(
            report,
            path,
            document,
            jobs,
            arguments.consumer_policy,
            canonical_canary_path,
        )
        canonical_candidate_jobs = canonical_container_candidate_jobs(
            report,
            path,
            jobs,
            arguments.consumer_policy,
            canonical_candidate_path,
        )
        for name, body, line in jobs:
            check_job(
                report,
                path,
                name,
                body,
                line,
                arguments.visibility,
                arguments.consumer_policy,
                name in canonical_jobs,
                name in canonical_candidate_jobs,
                arguments.consumer_policy and is_canonical_changelog_pr_gate(document),
                arguments.consumer_policy and is_guarded_fastlane_caller(document),
            )

    for message in report.anomalies:
        print(message, file=sys.stderr)
    for message in report.violations:
        print(message, file=sys.stderr)

    # A definite violation is reported as one; otherwise an incomplete sweep is
    # undetermined. Both are non-zero, so neither can be mistaken for clean, and
    # there is no path to green that leaves either unaddressed.
    if report.violations:
        return 1
    if report.anomalies:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
