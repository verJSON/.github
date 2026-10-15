#!/usr/bin/env python3
"""Audit and safely reconcile package type-surface rulesets."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# `python3 -I` does not put the script directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from github_identity import same_organization, same_repository  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config/package-type-surface-rulesets.json"
MUTABLE_KEYS = ("target", "enforcement", "bypass_actors", "conditions", "rules")
EXPECTED_ACK = "APPLY-PACKAGE-TYPE-SURFACE-RELEASE-1673"


class ContractError(Exception):
    pass


def require(condition: bool, message: str):
    if not condition:
        raise ContractError(message)


def load_json(text: str, source: str):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"{source} contains duplicate key {key}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=unique_object)
    except json.JSONDecodeError as error:
        raise ContractError(f"{source} is invalid JSON: {error}") from None


def mapping(value, location: str):
    require(isinstance(value, dict), f"{location} must be an object")
    return value


def array(value, location: str):
    require(isinstance(value, list), f"{location} must be an array")
    return value


def positive_integer(value, location: str):
    require(isinstance(value, int) and not isinstance(value, bool) and value > 0,
            f"{location} must be a positive integer")
    return value


def nonempty_string(value, location: str):
    require(isinstance(value, str) and value.strip(), f"{location} must be a non-empty string")
    return value


def validate_actor(value, location: str):
    item = mapping(value, location)
    require(set(item) == {"actor_type", "actor_id", "bypass_mode"}, f"{location} keys drifted")
    nonempty_string(item["actor_type"], f"{location}.actor_type")
    if item["actor_id"] is not None:
        positive_integer(item["actor_id"], f"{location}.actor_id")
    nonempty_string(item["bypass_mode"], f"{location}.bypass_mode")
    return item


def validate_image(value, location: str):
    item = mapping(value, location)
    require(set(item) == set(MUTABLE_KEYS), f"{location} keys drifted")
    nonempty_string(item["target"], f"{location}.target")
    nonempty_string(item["enforcement"], f"{location}.enforcement")
    for index, actor in enumerate(array(item["bypass_actors"], f"{location}.bypass_actors")):
        validate_actor(actor, f"{location}.bypass_actors[{index}]")
    mapping(item["conditions"], f"{location}.conditions")
    for index, rule_value in enumerate(array(item["rules"], f"{location}.rules")):
        rule = mapping(rule_value, f"{location}.rules[{index}]")
        nonempty_string(rule.get("type"), f"{location}.rules[{index}].type")
    return item


def read_policy(path: Path = POLICY_PATH):
    document = mapping(load_json(path.read_text(encoding="utf-8"), str(path)), "policy")
    require(set(document) == {"schema_version", "organization", "apply_acknowledgement", "release_authorization_bypass", "rulesets"}, "policy keys drifted")
    require(document["schema_version"] == 1, "policy schema_version must be 1")
    organization = nonempty_string(document["organization"], "policy.organization")
    acknowledgement = nonempty_string(document["apply_acknowledgement"], "policy.apply_acknowledgement")
    expected_actor = validate_actor(document["release_authorization_bypass"], "policy.release_authorization_bypass")
    require(expected_actor == {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"}, "policy must select only release-authorization")
    entries = []
    names = set()
    for index, raw in enumerate(array(document["rulesets"], "policy.rulesets")):
        location = f"policy.rulesets[{index}]"
        item = mapping(raw, location)
        require(set(item) == {"repository", "repository_id", "scope", "ruleset_id", "name", "preimage", "desired"}, f"{location} keys drifted")
        repository = nonempty_string(item["repository"], f"{location}.repository")
        positive_integer(item["repository_id"], f"{location}.repository_id")
        require(item["scope"] in {"repository", "organization"}, f"{location}.scope invalid")
        if item["ruleset_id"] is not None:
            positive_integer(item["ruleset_id"], f"{location}.ruleset_id")
        name = nonempty_string(item["name"], f"{location}.name")
        require(name not in names, f"duplicate ruleset name {name}")
        names.add(name)
        if item["preimage"] is not None:
            validate_image(item["preimage"], f"{location}.preimage")
        desired = validate_image(item["desired"], f"{location}.desired")
        require(desired["bypass_actors"] == [expected_actor], f"{location}.desired must have only release-authorization")
        entries.append({"repository": repository, "repository_id": item["repository_id"], "scope": item["scope"], "ruleset_id": item["ruleset_id"], "name": name, "preimage": item["preimage"], "desired": desired})
    require(entries, "policy.rulesets must not be empty")
    return organization, acknowledgement, entries


def gh_json(*arguments: str, input_path: Path | None = None):
    command = ["gh", "api", "--hostname", "github.com", *arguments]
    if input_path is not None:
        command.extend(["--input", str(input_path)])
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    finally:
        if input_path is not None:
            input_path.unlink(missing_ok=True)
    if result.returncode:
        raise RuntimeError(f"GitHub API request failed: {' '.join(arguments)}")
    return load_json(result.stdout, f"GitHub API {' '.join(arguments)}")


def endpoint(organization: str, entry: dict, ruleset_id: int | None = None):
    base = f"orgs/{organization}/rulesets" if entry["scope"] == "organization" else f"repos/{entry['repository']}/rulesets"
    return f"{base}/{ruleset_id}" if ruleset_id is not None else base


def mutable_image(value: dict):
    return {key: value.get(key) for key in MUTABLE_KEYS}


def ruleset_listing(organization: str, entry: dict):
    pages = gh_json("--paginate", "--slurp", endpoint(organization, entry))
    require(isinstance(pages, list), "ruleset listing pages must be an array")
    result = []
    for page in pages:
        require(isinstance(page, list), "ruleset listing page must be an array")
        result.extend(page)
    return result


def read_live(organization: str, entry: dict):
    if entry["ruleset_id"] is not None:
        return gh_json(endpoint(organization, entry, entry["ruleset_id"]))
    matches = [item for item in ruleset_listing(organization, entry) if item.get("name") == entry["name"]]
    require(len(matches) <= 1, f"multiple rulesets named {entry['name']}")
    return None if not matches else gh_json(endpoint(organization, entry, matches[0]["id"]))


def validate_live(organization: str, entry: dict, live: dict, desired: dict):
    require(live.get("name") == entry["name"], f"{entry['name']}: name drifted")
    if entry["scope"] == "organization":
        require(live.get("source_type") == "Organization", f"{entry['name']}: source type drifted")
        require(same_organization(live.get("source"), organization), f"{entry['name']}: source drifted")
    else:
        require(live.get("source_type") == "Repository", f"{entry['name']}: source type drifted")
        require(same_repository(live.get("source"), entry["repository"]), f"{entry['name']}: source drifted")
    if entry["ruleset_id"] is not None:
        require(live.get("id") == entry["ruleset_id"], f"{entry['name']}: id drifted")
    require(mutable_image(live) == desired, f"{entry['name']}: live image drifted")


def payload(entry: dict, image: dict):
    return {"name": entry["name"], **image}


def api_input(payload_value: dict):
    stream = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
    try:
        json.dump(payload_value, stream)
        stream.flush()
        return Path(stream.name)
    finally:
        stream.close()


def audit(organization: str, entries: list[dict]):
    failures = []
    for entry in entries:
        try:
            live = read_live(organization, entry)
            require(live is not None, f"{entry['name']}: ruleset is missing")
            validate_live(organization, entry, live, entry["desired"])
            print(f"conformant {entry['repository']} {entry['name']} id={live['id']}")
        except (ContractError, OSError, RuntimeError) as error:
            failures.append(str(error))
    if failures:
        raise ContractError("; ".join(failures))


def apply_one(organization: str, acknowledgement: str, entry: dict):
    require(acknowledgement == EXPECTED_ACK, "explicit apply acknowledgement required")
    live = read_live(organization, entry)
    desired = entry["desired"]
    if live is None:
        require(entry["preimage"] is None, f"{entry['name']}: missing live ruleset has a preimage")
        require(not [item for item in ruleset_listing(organization, entry) if item.get("name") == entry["name"]], f"{entry['name']}: appeared during apply preflight")
        staged = {**desired, "enforcement": "disabled"}
        created = gh_json("--method", "POST", endpoint(organization, entry), input_path=api_input(payload(entry, staged)))
        entry = {**entry, "ruleset_id": created.get("id")}
        require(isinstance(entry["ruleset_id"], int), f"{entry['name']}: create returned no id")
        staged_live = read_live(organization, entry)
        validate_live(organization, entry, staged_live, staged)
        print(f"created disabled {entry['name']} id={entry['ruleset_id']}")
    else:
        if mutable_image(live) == desired:
            validate_live(organization, entry, live, desired)
            print(f"already conformant {entry['name']} id={live['id']}")
            return
        require(entry["preimage"] is not None, f"{entry['name']}: unexpected live drift; no preimage permits mutation")
        validate_live(organization, entry, live, entry["preimage"])
        updated_at = live.get("updated_at")
        reread = read_live(organization, entry)
        require(reread.get("updated_at") == updated_at, f"{entry['name']}: preimage changed during apply preparation")
        validate_live(organization, entry, reread, entry["preimage"])
        entry = {**entry, "ruleset_id": reread["id"]}
    gh_json("--method", "PUT", endpoint(organization, entry, entry["ruleset_id"]), input_path=api_input(payload(entry, desired)))
    postimage = read_live(organization, entry)
    validate_live(organization, entry, postimage, desired)
    print(f"applied and verified {entry['name']} id={postimage['id']}")


def main(arguments=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("render", "audit", "apply"))
    parser.add_argument("--repository")
    parser.add_argument("--ack")
    args = parser.parse_args(arguments)
    try:
        organization, acknowledgement, entries = read_policy()
        require(acknowledgement == EXPECTED_ACK, "script acknowledgement does not match policy")
        if args.mode == "render":
            print(json.dumps({entry["name"]: payload(entry, entry["desired"]) for entry in entries}, indent=2, sort_keys=True))
            return 0
        if args.mode == "audit":
            audit(organization, entries)
            return 0
        require(args.repository, "--repository is required for apply")
        selected = [entry for entry in entries if same_repository(args.repository, entry["repository"])]
        require(len(selected) == 1, "--repository is not a managed package")
        apply_one(organization, args.ack, selected[0])
        return 0
    except (ContractError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
