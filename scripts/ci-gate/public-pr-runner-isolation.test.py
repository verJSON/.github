#!/usr/bin/env python3
"""Behavioral tests for scripts/ci-gate/public-pr-runner-isolation.py.

The policy: code from a pull request in a public repository never runs on a
persistent self-hosted runner. Every case writes a throwaway workflow tree and
runs the real checker against it, asserting both the exit status and the
message, because one "violation" exit cannot tell which rule tripped.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "public-pr-runner-isolation.py")

failures = 0


def ok(label: str) -> None:
    print(f"ok   - {label}")


def fail(label: str) -> None:
    global failures
    failures += 1
    print(f"FAIL - {label}")


def run_checker(workflows: dict[str, str], *arguments: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as directory:
        for name, body in workflows.items():
            with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(body))
        arguments = tuple(argument.replace("{dir}", directory) for argument in arguments)
        completed = subprocess.run(
            [sys.executable, SCRIPT, *arguments, directory],
            capture_output=True,
            text=True,
        )
    return completed.returncode, completed.stdout + completed.stderr


def expect(
    workflows: dict[str, str],
    code: int,
    fragments: tuple[str, ...],
    label: str,
    *arguments: str,
) -> None:
    actual, output = run_checker(workflows, *arguments)
    missing = [fragment for fragment in fragments if fragment not in output]
    if actual != code or missing:
        fail(f"{label} (expected exit {code} with {missing or fragments}, got {actual}: {output.strip()})")
    else:
        ok(label)


expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              test:
                runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("ci.yml", "test", "self-hosted"),
    "a pull_request job on the trusted lane is a violation",
)

expect(
    {
        "ci.yml": """
            on: [pull_request, push]
            jobs:
              publish:
                if: github.event_name == 'push'
                runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    0,
    ("clean",),
    "a job whose condition cannot hold on pull_request is not reachable",
)

expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              admission:
                if: github.repository == 'Verjson/elsewhere'
                runs-on: ubuntu-24.04
                steps:
                  - run: 'true'
              build:
                needs: admission
                if: ${{ !github.event.pull_request.draft }}
                runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    0,
    ("clean",),
    "a job needing a skipped job without a status function is not reachable",
)

expect(
    {
        "caller.yml": """
            on: pull_request
            jobs:
              lint:
                uses: ./.github/workflows/lint.yml
        """,
        "lint.yml": """
            on: workflow_call
            jobs:
              lint:
                runs-on: ${{ fromJSON(vars.CI_RUNNER_FASTLANE || vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("caller.yml", "lint.yml", "lint"),
    "a local reusable workflow called on pull_request is checked through its caller",
)

expect(
    {
        "caller.yml": """
            on: pull_request
            jobs:
              build:
                uses: ./.github/workflows/build.yml
                with:
                  runner: ${{ vars.CI_RUNNER_DEFAULT }}
        """,
        "build.yml": """
            on:
              workflow_call:
                inputs:
                  runner:
                    type: string
                    default: ''
            jobs:
              build:
                runs-on: ${{ inputs.runner != '' && fromJSON(inputs.runner) || fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("build.yml", "inputs"),
    "a reusable workflow honoring a caller runner input on pull_request is a violation",
)

def digest(selector) -> str:
    """The pending-row key: SHA-256 of the job's runs-on value as canonical JSON."""
    canonical = json.dumps(selector, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


TRUSTED_SELECTOR = """${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}"""
TRUSTED_LANE_PR_JOB = """
    on: pull_request
    jobs:
      test:
        runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
        steps:
          - run: 'true'
"""

expect(
    {
        "ci.yml": TRUSTED_LANE_PR_JOB,
        "pending.tsv": (
            "# path<TAB>job<TAB>selector digest<TAB>reason\n"
            f"ci.yml\ttest\t{digest(TRUSTED_SELECTOR)}\tselector pinned elsewhere until a follow-up\n"
        ),
    },
    0,
    ("PENDING: ci.yml: job 'test'", "selector pinned elsewhere"),
    "a reviewed pending entry is reported as pending instead of failing",
    "--pending",
    "{dir}/pending.tsv",
)

expect(
    {
        "ci.yml": TRUSTED_LANE_PR_JOB.replace("vars.CI_LANE_FALLBACK || ", ""),
        "pending.tsv": f"ci.yml\ttest\t{digest(TRUSTED_SELECTOR)}\tlisted against the reviewed selector\n",
    },
    1,
    ("VIOLATION: ci.yml: job 'test'", "selector digest changed"),
    "a pending job whose selector changed is a violation, not masked by its row",
    "--pending",
    "{dir}/pending.tsv",
)

expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              test:
                runs-on: ${{ fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
        "pending.tsv": f"ci.yml\ttest\t{digest(TRUSTED_SELECTOR)}\tfixed but still listed\n",
    },
    1,
    ("STALE PENDING: ci.yml: job 'test'",),
    "a pending entry that no longer violates fails until it is removed",
    "--pending",
    "{dir}/pending.tsv",
)

expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              test:
                if: ${{ !contains(github.event.pull_request.labels.*.name, 'skip-ci') }}
                runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("ci.yml", "test", "self-hosted"),
    "an object filter in a condition is parsed rather than left undetermined",
)

# ---------------------------------------------------------------------------
# Hardening: failure modes and the routing shapes the live tree relies on. Each
# was proven to fail under a targeted mutation of the checker (see #1827).
# ---------------------------------------------------------------------------


def pull_request_job(runs_on: str, extra: str = "") -> dict[str, str]:
    return {
        "ci.yml": f"""
            on: pull_request
            jobs:
              test:{extra}
                runs-on: {runs_on}
                steps:
                  - run: 'true'
        """,
    }


expect(
    pull_request_job("""${{ fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}"""),
    1,
    ("vars.CI_LANE_UNTRUSTED=<unset>", "vars.CI_LANE_FALLBACK="),
    "an untrusted lane that falls back to CI_LANE_FALLBACK can reach the persistent pool",
)
expect(
    pull_request_job("""${{ fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') }}"""),
    0,
    ("clean",),
    "the untrusted lane with a hosted tail is ephemeral whether or not it is set",
)
expect(
    pull_request_job("""${{ fromJSON(vars['CI_LANE_TRUSTED'] || '["ubuntu-24.04"]') }}"""),
    1,
    ("vars.CI_LANE_TRUSTED=",),
    "a bracket dereference of a persistent variable is enumerated like a dotted one",
)
expect(
    pull_request_job("[self-hosted, '${{ inputs.label }}']"),
    1,
    ("self-hosted",),
    "a literal self-hosted label in a runs-on list is a violation",
)
expect(
    pull_request_job("{group: DigitalOcean}"),
    1,
    ("group",),
    "a runner-group selector is never treated as ephemeral",
)
expect(
    pull_request_job("ubuntu-24.04"),
    0,
    ("clean",),
    "a literal GitHub-hosted image is ephemeral",
)
expect(
    pull_request_job("runner-${{ github.event_name }}"),
    2,
    ("UNDETERMINED", "interpolates"),
    "a selector built by string interpolation is undetermined, never clean",
)
expect(
    pull_request_job("${{ matrix.os }}"),
    2,
    ("UNDETERMINED", "cannot resolve"),
    "a selector the checker cannot resolve is undetermined, never clean",
)
expect(
    pull_request_job("""${{ fromJSON(vars.CI_LANE_TRUSTED ||| '["ubuntu-24.04"]') }}"""),
    2,
    ("UNDETERMINED",),
    "a malformed expression is undetermined, never clean",
)
expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              remote:
                uses: Verjson/.github/.github/workflows/node-ci.yml@0123456789abcdef0123456789abcdef01234567
        """,
    },
    2,
    ("UNDETERMINED", "cannot read"),
    "a reachable remote reusable workflow is undetermined, never clean",
)
expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              admission:
                if: github.repository == 'Verjson/elsewhere'
                runs-on: ubuntu-24.04
                steps:
                  - run: 'true'
              report:
                needs: admission
                if: always()
                runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("report",),
    "always() keeps a job reachable behind a skipped dependency",
)
expect(
    {
        "ci.yml": """
            on: push
            jobs:
              test:
                runs-on: [self-hosted, general]
                steps:
                  - run: 'true'
        """,
    },
    0,
    ("clean",),
    "a workflow without a pull_request trigger is outside the policy",
)
expect({}, 2, ("UNDETERMINED", "no workflow files"), "an empty workflow directory is undetermined")
expect(
    {**pull_request_job("ubuntu-24.04"), "pending.tsv": f"ci.yml\ttest\t{digest('ubuntu-24.04')}\n"},
    2,
    ("UNDETERMINED", "path<TAB>job<TAB>selector digest<TAB>reason"),
    "a pending row without a reason is rejected",
    "--pending",
    "{dir}/pending.tsv",
)
expect(
    {**pull_request_job("ubuntu-24.04"), "pending.tsv": "ci.yml\ttest\tsha256:abc\treason\n"},
    2,
    ("UNDETERMINED", "selector digest"),
    "a pending row with a malformed selector digest is rejected",
    "--pending",
    "{dir}/pending.tsv",
)


# ---------------------------------------------------------------------------
# Review of #1827 (blocker): a reference compared against a literal must be
# tried AS that literal, or any gate keyed on a value outside the fixed
# candidate set reads as unreachable and the job is reported clean.
# ---------------------------------------------------------------------------
expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              heavy:
                if: vars.ENABLE_HEAVY == 'true'
                runs-on: [self-hosted, general]
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("heavy", "vars.ENABLE_HEAVY=true"),
    "a variable compared to a literal in a condition is tried as that literal",
)
expect(
    pull_request_job(
        """${{ vars.LANE_MODE == 'shared' && fromJSON('["self-hosted","general"]') || 'ubuntu-24.04' }}"""
    ),
    1,
    ("vars.LANE_MODE=shared",),
    "a variable compared to a literal in a selector is tried as that literal",
)
expect(
    {
        "caller.yml": """
            on: pull_request
            jobs:
              build:
                uses: ./.github/workflows/build.yml
                with:
                  lane: ${{ github.head_ref }}
        """,
        "build.yml": """
            on:
              workflow_call:
                inputs:
                  lane:
                    type: string
                    default: ''
            jobs:
              build:
                runs-on: ${{ inputs.lane == 'shared' && fromJSON('["self-hosted","general"]') || 'ubuntu-24.04' }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("build.yml", "inputs.lane=shared"),
    "an attacker-shaped caller input compared to a literal is tried as that literal",
)
expect(
    pull_request_job(
        """${{ format('{0}', vars.LANE_MODE) == 'shared' && fromJSON('["self-hosted","general"]') || 'ubuntu-24.04' }}"""
    ),
    1,
    ("self-hosted",),
    "a comparison the checker cannot enumerate exactly is evaluated both ways",
)


# Only standard GitHub-hosted image names are ephemeral. A self-hosted runner
# can carry any custom label, so a look-alike must not pass as hosted.
for label in ("ubuntu-gpu-selfhosted", "ubuntu-24.04-large", "macos-14-xlarge",
              "windows-gpu", "ubuntu-２4.04", "Ubuntu-24.04x", '"ubuntu-24.04\\n"'):
    expect(
        pull_request_job(label),
        1,
        ("non-ephemeral",),
        f"look-alike label {label!r} is not treated as a standard hosted image",
    )
for label in ("ubuntu-latest", "ubuntu-22.04", "ubuntu-24.04-arm", "windows-latest",
              "windows-2025", "windows-11-arm", "macos-latest", "macos-15", "macos-15-intel"):
    expect(
        pull_request_job(label),
        0,
        ("clean",),
        f"standard hosted image {label!r} is ephemeral",
    )


# ---------------------------------------------------------------------------
# pull_request_target runs the BASE workflow with a privileged token, so it is
# dangerous exactly when a job materializes the pull request's head. Those jobs
# are held to the same placement rule; base-only jobs are not.
# ---------------------------------------------------------------------------


def target_job(steps: str, runs_on: str = TRUSTED_SELECTOR) -> dict[str, str]:
    return {
        "target.yml": f"""
            on: pull_request_target
            jobs:
              review:
                runs-on: {runs_on}
                steps:
{textwrap.indent(textwrap.dedent(steps), " " * 18)}
        """,
    }


expect(
    target_job("""
        - uses: actions/checkout@v4
          with:
            ref: ${{ github.event.pull_request.head.sha }}
        - run: make test
    """),
    1,
    ("target.yml [pull_request_target]", "review", "self-hosted"),
    "a pull_request_target job that checks out the head is held to the placement rule",
)
expect(
    target_job("""
        - run: gh pr checkout ${{ github.event.pull_request.number }} && make test
    """),
    1,
    ("[pull_request_target]", "review"),
    "a pull_request_target job running gh pr checkout is held to the placement rule",
)
expect(
    target_job("""
        - uses: actions/checkout@v4
          with:
            ref: ${{ env.CANDIDATE }}
    """),
    1,
    ("[pull_request_target]", "review"),
    "a pull_request_target checkout through an indirection is treated as head-capable",
)
expect(
    target_job("""
        - env:
            HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          run: git fetch --depth=1 origin "$HEAD_SHA" && git checkout FETCH_HEAD
    """),
    1,
    ("[pull_request_target]", "review"),
    "a pull_request_target git fetch of a head-derived value is held to the placement rule",
)
expect(
    target_job("""
        - uses: actions/checkout@v4
          with:
            ref: ${{ github.event.pull_request.base.sha }}
        - env:
            HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          run: gh api "repos/$GITHUB_REPOSITORY/commits/$HEAD_SHA/check-runs"
    """),
    0,
    ("clean",),
    "a pull_request_target job running only base code with head metadata is outside the rule",
)
expect(
    target_job(
        """
        - uses: actions/checkout@v4
          with:
            ref: ${{ github.event.pull_request.head.sha }}
        """,
        runs_on="ubuntu-24.04",
    ),
    0,
    ("clean",),
    "a pull_request_target head checkout on a hosted image is clean",
)

expect(
    {
        "target.yml": """
            on: pull_request_target
            jobs:
              arm:
                uses: Verjson/.github/.github/workflows/app-key-environment.yml@0123456789abcdef0123456789abcdef01234567
        """,
    },
    0,
    ("NOT FOLLOWED: target.yml [pull_request_target]: job 'arm'",),
    "a remote callee under pull_request_target is reported as not followed, never silently",
)
expect(
    target_job("""
        - run: git fetch origin "+refs/pull/1/merge" && git checkout FETCH_HEAD
    """),
    1,
    ("[pull_request_target]",),
    "a pull_request_target fetch of a refs/pull refspec is held to the placement rule",
)

expect(
    pull_request_job(
        """${{ vars.LANE_LEVEL > 2 && fromJSON('["self-hosted","general"]') || 'ubuntu-24.04' }}"""
    ),
    1,
    ("self-hosted",),
    "an ordering comparison over a variable is evaluated both ways",
)
expect(
    target_job("""
        - uses: actions/checkout@v4
          with:
            ref: refs/pull/1/merge
    """),
    1,
    ("[pull_request_target]",),
    "a pull_request_target checkout of a literal refs/pull ref is head-capable",
)

sys.exit(1 if failures else 0)
