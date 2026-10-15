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

sys.exit(1 if failures else 0)
