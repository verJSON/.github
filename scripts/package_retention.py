#!/usr/bin/env python3
"""Fail-closed retention for GitHub Packages npm and container versions."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


STABLE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
OCI_IMAGE_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
}
UNTAGGED_MINIMUM_AGE = datetime.timedelta(days=7)
REQUEST_TIMEOUT_SECONDS = 30


# The retention policy language, shared with the guard in every workflow that
# reads CI_PACKAGE_RETENTION_KEEP (scripts/ci-gate/release-verify-retention-keep.test.py
# holds them equal): an integer from 1 to 999 keeps that many newest stable
# versions, and the exact lowercase word `all` disables retention. Digits are
# spelled out so the pattern matches only ASCII.
KEEP_POLICY = re.compile(r"all|[123456789][0123456789]{0,2}", re.ASCII)
KEEP_ALL = "all"


def parse_keep(value: str) -> int | str:
    """Parse `--keep` into a positive window, or KEEP_ALL to delete nothing."""
    if not KEEP_POLICY.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"--keep must be an integer from 1 to 999 or 'all', got {value!r}"
        )
    return KEEP_ALL if value == KEEP_ALL else int(value)


class RetentionError(Exception):
    pass


@dataclass(frozen=True)
class Target:
    package_type: str
    name: str
    released_version: str


@dataclass(frozen=True)
class Deletion:
    target: Target
    version_id: int
    reason: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class ContainerSafety:
    deletable_untagged_ids: frozenset[int]
    protected_version_ids: frozenset[int]


class GitHubPackages:
    def __init__(self, owner: str, token: str, api_url: str) -> None:
        self.owner = owner
        self.token = token
        self.api_url = api_url.rstrip("/")

    def _request(self, method: str, path: str) -> tuple[Any, str | None]:
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read()
                return (json.loads(body) if body else None, response.headers.get("Link"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RetentionError(f"GitHub API {method} {path} failed: {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            # urlopen wraps only a connect-phase failure in URLError; a
            # response that stalls after connecting (getresponse()/read())
            # raises a bare TimeoutError instead.
            reason = getattr(error, "reason", error)
            raise RetentionError(
                f"GitHub API {method} {path} did not respond within {REQUEST_TIMEOUT_SECONDS}s: {reason}"
            ) from error

    def versions(self, target: Target) -> list[dict[str, Any]]:
        name = urllib.parse.quote(target.name, safe="")
        path = f"/orgs/{self.owner}/packages/{target.package_type}/{name}/versions?per_page=100"
        versions: list[dict[str, Any]] = []
        while path:
            page, link = self._request("GET", path)
            if not isinstance(page, list):
                raise RetentionError(f"versions response for {target.package_type}/{target.name} is not a list")
            versions.extend(page)
            path = _next_path(link, self.api_url)
        return versions

    def delete(self, deletion: Deletion) -> None:
        name = urllib.parse.quote(deletion.target.name, safe="")
        self._request(
            "DELETE",
            f"/orgs/{self.owner}/packages/{deletion.target.package_type}/{name}/versions/{deletion.version_id}",
        )


class OciInspector:
    def raw(self, reference: str) -> bytes:
        try:
            result = subprocess.run(
                ["docker", "buildx", "imagetools", "inspect", reference, "--raw"],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise RetentionError(f"cannot inspect OCI reference {reference}") from error
        return result.stdout


def _next_path(link: str | None, api_url: str) -> str:
    if not link:
        return ""
    next_urls = re.findall(r'<([^>]+)>;\s*rel="next"', link)
    if not next_urls:
        if 'rel="next"' in link:
            raise RetentionError("malformed GitHub API next-page metadata")
        return ""
    if len(next_urls) != 1:
        raise RetentionError("ambiguous GitHub API pagination metadata")
    next_url = next_urls[0]
    if not next_url.startswith(f"{api_url}/"):
        raise RetentionError("pagination escaped the configured GitHub API origin")
    return next_url[len(api_url) :]


def parse_targets(raw: str, owner: str) -> list[Target]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RetentionError(f"targets is not valid JSON: {error}") from error
    if not isinstance(data, list) or not data:
        raise RetentionError("targets must be a non-empty JSON array")
    targets: list[Target] = []
    identities: set[tuple[str, str]] = set()
    for item in data:
        if not isinstance(item, dict) or set(item) != {"type", "name", "releasedVersion"}:
            raise RetentionError("each target must contain exactly type, name, and releasedVersion")
        package_type = item["type"]
        name = item["name"]
        released_version = item["releasedVersion"]
        if package_type not in {"npm", "container"}:
            raise RetentionError(f"unsupported package type: {package_type!r}")
        if (
            not isinstance(name, str)
            or not PACKAGE_NAME.fullmatch(name)
            or any(segment in {"", ".", ".."} for segment in name.split("/"))
            or (package_type == "npm" and "/" in name)
        ):
            raise RetentionError(f"invalid package name: {name!r}")
        if package_type == "npm" and name.startswith("@"):
            raise RetentionError("npm REST package names must be unscoped package basenames")
        if not isinstance(released_version, str) or not STABLE_VERSION.fullmatch(released_version):
            raise RetentionError(f"releasedVersion is not stable SemVer: {released_version!r}")
        identity = (package_type, name)
        if identity in identities:
            raise RetentionError(f"duplicate target: {package_type}/{name}")
        identities.add(identity)
        targets.append(Target(package_type, name, released_version))
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner):
        raise RetentionError("owner is not a valid GitHub organization login")
    return targets


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = STABLE_VERSION.fullmatch(version)
    if not match:
        raise RetentionError(f"not stable SemVer: {version!r}")
    return tuple(map(int, match.groups()))  # type: ignore[return-value]


def plan_target(
    target: Target,
    versions: list[dict[str, Any]],
    keep: int = 3,
    deletable_untagged_ids: set[int] | None = None,
    protected_version_ids: set[int] | None = None,
) -> list[Deletion]:
    # `keep` defaults to the ADR 0108 policy of three and is a plain parameter
    # (CLI `--keep`, sourced from an org variable in the calling workflow) so a
    # human can change the retention window with a config change, not a code
    # change. Raising or lowering it from 3 is a storage-cost-vs-consumer-
    # breakage policy call for a human with billing visibility (verjson-agents
    # issue #987) — this function only enforces that whatever value is chosen
    # is a usable retention count, never which value is "correct".
    if not isinstance(keep, int) or isinstance(keep, bool) or keep < 1:
        raise RetentionError("retention count must be a positive integer")
    stable: dict[str, tuple[int, tuple[str, ...]]] = {}
    untagged: list[tuple[int, tuple[str, ...]]] = []
    seen_ids: set[int] = set()
    for version in versions:
        if not isinstance(version, dict) or not isinstance(version.get("id"), int):
            raise RetentionError(f"{target.package_type}/{target.name} has a version without an integer id")
        version_id = version["id"]
        if version_id in seen_ids:
            raise RetentionError(f"{target.package_type}/{target.name} returned duplicate version id {version_id}")
        seen_ids.add(version_id)
        if target.package_type == "npm":
            name = version.get("name")
            if not isinstance(name, str):
                raise RetentionError(f"npm version {version_id} has no string version name")
            labels = (name,)
        else:
            metadata = version.get("metadata")
            container = metadata.get("container") if isinstance(metadata, dict) else None
            tags = container.get("tags") if isinstance(container, dict) else None
            if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag for tag in tags):
                raise RetentionError(f"container version {version_id} has ambiguous tag metadata")
            if len(tags) != len(set(tags)):
                raise RetentionError(f"container version {version_id} has duplicate tags")
            labels = tuple(tags)
            if not tags:
                untagged.append((version_id, labels))
                continue
            stable_tags = [tag for tag in tags if STABLE_VERSION.fullmatch(tag)]
            if not stable_tags:
                continue
            if len(stable_tags) != 1:
                raise RetentionError(
                    f"container version {version_id} maps multiple numbered releases to one digest: {tags}"
                )
            name = stable_tags[0]
        if STABLE_VERSION.fullmatch(name):
            if name in stable:
                raise RetentionError(f"{target.package_type}/{target.name} maps {name} to multiple version ids")
            stable[name] = (version_id, labels)

    if target.released_version not in stable:
        raise RetentionError(
            f"released version {target.released_version} is absent from {target.package_type}/{target.name}"
        )
    retained = set(sorted(stable, key=_version_tuple, reverse=True)[:keep])
    if target.released_version not in retained:
        raise RetentionError(
            f"released version {target.released_version} is not among the newest {keep}; refusing historical cleanup"
        )
    deletions = [
        Deletion(target, version_id, f"numbered release older than newest {keep}", labels)
        for name, (version_id, labels) in stable.items()
        if name not in retained
        and version_id not in (protected_version_ids or set())
    ]
    if target.package_type == "container":
        deletions.extend(
            Deletion(target, version_id, "untagged container version", labels)
            for version_id, labels in untagged
            if version_id in (deletable_untagged_ids or set())
        )
    return sorted(deletions, key=lambda item: item.version_id)


def _parse_time(value: Any, context: str) -> datetime.datetime:
    if not isinstance(value, str):
        raise RetentionError(f"{context} has no created_at timestamp")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RetentionError(f"{context} has an invalid created_at timestamp") from error
    if parsed.tzinfo is None:
        raise RetentionError(f"{context} has a timezone-less created_at timestamp")
    return parsed


def _oci_document(raw: bytes, reference: str) -> dict[str, Any]:
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RetentionError(f"OCI reference {reference} returned invalid JSON") from error
    if not isinstance(manifest, dict) or manifest.get("mediaType") not in OCI_IMAGE_TYPES:
        raise RetentionError(f"OCI reference {reference} is not an image manifest or index")
    if "artifactType" in manifest and (
        not isinstance(manifest["artifactType"], str) or not manifest["artifactType"].strip()
    ):
        raise RetentionError(f"OCI reference {reference} has an invalid artifactType")
    return manifest


def _container_safety(
    owner: str,
    target: Target,
    versions: list[dict[str, Any]],
    inspector: OciInspector,
    now: datetime.datetime,
    keep: int = 3,
) -> ContainerSafety:
    if not isinstance(keep, int) or isinstance(keep, bool) or keep < 1:
        raise RetentionError("retention count must be a positive integer")
    # GitHub owner logins preserve display casing, while OCI registry
    # namespaces are canonical lowercase references.
    repository = f"ghcr.io/{owner.lower()}/{target.name}"
    stable: list[tuple[str, str]] = []
    untagged: list[dict[str, Any]] = []
    for version in versions:
        metadata = version.get("metadata") if isinstance(version, dict) else None
        container = metadata.get("container") if isinstance(metadata, dict) else None
        tags = container.get("tags") if isinstance(container, dict) else None
        if not isinstance(tags, list):
            raise RetentionError(f"container version metadata is ambiguous for {target.name}")
        digest = version.get("name")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise RetentionError(f"container version {version.get('id')} has no canonical digest name")
        stable_tags = [tag for tag in tags if isinstance(tag, str) and STABLE_VERSION.fullmatch(tag)]
        if len(stable_tags) == 1:
            stable.append((stable_tags[0], digest))
        elif not tags:
            untagged.append(version)

    retained = sorted(stable, key=lambda item: _version_tuple(item[0]), reverse=True)[:keep]
    protected = {digest for _, digest in retained}
    queue = [(f"{repository}:{tag}", digest) for tag, digest in retained]
    visited: set[str] = set()
    while queue:
        reference, expected_digest = queue.pop()
        if expected_digest in visited:
            continue
        raw = inspector.raw(reference)
        actual_digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if actual_digest != expected_digest:
            raise RetentionError(f"OCI reference {reference} does not match GitHub package digest")
        manifest = _oci_document(raw, reference)
        visited.add(expected_digest)
        children = manifest.get("manifests", [])
        if not isinstance(children, list):
            raise RetentionError(f"OCI reference {reference} has ambiguous child descriptors")
        for child in children:
            digest = child.get("digest") if isinstance(child, dict) else None
            if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                raise RetentionError(f"OCI reference {reference} has an invalid child digest")
            protected.add(digest)
            queue.append((f"{repository}@{digest}", digest))

    cutoff = now - UNTAGGED_MINIMUM_AGE
    safe: set[int] = set()
    for version in untagged:
        version_id = version["id"]
        digest = version["name"]
        if _parse_time(version.get("created_at"), f"container version {version_id}") > cutoff:
            continue
        if digest in protected:
            continue
        reference = f"{repository}@{digest}"
        raw = inspector.raw(reference)
        actual_digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if actual_digest != digest:
            raise RetentionError(f"OCI reference {reference} does not match GitHub package digest")
        document = _oci_document(raw, reference)
        if "artifactType" in document:
            protected.add(digest)
            continue
        safe.add(version_id)
    protected_version_ids = {
        version["id"]
        for version in versions
        if isinstance(version, dict) and version.get("name") in protected
    }
    return ContainerSafety(frozenset(safe), frozenset(protected_version_ids))


def build_plan(
    client: GitHubPackages,
    targets: list[Target],
    owner: str = "",
    inspector: OciInspector | None = None,
    now: datetime.datetime | None = None,
    keep: int = 3,
) -> list[Deletion]:
    inventories = [(target, client.versions(target)) for target in targets]
    plans: list[Deletion] = []
    for target, versions in inventories:
        deletable_untagged_ids: set[int] | None = None
        protected_version_ids: set[int] | None = None
        if target.package_type == "container":
            if not owner or inspector is None or now is None:
                raise RetentionError("container cleanup requires an owner, OCI inspector, and current time")
            safety = _container_safety(owner, target, versions, inspector, now, keep=keep)
            deletable_untagged_ids = set(safety.deletable_untagged_ids)
            protected_version_ids = set(safety.protected_version_ids)
        plans.extend(
            plan_target(
                target,
                versions,
                keep=keep,
                deletable_untagged_ids=deletable_untagged_ids,
                protected_version_ids=protected_version_ids,
            )
        )
    return plans


def apply_plan(
    client: GitHubPackages,
    deletions: list[Deletion],
    owner: str,
    inspector: OciInspector,
    keep: int = 3,
) -> None:
    for deletion in deletions:
        fresh_plan = build_plan(
            client,
            [deletion.target],
            owner=owner,
            inspector=inspector,
            now=datetime.datetime.now(datetime.timezone.utc),
            keep=keep,
        )
        if deletion not in fresh_plan:
            raise RetentionError(
                f"package inventory changed before deleting version id {deletion.version_id}; refusing stale plan"
            )
        client.delete(deletion)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--targets", required=True)
    parser.add_argument(
        "--keep",
        type=parse_keep,
        default=3,
        help=(
            "Number of newest stable versions to retain per package (1-999), or 'all' to "
            "disable retention. Defaults to the ADR 0108 "
            "policy of three; changing it is a storage-cost-vs-consumer-breakage policy decision "
            "for a human (verjson-agents issue #987), not something this script decides."
        ),
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    targets = parse_targets(args.targets, args.owner)
    if args.keep == KEEP_ALL:
        # Validate the inputs, then stop before any API call: `all` keeps every version.
        print("package-retention: disabled by policy (keep=all)")
        return 0
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RetentionError("GH_TOKEN is required")
    client = GitHubPackages(args.owner, token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    inspector = OciInspector()
    deletions = build_plan(
        client,
        targets,
        owner=args.owner,
        inspector=inspector,
        now=datetime.datetime.now(datetime.timezone.utc),
        keep=args.keep,
    )
    print(json.dumps({"delete": [deletion.__dict__ | {"target": deletion.target.__dict__} for deletion in deletions]}, default=list))
    if not args.apply:
        print("Dry run only; pass --apply to delete the validated inventory.")
        return 0
    apply_plan(client, deletions, args.owner, inspector, keep=args.keep)
    for deletion in deletions:
        print(
            f"Deleted {deletion.target.package_type}/{deletion.target.name} version id "
            f"{deletion.version_id}: {deletion.reason}."
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RetentionError as error:
        print(f"package-retention: {error}", file=sys.stderr)
        raise SystemExit(1)
