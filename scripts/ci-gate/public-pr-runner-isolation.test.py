#!/usr/bin/env python3
"""Behavioral tests for scripts/ci-gate/public-pr-runner-isolation.py.

The policy: code from a pull request in a public repository never runs on a
persistent self-hosted runner. Every case writes a throwaway workflow tree and
runs the real checker against it, asserting both the exit status and the
message, because one "violation" exit cannot tell which rule tripped.
"""

from __future__ import annotations

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
        "pending.tsv": "# path<TAB>job<TAB>reason\nci.yml\ttest\tselector pinned elsewhere until a follow-up\n",
    },
    0,
    ("PENDING: ci.yml: job 'test'", "selector pinned elsewhere"),
    "a reviewed pending entry is reported as pending instead of failing",
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
        "pending.tsv": "ci.yml\ttest\tfixed but still listed\n",
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
    {**pull_request_job("ubuntu-24.04"), "pending.tsv": "ci.yml\ttest\n"},
    2,
    ("UNDETERMINED", "path<TAB>job<TAB>reason"),
    "a pending row without a reason is rejected",
    "--pending",
    "{dir}/pending.tsv",
)

sys.exit(1 if failures else 0)
