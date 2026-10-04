"""Adversarial contract for terminal merge App-token isolation."""

from __future__ import annotations

import copy
import json
import pathlib

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ai-privileged-merge.yml"
TERMINAL = ROOT / "scripts/ci-gate/terminal-merge.sh"
ROLE_INVENTORY = ROOT / "config/app-role-custody-inventory.json"
ACTION = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
APP_TOKEN = "${{ steps.merge-app-token.outputs.token }}"


def workflow_call(document: dict) -> dict:
    return document.get("on", document.get(True))["workflow_call"]


def named_step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def validate(document: dict, raw: str) -> list[str]:
    problems: list[str] = []
    call = workflow_call(document)
    inputs = call.get("inputs") or {}
    secrets = call.get("secrets") or {}
    job = document["jobs"]["privileged_merge"]
    steps = job["steps"]
    preflight = named_step(job, "Validate terminal merge App and target repository")
    authorize = named_step(job, "Authorize terminal merge from trusted metadata")
    receipt = named_step(job, "Revalidate independent review receipt")
    capture = named_step(job, "Capture closing issue references")
    mint = named_step(job, "Mint exact-repository terminal merge App token")
    merge = named_step(job, "Merge the authorized head")
    export = named_step(job, "Export terminal merge cleanup receipt")
    report = named_step(job, "Report closing issue outcomes")
    confirm = named_step(job, "Confirm terminal merge state")
    cleanup_job = document["jobs"]["cleanup_arm_receipt"]
    cleanup = named_step(
        document["jobs"]["cleanup_arm_receipt"],
        "Delete consumed arm receipt artifact",
    )

    if not (inputs.get("merge_app_client_id") or {}).get("required"):
        problems.append("merge App client-ID input is not required")
    if secrets or not (inputs.get("merge_environment") or {}).get("required"):
        problems.append("reusable contract must require the merge environment without forwarded keys")
    if job.get("permissions") != {
        "actions": "read",
        "checks": "read",
        "contents": "read",
        "issues": "read",
        "pull-requests": "read",
    }:
        problems.append("authorization repository token is not exactly read-only")
    preflight_run = preflight.get("run", "")
    for required in (
        '[[ ! "$MERGE_APP_CLIENT_ID" =~ ^Iv[0-9A-Za-z]{18}$ ]]',
        '"$TARGET_REPO" = "$REPOSITORY_CONTEXT"',
        'owner="${TARGET_REPO%%/*}"',
        'repository="${TARGET_REPO#*/}"',
    ):
        if required not in preflight_run:
            problems.append(f"target/client-ID preflight lacks {required}")
    expected_mint = {
        "client-id": "${{ inputs.merge_app_client_id || vars.MERGE_APP_CLIENT_ID }}",
        "private-key": "${{ secrets.MERGE_APP_PRIVATE_KEY }}",
        "owner": "${{ steps.merge-target.outputs.owner }}",
        "repositories": "${{ steps.merge-target.outputs.repository }}",
        "permission-contents": "write",
        "permission-pull-requests": "write",
    }
    if mint.get("uses") != ACTION or mint.get("with") != expected_mint:
        problems.append("mint is not pinned to exact repository and merge permissions")
    terminal_condition = (
        "steps.authorize-merge.outputs.authorized == 'true' && "
        "steps.independent-review.outputs.authorized == 'true'"
    )
    if mint.get("if") != terminal_condition:
        problems.append("token can mint before successful authorization")
    if (authorize.get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
        problems.append("authorization does not use the read-only repository token")
    if (receipt.get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
        problems.append("independent-review revalidation does not use the read-only repository token")
    if (capture.get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
        problems.append("closing issue capture does not use the read-only repository token")
    if (report.get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
        problems.append("closing issue report does not use the read-only repository token")
    if (confirm.get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
        problems.append("confirmation does not return to the read-only repository token")
    if (cleanup.get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
        problems.append("receipt cleanup does not use the read-only repository token")
    if (merge.get("env") or {}) != {
        "GH_TOKEN": APP_TOKEN,
        "AUTHORIZED_BASE_SHA": "${{ steps.authorize-merge.outputs.authorized_base_sha }}",
        "DEFAULT_BRANCH": "${{ steps.authorize-merge.outputs.default_branch }}",
    }:
        problems.append("terminal token delivery is not isolated to merge")
    merge_run = merge.get("run", "")
    if merge_run.strip().splitlines()[-1] != "bash .gate-trust/scripts/ci-gate/terminal-merge.sh":
        problems.append("terminal token step does not delegate to the immutable live-base verifier")
    if "closing-issue-visibility.py capture" not in capture.get("run", ""):
        problems.append("closing issue references are not captured before merge")
    if "closing-issue-visibility.py report" not in report.get("run", ""):
        problems.append("closing issue outcomes are not reported after merge")
    terminal_run = TERMINAL.read_text(encoding="utf-8")
    for required in (
        'repos/$TARGET_REPO/pulls/$PR_NUMBER',
        # The live-base read is a PATH segment, so $DEFAULT_BRANCH reaches it only through
        # the @uri encoder -- without the query-value gsub, which would restore "/".
        '''branch_path="$(jq -rn --arg branch "$DEFAULT_BRANCH" '$branch | @uri')"''',
        'repos/$TARGET_REPO/git/ref/heads/$branch_path',
        '.base.ref == $branch and .base.sha == $base',
    ".draft == false",
        'index("HOLD")',
        'index("DO NOT MERGE")',
        'current_base_sha" = "$AUTHORIZED_BASE_SHA',
        'gh pr merge "$PR_NUMBER"',
        "--admin", "--squash", "--match-head-commit",
    ):
        if required not in terminal_run:
            problems.append(f"terminal live-base verifier lacks {required}")
    token_consumers = [step.get("name") for step in steps if APP_TOKEN in str(step)]
    if token_consumers != ["Merge the authorized head"]:
        problems.append("merge token escaped the terminal operation")
    report_condition = terminal_condition + " && steps.terminal-merge.outcome == 'success'"
    confirm_condition = "${{ always() && " + report_condition + " }}"
    cleanup_condition = "${{ always() && needs.privileged_merge.outputs.terminal_merge_succeeded == 'true' }}"
    if (
        capture.get("if") != terminal_condition
        or merge.get("if") != terminal_condition
        or report.get("if") != report_condition
        or confirm.get("if") != confirm_condition
        or confirm.get("id") != "confirm-terminal-merge"
        or export.get("if") != "steps.confirm-terminal-merge.outcome == 'success'"
        or cleanup_job.get("if") != cleanup_condition
    ):
        problems.append("terminal and closing issue operations do not preserve authorization order")
    order = [
        steps.index(step)
        for step in (preflight, authorize, receipt, capture, mint, merge, confirm, export, report)
    ]
    if order != sorted(order) or len(set(order)) != len(order):
        problems.append("preflight/authorization/mint/merge/confirmation ordering drifted")
    if "ORG_ADMIN_TOKEN" in raw:
        problems.append("terminal workflow still depends on ORG_ADMIN_TOKEN")
    return problems


def require_rejected(label: str, document: dict, raw: str) -> None:
    if not validate(document, raw):
        raise AssertionError(f"mutation survived: {label}")
    print(f"ok - rejects {label}")


def main() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    assert not validate(document, raw), validate(document, raw)
    inventory = json.loads(ROLE_INVENTORY.read_text(encoding="utf-8"))
    assert inventory["roles"]["merge"]["permissionCeiling"] == {
        "contents": "write",
        "metadata": "read",
        "pull_requests": "write",
    }
    print("ok - merge App token is exact-repository, non-widenable, and terminal-only")
    job = document["jobs"]["privileged_merge"]

    mutations: list[tuple[str, dict, str]] = []
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Mint exact-repository terminal merge App token")["with"]["repositories"] = "*"
    mutations.append(("repository wildcard", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Mint exact-repository terminal merge App token")["with"]["owner"] = "${{ inputs.target_owner }}"
    mutations.append(("attacker-controlled installation owner", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Mint exact-repository terminal merge App token")["with"]["permission-actions"] = "write"
    mutations.append(("widened App permission", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Mint exact-repository terminal merge App token")["with"]["permission-issues"] = "write"
    mutations.append(("merge App issues write permission", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Mint exact-repository terminal merge App token")["uses"] = "actions/create-github-app-token@v3"
    mutations.append(("mutable token action", mutant, raw))
    mutant = copy.deepcopy(document)
    mint = named_step(mutant["jobs"]["privileged_merge"], "Mint exact-repository terminal merge App token")
    mint["with"]["app-id"] = mint["with"].pop("client-id")
    mutations.append(("numeric App-ID interface", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Authorize terminal merge from trusted metadata")["env"]["GH_TOKEN"] = APP_TOKEN
    mutations.append(("merge token in authorization", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Capture closing issue references")["env"]["GH_TOKEN"] = APP_TOKEN
    mutations.append(("merge token in closing issue capture", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Report closing issue outcomes")["env"]["GH_TOKEN"] = APP_TOKEN
    mutations.append(("merge token in closing issue report", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Confirm terminal merge state")["env"]["GH_TOKEN"] = APP_TOKEN
    mutations.append(("merge token in post-merge confirmation", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["cleanup_arm_receipt"], "Delete consumed arm receipt artifact")["env"]["GH_TOKEN"] = APP_TOKEN
    mutations.append(("merge token in arm receipt cleanup", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Merge the authorized head")["run"] += '\ngh api orgs/Verjson\n'
    mutations.append(("second privileged operation", mutant, raw))
    mutant = copy.deepcopy(document)
    named_step(mutant["jobs"]["privileged_merge"], "Mint exact-repository terminal merge App token").pop("if")
    mutations.append(("mint before authorization", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["privileged_merge"]["permissions"].pop("issues")
    mutations.append(("missing caller issues read permission", mutant, raw))
    mutant = copy.deepcopy(document)
    workflow_call(mutant).setdefault("secrets", {})["ORG_ADMIN_TOKEN"] = {"required": True}
    mutations.append(("PAT fallback secret", mutant, raw + "\nORG_ADMIN_TOKEN"))
    for mutation in mutations:
        require_rejected(*mutation)


if __name__ == "__main__":
    main()
