#!/usr/bin/env python3
import json
import re


SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
PACKAGE = re.compile(r"@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*")
SCRIPT = re.compile(r"[a-z0-9][a-z0-9:._-]*")
NODE = re.compile(r"[0-9]+(?:\.[0-9]+){0,2}")


def load_required_node_config(path, require):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, f"duplicate config key: {key}")
            value[key] = item
        return value

    try:
        config = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, json.JSONDecodeError) as error:
        require(False, f"invalid config: {error}")

    require(isinstance(config, dict), "config must be an object")
    require(
        set(config) == {
            "schema_version",
            "repository",
            "protected_node_ci_sha",
            "consumer_node_ci_sha",
            "rollout_legacy_consumer_workflow_sha256",
            "node_versions",
            "approved_internal_packages",
            "scripts",
        },
        "config keys drifted",
    )
    require(
        type(config["schema_version"]) is int and config["schema_version"] == 1,
        "unsupported schema_version",
    )
    require(
        isinstance(config["repository"], str)
        and REPOSITORY.fullmatch(config["repository"]),
        "repository must be an exact owner/name",
    )
    require(
        config["repository"] == "Verjson/verjson-cli-projects",
        "this protected verifier is bound to Verjson/verjson-cli-projects",
    )
    for key in ("protected_node_ci_sha", "consumer_node_ci_sha"):
        require(
            isinstance(config[key], str) and SHA.fullmatch(config[key]),
            f"{key} must be immutable",
        )
    require(
        config["rollout_legacy_consumer_workflow_sha256"] is None
        or (
            isinstance(config["rollout_legacy_consumer_workflow_sha256"], str)
            and SHA256.fullmatch(config["rollout_legacy_consumer_workflow_sha256"])
        ),
        "rollout_legacy_consumer_workflow_sha256 must be null or a 64-character lowercase SHA-256",
    )

    versions = config["node_versions"]
    require(
        isinstance(versions, list)
        and len(versions) == 2
        and all(isinstance(value, str) and NODE.fullmatch(value) for value in versions),
        "node_versions must contain exactly two versions",
    )
    require(len(set(versions)) == 2, "node_versions must be unique")

    packages = config["approved_internal_packages"]
    require(
        isinstance(packages, list)
        and bool(packages)
        and all(isinstance(value, str) and PACKAGE.fullmatch(value) for value in packages),
        "approved_internal_packages must be exact package names",
    )
    require(len(set(packages)) == len(packages), "approved packages must be unique")

    scripts = config["scripts"]
    require(
        isinstance(scripts, list)
        and bool(scripts)
        and all(isinstance(value, str) and SCRIPT.fullmatch(value) for value in scripts),
        "scripts must be exact npm script names",
    )
    require(len(set(scripts)) == len(scripts), "scripts must be unique")
    return config
