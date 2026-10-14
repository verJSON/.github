#!/usr/bin/env python3
"""Unit tests for scripts/ci-gate/hosted-selector-policy.py (Verjson/.github#814).

The rules under test cannot be proven against this repository's own workflow
tree: `.github` has no macOS or Windows selector and no OS-scoped lane, so an
assertion pointed at `.github/workflows` can never fail and would prove nothing.
Every rule therefore gets a fixture that FAILS the check and a fixture that
PASSES it, under `scripts/fixtures/hosted-selector-policy/`.

Ported from the shell suite this replaces, case for case. The fixtures are the
regression suite for the parser rewrite, so nothing is dropped: a rewrite that
quietly loses coverage is how the TAB/IFS false negative would come back.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "hosted-selector-policy.py")
FIXTURES = os.path.join(os.path.dirname(HERE), "fixtures", "hosted-selector-policy")
REPOSITORY_ROOT = os.path.dirname(os.path.dirname(HERE))
CANONICAL_WORKFLOWS = os.path.join(REPOSITORY_ROOT, ".github", "workflows")
FIXTURE_CONTRACT_SHA = "0123456789abcdef0123456789abcdef01234567"

failures = 0


def ok(label: str) -> None:
    print(f"ok   - {label}")


def fail(label: str) -> None:
    global failures
    failures += 1
    print(f"FAIL - {label}")


def run_policy(*arguments: str) -> tuple[int, str]:
    environment = os.environ.copy()
    environment["VERJSON_HOSTED_SELECTOR_POLICY_SHA"] = FIXTURE_CONTRACT_SHA
    completed = subprocess.run(
        [sys.executable, SCRIPT, *arguments],
        capture_output=True,
        env=environment,
        text=True,
    )
    return completed.returncode, completed.stdout + completed.stderr


exercised_fixtures: set[str] = set()


def _invocation(visibility: str, fixture: str, sanctioned: tuple[str, ...]) -> list[str]:
    exercised_fixtures.add(fixture)
    arguments = ["--visibility", visibility]
    for name in sanctioned:
        arguments += ["--sanctioned", name]
    arguments.append(os.path.join(FIXTURES, fixture))
    return arguments


def assert_violation(visibility: str, fixture: str, expected: str, label: str,
                     *sanctioned: str) -> None:
    """The message substring is asserted, not only the exit status.

    A single "policy violation" exit proves the sweep tripped, not that it
    tripped on the rule the fixture exercises — and R3's failure modes are
    deliberately distinct messages, which an exit-status-only assertion could
    not tell apart.
    """
    code, output = run_policy(*_invocation(visibility, fixture, sanctioned))
    if code != 1:
        fail(f"{label} (expected exit 1, got {code}: {output.strip()})")
    elif expected not in output:
        fail(f"{label} (exit 1 but no {expected!r} in: {output.strip()})")
    else:
        ok(label)


def assert_clean(visibility: str, fixture: str, label: str, *sanctioned: str) -> None:
    code, output = run_policy(*_invocation(visibility, fixture, sanctioned))
    if code == 0:
        ok(label)
    else:
        fail(f"{label} (expected exit 0, got {code}: {output.strip()})")


def assert_undetermined_fixture(fixture: str, label: str, *sanctioned: str,
                                visibility: str = "public") -> None:
    code, output = run_policy(*_invocation(visibility, fixture, sanctioned))
    if code == 2:
        ok(label)
    else:
        fail(f"{label} (expected exit 2, got {code}: {output.strip()})")


def assert_undetermined(label: str, *arguments: str) -> None:
    """Exit 2 is "this sweep could not decide", distinct from exit 0 on purpose.

    A policy script that quietly passes when it cannot tell what it is scanning
    is the defect class this whole check exists to prevent, reproduced inside
    the check itself.
    """
    code, output = run_policy(*arguments)
    if code == 2:
        ok(label)
    else:
        fail(f"{label} (expected exit 2, got {code}: {output.strip()})")


def assert_metered_only(fixture: str, expected_code: int, label: str) -> None:
    exercised_fixtures.add(fixture)
    code, output = run_policy(
        "--consumer-policy", os.path.join(FIXTURES, fixture)
    )
    if code == expected_code:
        ok(label)
    else:
        fail(
            f"{label} (expected exit {expected_code}, got {code}: "
            f"{output.strip()})"
        )


def run_canonical_fixture(fixture: str) -> tuple[int, str]:
    exercised_fixtures.add(fixture)
    with tempfile.TemporaryDirectory() as repository:
        workflows = os.path.join(repository, ".github", "workflows")
        os.makedirs(workflows)
        policy_dir = os.path.join(repository, "scripts", "ci-gate")
        os.makedirs(policy_dir)
        canonical_script = os.path.join(policy_dir, "hosted-selector-policy.py")
        shutil.copy2(SCRIPT, canonical_script)
        for entry in os.listdir(os.path.join(FIXTURES, fixture)):
            shutil.copy2(os.path.join(FIXTURES, fixture, entry), workflows)
        subprocess.run(["git", "init", "--quiet", repository], check=True)
        subprocess.run(
            ["git", "-C", repository, "add", "scripts", ".github/workflows"],
            check=True,
        )
        completed = subprocess.run(
            [sys.executable, canonical_script, "--visibility", "public", workflows],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
        )
        return completed.returncode, completed.stdout + completed.stderr


with tempfile.TemporaryDirectory() as outside:
    completed = subprocess.run(
        [sys.executable, SCRIPT, "--visibility", "public", CANONICAL_WORKFLOWS],
        cwd=outside,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        ok("canonical policy authority is independent of caller cwd")
    else:
        fail(
            "canonical policy authority depended on caller cwd "
            f"({completed.returncode}: {(completed.stdout + completed.stderr).strip()})"
        )

with tempfile.TemporaryDirectory() as unexpected:
    moved_script = os.path.join(unexpected, "hosted-selector-policy.py")
    shutil.copy2(SCRIPT, moved_script)
    completed = subprocess.run(
        [sys.executable, moved_script, "--visibility", "public", CANONICAL_WORKFLOWS],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 2 and "verified Git worktree" in (
        completed.stdout + completed.stderr
    ):
        ok("a moved policy script cannot inherit canonical path authority")
    else:
        fail(
            "a moved policy script did not fail closed "
            f"({completed.returncode}: {(completed.stdout + completed.stderr).strip()})"
        )

with tempfile.TemporaryDirectory() as fake_repository:
    workflows = os.path.join(fake_repository, ".github", "workflows")
    policy_dir = os.path.join(fake_repository, "scripts", "ci-gate")
    os.makedirs(workflows)
    os.makedirs(policy_dir)
    os.mkdir(os.path.join(fake_repository, ".git"))
    fake_script = os.path.join(policy_dir, "hosted-selector-policy.py")
    shutil.copy2(SCRIPT, fake_script)
    shutil.copy2(
        os.path.join(FIXTURES, "runner-canary-exact", "runner-canary.yml"),
        os.path.join(workflows, "runner-canary.yml"),
    )
    completed = subprocess.run(
        [sys.executable, fake_script, "--visibility", "public", workflows],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 2 and "verified Git worktree" in (
        completed.stdout + completed.stderr
    ):
        ok("an empty .git marker cannot fabricate canonical path authority")
    else:
        fail(
            "an empty .git marker fabricated canonical path authority "
            f"({completed.returncode}: {(completed.stdout + completed.stderr).strip()})"
        )

with tempfile.TemporaryDirectory() as repository, tempfile.TemporaryDirectory() as outside:
    workflows = os.path.join(repository, ".github", "workflows")
    policy_dir = os.path.join(
        repository, ".verjson-actionlint-policy", "scripts", "ci-gate"
    )
    os.makedirs(workflows)
    os.makedirs(policy_dir)
    nested_script = os.path.join(policy_dir, "hosted-selector-policy.py")
    shutil.copy2(SCRIPT, nested_script)
    shutil.copy2(
        os.path.join(FIXTURES, "runner-canary-exact", "runner-canary.yml"),
        os.path.join(workflows, "runner-canary.yml"),
    )
    subprocess.run(["git", "init", "--quiet", repository], check=True)
    subprocess.run(
        ["git", "-C", repository, "add", ".github/workflows"], check=True
    )
    policy_root = os.path.join(repository, ".verjson-actionlint-policy")
    subprocess.run(["git", "init", "--quiet", policy_root], check=True)
    subprocess.run(
        ["git", "-C", policy_root, "add", "scripts/ci-gate/hosted-selector-policy.py"],
        check=True,
    )
    completed = subprocess.run(
        [sys.executable, nested_script, "--visibility", "public", workflows],
        cwd=outside,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        ok("the nested checked-out policy layout resolves the outer repository root")
    else:
        fail(
            "the nested checked-out policy layout lost canonical authority "
            f"({completed.returncode}: {(completed.stdout + completed.stderr).strip()})"
        )

with tempfile.TemporaryDirectory() as unexpected:
    linked_script = os.path.join(unexpected, "hosted-selector-policy.py")
    os.symlink(SCRIPT, linked_script)
    completed = subprocess.run(
        [sys.executable, linked_script, "--visibility", "public", CANONICAL_WORKFLOWS],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 2 and "non-symlink" in (completed.stdout + completed.stderr):
        ok("a symlinked policy script cannot inherit canonical path authority")
    else:
        fail(
            "a symlinked policy script did not fail closed "
            f"({completed.returncode}: {(completed.stdout + completed.stderr).strip()})"
        )


def assert_canonical_clean(fixture: str, label: str) -> None:
    code, output = run_canonical_fixture(fixture)
    if code == 0:
        ok(label)
    else:
        fail(f"{label} (expected exit 0, got {code}: {output.strip()})")


def assert_canonical_violation(fixture: str, expected: str, label: str) -> None:
    code, output = run_canonical_fixture(fixture)
    if code != 1:
        fail(f"{label} (expected exit 1, got {code}: {output.strip()})")
    elif expected not in output:
        fail(f"{label} (exit 1 but no {expected!r} in: {output.strip()})")
    else:
        ok(label)


# ---------------------------------------------------------------------------
# R1 / R2 (Tier A) — the metered families, zero exceptions.
# ---------------------------------------------------------------------------
assert_undetermined(
    "a directory that does not exist is undetermined, never clean",
    "--visibility", "public", os.path.join(FIXTURES, "does-not-exist"),
)
assert_violation("public", "metered-macos", "metered hosted runner family",
                 "a metered macOS selector is a hard failure")
assert_violation("public", "metered-windows", "metered hosted runner family",
                 "a metered Windows selector is a hard failure")
assert_violation("public", "metered-macos", "rolling -latest image",
                 "a rolling image in a metered family is reported as its own defect")
assert_violation("public", "rolling-linux-latest", "rolling -latest image",
                 "a rolling Linux image is refused independently of billing visibility")

# #815 exports only the visibility-independent metered-family rule through the
# reusable actionlint workflow. The repository-local R2/Tier B/R3-R6 rules stay
# local; #816 owns consumer Linux drift. The parser boundary remains fail closed.
assert_metered_only("metered-macos", 1,
                    "consumer mode refuses literal macOS hosted selectors")
assert_metered_only("metered-windows", 1,
                    "consumer mode refuses literal Windows hosted selectors")
assert_metered_only("evasion-matrix", 1,
                    "consumer mode resolves a metered matrix selector structurally")
assert_metered_only("rolling-linux-latest", 1,
                    "consumer mode refuses a rolling Linux hosted selector")
assert_metered_only("linux-hosted-literal", 1,
                    "consumer mode refuses a pinned Linux hosted selector")
assert_metered_only("invalid-yaml", 2,
                    "consumer mode remains fail closed on unparseable workflow YAML")
assert_metered_only("dynamic-format", 2,
                    "consumer mode refuses a format-built selector")
assert_metered_only("dynamic-join-fromjson", 2,
                    "consumer mode refuses a join/fromJSON-built selector")
for source_fixture, source_name in (
    ("dynamic-input-direct", "a direct arbitrary input"),
    ("dynamic-input-fromjson", "a fromJSON-decoded arbitrary input"),
    ("dynamic-var-direct", "a direct unreviewed repository variable"),
    ("dynamic-var-fromjson", "a fromJSON-decoded unreviewed repository variable"),
    ("dynamic-needs-direct", "a direct unreviewed needs output"),
    ("dynamic-needs-fromjson", "a fromJSON-decoded unreviewed needs output"),
):
    assert_metered_only(
        source_fixture,
        2,
        f"consumer mode refuses {source_name}",
    )
assert_metered_only("lane-with-fallback", 0,
                    "consumer mode retains reviewed canonical lane expressions")
assert_metered_only("matrix-linux-literal", 1,
                    "consumer mode refuses static Linux matrix selectors")
assert_metered_only("reusable-input-macos", 1,
                    "consumer mode refuses macOS through with.runner")
assert_metered_only("reusable-input-windows", 1,
                    "consumer mode refuses Windows through with.runner_labels")
assert_metered_only("reusable-input-dynamic", 2,
                    "consumer mode refuses a dynamic reusable runner input")
assert_metered_only("reusable-input-linux", 1,
                    "consumer mode refuses literal Linux reusable inputs")
assert_metered_only("reusable-input-unrelated", 0,
                    "consumer mode ignores unrelated reusable inputs mentioning macOS")
assert_metered_only("step-input-unrelated", 0,
                    "consumer mode ignores step inputs mentioning macOS")
assert_metered_only("reusable-input-malformed", 2,
                    "consumer mode fails closed on a malformed reusable with mapping")
assert_metered_only("reusable-input-canonical", 0,
                    "consumer mode preserves the generated canonical runner expression")
assert_metered_only(
    "reusable-input-fastlane",
    0,
    "consumer mode allows the guarded node-ci fastlane runner expression",
)
assert_metered_only(
    "reusable-input-fastlane-unguarded",
    2,
    "consumer mode refuses fork PR fastlane routing without secretless isolation",
)
assert_metered_only(
    "reusable-input-fastlane-merge-group",
    2,
    "consumer mode refuses merge-group code on the fastlane",
)
assert_metered_only(
    "reusable-input-fastlane-nondefault-push",
    2,
    "consumer mode refuses non-default push code on the fastlane",
)
assert_metered_only(
    "reusable-input-fastlane-dispatch",
    2,
    "consumer mode refuses dispatch-selected ref code on the fastlane",
)
assert_metered_only(
    "reusable-input-fastlane-wrong-pin",
    2,
    "consumer mode binds node-ci and selector policy to one immutable contract",
)
assert_metered_only(
    "reusable-input-fastlane-unreviewed",
    2,
    "consumer mode refuses unreviewed reusable runner expressions",
)
assert_metered_only("reusable-input-static-matrix", 1,
                    "consumer mode resolves and refuses a static Linux input matrix")
assert_metered_only("reusable-input-metered-matrix", 1,
                    "consumer mode resolves and refuses a metered input matrix")

# ---------------------------------------------------------------------------
# Tier B — literal Linux hosted selectors, keyed on repository visibility.
# ---------------------------------------------------------------------------
assert_violation("private", "linux-hosted-literal", "literal Linux hosted selector",
                 "a private repository may not hardcode a Linux hosted selector")
assert_violation("public", "linux-hosted-literal", "rolling -latest image",
                 "public visibility does not permit a rolling Linux image")

# ---------------------------------------------------------------------------
# R3 — bounded, not merely annotated.
# ---------------------------------------------------------------------------
assert_violation("public", "os-lane-no-timeout",
                 "R3 OS-lane job declares no timeout-minutes",
                 "an OS-lane job with no timeout-minutes is refused",
                 "desktop-release.yml")
assert_violation("public", "os-lane-timeout-over",
                 "R3 OS-lane timeout-minutes 360 exceeds the 60 ceiling",
                 "a present-but-unbounded timeout is refused, and says so distinctly",
                 "desktop-release.yml")
assert_clean("public", "os-lane-bounded",
             "both OS lanes bounded at 45 minutes, with no fallback tail, are accepted",
             "desktop-release.yml")
assert_clean("public", "os-lane-dispatch-mapping",
             "a mapped workflow_dispatch trigger with reviewed inputs is accepted",
             "desktop-release.yml")
# 60 is the ceiling and is accepted; 61 is not. A strict `<` would move the
# boundary without anyone deciding to. A step-level timeout does not bound the
# JOB — GitHub applies its six-hour default regardless — so it does not satisfy
# R3 either, which is the presence check's other false positive.
assert_violation("public", "os-lane-timeout-boundary-61", "exceeds the 60 ceiling",
                 "one minute over the ceiling is refused", "desktop-release.yml")
assert_clean("public", "os-lane-timeout-boundary-60",
             "exactly the ceiling is accepted", "desktop-release.yml")
assert_violation("public", "os-lane-step-timeout-only",
                 "R3 OS-lane job declares no timeout-minutes",
                 "a step-level timeout does not bound the job", "desktop-release.yml")

# ---------------------------------------------------------------------------
# R4 — the OS lanes fail closed; the ordinary lanes must still land somewhere.
# ---------------------------------------------------------------------------
assert_violation("public", "os-lane-with-fallback", "R4 OS lane carries a fallback tail",
                 "an OS lane that can degrade to Linux is refused",
                 "desktop-release.yml")
assert_violation("public", "lane-without-terminal-landing",
                 "R4 lane selector with no terminal landing",
                 "an ordinary lane selector with no terminal landing is still refused")
assert_clean("public", "lane-with-fallback",
             "both conforming Linux chains — the ADR 0040 tail and ADR 0086's — are accepted")
# The Tier B boundary that matters most for #815. ADR 0040's portability tail
# appears in the generated caller of every consumer, and ADR 0086's acquisition
# job lands on the same tail directly. Those are lane chains, not hardcoded
# placements; firing Tier B on them would reject ~89 private repositories for
# conforming to the contract.
assert_clean("private", "lane-with-fallback",
             "the sanctioned portability tail is not a hardcoded selector, even on a "
             "private repository")
assert_clean("private", "os-lane-bounded",
             "an OS-lane release workflow on a private repository is Tier A's business, "
             "not Tier B's", "desktop-release.yml")

# #1156: one path-bound direct input routes the transaction-unique canary. It
# must not become a generally reviewed expression or a filename-shaped escape.
assert_canonical_clean(
    "runner-canary-exact",
    "the dispatch-only canonical canary accepts only its exact two-label selector",
)
assert_undetermined_fixture(
    "candidate-selector-unbound",
    "the candidate runner selector is not globally trusted outside its canonical workflow",
)
assert_undetermined_fixture(
    "runner-canary-exact",
    "a caller-chosen alternate scan directory cannot rebase canonical path authority",
)
assert_undetermined_fixture(
    "runner-canary-wrong-path",
    "the direct runner-label input remains unreviewed under another filename",
)
assert_canonical_violation(
    "runner-canary-wrong-trigger", "workflow_dispatch-only",
    "the canonical canary is refused when reachable from a non-dispatch trigger",
)
assert_canonical_violation(
    "runner-canary-extra-label", "runs-on must exactly equal",
    "the canonical canary cannot add another self-hosted capability label",
)
assert_canonical_violation(
    "runner-canary-hosted-label", "runs-on must exactly equal",
    "the canonical canary cannot add a hosted selector",
)
assert_canonical_violation(
    "runner-canary-expression-drift", "runs-on must exactly equal",
    "the canonical canary cannot select a different direct input",
)
assert_metered_only(
    "runner-canary-exact", 2,
    "consumer policy does not inherit the canonical repository's direct-input exception",
)
with tempfile.TemporaryDirectory() as repository:
    workflows = os.path.join(repository, ".github", "workflows")
    os.makedirs(workflows)
    policy_dir = os.path.join(repository, "scripts", "ci-gate")
    os.makedirs(policy_dir)
    canonical_script = os.path.join(policy_dir, "hosted-selector-policy.py")
    shutil.copy2(SCRIPT, canonical_script)
    os.symlink(
        os.path.join(FIXTURES, "runner-canary-exact", "runner-canary.yml"),
        os.path.join(workflows, "runner-canary.yml"),
    )
    subprocess.run(["git", "init", "--quiet", repository], check=True)
    subprocess.run(
        ["git", "-C", repository, "add", "scripts", ".github/workflows"],
        check=True,
    )
    completed = subprocess.run(
        [sys.executable, canonical_script, "--visibility", "public", workflows],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 2 and "regular non-symlink" in (completed.stdout + completed.stderr):
        ok("the exact canonical workflow path cannot be a symlink")
    else:
        fail(
            "the exact canonical workflow symlink was not refused "
            f"({completed.returncode}: {(completed.stdout + completed.stderr).strip()})"
        )
with tempfile.TemporaryDirectory() as repository:
    workflows = os.path.join(repository, ".github", "workflows")
    os.makedirs(workflows)
    policy_dir = os.path.join(repository, "scripts", "ci-gate")
    os.makedirs(policy_dir)
    canonical_script = os.path.join(policy_dir, "hosted-selector-policy.py")
    shutil.copy2(SCRIPT, canonical_script)
    os.mkfifo(os.path.join(workflows, "runner-canary.yml"))
    subprocess.run(["git", "init", "--quiet", repository], check=True)
    subprocess.run(["git", "-C", repository, "add", "scripts"], check=True)
    completed = subprocess.run(
        [sys.executable, canonical_script, "--visibility", "public", workflows],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 2 and "regular non-symlink" in (completed.stdout + completed.stderr):
        ok("the exact canonical workflow path cannot be a non-regular file")
    else:
        fail(
            "the exact canonical workflow non-regular file was not refused "
            f"({completed.returncode}: {(completed.stdout + completed.stderr).strip()})"
        )

# ---------------------------------------------------------------------------
# R5 — no off-path reference to the OS lane variables.
# ---------------------------------------------------------------------------
assert_violation("public", "os-lane-unsanctioned",
                 "R5 off-path reference to an OS lane variable",
                 "an OS lane reference outside the sanctioned path is refused")
assert_violation("public", "os-lane-reference-off-runs-on",
                 "R5 off-path reference to an OS lane variable",
                 "an OS lane reference outside runs-on is refused too")
assert_clean("public", "os-lane-bounded",
             "the sanctioned desktop-release path may name the OS lane variables",
             "desktop-release.yml")
for trigger_fixture, trigger_name in (
    ("os-lane-pull-request", "pull_request"),
    ("os-lane-push", "push"),
    ("os-lane-schedule", "schedule"),
):
    assert_violation(
        "public", trigger_fixture, "dispatch-only",
        f"an OS lane reachable from {trigger_name} is refused",
        "desktop-release.yml",
    )

# ---------------------------------------------------------------------------
# Undetermined outcomes. Each is a way the sweep ends up knowing nothing, and
# every one must be exit 2 — the failure mode guarded against is a check that
# reports green because it never looked.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as empty_dir:
    assert_undetermined("a directory containing no workflow files is undetermined",
                        "--visibility", "public", empty_dir)
assert_undetermined("no visibility at all is undetermined, never permissive",
                    os.path.join(FIXTURES, "metered-macos"))
assert_undetermined("an empty visibility is undetermined",
                    "--visibility", "", os.path.join(FIXTURES, "metered-macos"))
assert_undetermined("an unrecognized visibility is undetermined",
                    "--visibility", "internal", os.path.join(FIXTURES, "metered-macos"))
assert_undetermined("a --visibility flag with no value is undetermined", "--visibility")
assert_undetermined("no directory at all is undetermined", "--visibility", "public")
assert_undetermined("an unrecognized option is undetermined",
                    "--visibility", "public", "--allow-macos",
                    os.path.join(FIXTURES, "metered-macos"))
# Argument hardening (#818 review). Last-wins on a repeated flag is how a caller
# typo silently changes which policy tier is enforced; a stray positional is how
# a file gets sanctioned without anyone deciding to.
assert_undetermined("a repeated --visibility is undetermined, not last-wins",
                    "--visibility", "private", "--visibility", "public",
                    os.path.join(FIXTURES, "linux-hosted-literal"))
assert_undetermined("consumer mode cannot accidentally add visibility-scoped rules",
                    "--consumer-policy", "--visibility", "private",
                    os.path.join(FIXTURES, "metered-macos"))
assert_undetermined("consumer mode cannot sanction repository-local OS-lane paths",
                    "--consumer-policy", "--sanctioned", "desktop-release.yml",
                    os.path.join(FIXTURES, "metered-macos"))
assert_undetermined("a stray positional cannot silently sanction a workflow",
                    "--visibility", "public",
                    os.path.join(FIXTURES, "os-lane-unsanctioned"), "ci.yml")

# ---------------------------------------------------------------------------
# Evasion shapes. Each is a way a metered selector reaches a runner while
# looking like something a line-oriented scan would not match.
# ---------------------------------------------------------------------------
assert_violation("public", "evasion-yaml-suffix", "metered hosted runner family",
                 "a .yaml workflow is scanned, not evaded by the suffix (#401)")
assert_violation("public", "evasion-flow-sequence", "metered hosted runner family",
                 "a flow sequence is scanned")
assert_violation("public", "evasion-block-sequence", "metered hosted runner family",
                 "a block sequence, whose value is on the next line, is scanned")
assert_violation("public", "evasion-quoted", "metered hosted runner family",
                 "a quoted scalar is scanned")
assert_violation("public", "evasion-trailing-comment", "metered hosted runner family",
                 "a trailing comment does not hide the value it follows")
assert_violation("public", "evasion-matrix", "metered hosted runner family",
                 "a matrix indirection is resolved back to the values that place the job")
assert_violation("public", "unicode-job-name", "metered hosted runner family",
                 "a non-ASCII job key does not fall out of the job parse")
assert_violation("public", "evasion-quoted-key", "metered hosted runner family",
                 'a quoted "runs-on" key is still a selector')
assert_clean("public", "comment-mentions-metered",
             "a comment naming a metered family is prose, not a selector")
assert_violation("public", "matrix-unreferenced-key", "metered hosted runner family",
                 "a metered word anywhere in a referenced strategy block is refused, "
                 "conservatively")
assert_undetermined_fixture(
    "dynamic-format",
    "a format-built selector is unresolved rather than silently clean",
)
assert_undetermined_fixture(
    "dynamic-join-fromjson",
    "a join/fromJSON-built selector is unresolved rather than silently clean",
)
for source_fixture, source_name in (
    ("dynamic-input-direct", "a direct arbitrary input"),
    ("dynamic-input-fromjson", "a fromJSON-decoded arbitrary input"),
    ("dynamic-var-direct", "a direct unreviewed repository variable"),
    ("dynamic-var-fromjson", "a fromJSON-decoded unreviewed repository variable"),
    ("dynamic-needs-direct", "a direct unreviewed needs output"),
    ("dynamic-needs-fromjson", "a fromJSON-decoded unreviewed needs output"),
):
    assert_undetermined_fixture(
        source_fixture,
        f"{source_name} is unresolved rather than trusted as a routing source",
    )
assert_clean("public", "lane-with-fallback",
             "known fromJSON lane chains remain permitted selector expressions")

# ---------------------------------------------------------------------------
# Parser-level shapes GitHub accepts (#818 review). Each of these returned
# exit 0 against the line-oriented implementation this replaces — a false
# negative on a genuinely metered job, which is why the parser was replaced
# rather than patched.
# ---------------------------------------------------------------------------
assert_violation("public", "flow-job-body", "metered hosted runner family",
                 "a flow-style job body is a job, not a job name")
assert_violation("public", "flow-jobs-mapping", "metered hosted runner family",
                 "a flow-mapping jobs: block is still scanned")
assert_violation("public", "space-before-colon", "metered hosted runner family",
                 "a space before the colon does not drop the selector")
assert_violation("public", "runs-on-group-mapping", "metered hosted runner family",
                 "a runner-group mapping selector is resolved structurally")
assert_undetermined_fixture("unreadable-sibling",
                            "one unreadable file among readable siblings is undetermined")
assert_undetermined_fixture("multi-document",
                            "a multi-document workflow file is undetermined")
assert_undetermined_fixture("jobs-not-a-mapping",
                            "a jobs: value that is not a mapping is undetermined")
assert_undetermined_fixture("job-not-a-mapping",
                            "a job body that is not a mapping is undetermined")
assert_undetermined_fixture("invalid-yaml",
                            "a file PyYAML cannot parse is undetermined, never clean")
assert_undetermined_fixture("no-jobs",
                            "a workflow set that yields no jobs at all is undetermined")
# A size cap does NOT bound this: the bomb is under a kilobyte and expands
# without limit. GitHub Actions does not support anchors in workflows at all,
# so refusing them is both safe and correct — and it is the guard that actually
# addresses the class the cap only appears to.
assert_undetermined_fixture("alias-expansion",
                            "a YAML alias bomb is refused before it expands")

# ---------------------------------------------------------------------------
# The matrix indirection, against EVERY rule rather than only the two that
# happened to fold the strategy block in. This is the shape #810 proposes and
# AiB will adopt. Every one must be SANCTIONED in the invocation: without that,
# R5 fires on the OS-lane reference and returns exit 1 for the wrong reason,
# masking the exact gap under test.
# ---------------------------------------------------------------------------
assert_violation("public", "matrix-os-lane-no-timeout",
                 "R3 OS-lane job declares no timeout-minutes",
                 "an OS lane reached through a matrix is still bound by R3",
                 "desktop-release.yml")
assert_violation("public", "matrix-os-lane-fallback", "R4 OS lane carries a fallback tail",
                 "an OS lane reached through a matrix may not degrade to Linux",
                 "desktop-release.yml")
assert_violation("public", "matrix-os-lane-fallback", "exceeds the 60 ceiling",
                 "an OS lane reached through a matrix is still bound by the ceiling",
                 "desktop-release.yml")
assert_violation("public", "bracket-os-lane-no-timeout",
                 "R3 OS-lane job declares no timeout-minutes",
                 "single-quoted bracket OS lanes remain bound by R3",
                 "desktop-release.yml")
assert_violation("public", "bracket-os-lane-fallback",
                 "R4 OS lane carries a fallback tail",
                 "double-quoted bracket OS lanes remain bound by R4",
                 "desktop-release.yml")
assert_violation("public", "matrix-lane-without-terminal-landing",
                 "R4 lane selector with no terminal landing",
                 "an ordinary lane reached through a matrix still needs a terminal landing")
assert_violation("private", "matrix-linux-literal", "literal Linux hosted selector",
                 "a Linux literal placed through a matrix is still a literal on a "
                 "private repository")
assert_violation("public", "matrix-linux-literal", "rolling -latest image",
                 "a matrix cannot permit a rolling Linux image through public visibility")
assert_clean("public", "matrix-os-lane-bounded",
             "the conforming matrix form — both lanes, bounded, no fallback — is accepted",
             "desktop-release.yml")

# ---------------------------------------------------------------------------
# R4's terminal-landing rule covers every placement variable, not only the three
# lane names (#818 review). A chain on the fast lane or the overflow lane with no
# tail hits the #401 forever-queue mode just as squarely.
# ---------------------------------------------------------------------------
assert_violation("public", "fastlane-without-terminal-landing",
                 "R4 lane selector with no terminal landing",
                 "a fast-lane chain with no terminal landing is refused")
assert_violation("public", "overflow-without-terminal-landing",
                 "R4 lane selector with no terminal landing",
                 "an overflow-lane chain with no terminal landing is refused")

# ---------------------------------------------------------------------------
# Resource bounds. Generated rather than committed, because the point is the
# size itself.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as oversized_dir:
    with open(os.path.join(oversized_dir, "huge.yml"), "w", encoding="utf-8") as stream:
        stream.write("name: huge\non: push\njobs:\n  build:\n    runs-on: macos-15\n")
        stream.write("# padding\n" * 200000)
    assert_undetermined("a workflow file past the size ceiling is undetermined",
                        "--visibility", "public", oversized_dir)

# ---------------------------------------------------------------------------
# --dependency-path: the reusable workflow starts the script with `python3 -I -S`,
# which ignores PYTHONPATH and site-packages, so PyYAML arrives only through an
# explicit, absolute, non-symlink directory.
# ---------------------------------------------------------------------------
def run_isolated_policy(dependency_path: str, *arguments: str) -> tuple[int, str]:
    environment = os.environ.copy()
    environment["VERJSON_HOSTED_SELECTOR_POLICY_SHA"] = FIXTURE_CONTRACT_SHA
    completed = subprocess.run(
        [sys.executable, "-I", "-S", SCRIPT, "--dependency-path", dependency_path, *arguments],
        capture_output=True,
        env=environment,
        text=True,
    )
    return completed.returncode, completed.stdout + completed.stderr


import yaml  # noqa: E402 - located only to seed the isolated dependency directory

with tempfile.TemporaryDirectory() as dependency_root:
    dependency_root = os.path.realpath(dependency_root)
    dependency_dir = os.path.join(dependency_root, "python")
    shutil.copytree(os.path.dirname(yaml.__file__), os.path.join(dependency_dir, "yaml"))
    linked_dir = os.path.join(dependency_root, "linked")
    os.symlink(dependency_dir, linked_dir)
    cases = (
        ("an isolated start imports PyYAML from the explicit dependency path",
         dependency_dir, 0),
        ("a relative dependency path is undetermined",
         os.path.relpath(dependency_dir), 2),
        ("a symlinked dependency path is undetermined", linked_dir, 2),
        ("a missing dependency directory is undetermined",
         os.path.join(dependency_root, "absent"), 2),
    )
    for label, path, expected in cases:
        code, output = run_isolated_policy(path, "--visibility", "public", CANONICAL_WORKFLOWS)
        if code == expected:
            ok(label)
        else:
            fail(f"{label} (expected exit {expected}, got {code}: {output.strip()})")
    code, output = run_isolated_policy(dependency_root)
    if code == 2 and "UNDETERMINED" in output:
        ok("a dependency path without PyYAML cannot fall back to an ambient installation")
    else:
        fail(f"an isolated start without PyYAML did not fail closed (exit {code}: {output.strip()})")
    completed = subprocess.run(
        [sys.executable, "-I", "-S", SCRIPT, "--dependency-path"],
        capture_output=True, text=True,
    )
    if completed.returncode == 2:
        ok("--dependency-path without a directory is undetermined")
    else:
        fail(f"--dependency-path without a directory exited {completed.returncode}")

# ---------------------------------------------------------------------------
# The real tree this check gates, exercised through the same entry point CI uses.
# ---------------------------------------------------------------------------
code, output = run_policy("--visibility", "public",
                          os.path.join(os.path.dirname(ROOT), ".github", "workflows"))
if code == 0:
    ok("this repository's own workflows pass the policy")
else:
    fail(f"this repository's own workflows do not pass (exit {code}): {output.strip()}")

# ---------------------------------------------------------------------------
# Coverage guard. Porting this suite from shell silently dropped three fixtures
# — both R3 ceiling boundaries and the step-level timeout — which is precisely
# the "a rewrite quietly loses coverage" failure the fixtures exist to prevent.
# An unreferenced fixture is dead weight that reads as coverage, so the suite
# now fails rather than letting the next port lose one.
# ---------------------------------------------------------------------------
on_disk = {
    entry for entry in os.listdir(FIXTURES)
    if os.path.isdir(os.path.join(FIXTURES, entry))
}
unexercised = sorted(on_disk - exercised_fixtures)
if unexercised:
    fail(f"fixture(s) on disk that no assertion exercises: {', '.join(unexercised)}")
else:
    ok(f"every one of the {len(on_disk)} fixtures on disk is exercised by an assertion")

if failures == 0:
    print("All tests passed.")
    sys.exit(0)
print(f"{failures} test(s) failed.")
sys.exit(1)
