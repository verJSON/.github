"""Adversarial contract for read-only Renovate compatibility App tokens."""

from __future__ import annotations

import copy
import pathlib
import re

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
ACTION = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
CLIENT_ID = "${{ vars.RENOVATE_COMPATIBILITY_CLIENT_ID }}"
PRIVATE_KEY = "${{ secrets.RENOVATE_COMPATIBILITY_APP_PRIVATE_KEY }}"
TOKEN = "${{ steps.compatibility-app-token.outputs.token }}"
MANAGED_REPOSITORIES = (
    ".github,agents,renovate-config,verjson-agents,verjson-cli,verjson-cli-cloud,"
    "verjson-cli-projects,verjson-eslint-config,verjson-git-runners,verjson-infra,"
    "verjson-tsconfig"
)


def load(name: str) -> tuple[dict, str]:
    raw = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    return yaml.safe_load(raw), raw


def mint(job: dict) -> dict:
    return next(step for step in job["steps"] if step.get("id") == "compatibility-app-token")


def validate_planner(document: dict, raw: str) -> list[str]:
    problems: list[str] = []
    job = document["jobs"]["plan"]
    token_step = mint(job)
    checkout = next(step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@"))
    expected = {
        "client-id": CLIENT_ID,
        "private-key": PRIVATE_KEY,
        "owner": "Verjson",
        "repositories": "renovate-config",
        "permission-contents": "read",
    }
    if document.get("permissions") != {"contents": "read"}:
        problems.append("planner GITHUB_TOKEN is not read-only")
    if token_step.get("uses") != ACTION or token_step.get("with") != expected:
        problems.append("planner mint is not exactly policy-repository contents-read")
    if (checkout.get("with") or {}).get("token") != TOKEN:
        problems.append("planner checkout does not receive only the minted token")
    if (checkout.get("with") or {}).get("persist-credentials") is not False:
        problems.append("planner persists the minted token")
    problems.extend(validate_common(raw))
    return problems


def validate_reconciler(document: dict, raw: str) -> list[str]:
    problems: list[str] = []
    job = document["jobs"]["observe"]
    token_step = mint(job)
    expected = {
        "client-id": CLIENT_ID,
        "private-key": PRIVATE_KEY,
        "owner": "Verjson",
        "repositories": MANAGED_REPOSITORIES,
        "permission-actions": "read",
        "permission-checks": "read",
        "permission-contents": "read",
        "permission-pull-requests": "read",
        "permission-statuses": "read",
    }
    if document.get("permissions") != {"contents": "read"}:
        problems.append("reconciler GITHUB_TOKEN is not read-only")
    if token_step.get("uses") != ACTION or token_step.get("with") != expected:
        problems.append("reconciler mint scope or permissions widened")
    token_consumers = [
        step.get("name")
        for step in job["steps"]
        if ((step.get("env") or {}).get("GH_TOKEN") == TOKEN)
    ]
    if token_consumers != [
        "Fetch the reviewed policy registry",
        "Inventory Renovate failures for controlled retry triage",
    ]:
        problems.append("reconciler token delivery escaped the two read-only calls")
    if 'mode:"observe-only"' not in raw:
        problems.append("observe-only receipt is absent")
    problems.extend(validate_common(raw))
    return problems


def validate_common(raw: str) -> list[str]:
    problems: list[str] = []
    if "RENOVATE_COMPATIBILITY_PAT" in raw or "ORG_ADMIN_TOKEN" in raw:
        problems.append("a broad credential path remains")
    if not re.search(
        r'\[\[ -z "\$RENOVATE_COMPATIBILITY_CLIENT_ID" \|\| '
        r'"\$RENOVATE_COMPATIBILITY_CLIENT_ID" =~ \^\[0-9\]\+\$ \]\]',
        raw,
    ):
        problems.append("missing or numeric client IDs do not fail closed")
    return problems


def require_rejected(label: str, validator, document: dict, raw: str) -> None:
    if not validator(document, raw):
        raise AssertionError(f"mutation survived: {label}")
    print(f"ok - rejects {label}")


def main() -> None:
    planner, planner_raw = load("renovate-grouping-plan.yml")
    reconciler, reconciler_raw = load("renovate-compatibility-reconcile.yml")
    assert not validate_planner(planner, planner_raw), validate_planner(planner, planner_raw)
    assert not validate_reconciler(reconciler, reconciler_raw), validate_reconciler(reconciler, reconciler_raw)
    print("ok - exact repository scope, read permissions, and token delivery are enforced")

    mutations = []
    mutant = copy.deepcopy(planner)
    mint(mutant["jobs"]["plan"])["with"]["repositories"] = ".github,renovate-config"
    mutations.append(("planner repository widening", validate_planner, mutant, planner_raw))
    mutant = copy.deepcopy(planner)
    mint(mutant["jobs"]["plan"])["with"]["permission-pull-requests"] = "read"
    mutations.append(("planner permission widening", validate_planner, mutant, planner_raw))
    mutant = copy.deepcopy(reconciler)
    mint(mutant["jobs"]["observe"])["with"]["repositories"] = "*"
    mutations.append(("reconciler repository widening", validate_reconciler, mutant, reconciler_raw))
    mutant = copy.deepcopy(reconciler)
    mint(mutant["jobs"]["observe"])["with"]["permission-issues"] = "write"
    mutations.append(("reconciler write permission", validate_reconciler, mutant, reconciler_raw))
    mutant = copy.deepcopy(reconciler)
    mint(mutant["jobs"]["observe"])["uses"] = "actions/create-github-app-token@v3"
    mutations.append(("mutable token action", validate_reconciler, mutant, reconciler_raw))
    mutant = copy.deepcopy(reconciler)
    mint(mutant["jobs"]["observe"])["with"]["app-id"] = mint(mutant["jobs"]["observe"])["with"].pop("client-id")
    mutations.append(("numeric App-ID interface", validate_reconciler, mutant, reconciler_raw))
    mutant = copy.deepcopy(reconciler)
    mint(mutant["jobs"]["observe"])["with"]["owner"] = "${{ github.repository_owner }}"
    mutations.append(("attacker-influenced installation owner", validate_reconciler, mutant, reconciler_raw))
    mutant = copy.deepcopy(reconciler)
    mint(mutant["jobs"]["observe"])["with"].pop("private-key")
    mutations.append(("missing private key", validate_reconciler, mutant, reconciler_raw))
    mutant = copy.deepcopy(reconciler)
    mint(mutant["jobs"]["observe"])["with"]["skip-token-revoke"] = True
    mutations.append(("token lifetime widening", validate_reconciler, mutant, reconciler_raw))
    for mutation in mutations:
        require_rejected(*mutation)


if __name__ == "__main__":
    main()
