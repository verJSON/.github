#!/usr/bin/env python3
"""Validate, render, and release changelog fragments using only the stdlib."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# The sole unreleased store (ADR 0038). Named once so the selection diagnostics
# below and the directory they describe cannot drift apart.
UNRELEASED_DIR = "NEXT"

CANONICAL_NAME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})-issue-(?P<identity>\d+|[0-9]{8}T[0-9]{6}Z|[0-9a-fA-F]{6,12})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.md$"
)
SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*\.md$")
LEGACY_ISSUE = re.compile(r"(?:^|[^A-Za-z0-9])#(?P<issue>[1-9]\d*)(?:\b|$)")


class ChangelogError(Exception):
    pass


@dataclass(frozen=True)
class Fragment:
    path: Path
    metadata: dict[str, str]
    body: str
    identity: str
    canonical: bool

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (
            self.metadata.get("date", "0000-00-00"),
            self.identity,
            self.path.name,
        )


@dataclass(frozen=True)
class PreContractMigration:
    source: str
    version: str
    sha256: str
    destination: str


def unquote_scalar(value: str) -> str:
    """Resolve a YAML-quoted scalar to the text it denotes.

    Front matter is read line-wise rather than with a YAML library, because this
    contract runs on the stdlib alone. That subset still has to cover quoting:
    YAML *requires* a quoted scalar wherever a value contains `: `, which is the
    shape of every conventional-commit title. Keeping the quotes as literal text
    renders `## 'Fix: thing'` and penalises the one spelling a YAML parser
    accepts, so the only correctly-written fragments are the ones that look
    broken once released (#420).

    A value that merely begins and ends with a quote is not a quoted scalar and
    is returned untouched — truncating it would corrupt a title rather than
    tidy it.
    """
    if len(value) < 2 or value[0] not in "'\"" or value[-1] != value[0]:
        return value
    quote, inner = value[0], value[1:-1]
    index = 0
    while index < len(inner):
        if quote == '"' and inner[index] == "\\":
            index += 2
            continue
        if inner[index] == quote:
            # A single-quoted scalar escapes its quote by doubling it; anything
            # else closes the scalar early, so this was never one scalar.
            if quote == "'" and inner[index : index + 2] == "''":
                index += 2
                continue
            return value
        index += 1
    if quote == "'":
        return inner.replace("''", "'")
    return inner.replace('\\"', '"').replace("\\\\", "\\")


def parse_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    return parse_frontmatter_text(path, path.read_text(encoding="utf-8"))


def parse_frontmatter_text(path: Path, text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ChangelogError(f"{path}: missing metadata front matter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ChangelogError(f"{path}: unterminated metadata front matter") from exc
    metadata: dict[str, str] = {}
    metadata_lines = lines[1:end]
    index = 0
    while index < len(metadata_lines):
        line = metadata_lines[index]
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise ChangelogError(f"{path}: invalid metadata line: {line!r}")
        key = key.strip()
        if key in metadata:
            raise ChangelogError(f"{path}: duplicate metadata key {key!r}")
        value = value.strip()
        if key == "summary" and value[0] in ">|" and not re.fullmatch(r"[>|][+-]?", value):
            raise ChangelogError(f"{path}: invalid summary block scalar indicator {value!r}")
        if key == "summary" and re.fullmatch(r"[>|][+-]?", value):
            block_lines: list[str] = []
            index += 1
            while index < len(metadata_lines):
                continuation = metadata_lines[index]
                if continuation and not continuation.startswith(" "):
                    break
                block_lines.append(continuation)
                index += 1
            content_lines = [item for item in block_lines if item]
            if not content_lines:
                raise ChangelogError(
                    f"{path}: summary block scalar requires an indented continuation"
                )
            indent = min(len(item) - len(item.lstrip(" ")) for item in content_lines)
            if indent < 1:
                raise ChangelogError(
                    f"{path}: summary block scalar requires an indented continuation"
                )
            dedented = [item[indent:] if item else "" for item in block_lines]
            if value[0] == ">":
                block = dedented[0]
                for previous, current in zip(dedented, dedented[1:]):
                    if previous and not previous.startswith(" ") and not current:
                        separator = ""
                    elif not previous and current.startswith(" "):
                        separator = "\n\n"
                    elif (
                        previous
                        and current
                        and not previous.startswith(" ")
                        and not current.startswith(" ")
                    ):
                        separator = " "
                    else:
                        separator = "\n"
                    block += separator + current
            else:
                block = "\n".join(dedented)
            if value.endswith("-"):
                block = block.rstrip("\n")
            elif value.endswith("+"):
                trailing_empty_lines = 0
                for item in reversed(dedented):
                    if item:
                        break
                    trailing_empty_lines += 1
                block = block.rstrip("\n") + "\n" * (trailing_empty_lines + 1)
            else:
                block = block.rstrip("\n") + "\n"
            metadata[key] = block
            continue
        metadata[key] = unquote_scalar(value)
        index += 1
    return metadata, "\n".join(lines[end + 1 :]).strip() + "\n"


KNOWN_KEYS = frozenset(
    {"date", "issue", "id", "title", "refs", "summary", "component", "impact"}
)
COMPONENT_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
RELEASE_IMPACTS = ("patch", "minor", "major")
SEMVER_PREFIX_PATTERN = r"(?:[a-z0-9][a-z0-9._-]*-)?v"
SEMVER_PREFIX = re.compile(rf"^{SEMVER_PREFIX_PATTERN}$")
SEMVER_PRERELEASE_IDENTIFIER_PATTERN = (
    r"(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
)
SEMVER_PRERELEASE_PATTERN = (
    rf"{SEMVER_PRERELEASE_IDENTIFIER_PATTERN}"
    rf"(?:\.{SEMVER_PRERELEASE_IDENTIFIER_PATTERN})*"
)
SEMVER_BUILD_PATTERN = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
SEMVER_RELEASE = re.compile(
    rf"^(?P<prefix>{SEMVER_PREFIX_PATTERN})"
    r"(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    rf"(?:-(?P<prerelease>{SEMVER_PRERELEASE_PATTERN}))?"
    rf"(?:\+(?P<build>{SEMVER_BUILD_PATTERN}))?$"
)


def quoting_is_ambiguous(value: str) -> bool:
    """True when a value opens and closes with a quote but is not one scalar.

    `unquote_scalar` resolves genuine quoted scalars and deliberately leaves
    this shape alone (`"a" and "b"`, `'a' or 'b'`), because stripping by
    position would corrupt the text rather than tidy it. That residue reaches
    the release snapshot verbatim, and a snapshot is immutable — so the author
    has to hear about it at PR time, while the fragment is still editable
    (#425).
    """
    return (
        len(value) >= 2
        and value[0] in "'\""
        and value[-1] == value[0]
        and unquote_scalar(value) == value
    )


def validate_metadata(path: Path, metadata: dict[str, str]) -> str:
    # Rejecting an unknown key makes every future metadata addition a flag day.
    # Each repository pins its own contract SHA, so a fragment carrying a new
    # key fails validation in every repository that has not bumped yet, and
    # fails again if one ever pins backward with such fragments still in
    # `NEXT/`. Warning instead lets a forward-compatible fragment stay readable
    # by an older contract (#424).
    #
    # The cost is stated rather than hidden: a typo in an OPTIONAL key (`ref:`
    # for `refs:`) now degrades silently instead of failing, so the warning
    # names the key. Required keys are unaffected — a typo in `date:`, `title:`
    # or `issue:` still trips the checks below, because those test for the
    # correct key's presence rather than for the absence of a wrong one.
    unknown = set(metadata) - KNOWN_KEYS
    if unknown:
        print(
            f"changelog: warning: {path}: ignoring unknown metadata: "
            f"{', '.join(sorted(unknown))}"
            " (a newer contract may define it; check the spelling of optional keys)",
            file=sys.stderr,
        )
    if not metadata.get("title"):
        raise ChangelogError(f"{path}: title is required")
    if "component" in metadata and not COMPONENT_NAME.fullmatch(metadata["component"]):
        raise ChangelogError(
            f"{path}: component must be a 1-64 character lowercase identifier"
            " using letters, digits, dot, underscore, or hyphen, and must start"
            " and end with a letter or digit"
        )
    if metadata.get("impact", "patch") not in RELEASE_IMPACTS:
        raise ChangelogError(f"{path}: impact must be one of major, minor, or patch")
    # Both of these are what a released snapshot says about the entry, so both
    # get the same scrutiny: whatever is wrong with them becomes permanent.
    for key in ("title", "summary"):
        value = metadata.get(key)
        if value is None:
            continue
        if not value.strip():
            raise ChangelogError(f"{path}: {key} must not be empty")
        if quoting_is_ambiguous(value):
            quote = value[0]
            raise ChangelogError(
                f"{path}: {key} opens and closes with {quote} but is not a single quoted"
                f" scalar, so the quotes would ship literally into a release snapshot that"
                f" can never be edited. Either remove the outer pair, or escape the interior"
                f" quotes ({quote}{quote} inside single quotes, backslash inside double)."
            )
    try:
        dt.date.fromisoformat(metadata.get("date", ""))
    except ValueError as exc:
        raise ChangelogError(f"{path}: date must be YYYY-MM-DD") from exc
    identities = [key for key in ("issue", "id") if key in metadata]
    if len(identities) != 1:
        raise ChangelogError(f"{path}: exactly one of issue or id is required")
    if "issue" in metadata:
        if not metadata["issue"].isdigit() or int(metadata["issue"]) < 1:
            if re.fullmatch(r"(?:[0-9]{8}T[0-9]{6}Z|[0-9a-fA-F]{6,12})", metadata["issue"]):
                raise ChangelogError(
                    f"{path}: issue must be a positive integer; {metadata['issue']} "
                    "is an issue-less identity — move it to the id key "
                    "(the filename keeps its -issue- segment either way)"
                )
            raise ChangelogError(f"{path}: issue must be a positive integer")
        reference_issues(path, metadata)
        return f"issue:{int(metadata['issue'])}"
    if not re.fullmatch(r"(?:[0-9]{8}T[0-9]{6}Z|[0-9a-fA-F]{6,12})", metadata["id"]):
        raise ChangelogError(f"{path}: id must be a UTC timestamp or short hexadecimal UUID")
    reference_issues(path, metadata)
    return f"id:{metadata['id'].lower()}"


def reference_issues(path: Path, metadata: dict[str, str]) -> list[int]:
    """Issue numbers this entry links but does not own.

    Identity must stay unique — it is what makes fragments conflict-free — so
    only one entry per issue may carry `issue:`. Several entries can still be
    work on that issue, and before `refs` the rest silently lost their release
    back-link because only issue-form identities render `#n`. `refs` separates
    linkage from ownership so all of them link it (#316).
    """
    raw = metadata.get("refs", "").strip()
    if not raw:
        return []
    own = int(metadata["issue"]) if "issue" in metadata else None
    seen: list[int] = []
    for token in raw.split(","):
        token = token.strip().lstrip("#").strip()
        if not token.isdigit() or int(token) < 1:
            raise ChangelogError(
                f"{path}: refs must be a comma-separated list of positive issue numbers"
            )
        number = int(token)
        if number == own:
            raise ChangelogError(
                f"{path}: refs must not repeat this entry's own issue #{number}"
            )
        if number in seen:
            raise ChangelogError(f"{path}: refs lists #{number} more than once")
        seen.append(number)
    return seen


def load_canonical(path: Path) -> Fragment:
    return load_canonical_text(path, path.read_text(encoding="utf-8"))


def load_canonical_text(path: Path, text: str) -> Fragment:
    match = CANONICAL_NAME.fullmatch(path.name)
    if not match:
        raise ChangelogError(f"{path}: filename does not follow the canonical contract")
    metadata, body = parse_frontmatter_text(path, text)
    identity = validate_metadata(path, metadata)
    filename_identity = match["identity"].lower()
    expected_identity = metadata.get("issue", metadata.get("id", "")).lower()
    if metadata["date"] != match["date"] or filename_identity != expected_identity:
        raise ChangelogError(f"{path}: filename identity/date does not match metadata")
    if not body.strip():
        raise ChangelogError(f"{path}: fragment body is empty")
    return Fragment(path, metadata, body, identity, True)


def load_legacy(
    path: Path,
    require_identity: bool = True,
    infer_issue_from_prose: bool = True,
) -> Fragment:
    text = path.read_text(encoding="utf-8")
    metadata: dict[str, str]
    body: str
    try:
        metadata, body = parse_frontmatter(path)
        identity = validate_metadata(path, metadata)
    except ChangelogError:
        issues = (
            {match.group("issue") for match in LEGACY_ISSUE.finditer(text)}
            if infer_issue_from_prose
            else set()
        )
        if len(issues) != 1 and require_identity:
            raise ChangelogError(
                f"{path}: legacy fragment needs metadata or exactly one issue reference"
            )
        issue = issues.pop() if len(issues) == 1 else None
        date_match = re.match(r"(?P<date>\d{4}-\d{2}-\d{2})-", path.name)
        metadata = {
            "date": date_match["date"] if date_match else "1970-01-01",
            "title": path.stem,
        }
        if issue:
            metadata["issue"] = issue
            identity = f"issue:{int(issue)}"
        else:
            metadata["id"] = path.stem
            identity = f"legacy-file:{path.name}"
        body = text.strip() + "\n"
    return Fragment(path, metadata, body, identity, False)


def fragments(
    repo_root: Path,
    legacy_dir: str | None = None,
    allow_legacy_next: bool = False,
) -> list[Fragment]:
    result: list[Fragment] = []
    next_dir = repo_root / UNRELEASED_DIR
    if next_dir.is_dir():
        for path in sorted(next_dir.glob("*.md")):
            if path.name == "README.md":
                continue
            if path.name == "0000-archive.md" and not allow_legacy_next:
                continue
            if CANONICAL_NAME.fullmatch(path.name):
                result.append(load_canonical(path))
            elif allow_legacy_next:
                result.append(
                    load_legacy(
                        path,
                        require_identity=False,
                        infer_issue_from_prose=False,
                    )
                )
            else:
                raise ChangelogError(
                    f"{path}: filename does not follow the canonical contract"
                )
    if legacy_dir:
        legacy_root = (repo_root / legacy_dir).resolve()
        if legacy_root == next_dir.resolve():
            raise ChangelogError("legacy directory must differ from NEXT/")
        if legacy_root.is_dir():
            result.extend(load_legacy(path) for path in sorted(legacy_root.glob("*.md")))
    seen: dict[str, Path] = {}
    for fragment in result:
        if previous := seen.get(fragment.identity):
            raise ChangelogError(
                f"duplicate identity {fragment.identity}: {previous} and {fragment.path}"
            )
        seen[fragment.identity] = fragment.path
    return result


def require_revision(option: str, value: str) -> None:
    """Refuse a blank revision instead of letting Git resolve it to HEAD.

    `git diff "...HEAD"` and `git diff "BASE..."` both fill a blank side in
    with HEAD, so an unset `BASE_SHA` reaching this gate compares HEAD against
    itself: every requirement below evaluates over an empty diff and the gate
    exits 0. A caller that lost its revision must be told so, not told the
    pull request is clean.
    """
    if not value.strip():
        raise ChangelogError(
            f"{option} is empty; a revision is required so the comparison "
            "cannot silently resolve to HEAD and pass over an empty diff"
        )
    if value.startswith("-"):
        raise ChangelogError(
            f"{option} looks like an option, not a revision: {value!r}. "
            "Git would consume it as one and diff nothing, so the gate would "
            "pass over an empty diff."
        )


def added_fragment_paths(repo_root: Path, base: str, head: str) -> set[str]:
    added: set[str] = set()
    for line in git(
        repo_root,
        "diff",
        "--find-renames",
        "--name-status",
        "--end-of-options",
        f"{base}...{head}",
    ).splitlines():
        fields = line.split("\t")
        status = fields[0]
        if status == "A" and len(fields) == 2 and fields[1].startswith(f"{UNRELEASED_DIR}/"):
            added.add(fields[1])
        if status.startswith("R") and len(fields) == 3:
            source, destination = fields[1:]
            if destination.startswith(f"{UNRELEASED_DIR}/") and (
                not source.startswith(f"{UNRELEASED_DIR}/")
                or not same_canonical_fragment_identity(
                    repo_root,
                    base,
                    source,
                    destination,
                )
            ):
                added.add(destination)
    return added


def same_canonical_fragment_identity(
    repo_root: Path,
    base: str,
    source: str,
    destination: str,
) -> bool:
    source_path = PurePosixPath(source)
    destination_path = PurePosixPath(destination)
    canonical_parent = PurePosixPath(UNRELEASED_DIR)
    if source_path.parent != canonical_parent or destination_path.parent != canonical_parent:
        return False
    try:
        source_fragment = load_canonical_text(
            Path(source),
            git(repo_root, "show", f"{base}:{source}"),
        )
        destination_fragment = load_canonical(repo_root / destination)
    except ChangelogError:
        return False
    return (
        source_fragment.metadata["date"],
        source_fragment.identity,
    ) == (
        destination_fragment.metadata["date"],
        destination_fragment.identity,
    )


def validate_new_fragment_impacts(
    repo_root: Path,
    base: str,
    head: str,
    entries: list[Fragment] | None = None,
) -> None:
    require_revision("--base", base)
    require_revision("--head", head)
    if entries is None:
        entries = fragments(repo_root)
    entries_by_path = {
        str(entry.path.relative_to(repo_root)): entry
        for entry in entries
        if entry.canonical
    }
    missing = sorted(
        path
        for path in added_fragment_paths(repo_root, base, head)
        if path in entries_by_path and "impact" not in entries_by_path[path].metadata
    )
    if missing:
        raise ChangelogError(
            f"{missing[0]}: impact is required for every new fragment and must be one of "
            "major, minor, or patch"
        )


def impact_migration_window_active(through: str | None) -> bool:
    if through is None:
        return False
    try:
        deadline = dt.date.fromisoformat(through)
    except ValueError as exc:
        raise ChangelogError(
            "missing-impact migration deadline must be YYYY-MM-DD"
        ) from exc
    return dt.datetime.now(dt.timezone.utc).date() <= deadline


def _rendered_refs(entry: Fragment) -> str:
    numbers = reference_issues(entry.path, entry.metadata)
    if not numbers:
        return ""
    return "; refs " + ", ".join(f"#{number}" for number in numbers)


def lead_paragraph(body: str) -> str:
    """The first blank-line-delimited paragraph of a fragment body.

    Deliberately a plain split with no block-type detection. A first pass that
    tried to recognise non-prose openers produced 7 false positives across
    v0.11.0, every one of them prose beginning `#79 threaded ...` — not a
    heading in CommonMark, but the easy way to write that bug. Coarse beats
    clever here, because the result is immutable the moment it is released.
    """
    return body.strip().split("\n\n", 1)[0].strip()


def release_note(entry: Fragment) -> str:
    """What a released snapshot says about an entry.

    `summary` is an override, not a requirement: all 62 entries of the release
    that prompted this had a usable lead paragraph, so no existing fragment
    needs editing (#426).
    """
    return entry.metadata.get("summary") or lead_paragraph(entry.body)


def rendered_identity(entry: Fragment) -> str:
    """How an entry names itself on the page.

    `identity` is a comparison key, and it is lower-cased so two spellings of
    one hexadecimal id cannot become two entries. That normalisation must not
    reach the reader: a timestamp identity is ISO-8601, where `T` and `Z` are
    literals, so `id:20260805t000000z` is a mangled timestamp rather than a
    quieter one — and permanent once released (#434).
    """
    if entry.identity.startswith("issue:"):
        return entry.identity.replace(":", " #", 1)
    if entry.identity.startswith("id:"):
        return f"id:{entry.metadata['id']}"
    return entry.identity


def render(entries: list[Fragment], released: bool = False) -> str:
    """Render entries for the running log, or for a released snapshot.

    One renderer served two audiences with opposite needs, and the running log
    won by default: the org convention asks a fragment to carry its rationale,
    so `CHANGELOG/<version>.md` shipped the engineering diary as release notes
    — 174 KB for 62 entries. The released form keeps the lead paragraph and
    leaves the argument in `NEXT/`, where git history still has it (#426).
    """
    sections = []
    for entry in sorted(entries, key=lambda item: item.sort_key, reverse=True):
        sections.append(
            f"## {entry.metadata['title']}\n\n"
            f"{release_note(entry) if released else entry.body.strip()}\n\n"
            f"_Date: {entry.metadata['date']}; "
            f"{rendered_identity(entry)}"
            f"{_rendered_refs(entry)}_"
        )
    return "\n\n".join(sections) + ("\n" if sections else "")


def select_component(entries: list[Fragment], component: str | None) -> list[Fragment]:
    if component is not None and not COMPONENT_NAME.fullmatch(component):
        raise ChangelogError("component selector is not a valid component name")
    return [
        entry
        for entry in entries
        if (entry.metadata.get("component") or None) == component
    ]


def render_next(
    repo_root: Path,
    component: str | None = None,
    legacy_dir: str | None = None,
    allow_legacy_next: bool = False,
    released: bool = False,
    selected_names: list[str] | None = None,
) -> str:
    if selected_names is None:
        selected = select_component(
            fragments(repo_root, legacy_dir, allow_legacy_next),
            component,
        )
    else:
        if legacy_dir or allow_legacy_next:
            raise ChangelogError(
                "fragment selection is available only for canonical NEXT/ fragments"
            )
        selected = select_release_fragments(repo_root, selected_names, component)
    if not selected:
        stream = f" for component {component}" if component is not None else ""
        raise ChangelogError(f"no unreleased fragments{stream}")
    return render(selected, released=released)


def snapshot_paths(repo_root: Path) -> list[Path]:
    root = repo_root / "CHANGELOG"
    if not root.is_dir():
        return []
    paths = [path for path in root.glob("*.md") if SNAPSHOT_NAME.fullmatch(path.name)]
    def version_key(path: Path) -> tuple[object, ...]:
        semantic = re.fullmatch(
            r"v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
            r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?",
            path.stem,
        )
        if semantic:
            prerelease = semantic["prerelease"]
            prerelease_key = tuple(
                (0, int(part)) if part.isdigit() else (1, part.lower())
                for part in prerelease.split(".")
            ) if prerelease else ()
            return (
                1,
                int(semantic["major"]),
                int(semantic["minor"]),
                int(semantic["patch"]),
                1 if prerelease is None else 0,
                prerelease_key,
            )
        natural = tuple(
            (0, int(part)) if part.isdigit() else (1, part.lower())
            for part in re.findall(r"\d+|[^\d]+", path.stem)
        )
        return (0, natural)

    return sorted(paths, key=version_key, reverse=True)


def render_released(repo_root: Path) -> str:
    sections = []
    for path in snapshot_paths(repo_root):
        sections.append(f"# {path.stem}\n\n{path.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(sections) + ("\n" if sections else "")


def release_impact(entries: list[Fragment]) -> str:
    if not entries:
        raise ChangelogError("cannot determine impact for an empty release")
    order = {name: index for index, name in enumerate(RELEASE_IMPACTS)}
    return max(
        (entry.metadata.get("impact", "patch") for entry in entries),
        key=order.__getitem__,
    )


def derived_release_bump(
    repo_root: Path,
    prefix: str,
    selected: list[Fragment],
    *,
    allow_qualified_bootstrap: bool = False,
    allow_newer_qualified_history: bool = False,
) -> tuple[tuple[int, int, int], str, tuple[int, int, int]] | None:
    stable_cores = []
    qualified_cores = []
    for path in snapshot_paths(repo_root):
        previous = SEMVER_RELEASE.fullmatch(path.stem)
        if previous is not None and previous["prefix"] == prefix:
            core = tuple(int(previous[name]) for name in ("major", "minor", "patch"))
            if previous["prerelease"] is None and previous["build"] is None:
                stable_cores.append(core)
            else:
                qualified_cores.append(core)
    if not stable_cores:
        if qualified_cores and not allow_qualified_bootstrap:
            raise ChangelogError(
                f"cannot derive the next version for {prefix}: only prerelease or "
                "build-qualified snapshots exist; supply an explicit bootstrap version"
            )
        return None
    previous_core = max(stable_cores)
    if (
        qualified_cores and max(qualified_cores) > previous_core
        and not allow_newer_qualified_history
    ):
        raise ChangelogError(
            f"cannot determine the next version for {prefix}: a prerelease or "
            "build-qualified snapshot is newer than the latest stable snapshot"
        )
    impact = release_impact(selected)
    if impact == "major":
        expected = (previous_core[0] + 1, 0, 0)
    elif impact == "minor":
        expected = (previous_core[0], previous_core[1] + 1, 0)
    else:
        expected = (previous_core[0], previous_core[1], previous_core[2] + 1)
    return previous_core, impact, expected


def derived_release_version(
    repo_root: Path,
    prefix: str,
    selected: list[Fragment],
) -> str | None:
    bump = derived_release_bump(repo_root, prefix, selected)
    if bump is None:
        return None
    return prefix + ".".join(map(str, bump[2]))


def prerelease_precedence(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in value.split(".")
    )


def validate_release_bump(repo_root: Path, version: str, selected: list[Fragment]) -> None:
    requested = SEMVER_RELEASE.fullmatch(version)
    if requested is None:
        raise ChangelogError("version must be v-prefixed SemVer, optionally with a stream prefix")
    prefix = requested["prefix"]
    requested_core = tuple(int(requested[name]) for name in ("major", "minor", "patch"))
    bump = derived_release_bump(
        repo_root, prefix, selected,
        allow_qualified_bootstrap=True, allow_newer_qualified_history=True,
    )
    same_core_prereleases = []
    for path in snapshot_paths(repo_root):
        previous = SEMVER_RELEASE.fullmatch(path.stem)
        if previous is None or previous["prefix"] != prefix:
            continue
        core = tuple(int(previous[name]) for name in ("major", "minor", "patch"))
        if bump is None and core > requested_core:
            raise ChangelogError(
                f"explicit bootstrap version {version} precedes existing qualified snapshot {path.stem}"
            )
        if core != requested_core:
            continue
        if previous["prerelease"] is not None:
            same_core_prereleases.append(previous["prerelease"])
        elif requested["prerelease"] is None:
            raise ChangelogError(f"explicit version {version} has equal precedence to existing snapshot {path.stem}")
        if requested["prerelease"] is not None:
            if previous["prerelease"] is None or prerelease_precedence(requested["prerelease"]) <= prerelease_precedence(previous["prerelease"]):
                raise ChangelogError(f"explicit version {version} does not advance the precedence of {path.stem}")
    if bump is None:
        return
    previous_core, impact, expected_core = bump
    if requested_core == expected_core:
        return
    # Explicit progression or graduation can target an existing prerelease core.
    # Those fragments may describe only changes since the prerelease.
    if requested_core > previous_core and requested_core >= expected_core and same_core_prereleases:
        return
    previous_text = ".".join(map(str, previous_core))
    expected_text = ".".join(map(str, expected_core))
    requested_text = ".".join(map(str, requested_core))
    raise ChangelogError(
        f"selected fragments require a {impact} bump from {prefix}{previous_text}"
        f" to {prefix}{expected_text}; requested {prefix}{requested_text}"
    )


def git(repo_root: Path, *args: str) -> str:
    # core.hooksPath=/dev/null: this helper's `commit` call must never execute a
    # repository hook (pre-commit, commit-msg) — release() runs it with a freshly
    # minted, short-lived release credential in its environment (container-release.yml),
    # and a hook is exactly the kind of side channel that credential must never reach.
    # Applied to every invocation, not just `commit`, so no future call site has to
    # remember it.
    completed = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise ChangelogError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _selected_key(name: str, by_name: dict[str, "Fragment"]) -> str:
    """The basename a `--fragment` value denotes, or a diagnosis of why it does not.

    Selection is indexed by basename, but release callers naturally forward the
    repository-relative path they were given — `NEXT/<file>.md` — and that was
    rejected as "selected fragment does not exist", which reads as a missing
    fragment rather than a path-shape mismatch (#328). Both spellings are accepted;
    anything else is refused with the accepted forms named, because guessing at an
    unexpected shape is how a release consumes a fragment nobody selected.

    Path traversal and absolute paths are refused outright rather than reduced to a
    basename: `--fragment ../../etc/passwd` happening to end in a name that exists
    must not select it, and a value pointing outside the unreleased directory is a
    caller bug worth surfacing, not normalising away.
    """
    raw = name.strip()
    if not raw:
        raise ChangelogError("selected fragment is empty")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or raw.startswith("/"):
        raise ChangelogError(f"selected fragment must be repository-relative: {name}")
    if ".." in candidate.parts:
        raise ChangelogError(f"selected fragment must not traverse directories: {name}")
    parents = [part for part in candidate.parts[:-1] if part != "."]
    if parents and parents != [UNRELEASED_DIR]:
        raise ChangelogError(
            f"selected fragment must be a bare filename or {UNRELEASED_DIR}/<file>: {name}"
        )
    key = candidate.name
    if key not in by_name:
        raise ChangelogError(f"selected fragment does not exist: {name}")
    return key


def select_release_fragments(
    repo_root: Path,
    selected_names: list[str],
    component: str | None = None,
    allow_empty: bool = False,
) -> list[Fragment]:
    entries = fragments(repo_root)
    by_name = {entry.path.name: entry for entry in entries if entry.canonical}
    stream_entries = select_component(entries, component)
    selected = stream_entries if not selected_names else []
    selected_keys: set[str] = set()
    for name in selected_names:
        key = _selected_key(name, by_name)
        if key in selected_keys:
            raise ChangelogError(f"selected fragment was repeated: {name}")
        selected_keys.add(key)
        entry = by_name[key]
        if entry not in stream_entries:
            actual = entry.metadata.get("component") or "unscoped"
            expected = component or "unscoped"
            raise ChangelogError(
                f"selected fragment belongs to component {actual}, not {expected}"
            )
        selected.append(entry)
    if not selected and not allow_empty:
        raise ChangelogError("release selected no fragments")
    return selected


def selection_digest(
    repo_root: Path,
    selected_names: list[str],
    component: str | None = None,
    prefix: str = "v",
) -> str | None:
    if SEMVER_PREFIX.fullmatch(prefix) is None:
        raise ChangelogError(
            "version prefix must be v or a lowercase stream name followed by -v"
        )
    selected = select_release_fragments(
        repo_root,
        selected_names,
        component,
        allow_empty=True,
    )
    if not selected:
        return None
    return selection_digest_for_entries(component, prefix, selected)


def selection_digest_for_entries(
    component: str | None,
    prefix: str,
    selected: list[Fragment],
) -> str:
    canonical = json.dumps(
        {
            "component": component or "",
            "fragments": sorted(entry.identity for entry in selected),
            "prefix": prefix,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def next_version_for_entries(
    repo_root: Path,
    prefix: str,
    selected: list[Fragment],
) -> str:
    version = derived_release_version(repo_root, prefix, selected)
    if version is None:
        raise ChangelogError(
            f"cannot derive the next version for {prefix} without a previous release"
        )
    return version


def next_version(
    repo_root: Path,
    selected_names: list[str],
    component: str | None = None,
    prefix: str = "v",
) -> str:
    selected = select_release_fragments(repo_root, selected_names, component)
    if SEMVER_PREFIX.fullmatch(prefix) is None:
        raise ChangelogError(
            "version prefix must be v or a lowercase stream name followed by -v"
        )
    return next_version_for_entries(repo_root, prefix, selected)


def release_plan(
    repo_root: Path,
    version: str,
    selected_names: list[str],
    component: str | None = None,
    prefix: str = "v",
) -> dict[str, object]:
    if SEMVER_PREFIX.fullmatch(prefix) is None:
        raise ChangelogError(
            "version prefix must be v or a lowercase stream name followed by -v"
        )
    selected = select_release_fragments(
        repo_root,
        selected_names,
        component,
        allow_empty=True,
    )
    requested = SEMVER_RELEASE.fullmatch(version) if version else None
    if version and requested is None:
        raise ChangelogError(
            "version must be v-prefixed SemVer, optionally with a stream prefix"
        )
    if requested is not None and requested["prefix"] != prefix:
        raise ChangelogError(
            f"version namespace {requested['prefix']!r} does not match prefix {prefix!r}"
        )
    if not selected:
        return {
            "bump": None,
            "bump_rationale": "No unreleased fragments are selected; no release will be created.",
            "component": component or "",
            "fragments": [],
            "previous_release": None,
            "preview": "",
            "prefix": prefix,
            "selected": False,
            "selection_digest": None,
            "version": version or None,
        }
    resolved = version or next_version_for_entries(repo_root, prefix, selected)
    validate_release_bump(repo_root, resolved, selected)
    bump = derived_release_bump(
        repo_root, prefix, selected, allow_qualified_bootstrap=bool(version),
        allow_newer_qualified_history=bool(version),
    )
    previous_release = None
    impact = "bootstrap"
    rationale = (
        "Bootstrap release: the explicit version is accepted because no prior "
        "release exists for this namespace."
    )
    if bump is not None:
        previous_core, impact, expected_core = bump
        previous_release = prefix + ".".join(map(str, previous_core))
        expected_release = prefix + ".".join(map(str, expected_core))
        resolved_match = SEMVER_RELEASE.fullmatch(resolved)
        resolved_core = tuple(int(resolved_match[name]) for name in ("major", "minor", "patch"))
        if resolved_core != expected_core:
            action = "advances" if resolved_match["prerelease"] else "graduates"
            rationale = (
                f"The explicit version {resolved} {action} an existing prerelease "
                f"after {previous_release}; the selected fragment impact is {impact}."
            )
        elif resolved_match["prerelease"] is not None or resolved_match["build"] is not None:
            rationale = (
                f"The highest selected fragment impact is {impact}; the explicit "
                f"version {resolved} has the required core {expected_release}."
            )
        else:
            rationale = (
                f"The highest selected fragment impact is {impact}; the canonical "
                f"release rule advances {previous_release} to {expected_release}."
            )
    return {
        "bump": impact,
        "bump_rationale": rationale,
        "component": component or "",
        "fragments": [entry.path.name for entry in selected],
        "previous_release": previous_release,
        "preview": render(selected, released=True),
        "prefix": prefix,
        "selected": True,
        "selection_digest": selection_digest_for_entries(component, prefix, selected),
        "version": resolved,
    }


def release(
    repo_root: Path,
    version: str,
    selected_names: list[str],
    component: str | None = None,
) -> None:
    if not SNAPSHOT_NAME.fullmatch(f"{version}.md"):
        raise ChangelogError("version contains unsupported characters")
    if git(repo_root, "status", "--porcelain", "--untracked-files=no"):
        raise ChangelogError("release requires a clean working tree")
    lock_path = Path(git(repo_root, "rev-parse", "--git-path", "changelog-release.lock"))
    if not lock_path.is_absolute():
        lock_path = repo_root / lock_path
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ChangelogError("another release is already running") from exc
        snapshot = repo_root / "CHANGELOG" / f"{version}.md"
        if snapshot.exists():
            raise ChangelogError(f"released snapshot already exists: {snapshot}")
        if git(repo_root, "tag", "--list", version):
            raise ChangelogError(f"release tag already exists: {version}")
        selected = select_release_fragments(repo_root, selected_names, component)
        validate_release_bump(repo_root, version, selected)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(render(selected, released=True), encoding="utf-8")
        for entry in selected:
            entry.path.unlink()
        aggregate = repo_root / "CHANGELOG.md"
        aggregate.write_text(render_released(repo_root), encoding="utf-8")
        git(repo_root, "add", "NEXT", "CHANGELOG", "CHANGELOG.md")
        git(repo_root, "commit", "-m", f"release: {version}")
        release_commit = git(repo_root, "rev-parse", "HEAD")
        git(repo_root, "tag", "-a", version, "-m", f"Release {version}", release_commit)
        if git(repo_root, "rev-list", "-n", "1", version) != release_commit:
            raise ChangelogError("release tag does not point to the release commit")


def changed_paths(repo_root: Path, base: str, head: str) -> set[str]:
    output = git(
        repo_root,
        "diff",
        "--find-renames",
        "--name-only",
        "--end-of-options",
        f"{base}...{head}",
    )
    return {line for line in output.splitlines() if line}


DEPENDENCY_FILENAMES = {
    ".terraform.lock.hcl",
    "Cargo.lock",
    "Cargo.toml",
    "Pipfile",
    "Pipfile.lock",
    "go.mod",
    "go.sum",
    "npm-shrinkwrap.json",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "uv.lock",
    "yarn.lock",
}

PRE_CONTRACT_MIGRATIONS_PATH = ".github/changelog/pre-contract-migrations.json"
PRE_CONTRACT_MIGRATION_KEYS = frozenset(
    {"source", "version", "sha256", "destination"}
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def safe_repository_path(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ChangelogError(f"pre-contract migration {field} is not a safe path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
        or path.parts[0] == ".git"
    ):
        raise ChangelogError(f"pre-contract migration {field} is not a safe path")
    return value


def parse_pre_contract_migrations(content: bytes) -> tuple[PreContractMigration, ...]:
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChangelogError("pre-contract migration permit is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "migrations"}:
        raise ChangelogError("pre-contract migration permit has unsupported fields")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ChangelogError("pre-contract migration permit has unsupported schema")
    if not isinstance(document["migrations"], list):
        raise ChangelogError("pre-contract migration permit has unsupported schema")

    migrations = []
    sources = set()
    destinations = set()
    for item in document["migrations"]:
        if not isinstance(item, dict) or set(item) != PRE_CONTRACT_MIGRATION_KEYS:
            raise ChangelogError("pre-contract migration entry has unsupported fields")
        source = safe_repository_path(item["source"], "source")
        destination = safe_repository_path(item["destination"], "destination")
        version = item["version"]
        digest = item["sha256"]
        if not isinstance(version, str) or source != f"CHANGELOG/{version}.md":
            raise ChangelogError("pre-contract migration source does not match its version")
        if not SNAPSHOT_NAME.fullmatch(f"{version}.md"):
            raise ChangelogError("pre-contract migration version is invalid")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ChangelogError("pre-contract migration sha256 is invalid")
        if (
            destination == "CHANGELOG.md"
            or destination == PRE_CONTRACT_MIGRATIONS_PATH
            or destination.startswith("CHANGELOG/")
            or destination.startswith("NEXT/")
        ):
            raise ChangelogError("pre-contract migration destination is active changelog state")
        if source in sources or destination in destinations:
            raise ChangelogError("pre-contract migration permit contains duplicate identities")
        sources.add(source)
        destinations.add(destination)
        migrations.append(PreContractMigration(source, version, digest, destination))
    return tuple(migrations)


def git_file(repo_root: Path, revision: str, path: str) -> bytes | None:
    completed = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode == 0:
        return completed.stdout
    if completed.returncode in (1, 128):
        return None
    raise ChangelogError(completed.stderr.decode(errors="replace").strip())


def git_file_mode(repo_root: Path, revision: str, path: str) -> str | None:
    completed = subprocess.run(
        ["git", "ls-tree", revision, "--", path],
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise ChangelogError(completed.stderr.strip() or "could not inspect migration path")
    if not completed.stdout:
        return None
    return completed.stdout.split(None, 1)[0]


def source_exists_in_any_tag(repo_root: Path, source: str) -> bool:
    tags = git(repo_root, "for-each-ref", "--format=%(refname)", "refs/tags")
    for tag in tags.splitlines():
        completed = subprocess.run(
            ["git", "cat-file", "-e", f"{tag}:{source}"],
            cwd=repo_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            return True
        if completed.returncode not in (1, 128):
            raise ChangelogError(f"could not inspect tag {tag} for {source}")
    return False


def require_complete_tag_visibility(repo_root: Path) -> None:
    shallow = git(repo_root, "rev-parse", "--is-shallow-repository")
    if shallow != "false":
        raise ChangelogError(
            "pre-contract migration requires a complete non-shallow tag checkout"
        )


def authorize_pre_contract_migration(
    repo_root: Path,
    base: str,
    head: str,
    changed: set[str],
    status_by_path: dict[str, str],
) -> set[str]:
    base_document = git_file(repo_root, base, PRE_CONTRACT_MIGRATIONS_PATH)
    if base_document is None:
        return set()
    permits = parse_pre_contract_migrations(base_document)
    candidates = [
        permit
        for permit in permits
        if permit.source in changed or permit.destination in changed
    ]
    if not candidates:
        return set()
    if len(candidates) != 1:
        raise ChangelogError("a pull request can use only one pre-contract migration permit")
    permit = candidates[0]
    if PRE_CONTRACT_MIGRATIONS_PATH in changed:
        raise ChangelogError("a pull request cannot widen its own pre-contract migration permit")
    if (
        status_by_path.get(permit.source) != "D"
        or status_by_path.get(permit.destination) != "A"
    ):
        raise ChangelogError(
            "pre-contract migration must delete its source and add its destination"
        )
    if {path for path in changed if path.startswith("CHANGELOG/")} != {permit.source}:
        raise ChangelogError("a pre-contract migration cannot change other release snapshots")
    source = git_file(repo_root, base, permit.source)
    destination_before = git_file(repo_root, base, permit.destination)
    destination_after = git_file(repo_root, head, permit.destination)
    source_after = git_file(repo_root, head, permit.source)
    if (
        source is None
        or destination_after is None
        or destination_before is not None
        or source_after is not None
    ):
        raise ChangelogError("pre-contract migration source or destination state is invalid")
    source_mode = git_file_mode(repo_root, base, permit.source)
    destination_mode = git_file_mode(repo_root, head, permit.destination)
    if source_mode not in ("100644", "100755") or destination_mode != source_mode:
        raise ChangelogError("pre-contract migration must preserve a regular file mode")
    if hashlib.sha256(source).hexdigest() != permit.sha256:
        raise ChangelogError("pre-contract migration source digest does not match its permit")
    if source != destination_after:
        raise ChangelogError("pre-contract migration destination is not byte-identical")
    require_complete_tag_visibility(repo_root)
    if source_exists_in_any_tag(repo_root, permit.source):
        raise ChangelogError("pre-contract migration cannot move a snapshot present in a tag")
    return {permit.source, permit.destination}


def validate_permit_update(repo_root: Path, base: str, head: str) -> None:
    base_document = git_file(repo_root, base, PRE_CONTRACT_MIGRATIONS_PATH)
    head_document = git_file(repo_root, head, PRE_CONTRACT_MIGRATIONS_PATH)
    if head_document is None:
        raise ChangelogError("pre-contract migration permit is append-only")
    head_permits = parse_pre_contract_migrations(head_document)
    base_permits = () if base_document is None else parse_pre_contract_migrations(base_document)
    if head_permits[: len(base_permits)] != base_permits:
        raise ChangelogError("pre-contract migration permit is append-only")


# `Dockerfile.<x>` is a Dockerfile variant only when `<x>` names a variant.
# These suffixes name a different kind of file that merely sits beside one:
# prose and editor droppings, and the build-context exclusion lists — a
# `Dockerfile.dockerignore` pins no dependency version and must not force a
# fragment on its own. Reproduced in `Verjson/verjson-ci` on a real file.
NON_DEPENDENCY_SIBLING_SUFFIXES = frozenset(
    {
        ".adoc",
        ".backup",
        ".bak",
        ".containerignore",
        ".dockerignore",
        ".gitignore",
        ".ignore",
        ".markdown",
        ".md",
        ".npmignore",
        ".old",
        ".orig",
        ".rst",
        ".save",
        ".swp",
        ".tmp",
        ".txt",
    }
)


def is_dependency_file(path: str) -> bool:
    filename = Path(path).name
    if re.fullmatch(r"(?:docker-compose|compose)(?:\.[A-Za-z0-9_-]+)*\.ya?ml", filename):
        return True
    if Path(filename).suffix.lower() not in NON_DEPENDENCY_SIBLING_SUFFIXES and re.fullmatch(
        r"(?:(?:Dockerfile|Containerfile)(?:\.[A-Za-z0-9_-]+)*|"
        r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:Dockerfile|Containerfile))",
        filename,
    ):
        return True
    return filename in DEPENDENCY_FILENAMES or bool(
        re.fullmatch(r"requirements(?:-[A-Za-z0-9_.-]+)?\.txt", filename)
    )


# Everything the running log is meant to describe, minus an explicit exemption
# list. An allow-list of "production" directories cannot be kept current across
# every adopter shape, so the rule is keyed on the file being source code and
# each exemption is named here rather than inferred.
PRODUCTION_SOURCE_SUFFIXES = frozenset(
    {
        ".bash",
        ".c",
        ".cjs",
        ".cpp",
        ".cs",
        ".cts",
        ".go",
        ".h",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".mjs",
        ".mts",
        ".php",
        ".ps1",
        ".psm1",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".svelte",
        ".swift",
        ".tf",
        ".ts",
        ".tsx",
        ".vue",
    }
)

# Exempt from the production-source classifier because they are either changelog
# stores, documentation, or tests. Documentation is still release-relevant and
# is enforced by `is_release_relevant_change`; tests remain the only content
# exemption because their change is described by the source change they cover.
EXEMPT_SOURCE_PREFIXES = ("CHANGELOG/", "docs/", f"{UNRELEASED_DIR}/")
# A test root is anchored rather than matched at any depth. `tests`, `test`, and
# `spec` are ordinary words: `packages/api/spec/` is a package's own tree far
# more often than a test tree, and exempting it at any depth hid whole source
# subtrees from the running log. `__tests__/` and `__mocks__/` carry no such
# ambiguity, so they stay depth-free.
TEST_ROOT = re.compile(
    r"^(?:[^/]+/){0,2}tests?/"
    r"|^spec/"
    r"|(?:^|/)(?:__tests__|__mocks__)/"
)
# Self-identifying filenames: the marker sits against the extension, so
# `adapter.test.ts` cannot plausibly be production code wherever it lives. A
# bare `test_` prefix is not self-identifying — `tools/test_runner.py` is
# production tooling — so it exempts only by sitting under a test root above.
TEST_FILENAME = re.compile(
    r"(?:^|/)[^/]*[._-]test\.[A-Za-z0-9]+$"
    r"|(?:^|/)[^/]*[._-]spec\.[A-Za-z0-9]+$"
)

RELEASE_LOG_EXEMPT_PATHS = frozenset({"CHANGELOG.md"})
RELEASE_LOG_EXEMPT_PREFIXES = ("CHANGELOG/", f"{UNRELEASED_DIR}/")
GITHUB_WORKFLOW_DEFINITION = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
LOCAL_ACTION_METADATA = frozenset({"action.yml", "action.yaml"})


def is_test_source(path: str) -> bool:
    return bool(TEST_ROOT.search(path) or TEST_FILENAME.search(path))


def is_production_source(path: str) -> bool:
    if path.startswith(EXEMPT_SOURCE_PREFIXES) or is_test_source(path):
        return False
    return Path(path).suffix.lower() in PRODUCTION_SOURCE_SUFFIXES


def is_release_relevant_change(path: str) -> bool:
    """Whether a changed path needs release context in a new fragment.

    The running log covers behavior, configuration, code, documentation, and
    pins. New repository shapes therefore fail closed by default. Only the
    changelog stores and explicitly classified test-only paths are exempt.
    """
    if (
        GITHUB_WORKFLOW_DEFINITION.fullmatch(path)
        or Path(path).name in LOCAL_ACTION_METADATA
    ):
        return True
    if path in RELEASE_LOG_EXEMPT_PATHS or path.startswith(
        RELEASE_LOG_EXEMPT_PREFIXES
    ):
        return False
    return not is_test_source(path)


def is_fragment_path(path: str) -> bool:
    return path.startswith(f"{UNRELEASED_DIR}/") and path != f"{UNRELEASED_DIR}/README.md"


def valid_added_fragments(repo_root: Path, added_fragments: set[str]) -> set[str]:
    canonical = {
        str(entry.path.relative_to(repo_root))
        for entry in fragments(repo_root)
        if entry.canonical
    }
    return added_fragments & canonical


def require_valid_added_fragments(
    repo_root: Path, added_fragments: set[str], message: str
) -> None:
    invalid_added = added_fragments - valid_added_fragments(repo_root, added_fragments)
    if invalid_added:
        raise ChangelogError(
            f"{message}; invalid additions: " + ", ".join(sorted(invalid_added))
        )


def check_pr(repo_root: Path, base: str, head: str) -> None:
    require_revision("--base", base)
    require_revision("--head", head)
    status_by_path = {}
    for line in git(
        repo_root,
        "diff",
        "--no-renames",
        "--name-status",
        "--end-of-options",
        f"{base}...{head}",
    ).splitlines():
        status, path = line.split("\t", 1)
        status_by_path[path] = status
    # Policy evaluates both sides of a move. Rename-aware name-only output can
    # collapse a delete/add pair to its destination and hide consumption of an
    # immutable source from the authorization boundary.
    changed = set(status_by_path)
    if PRE_CONTRACT_MIGRATIONS_PATH in changed:
        validate_permit_update(repo_root, base, head)
    forbidden = {"CHANGELOG.md"} & changed
    forbidden.update(path for path in changed if path.startswith("CHANGELOG/"))
    permitted = authorize_pre_contract_migration(
        repo_root, base, head, changed, status_by_path
    )
    forbidden.difference_update(permitted)
    if forbidden:
        raise ChangelogError(
            "ordinary pull requests cannot edit generated aggregates or released snapshots: "
            + ", ".join(sorted(forbidden))
        )
    consumed = set()
    added_fragments = set()
    for line in git(
        repo_root,
        "diff",
        "--find-renames",
        "--name-status",
        "--end-of-options",
        f"{base}...{head}",
    ).splitlines():
        fields = line.split("\t")
        status = fields[0]
        if status == "D" and len(fields) == 2 and is_fragment_path(fields[1]):
            consumed.add(fields[1])
        if status == "A" and len(fields) == 2 and is_fragment_path(fields[1]):
            added_fragments.add(fields[1])
        if (
            status.startswith("R")
            and len(fields) == 3
            and is_fragment_path(fields[1])
            and not is_fragment_path(fields[2])
        ):
            consumed.add(fields[1])
        if (
            status.startswith("R")
            and len(fields) == 3
            and not is_fragment_path(fields[1])
            and is_fragment_path(fields[2])
        ):
            added_fragments.add(fields[2])
    if consumed:
        raise ChangelogError(
            "ordinary pull requests cannot consume NEXT fragments: "
            + ", ".join(sorted(consumed))
        )
    dependencies = sorted(path for path in changed if is_dependency_file(path))
    if dependencies and not added_fragments:
        raise ChangelogError(
            "dependency manifests or lockfiles require a new NEXT fragment: "
            + ", ".join(dependencies)
        )
    if dependencies:
        require_valid_added_fragments(
            repo_root,
            added_fragments,
            "dependency manifests or lockfiles require a new valid NEXT fragment",
        )
    production = sorted(path for path in changed if is_production_source(path))
    if production and not added_fragments:
        raise ChangelogError(
            "production source changes require a new NEXT fragment: "
            + ", ".join(production)
        )
    if production:
        require_valid_added_fragments(
            repo_root,
            added_fragments,
            "production source changes require a new valid NEXT fragment",
        )
    release_relevant = sorted(
        path
        for path in changed
        if is_release_relevant_change(path)
        and path not in permitted
        and not is_dependency_file(path)
        and not is_production_source(path)
    )
    if release_relevant and not added_fragments:
        raise ChangelogError(
            "release-relevant changes require a new NEXT fragment "
            "(all paths except changelog stores and test-only files): "
            + ", ".join(release_relevant)
        )
    if release_relevant:
        require_valid_added_fragments(
            repo_root,
            added_fragments,
            "release-relevant changes require a new valid NEXT fragment",
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Validate, render, inspect, and release changelog fragments."
    )
    subparsers = result.add_subparsers(dest="command", required=True)
    for name in ("validate", "render-next"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--repo-root", type=Path, default=Path.cwd())
        sub.add_argument("--legacy-dir")
        sub.add_argument("--allow-legacy-next", action="store_true")
        if name == "validate":
            sub.add_argument(
                "--base",
                help="require explicit impact on fragments added since this Git revision",
            )
            sub.add_argument("--head", default="HEAD")
            sub.add_argument(
                "--allow-missing-impact-through",
                metavar="YYYY-MM-DD",
                help="temporarily accept added fragments without impact through this UTC date",
            )
        else:
            # The released form is the one nobody reads until it can no longer
            # be changed. This makes it viewable while the fragments still can.
            sub.add_argument("--as-released", action="store_true")
            sub.add_argument("--component")
            sub.add_argument(
                "--fragment",
                action="append",
                default=None,
                help="render one selected NEXT fragment; repeat to select several",
            )
    released = subparsers.add_parser("render-released")
    released.add_argument("--repo-root", type=Path, default=Path.cwd())
    pr = subparsers.add_parser("check-pr")
    pr.add_argument("--repo-root", type=Path, default=Path.cwd())
    pr.add_argument("--base", required=True)
    pr.add_argument("--head", default="HEAD")
    release_parser = subparsers.add_parser("release")
    release_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    release_parser.add_argument("--version", required=True)
    release_parser.add_argument("--fragment", action="append", default=[])
    release_parser.add_argument("--component")
    next_parser = subparsers.add_parser(
        "next-version",
        help="print the exact next release tag without changing the repository",
        description=(
            "Print the exact tag accepted by release for the selected fragments "
            "without changing the repository."
        ),
    )
    next_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    next_parser.add_argument(
        "--fragment",
        action="append",
        default=[],
        help="select one NEXT fragment; repeat to select several",
    )
    next_parser.add_argument(
        "--component",
        help="select one component stream; omitted selects the unscoped stream",
    )
    next_parser.add_argument(
        "--prefix",
        default="v",
        help="select the version history prefix; defaults to v",
    )
    plan_parser = subparsers.add_parser(
        "release-plan",
        help="resolve and validate one release without changing the repository",
        description=(
            "Resolve the selected fragments, version, bump rationale, and release "
            "preview without changing the repository."
        ),
    )
    plan_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    plan_parser.add_argument(
        "--version",
        default="",
        help="explicit release tag; omit to derive it from the selected fragments",
    )
    plan_parser.add_argument("--fragment", action="append", default=[])
    plan_parser.add_argument("--component")
    plan_parser.add_argument("--prefix", default="v")
    digest_parser = subparsers.add_parser(
        "selection-digest",
        help="print a canonical digest for one release selection",
        description=(
            "Print a stable digest of the component, version prefix, and selected "
            "fragment identities; print nothing when the selected stream is empty."
        ),
    )
    digest_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    digest_parser.add_argument("--fragment", action="append", default=[])
    digest_parser.add_argument("--component")
    digest_parser.add_argument("--prefix", default="v")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        repo_root = args.repo_root.resolve()
        if args.command == "validate":
            entries = fragments(repo_root, args.legacy_dir, args.allow_legacy_next)
            grace_active = impact_migration_window_active(
                args.allow_missing_impact_through
            )
            # `--head` is checked whether or not `--base` was given: it is
            # unused while `--base` is None, but a caller who passed a lost
            # variable should hear that now rather than the next time the
            # surrounding condition changes.
            if args.head is not None:
                require_revision("--head", args.head)
            if args.base is not None:
                require_revision("--base", args.base)
            if args.allow_missing_impact_through and args.base is None:
                raise ChangelogError(
                    "--allow-missing-impact-through requires --base"
                )
            if args.base is not None and not grace_active:
                validate_new_fragment_impacts(
                    repo_root,
                    args.base,
                    args.head,
                    entries,
                )
        elif args.command == "render-next":
            sys.stdout.write(
                render_next(
                    repo_root,
                    component=args.component,
                    legacy_dir=args.legacy_dir,
                    allow_legacy_next=args.allow_legacy_next,
                    released=args.as_released,
                    selected_names=args.fragment,
                )
            )
        elif args.command == "render-released":
            sys.stdout.write(render_released(repo_root))
        elif args.command == "check-pr":
            check_pr(repo_root, args.base, args.head)
        elif args.command == "release":
            release(repo_root, args.version, args.fragment, component=args.component)
        elif args.command == "next-version":
            print(
                next_version(
                    repo_root,
                    args.fragment,
                    component=args.component,
                    prefix=args.prefix,
                )
            )
        elif args.command == "release-plan":
            print(
                json.dumps(
                    release_plan(
                        repo_root,
                        args.version,
                        args.fragment,
                        component=args.component,
                        prefix=args.prefix,
                    ),
                    sort_keys=True,
                )
            )
        elif args.command == "selection-digest":
            digest = selection_digest(
                repo_root,
                args.fragment,
                component=args.component,
                prefix=args.prefix,
            )
            if digest is not None:
                print(digest)
    except ChangelogError as exc:
        print(f"changelog: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
