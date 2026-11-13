#!/usr/bin/env python3
"""Audit App key storage and main-only admission using metadata, never key values."""

import argparse
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "config/app-key-roles.json"
SECRET_REFERENCE = re.compile(
    r"\bsecrets\s*(?:\.\s*([A-Za-z0-9_-]+)"
    r"|\[\s*(?:'([^']*)'|\"([^\"]*)\")\s*\])",
    re.IGNORECASE,
)
SECRET_TOKEN = re.compile(r"\bsecrets\b", re.IGNORECASE)
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
SECRET_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def is_app_key(name):
    """Whether a secret name carries a GitHub App private key.

    Keyed on the words rather than one canonical suffix: RELEASE_APP_KEY_PEM and a
    lowercase x_app_private_key hold the same credential as RELEASE_APP_PRIVATE_KEY,
    and a scan that cannot see them proves nothing about them (#1285).
    """
    upper = name.upper()
    return "APP" in upper and "KEY" in upper


def app_keys(text):
    """Every App private key name a GitHub Actions expression in text reads.

    Both `secrets.NAME` and the equivalent `secrets['NAME']` index syntax count; a
    binding spelled the second way is not a weaker binding.
    """
    found = set()
    for expression in EXPRESSION.finditer(text):
        body = expression.group(1)
        references = list(SECRET_REFERENCE.finditer(body))
        covered = {index for reference in references for index in range(reference.start(), reference.end())}
        for token in SECRET_TOKEN.finditer(body):
            if token.start() not in covered:
                raise ValueError(f"unresolved secrets expression: {body.strip()}")
        for match in references:
            name = next(group for group in match.groups() if group is not None)
            if is_app_key(name):
                found.add(name)
    return found
CONFINEMENTS = ("canonical", "caller-owned", "unconfined")


def load_roles(path=MANIFEST):
    """Declared App private keys, in manifest order."""
    roles = json.loads(Path(path).read_text(encoding="utf-8"))["roles"]
    if not isinstance(roles, list) or not roles:
        raise ValueError("App key role manifest is empty")
    for entry in roles:
        if (entry.get("confinement") not in CONFINEMENTS
                or not isinstance(entry.get("secret"), str)
                or not SECRET_NAME.fullmatch(entry["secret"]) or not is_app_key(entry["secret"])
                or not isinstance(entry.get("role"), str) or not entry["role"]
                or not isinstance(entry.get("environment"), str) or not entry["environment"]
                or entry.get("organization_copy") not in ("withdraw", "absent")
                or (entry["confinement"] == "canonical"
                    and entry["environment"] != f"{entry['role']}-app")):
            raise ValueError(f"App key role entry is malformed: {entry.get('secret')!r}")
    if len({entry["secret"] for entry in roles}) != len(roles):
        raise ValueError("App key role manifest declares a secret twice")
    return roles


ROLES = {entry["role"]: entry["secret"]
         for entry in load_roles() if entry["confinement"] == "canonical"}


def github(path):
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", path],
        check=True, capture_output=True, text=True, timeout=60,
    )
    pages = json.loads(result.stdout)
    if not isinstance(pages, list) or not pages:
        raise ValueError("GitHub returned no metadata pages")
    return pages


def names(pages, field):
    values = []
    for page in pages:
        records = page.get(field)
        if not isinstance(records, list):
            raise ValueError("GitHub metadata collection is unavailable")
        for record in records:
            name = record.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("GitHub metadata identity is unavailable")
            values.append(name)
    if len(values) != len(set(values)) or any(type(page.get("total_count")) is not int or page["total_count"] != len(values) for page in pages):
        raise ValueError("GitHub metadata pagination is incomplete or inconsistent")
    return set(values)


def audit(repository, api=github):
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise ValueError("repository must be owner/name")
    metadata = api(f"repos/{repository}")[0]
    if metadata.get("default_branch") != "main":
        raise ValueError("repository default branch must be main")
    owner_type = metadata.get("owner", {}).get("type")
    if owner_type not in ("User", "Organization"):
        raise ValueError("repository owner identity is unavailable")
    roles = load_roles()
    keys = {entry["secret"] for entry in roles}
    broad_repo = names(api(f"repos/{repository}/actions/secrets?per_page=100"), "secrets") & keys
    broad_org = set()
    if owner_type == "Organization":
        owner = repository.split("/", 1)[0]
        broad_org = names(api(f"orgs/{owner}/actions/secrets?per_page=100"), "secrets") & keys
    missing = []
    for entry in roles:
        if entry["confinement"] != "canonical":
            continue
        name, key = entry["environment"], entry["secret"]
        path = f"repos/{repository}/environments/{name}"
        environment = api(path)[0]
        policy = environment.get("deployment_branch_policy")
        if environment.get("name") != name or type(environment.get("id")) is not int or environment["id"] <= 0:
            raise ValueError(f"{name} environment identity is unavailable")
        if (not isinstance(policy, dict)
                or set(policy) != {"protected_branches", "custom_branch_policies"}
                or policy["protected_branches"] is not False
                or policy["custom_branch_policies"] is not True):
            raise ValueError(f"{name} must use explicit main-only branch admission")
        pages = api(path + "/deployment-branch-policies?per_page=100")
        if (names(pages, "branch_policies") != {"main"}
                or any(item.get("type") != "branch" for page in pages for item in page["branch_policies"])):
            raise ValueError(f"{name} permits a ref other than the main branch")
        if key not in names(api(path + "/secrets?per_page=100"), "secrets"):
            missing.append(f"{name}/{key}")
    return {
        "repository": repository,
        "compliant": not (broad_repo or broad_org or missing),
        "broadRepositoryKeys": sorted(broad_repo),
        "broadOrganizationKeys": sorted(broad_org),
        "missingEnvironmentKeys": missing,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    try:
        result = audit(args.repo)
    except (ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("App key isolation could not be verified; check metadata access and "
              f"environment policy: {type(error).__name__}: {error}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["compliant"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
