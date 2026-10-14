#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

import yaml

# Workflows start this script with `python3 -I`, which leaves its own directory
# off the module path; the sibling it imports lives there.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli_projects_required_node_config import load_required_node_config  # noqa: E402


SHA = re.compile(r"[0-9a-f]{40}")
REQUIRED_NODE_CONFIG = (
    Path(__file__).resolve().parents[1] / "config/cli-projects-required-node-ci.json"
)
EXPECTED_BINS = {
    "create-verjson-platform": "src/cli.js",
    "verjson-project-init": "src/cli.js",
    "verjson-repo-scripts": "src/repo-scripts.js",
}
PINNED_USE = re.compile(r"(?:^[^@\s]+(?:/[^@\s]+)+)@([0-9a-f]{40})$")
PINNED_CONTAINER = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-f]{64}$")


class ContractError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def required_node_engine(path=REQUIRED_NODE_CONFIG):
    return f">={load_required_node_config(path, require)['node_versions'][1]}"


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def construct_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        require(key not in mapping, f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
)


def load_yaml(path):
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ContractError(f"{path} is invalid YAML: {error.problem}") from None
    require(isinstance(document, dict), f"{path} must contain a YAML mapping")
    return document


def workflow_trigger(document):
    return document.get(True, document.get("on"))


def iter_steps(document):
    jobs = document.get("jobs")
    require(isinstance(jobs, dict) and jobs, "workflow jobs must be a non-empty mapping")
    for job_name, job in jobs.items():
        require(isinstance(job, dict), f"job {job_name} must be a mapping")
        if "uses" in job:
            yield f"jobs.{job_name}", job
        steps = job.get("steps", [])
        require(isinstance(steps, list), f"job {job_name} steps must be a list")
        for index, step in enumerate(steps):
            require(isinstance(step, dict), f"job {job_name} step {index} must be a mapping")
            yield f"jobs.{job_name}.steps.{index}", step


def validate_uses(location, value):
    require(isinstance(value, str), f"{location} uses must be a string")
    if value.startswith("./"):
        return
    if value.startswith("docker://"):
        require(PINNED_CONTAINER.fullmatch(value), f"{location} container must use sha256 digest")
        return
    require(PINNED_USE.fullmatch(value), f"{location} action must use a 40-hex commit")


def validate_workflow(path, *, release=False):
    document = load_yaml(path)
    trigger = workflow_trigger(document)
    require(trigger is not None, f"{path} must declare on")
    if release:
        require(trigger == {"workflow_dispatch": trigger.get("workflow_dispatch")}
                if isinstance(trigger, dict) else False,
                f"{path} release trigger must be workflow_dispatch only")
    for location, step in iter_steps(document):
        if "uses" in step:
            validate_uses(f"{path}:{location}", step["uses"])
        require(step.get("continue-on-error") is not True,
                f"{path}:{location} may not continue on error")
        condition = step.get("if")
        require(condition not in (False, "false", "${{ false }}"),
                f"{path}:{location} may not be unconditionally skipped")
        command = step.get("run")
        if isinstance(command, str):
            require(re.search(r"\|\|\s*true(?:\s|$)", command) is None,
                    f"{path}:{location} may not hide failure with || true")


def validate_manifest(root):
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    require(package.get("name") == "@verjson/cli-projects", "package name drifted")
    require(package.get("type") == "module", "package must remain ESM")
    require(package.get("bin") == EXPECTED_BINS, "CLI binary surface drifted")
    require(package.get("files") == ["src", "templates", "README.md"],
            "published file allowlist drifted")
    require(package.get("publishConfig") == {"registry": "https://npm.pkg.github.com"},
            "publish registry drifted")
    require(package.get("scripts", {}).get("build") == "bash scripts/check-sources.sh",
            "build contract drifted")
    require(package.get("engines", {}).get("node") == required_node_engine(),
            "supported Node engine floor drifted")


def validate_tree(root):
    root = root.resolve()
    require(root.is_dir(), "candidate root is not a directory")
    validate_manifest(root)
    validate_workflow(root / ".github/workflows/release.yml", release=True)
    validate_workflow(root / "templates/package/.github/workflows/ci.yml.tmpl")
    validate_workflow(root / "templates/package/.github/workflows/actionlint.yml.tmpl")


def exercise_package(root):
    with tempfile.TemporaryDirectory(prefix="cli-projects-package-surface-") as scratch:
        scratch_path = Path(scratch)
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(scratch_path / "home"),
            "NPM_CONFIG_USERCONFIG": str(scratch_path / "npmrc"),
        }
        (scratch_path / "home").mkdir()
        (scratch_path / "npmrc").write_text(
            "registry=https://registry.npmjs.org/\nignore-scripts=true\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["npm", "pack", str(root), "--ignore-scripts", "--json", "--pack-destination", scratch],
            cwd=scratch,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
            timeout=120,
        )
        require(result.returncode == 0, "npm pack failed")
        try:
            packed = json.loads(result.stdout)
            if isinstance(packed, list):
                require(len(packed) == 1, "npm pack returned multiple package receipts")
                receipt = packed[0]
            else:
                require(isinstance(packed, dict) and list(packed) == ["@verjson/cli-projects"],
                        "npm pack returned an unexpected package receipt")
                receipt = packed["@verjson/cli-projects"]
            filename = receipt["filename"]
            require(
                isinstance(filename, str)
                and filename == Path(filename).name
                and filename.endswith(".tgz"),
                "npm pack returned an unsafe tarball filename",
            )
            tarball = scratch_path / filename
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise ContractError("npm pack returned an invalid receipt") from None
        require(tarball.is_file(), "npm pack did not create its reported tarball")
        with tarfile.open(tarball, "r:gz") as archive:
            names = set(archive.getnames())
        required = {"package/package.json", "package/README.md"} | {
            f"package/{target}" for target in EXPECTED_BINS.values()
        }
        require(required <= names, "packed archive omits a required public entrypoint")
        forbidden = [name for name in names if name.startswith("package/.git/") or name == "package/.npmrc"]
        require(not forbidden, "packed archive contains repository or credential configuration")
        consumer = scratch_path / "consumer"
        consumer.mkdir()
        (consumer / "package.json").write_text(
            '{"name":"package-surface-consumer","private":true}', encoding="utf-8"
        )
        install = subprocess.run(
            ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund", str(tarball)],
            cwd=consumer,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
            timeout=120,
        )
        require(install.returncode == 0, "packed package could not be installed credentiallessly")
        for binary in sorted(EXPECTED_BINS):
            invocation = subprocess.run(
                [str(consumer / "node_modules/.bin" / binary), "--help"],
                cwd=consumer,
                capture_output=True,
                text=True,
                check=False,
                env=environment,
                timeout=30,
            )
            if binary == "verjson-repo-scripts":
                require(
                    invocation.returncode == 2
                    and "usage: verjson-repo-scripts" in invocation.stderr,
                    "public binary contract drifted: verjson-repo-scripts",
                )
            else:
                require(invocation.returncode == 0, f"public binary failed: {binary}")


def main(arguments=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("validate", "verify"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args(arguments)
    validate_tree(args.root)
    if args.mode == "verify":
        exercise_package(args.root.resolve())
    print("verified: cli-projects package and workflow surface")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
