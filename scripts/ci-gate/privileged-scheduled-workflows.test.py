#!/usr/bin/env python3
"""Semantic contracts for scheduled workflows that receive ORG_ADMIN_TOKEN."""

from pathlib import Path
import re
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = ROOT / ".github/workflows/fleet-watchdog.yml"
ADMISSION = ROOT / ".github/workflows/runner-admission-reconcile.yml"
SECRET_SCOPE = ROOT / ".github/workflows/org-secret-scope-audit.yml"
RULESET_CONFORMANCE = ROOT / ".github/workflows/org-ruleset-conformance.yml"
CHECKOUT = re.compile(r"^actions/checkout@[0-9a-f]{40}$")
UNIQUE_SUFFIX = "${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}"
WATCHDOG_SOURCE = f".fleet-watchdog-source-{UNIQUE_SUFFIX}"
ADMISSION_SOURCE = f".runner-admission-reconcile-source-{UNIQUE_SUFFIX}"
SECRET_SCOPE_SOURCE = f".org-secret-scope-audit-source-{UNIQUE_SUFFIX}"
RULESET_CONFORMANCE_SOURCE = f".org-ruleset-conformance-source-{UNIQUE_SUFFIX}"
SETUP_PYTHON = re.compile(r"^actions/setup-python@[0-9a-f]{40}$")
# The arm audit reports through the finding-state adjudicator so a newly
# appearing finding is loud and a recorded one is quiet (#1409). Both the audit
# and its reviewed expectation are repository-controlled paths; neither may be
# redirected to an inherited or untrusted one.
ARM_AUDIT_COMMAND = (
    "python3 -I scripts/audit-finding-state.py"
    " --audit ai-review-arm"
    " --expectations config/audit-expected-findings.json"
    # The audit declares the exit statuses it uses, so any other status is a
    # crash rather than a verdict. Pinned here because widening the declared
    # set is how a crashed privileged audit would start reading as conformance.
    " --expect-status 0 --expect-status 1"
    " -- python3 -I scripts/ai-review-required-workflow-audit.py"
)


class ContractError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def require_keys(value: object, expected: set[str], location: str) -> dict:
    require(isinstance(value, dict), f"{location} must be a mapping")
    actual = set(value)
    require(actual == expected, f"{location} keys are {actual}, expected {expected}")
    return value


def workflow_on(document: dict) -> object:
    # PyYAML follows YAML 1.1 and parses an unquoted `on` key as boolean true.
    keys = [key for key in document if key is True or key == "on"]
    require(len(keys) == 1, "workflow must have exactly one on key")
    return document[keys[0]]


def validate_common(
    document: object, job_name: str, expected_jobs: set[str] | None = None
) -> tuple[dict, list[dict]]:
    require(isinstance(document, dict), "workflow root must be a mapping")
    root_keys = {"on" if key is True else key for key in document}
    require(
        root_keys == {"name", "on", "permissions", "concurrency", "jobs"},
        f"workflow keys expose an unexpected execution surface: {root_keys}",
    )

    triggers = require_keys(workflow_on(document), {"schedule"}, "on")
    require(isinstance(triggers["schedule"], list), "schedule must be a sequence")

    jobs = require_keys(document["jobs"], expected_jobs or {job_name}, "jobs")
    job = jobs[job_name]
    steps = job.get("steps") if isinstance(job, dict) else None
    require(isinstance(steps, list), f"jobs.{job_name}.steps must be a sequence")
    require(all(isinstance(step, dict) for step in steps), "every step must be a mapping")
    require(all(isinstance(step.get("name"), str) for step in steps), "every step must be named")
    return job, steps


def validate_isolation(job: dict, source: str, name: str) -> None:
    defaults = require_keys(job["defaults"], {"run"}, f"{name}.defaults")
    run_defaults = require_keys(defaults["run"], {"working-directory"}, f"{name}.defaults.run")
    require(
        run_defaults["working-directory"] == source,
        f"{name} run steps must use the isolated checkout",
    )


def validate_checkout(step: dict, name: str, source: str) -> None:
    require_keys(step, {"name", "uses", "with"}, name)
    require(isinstance(step["uses"], str), f"{name}.uses must be a string")
    require(CHECKOUT.fullmatch(step["uses"]) is not None, f"{name}.uses must be a full-SHA checkout pin")
    checkout_with = require_keys(
        step["with"], {"ref", "path", "persist-credentials"}, f"{name}.with"
    )
    require(checkout_with["ref"] == "${{ github.sha }}", f"{name}.with.ref must equal github.sha")
    require(checkout_with["path"] == source, f"{name} path must be unique per run, attempt, and job")
    require(checkout_with["persist-credentials"] is False, f"{name} must not persist credentials")


def validate_cleanup(step: dict, name: str, source: str) -> None:
    require_keys(step, {"name", "if", "working-directory", "run"}, name)
    require(step["if"] == "${{ always() }}", f"{name} must always run")
    require(
        step["working-directory"] == "${{ github.workspace }}",
        f"{name} must run from the workspace root",
    )
    require(step["run"] == f'rm -rf "{source}"', f"{name} must remove only its isolated checkout")


def validate_watchdog(document: object) -> None:
    expected_jobs = {"selector-health", "watchdog"}
    job, steps = validate_common(document, "watchdog", expected_jobs)
    require_keys(document["permissions"], {"actions", "contents"}, "permissions")
    require(document["permissions"]["actions"] == "read", "watchdog requires actions: read for cadence history")
    require(document["permissions"]["contents"] == "read", "watchdog contents permission changed")
    schedule = require_keys(workflow_on(document), {"schedule"}, "on")["schedule"]
    require(schedule == [{"cron": "*/15 * * * *"}], "watchdog nominal schedule changed")
    require_keys(job, {"runs-on", "defaults", "timeout-minutes", "steps"}, "jobs.watchdog")
    validate_isolation(job, WATCHDOG_SOURCE, "jobs.watchdog")
    require(len(steps) == 4, "watchdog must have exactly four steps")
    require(steps[0]["name"] == "Check out the watchdog", "watchdog checkout step name changed")
    validate_checkout(steps[0], "watchdog checkout", WATCHDOG_SOURCE)

    sweep = require_keys(steps[1], {"name", "env", "run"}, "watchdog sweep")
    require(
        sweep["name"] == "Confirm the retired poll watchdog has no candidates",
        "watchdog sweep step name changed",
    )
    env = require_keys(
        sweep["env"],
        {
            "GH_TOKEN",
            "WATCHDOG_ORG",
            "WATCHDOG_DRY_RUN",
            "WATCHDOG_POLL_STEP_DRY_RUN",
            "WATCHDOG_MIN_AGE_MINUTES",
            "WATCHDOG_MIN_POLL_MINUTES",
        },
        "watchdog sweep env",
    )
    require(env["GH_TOKEN"] == "${{ secrets.ORG_ADMIN_TOKEN }}", "watchdog token binding changed")
    require(sweep["run"] == "bash scripts/fleet-watchdog.sh", "watchdog command must remain static")

    cadence = require_keys(steps[2], {"name", "if", "env", "run"}, "watchdog cadence")
    require(cadence["name"] == "Record the observed scheduler interval", "watchdog cadence step name changed")
    require(cadence["if"] == "${{ always() }}", "watchdog cadence must run after a failed sweep")
    cadence_env = require_keys(
        cadence["env"],
        {"GH_TOKEN", "WATCHDOG_MAX_GAP_MINUTES"},
        "watchdog cadence env",
    )
    require(cadence_env["GH_TOKEN"] == "${{ github.token }}", "cadence probe must use the job token")
    require(cadence_env["WATCHDOG_MAX_GAP_MINUTES"] == "30", "cadence observation threshold changed")
    require(
        cadence["run"] == "python3 -I scripts/fleet-watchdog-cadence.py",
        "watchdog cadence command must remain static",
    )
    validate_cleanup(steps[3], "watchdog cleanup", WATCHDOG_SOURCE)

    selector_job = require_keys(
        document["jobs"]["selector-health"],
        {"runs-on", "defaults", "timeout-minutes", "steps"},
        "jobs.selector-health",
    )
    validate_isolation(selector_job, WATCHDOG_SOURCE, "jobs.selector-health")
    require(
        selector_job["timeout-minutes"] == 10,
        "selector health must have a budget independent from watchdog",
    )
    selector_steps = selector_job["steps"]
    require(
        isinstance(selector_steps, list) and len(selector_steps) == 3,
        "selector health must have exactly three steps",
    )
    require(
        selector_steps[0]["name"] == "Check out selector health",
        "selector-health checkout step name changed",
    )
    validate_checkout(selector_steps[0], "selector-health checkout", WATCHDOG_SOURCE)
    selector_health = require_keys(
        selector_steps[1], {"name", "env", "run"}, "selector health report"
    )
    require(
        selector_health["name"] == "Report unsatisfiable runner selectors",
        "watchdog selector-health step name changed",
    )
    selector_env = require_keys(
        selector_health["env"], {"GH_TOKEN", "ORG"}, "watchdog selector-health env"
    )
    require(
        selector_env["GH_TOKEN"] == "${{ secrets.ORG_ADMIN_TOKEN }}",
        "selector health token binding changed",
    )
    require(
        selector_health["run"] == "bash scripts/runner-selector-health.sh",
        "selector health must remain report-only",
    )
    validate_cleanup(selector_steps[2], "selector-health cleanup", WATCHDOG_SOURCE)


def validate_admission(document: object) -> None:
    job, steps = validate_common(document, "reconcile")
    require_keys(job, {"runs-on", "defaults", "timeout-minutes", "steps"}, "jobs.reconcile")
    validate_isolation(job, ADMISSION_SOURCE, "jobs.reconcile")
    require(len(steps) == 5, "runner admission must have exactly five steps")
    require(steps[0]["name"] == "Check out the runner admission reconciler", "admission checkout step name changed")
    validate_checkout(steps[0], "admission checkout", ADMISSION_SOURCE)

    reconcile = require_keys(steps[1], {"name", "id", "env", "run"}, "admission reconcile")
    require(reconcile["name"] == "Reconcile runner admission against routing policy", "reconcile step name changed")
    require(reconcile["id"] == "reconcile", "reconcile step id changed")
    env = require_keys(
        reconcile["env"],
        {"GH_TOKEN", "ORG", "GENERAL_GROUP_NAME", "UNTRUSTED_GROUP_NAME"},
        "admission reconcile env",
    )
    require(env["GH_TOKEN"] == "${{ secrets.ORG_ADMIN_TOKEN }}", "admission token binding changed")
    # The two group names are pinned to exact expressions, not merely allowed to
    # exist. Widening the key set alone would let this privileged step carry an
    # arbitrary literal, which is the shape #350 is about. Sourcing them from org
    # variables keeps a pool rename a variable flip (#401) while leaving the value
    # under the same org-admin control as the token itself.
    require(
        env["GENERAL_GROUP_NAME"] == "${{ vars.CI_RUNNER_GENERAL_GROUP || 'DigitalOcean' }}",
        "general runner group must come from the org variable with its fallback",
    )
    require(
        env["UNTRUSTED_GROUP_NAME"] == "${{ vars.CI_RUNNER_UNTRUSTED_GROUP }}",
        "untrusted runner group must come from the org variable, with no fallback naming a deleted group",
    )
    require(isinstance(reconcile["run"], str), "admission reconcile command must be a string")
    require(
        'bash scripts/ci-gate/runner-admission-reconcile.sh' in reconcile["run"],
        "admission reconcile command changed",
    )

    expected_followups = [
        (
            "Reopen or update the durable drift issue",
            "1",
            {"GH_TOKEN", "REPORT", "MARKER", "DRIFT_ISSUE", "REPORT_ACTOR_ID"},
        ),
        (
            "Close the durable drift issue once the org is clean again",
            "0",
            {"GH_TOKEN", "DRIFT_ISSUE"},
        ),
    ]
    for step, (name, code, env_keys) in zip(steps[2:], expected_followups):
        followup = require_keys(step, {"name", "if", "env", "run"}, name)
        require(followup["name"] == name, f"{name} step name changed")
        require(followup["if"] == f"steps.reconcile.outputs.code == '{code}'", f"{name} condition changed")
        followup_env = require_keys(followup["env"], env_keys, f"{name} env")
        require(followup_env["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}", f"{name} gained a privileged token")
        require(followup_env["DRIFT_ISSUE"] == "820", f"{name} must reuse issue 820")
        if code == "1":
            require(
                followup_env["REPORT_ACTOR_ID"] == "41898282",
                f"{name} must bind report comments to github-actions[bot]'s immutable id",
            )
    validate_cleanup(steps[4], "admission cleanup", ADMISSION_SOURCE)


def validate_secret_scope(document: object) -> None:
    job, steps = validate_common(document, "audit")
    permissions = require_keys(document["permissions"], {"contents"}, "permissions")
    require(permissions["contents"] == "read", "secret-scope contents permission changed")
    require(
        workflow_on(document)["schedule"] == [{"cron": "41 9 * * *"}],
        "secret-scope schedule changed",
    )
    concurrency = require_keys(document["concurrency"], {"group", "cancel-in-progress"}, "concurrency")
    require(concurrency["group"] == "org-secret-scope-audit", "secret-scope concurrency group changed")
    require(concurrency["cancel-in-progress"] is False, "secret-scope runs must not cancel in progress")
    require_keys(job, {"runs-on", "defaults", "timeout-minutes", "steps"}, "jobs.audit")
    validate_isolation(job, SECRET_SCOPE_SOURCE, "jobs.audit")
    require(
        job["runs-on"]
        == "${{ fromJSON(vars.CI_LANE_PRIVILEGED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') }}",
        "secret-scope runner route changed",
    )
    require(job["timeout-minutes"] == 10, "secret-scope timeout changed")
    require(len(steps) == 3, "secret-scope audit must have exactly three steps")
    require(steps[0].get("name") == "Check out the secret-scope audit", "secret-scope checkout name changed")
    validate_checkout(steps[0], "secret-scope checkout", SECRET_SCOPE_SOURCE)
    audit = require_keys(steps[1], {"name", "env", "run"}, "secret-scope audit")
    require(audit["name"] == "Compare live scope with reviewed policy", "secret-scope audit name changed")
    env = require_keys(audit["env"], {"GH_TOKEN"}, "secret-scope audit env")
    require(env["GH_TOKEN"] == "${{ secrets.ORG_ADMIN_TOKEN }}", "secret-scope token binding changed")
    require(audit["run"] == "python3 -I scripts/org-secret-scope-audit.py", "secret-scope command changed")
    validate_cleanup(steps[2], "secret-scope cleanup", SECRET_SCOPE_SOURCE)


def validate_arm_audit(document: object) -> None:
    """The arm audit has to be scheduled, or it is not a control.

    It sat unrunnable and uninvoked while the fleet gap it exists to catch went
    undetected (#1404). It shares this workflow's schedule but keeps its own job
    so that its exit status is attributable and cannot mask the release
    authorization conformance result, and it is deliberately not wired into any
    pull-request gate: it reports drift, it does not block merges.
    """
    job = require_keys(
        document["jobs"]["arm-audit"],
        {"runs-on", "defaults", "timeout-minutes", "steps"},
        "jobs.arm-audit",
    )
    validate_isolation(job, RULESET_CONFORMANCE_SOURCE, "jobs.arm-audit")
    require(
        job["runs-on"]
        == "${{ fromJSON(vars.CI_LANE_PRIVILEGED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') }}",
        "arm-audit runner route changed",
    )
    steps = job["steps"]
    require(isinstance(steps, list) and len(steps) == 5, "arm audit must have exactly five steps")
    require(
        steps[0].get("name") == "Check out the authorization arm audit",
        "arm-audit checkout name changed",
    )
    validate_checkout(steps[0], "arm-audit checkout", RULESET_CONFORMANCE_SOURCE)
    provision = require_keys(steps[1], {"name", "uses", "with"}, "arm-audit python")
    require(
        SETUP_PYTHON.fullmatch(provision["uses"]) is not None,
        "arm-audit Python provisioning must be a full-SHA pin",
    )
    install = require_keys(steps[2], {"name", "run"}, "arm-audit dependencies")
    require(
        "--require-hashes" in install["run"]
        and "scripts/actions-ci-python.requirements.txt" in install["run"],
        "arm-audit dependencies must come from the hash-pinned requirements",
    )
    audit = require_keys(steps[3], {"name", "env", "run"}, "arm audit")
    require(
        audit["name"] == "Audit the AI authorization arm against its reviewed contract",
        "arm-audit step name changed",
    )
    env = require_keys(audit["env"], {"GH_TOKEN"}, "arm audit env")
    require(env["GH_TOKEN"] == "${{ secrets.ORG_ADMIN_TOKEN }}", "arm-audit token binding changed")
    require(
        audit["run"] == ARM_AUDIT_COMMAND,
        "arm-audit command must remain the read-only audit behind its reviewed expectation",
    )
    validate_cleanup(steps[4], "arm-audit cleanup", RULESET_CONFORMANCE_SOURCE)


def validate_ruleset_conformance(document: object) -> None:
    job, steps = validate_common(document, "audit", {"audit", "arm-audit"})
    validate_arm_audit(document)
    permissions = require_keys(document["permissions"], {"contents"}, "permissions")
    require(permissions["contents"] == "read", "ruleset-conformance contents permission changed")
    require(
        workflow_on(document)["schedule"] == [{"cron": "53 9 * * *"}],
        "ruleset-conformance schedule changed",
    )
    concurrency = require_keys(document["concurrency"], {"group", "cancel-in-progress"}, "concurrency")
    require(
        concurrency["group"] == "org-ruleset-conformance",
        "ruleset-conformance concurrency group changed",
    )
    require(
        concurrency["cancel-in-progress"] is False,
        "ruleset-conformance runs must not cancel in progress",
    )
    require_keys(job, {"runs-on", "defaults", "timeout-minutes", "steps"}, "jobs.audit")
    validate_isolation(job, RULESET_CONFORMANCE_SOURCE, "jobs.audit")
    require(
        job["runs-on"]
        == "${{ fromJSON(vars.CI_LANE_PRIVILEGED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') }}",
        "ruleset-conformance runner route changed",
    )
    require(job["timeout-minutes"] == 10, "ruleset-conformance timeout changed")
    require(len(steps) == 4, "ruleset-conformance audit must have exactly four steps")
    require(
        steps[0].get("name") == "Check out the ruleset conformance audit",
        "ruleset-conformance checkout name changed",
    )
    validate_checkout(
        steps[0], "ruleset-conformance checkout", RULESET_CONFORMANCE_SOURCE
    )
    audit = require_keys(steps[1], {"name", "env", "run"}, "ruleset-conformance audit")
    require(
        audit["name"] == "Verify release authorization across ~DEFAULT_BRANCH rulesets",
        "ruleset-conformance audit name changed",
    )
    env = require_keys(audit["env"], {"GH_TOKEN"}, "ruleset-conformance audit env")
    require(
        env["GH_TOKEN"] == "${{ secrets.ORG_ADMIN_TOKEN }}",
        "ruleset-conformance token binding changed",
    )
    require(
        audit["run"] == "python3 -I scripts/org-ruleset-conformance.py",
        "ruleset-conformance command changed",
    )
    package_audit = require_keys(
        steps[2], {"name", "env", "run"}, "package type-surface audit"
    )
    require(
        package_audit["name"] == "Verify package type-surface rulesets",
        "package type-surface audit name changed",
    )
    package_env = require_keys(
        package_audit["env"], {"GH_TOKEN"}, "package type-surface audit env"
    )
    require(
        package_env["GH_TOKEN"] == "${{ secrets.ORG_ADMIN_TOKEN }}",
        "package type-surface audit token binding changed",
    )
    require(
        package_audit["run"]
        == "python3 -I scripts/package-type-surface-ruleset.py audit",
        "package type-surface audit command changed",
    )
    validate_cleanup(
        steps[3], "ruleset-conformance cleanup", RULESET_CONFORMANCE_SOURCE
    )


def load(text: str) -> object:
    return yaml.safe_load(text)


def expect_valid(label: str, validator, text: str) -> int:
    try:
        validator(load(text))
    except (ContractError, yaml.YAMLError) as error:
        print(f"FAIL - {label}: {error}")
        return 1
    print(f"ok   - {label}")
    return 0


def expect_invalid(label: str, validator, text: str) -> int:
    try:
        document = load(text)
    except yaml.YAMLError as error:
        print(f"FAIL - {label}: mutation is not valid YAML: {error}")
        return 1
    try:
        validator(document)
    except ContractError:
        print(f"ok   - {label}")
        return 0
    print(f"FAIL - {label}: mutation escaped the semantic contract")
    return 1


def mutations(text: str, next_step: str) -> tuple[str, str]:
    bare_dash = text.replace(
        next_step,
        "      -\n        run: echo bare-dash-bypass\n" + next_step,
        1,
    )
    quoted_key = text.replace(
        "          persist-credentials: false",
        '          persist-credentials: false\n        "run": echo quoted-key-bypass',
        1,
    )
    require(bare_dash != text and quoted_key != text, "mutation fixture marker not found")
    return bare_dash, quoted_key


def main() -> int:
    watchdog_text = WATCHDOG.read_text()
    admission_text = ADMISSION.read_text()
    secret_scope_text = SECRET_SCOPE.read_text()
    ruleset_conformance_text = RULESET_CONFORMANCE.read_text()
    failures = 0
    failures += expect_valid("fleet watchdog is schedule-only and event-SHA-bound", validate_watchdog, watchdog_text)
    failures += expect_valid("runner admission is schedule-only and event-SHA-bound", validate_admission, admission_text)
    failures += expect_valid("secret-scope audit is schedule-only and event-SHA-bound", validate_secret_scope, secret_scope_text)
    failures += expect_valid(
        "ruleset-conformance audit is schedule-only and event-SHA-bound",
        validate_ruleset_conformance,
        ruleset_conformance_text,
    )

    cases = [
        (
            "fleet watchdog",
            validate_watchdog,
            watchdog_text,
            "      - name: Confirm the retired poll watchdog has no candidates",
        ),
        (
            "runner admission",
            validate_admission,
            admission_text,
            "      - name: Reconcile runner admission against routing policy",
        ),
        (
            "secret-scope audit",
            validate_secret_scope,
            secret_scope_text,
            "      - name: Compare live scope with reviewed policy",
        ),
        (
            "ruleset-conformance audit",
            validate_ruleset_conformance,
            ruleset_conformance_text,
            "      - name: Verify release authorization across ~DEFAULT_BRANCH rulesets",
        ),
    ]
    for label, validator, text, marker in cases:
        try:
            bare_dash, quoted_key = mutations(text, marker)
        except ContractError as error:
            print(f"FAIL - {label} mutation setup: {error}")
            failures += 1
            continue
        failures += expect_invalid(f"{label} rejects a bare-dash executable step", validator, bare_dash)
        failures += expect_invalid(f"{label} rejects a quoted executable key", validator, quoted_key)

    secret_scope_mutations = {
        "manual dispatch": secret_scope_text.replace(
            '    - cron: "41 9 * * *"\n', '    - cron: "41 9 * * *"\n  workflow_dispatch: {}\n', 1
        ),
        "widened permissions": secret_scope_text.replace("  contents: read", "  contents: write", 1),
        "missing concurrency": re.sub(r"\nconcurrency:\n(?:  .*\n){2}", "\n", secret_scope_text, count=1),
        "cancelling concurrency": secret_scope_text.replace("cancel-in-progress: false", "cancel-in-progress: true", 1),
        "missing timeout": secret_scope_text.replace("    timeout-minutes: 10\n", "", 1),
        "mutable checkout": secret_scope_text.replace(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@v7", 1
        ),
        "unsafe runner route": secret_scope_text.replace("vars.CI_LANE_PRIVILEGED", "vars.CI_RUNNER_PRIVILEGED", 1),
    }
    for label, mutation in secret_scope_mutations.items():
        require(mutation != secret_scope_text, f"secret-scope {label} mutation fixture marker not found")
        failures += expect_invalid(f"secret-scope rejects {label}", validate_secret_scope, mutation)

    ruleset_conformance_mutations = {
        "manual dispatch": ruleset_conformance_text.replace(
            '    - cron: "53 9 * * *"\n',
            '    - cron: "53 9 * * *"\n  workflow_dispatch: {}\n',
            1,
        ),
        "widened permissions": ruleset_conformance_text.replace(
            "  contents: read", "  contents: write", 1
        ),
        "missing concurrency": re.sub(
            r"\nconcurrency:\n(?:  .*\n){2}", "\n", ruleset_conformance_text, count=1
        ),
        "cancelling concurrency": ruleset_conformance_text.replace(
            "cancel-in-progress: false", "cancel-in-progress: true", 1
        ),
        "missing timeout": ruleset_conformance_text.replace(
            "    timeout-minutes: 10\n", "", 1
        ),
        "mutable checkout": ruleset_conformance_text.replace(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/checkout@v7",
            1,
        ),
        "unsafe runner route": ruleset_conformance_text.replace(
            "vars.CI_LANE_PRIVILEGED", "vars.CI_RUNNER_PRIVILEGED", 1
        ),
        "inherited policy path": ruleset_conformance_text.replace(
            "          GH_TOKEN: ${{ secrets.ORG_ADMIN_TOKEN }}\n",
            "          GH_TOKEN: ${{ secrets.ORG_ADMIN_TOKEN }}\n"
            "          ORG_RULESET_POLICY: /tmp/untrusted.json\n",
            1,
        ),
        "inherited expectation path": ruleset_conformance_text.replace(
            "--expectations config/audit-expected-findings.json",
            "--expectations /tmp/untrusted.json",
            1,
        ),
        "unadjudicated arm audit": ruleset_conformance_text.replace(
            ARM_AUDIT_COMMAND,
            "python3 -I scripts/ai-review-required-workflow-audit.py",
            1,
        ),
        "test policy argument": ruleset_conformance_text.replace(
            "run: python3 -I scripts/org-ruleset-conformance.py",
            "run: python3 -I scripts/org-ruleset-conformance.py --test-policy /tmp/untrusted.json",
            1,
        ),
    }
    for label, mutation in ruleset_conformance_mutations.items():
        require(
            mutation != ruleset_conformance_text,
            f"ruleset-conformance {label} mutation fixture marker not found",
        )
        failures += expect_invalid(
            f"ruleset-conformance rejects {label}",
            validate_ruleset_conformance,
            mutation,
        )

    if failures:
        print(f"{failures} test(s) failed.")
        return 1
    print("All tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
