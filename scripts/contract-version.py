#!/usr/bin/env python3
"""Resolve, classify, and verify an adopter's declared organization contract version.

A `uses:` SHA is a content address. It cannot say which contract an adopter is
on, whether that contract is behind, or whether it is still supported, which is
why 22 of the 29 enforced repositories carry a review caller spread across
eleven distinct contract SHAs (ADR 0185, #1374).

This module is the readback half of ADR 0191: a version an adopter declares but
nothing verifies fails exactly like a pin nobody advances. It resolves the
declared version against published contract releases and then asserts that every
`Verjson/.github` reference on disk names that version's release commit.

Wiring this into a merge gate has a precondition, stated in ADR 0191's
Consequences: the release train must be running first. Restricting legal pins to
release commits before releases are cut on a cadence converts "adopters rot on
their own schedule" into "adopters cannot advance at all".
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import re
import subprocess
import sys

# This file is run as `python3 scripts/contract-version.py` *and* loaded straight
# from its path by the CI-gate suite, which does not put `scripts/` on the path.
# The sibling shared recognizer has to be importable under both.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# The window is never narrower than GitHub Packages retention (ADR 0108 keeps the
# three newest stable versions), so a version the retention policy still considers
# current can never be one this contract refuses.
RETENTION_FLOOR = 3
# Two minor lines: the current one and the one before it.
SUPPORTED_MINOR_LINES = 2


@dataclasses.dataclass(frozen=True)
class Release:
    """One published contract release. `commit` is the tag's resolved object id."""
    version: str
    commit: str
    published: str


def parse_version(version: str) -> tuple[int, int, int] | None:
    """`vX.Y.Z` -> ordering key. Anything else is not a contract version."""
    match = VERSION_RE.match(version or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _ordered(releases):
    """Releases newest first, dropping anything that is not `vX.Y.Z`.

    A prerelease or a non-contract tag must not occupy a slot in a window whose
    whole purpose is to say which contract an adopter may run.
    """
    keyed = [(parse_version(r.version), r) for r in releases]
    return [r for key, r in sorted(
        ((k, r) for k, r in keyed if k is not None), key=lambda p: p[0], reverse=True)]


def supported_versions(releases) -> set[str]:
    """The supported set: two minor lines, but never fewer than the newest three.

    "Current and previous minor" alone can be narrower than package retention --
    three releases on three minor lines leave the oldest unsupported while
    retention still keeps it -- and a window narrower than retention strands a
    consumer the organization's own policy calls current. The union is therefore
    the floor, not the intersection.
    """
    ordered = _ordered(releases)
    lines, supported = [], set()
    for release in ordered:
        major, minor, _ = parse_version(release.version)
        if (major, minor) not in lines:
            if len(lines) == SUPPORTED_MINOR_LINES:
                break
            lines.append((major, minor))
        supported.add(release.version)
    supported.update(r.version for r in ordered[:RETENTION_FLOOR])
    return supported


SUPPORTED = "SUPPORTED"
DEPRECATED = "DEPRECATED"
EXPIRED = "EXPIRED"
UNKNOWN = "UNKNOWN"

# Grace runs from the publication of the release that pushed a version out of the
# window, not from that version's own release: the clock a maintainer can act on
# starts when the obligation appears.
GRACE_DAYS = 90
# A major, by definition, requires adopter work rather than a repin, so crossing
# one buys the longer clock.
MAJOR_GRACE_DAYS = 180


@dataclasses.dataclass(frozen=True)
class Verdict:
    state: str
    reason: str
    expires_on: str | None = None


def _published_order(releases):
    """Oldest first. Publication order is the order the window actually moved in."""
    keyed = [(parse_version(r.version), r) for r in releases]
    return [r for _, r in sorted(
        ((k, r) for k, r in keyed if k is not None),
        key=lambda p: (p[1].published, p[0]))]


def _add_days(iso_date: str, days: int) -> str:
    return (datetime.date.fromisoformat(iso_date) + datetime.timedelta(days=days)).isoformat()


def classify(version: str, releases, today: str) -> Verdict:
    """Where a declared version sits in the window, and when it stops being runnable.

    UNKNOWN is not a soft SUPPORTED. A version that resolves to no published
    release cannot be checked against anything, and reporting that as runnable is
    the fail-open shape ADR 0185 refused: `[ "$a" = "$b" ]` calling two failed
    lookups equal.
    """
    if parse_version(version) is None:
        return Verdict(UNKNOWN, f"{version!r} is not a vX.Y.Z contract version")
    published = _published_order(releases)
    if version not in {r.version for r in published}:
        return Verdict(UNKNOWN, f"{version} is not a published contract release")
    if version in supported_versions(published):
        return Verdict(SUPPORTED, f"{version} is inside the supported window")

    declared_major = parse_version(version)[0]
    superseder = None
    seen = []
    for release in published:
        seen.append(release)
        if version not in {r.version for r in seen}:
            continue
        if version not in supported_versions(seen):
            superseder = release
            break
    if superseder is None:
        # Unreachable against a consistent release list, and stated rather than
        # assumed: without a superseder there is no clock, and a version with no
        # clock must not be reported as runnable.
        return Verdict(UNKNOWN,
                       f"{version} is outside the window but no release explains when it left")
    grace = MAJOR_GRACE_DAYS if parse_version(superseder.version)[0] > declared_major else GRACE_DAYS
    expires_on = _add_days(superseder.published, grace)
    if today < expires_on:
        return Verdict(DEPRECATED,
                       f"{version} left the supported window at {superseder.version} "
                       f"and is refused from {expires_on}", expires_on)
    return Verdict(EXPIRED,
                   f"{version} left the supported window at {superseder.version} "
                   f"and expired on {expires_on}", expires_on)


DECLARATION_PATH = ".github/verjson-contract.json"
# One adopter-controlled field. A declaration that also carried the commit would
# reintroduce the skew it exists to remove, with the version and the commit free
# to disagree; the commit is resolved from the hub's release metadata instead.
DECLARATION_KEY = "contract_version"

# What a contract reference *is* lives in one place, shared with the fleet
# inventory sweep, so the two cannot drift apart again (Verjson/.github#1482).
# `SHA_RE` travels with it: it is the inventory's pin filter and this script's
# release-commit test, and those must agree on what a commit looks like.
from contract_reference import SHA_RE, USES_RE  # noqa: E402
# The trailing boundary keeps a hex run longer than 40 characters from being
# truncated into a contract SHA: without it, `[0-9a-f]{40}` matches the first 40
# characters of a 64-hex container digest on a line that also names the hub, and
# the repository gets a PIN_MISMATCH naming a reference it does not have.
# Stated as what is measured rather than as an observed incident: no line in this
# repository matches both `Verjson/.github` and a 64-hex run today
# (`grep -rInE 'Verjson/\.github.*[0-9a-f]{64}'` finds nothing), so the covering
# test constructs that line. The boundary is kept because the two shapes it joins
# -- a generated hub header, and a digest -- are both comment-line content, and a
# header pass that reads every comment line has no window keeping them apart.
HEADER_RE = re.compile(
    r"Verjson/\.github[^\n]*?\b(?P<sha>[0-9a-f]{40})(?![0-9a-f])", re.IGNORECASE)
# The gap half of the recognizer. `USES_RE` reads a pin that is on its key's
# line; a `uses:` key whose value is somewhere else is not a reference this scan
# can resolve, and reporting it as no reference is the clean PASS on a real skew.
# Both patterns below are anchored to the start of the line, with a balanced
# quote if the key is quoted, and that anchoring is load-bearing rather than
# tidy: measured over the 1208 tracked YAML files and 7361 Markdown files of 94
# organization repositories plus this one, the unanchored form reports 30 lines
# -- `statuses:` contains `uses:`, prose ends a sentence with `uses:`, and a
# Python fixture string opens with `"uses:` -- and the anchored form reports
# none (#1433). The leading `^` and the `.match()` below each anchor on their
# own, so neither is individually mutation-killable; the suite kills them
# together rather than pretending one of them is redundant.
# Case-sensitive, unlike every other pattern here, and that asymmetry is the
# point: Actions requires the key to be lowercase, so `Uses:` is a workflow parse
# error rather than a reference. A case-insensitive key can therefore only invent
# gaps and can never catch a pin -- an English list item reading `- Uses:` in a
# file that names the hub becomes a permanent red check an adopter clears only by
# rewriting prose. `USES_RE` keeps its flag because the hub name it also matches
# really is case-folded by GitHub; this pattern contains no hub text at all.
USES_KEY_RE = re.compile(r"""^\s*(?:-\s+)?(?:uses|"uses"|'uses')\s*:""")
SOURCE_VARIABLE_REF_RE = re.compile(
    r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})\Z")
# What is left on the line once the key is consumed, when the value is not:
# nothing (a plain scalar on the following line), a block-scalar indicator, or
# an alias to an anchor defined elsewhere in the file.
OFFLINE_VALUE_RE = re.compile(r"""\s*(?:[>|][-+0-9]*|\*\S+)?\s*(?:#.*)?$""")
HUB_RE = re.compile(r"Verjson/\.github", re.IGNORECASE)
MAX_SCAN_BYTES = 1 << 20
# Git's own binary heuristic reads the first 8000 bytes; matching it means a
# file this scan calls binary is a file git calls binary.
SNIFF_BYTES = 8000
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
# `\xff\xfe` also opens the UTF-32LE BOM, so a UTF-32 or binary file that
# starts with it is not a UTF-16 file: without this it escaped the binary
# heuristic and then failed to decode, becoming a gap on a file that holds
# no text at all.
UTF32_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")


class TreeNotEnumerable(Exception):
    """The tree could not be enumerated, so nothing about it was compared."""


@dataclasses.dataclass(frozen=True)
class Finding:
    kind: str
    detail: str


def _git(root, *args):
    """Run one read-only git command in `root`, or refuse the whole tree."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        raise TreeNotEnumerable(f"{root}: git could not be run ({error})") from error
    if result.returncode != 0:
        raise TreeNotEnumerable(
            f"{root}: git {args[0]} failed "
            f"({result.stderr.decode('utf-8', 'replace').strip()})")
    return result


def _tracked_paths(root):
    """`(relative, is_sparse)` for every path in the index, sorted.

    The index -- not a walk of the directory -- is the boundary, because the
    index is what Actions checks out and executes. A walk reads whatever the
    working tree happens to hold: an untracked `node_modules`, a build
    directory, a tool cache, a downloaded runner binary. None of that is
    repository content, none of it reaches a workflow run, and every binary in
    it past the scan limit became an `UNSCANNED` finding no adopter could ever
    clear. A permanent exit 1 is the muted check ADR 0185 warns about, so the
    scan that reported it was not more total, only louder.

    Over the content that does ship, this is strictly wider than the walk it
    replaces: a tracked file inside an otherwise ignored directory is in the
    index and is scanned, and `.git` needs no special case because git's own
    object storage is never indexed.
    """
    # A bare repository has an index command that exits 0 with no output, so
    # without this the sweep scanned zero files, found zero references and
    # reported PASS -- the "scanned nothing, found nothing" fail-open that is
    # indistinguishable from a conformant tree. There is no work tree to
    # compare, so the honest answer is a refusal, not a verdict.
    if _git(root, "rev-parse", "--is-bare-repository").stdout.strip() == b"true":
        raise TreeNotEnumerable(f"{root}: is a bare repository, so it has no work tree to scan")
    # `--show-toplevel` fails in a git directory handed in as the root, where
    # `ls-files` still enumerates the index and every path it names resolves
    # against `.git/` and exists nowhere -- a tree of gaps that reads as
    # unreadable content rather than as the wrong root.
    _git(root, "rev-parse", "--show-toplevel")
    result = _git(root, "ls-files", "-s", "-v", "-z")
    paths = []
    # `-z` emits raw bytes with no quoting, so a non-UTF-8 path survives the
    # round trip instead of becoming an unopenable escaped name. `-s -v`
    # prefixes `<tag> <mode> <object> <stage>\t`, and both halves are read:
    #
    # `<mode>` tells a gitlink from a file. A submodule is an index entry whose
    # path is a *directory*, so opening it raises IsADirectoryError and the
    # path becomes an UNSCANNED finding no adopter can clear -- the permanent
    # exit 1 this function was written to remove. `actions/checkout` does not
    # fetch submodule content by default, so that content is not in the tree
    # this check compares.
    #
    # `<tag>` of `S` (or lowercase, which adds assume-unchanged) is
    # skip-worktree: a sparse checkout left the path out of the work tree. That
    # path is still repository content the enforcing checkout materializes in
    # full, so it stays a gap rather than becoming a skip -- but the gap says
    # so, instead of reporting a missing file as if the tree were broken.
    for entry in result.stdout.split(b"\x00"):
        if not entry:
            continue
        head, _, path = entry.partition(b"\t")
        fields = head.split(b" ")
        tag, mode = fields[0], fields[1]
        if mode == b"160000":
            continue
        paths.append((path.decode("utf-8", "surrogateescape"), tag.upper() == b"S"))
    return sorted(paths)


def _declares_utf16(raw: bytes) -> bool:
    """Whether these bytes open with a UTF-16 byte-order mark and not a UTF-32 one."""
    return raw.startswith(UTF16_BOMS) and not raw.startswith(UTF32_BOMS)


def _utf16_shaped(prefix: bytes) -> bool:
    """Whether these bytes carry the alternating-NUL structure of UTF-16 text.

    Trying `bytes.decode("utf-16")` instead would call almost every binary
    UTF-16: any even-length byte string decodes unless it happens to hold an
    unpaired surrogate, so the gap would fire on every image in the tree and
    get muted. The structure is the discriminator -- one half of the byte
    offsets all NUL while the other half is not -- and it is what a UTF-16
    `uses:` line actually looks like.
    """
    body = prefix[:len(prefix) - len(prefix) % 2]
    if len(body) < 4:
        return False
    low, high = body[1::2], body[0::2]
    return (not any(low) and any(high)) or (not any(high) and any(low))


def _scan_files(root):
    """`(relative, text, unscanned_reason)` for every tracked file in the tree.

    Scoping the scan to a list of known adopter files would reproduce the defect
    it is meant to catch: a contract reference somewhere the list did not name is
    exactly the intra-repository skew ADR 0185 measured. The same argument
    applies to a tracked file the scan reaches and cannot read: skipping it
    silently turns "this file might carry a skewed reference" into a clean PASS,
    so exactly one of `text` and `unscanned_reason` is ever None.
    """
    root = pathlib.Path(root)
    for relative, is_sparse in _tracked_paths(root):
        path = root / relative
        if is_sparse and not path.exists():
            yield relative, None, ("is marked skip-worktree and absent, so this sparse "
                                   "checkout never materialized it")
            continue
        if path.is_symlink():
            # A symlink is tracked as its target *path*, which is never a
            # `uses:` line, and following it would read either a file that is
            # itself tracked and therefore already scanned on its own entry, or
            # something outside the tree being verified.
            continue
        try:
            size = path.stat().st_size
        except OSError as error:
            yield relative, None, f"could not be sized ({error.strerror or error})"
            continue
        try:
            with open(path, "rb") as handle:
                prefix = handle.read(SNIFF_BYTES)
        except OSError as error:
            yield relative, None, f"could not be read ({error.strerror or error})"
            continue
        if not _declares_utf16(prefix) and b"\x00" in prefix:
            if _utf16_shaped(prefix):
                # UTF-16 without a BOM is as full of NUL bytes as UTF-16 with
                # one, so the binary heuristic dropped it with no finding and
                # no gap -- a clean PASS on a file whose `uses:` line is
                # sitting there in its own encoding. An undeclared encoding is
                # a guess rather than the BOM's claim, so this is reported as
                # a gap to resolve by hand instead of decoded into a verdict.
                yield relative, None, ("holds UTF-16-shaped text but declares no "
                                       "byte-order mark")
                continue
            # The binary heuristic speaks before the size limit, not after it.
            # Running the limit first meant every binary over 1 MiB -- a large
            # image, an archive, a compiled artifact -- became an UNSCANNED
            # finding, and a file does not stop being binary at 1048577 bytes.
            # Sniffing a prefix keeps that cheap: the oversize file is never
            # read whole either way.
            continue
        if size > MAX_SCAN_BYTES:
            yield relative, None, f"is {size} bytes, past the {MAX_SCAN_BYTES}-byte scan limit"
            continue
        try:
            raw = prefix if size <= len(prefix) else path.read_bytes()
        except OSError as error:
            yield relative, None, f"could not be read ({error.strerror or error})"
            continue
        if _declares_utf16(raw):
            # A UTF-16 BOM is checked before the NUL heuristic below, because
            # that heuristic's premise is true while the conclusion previously
            # drawn from it was not: a file with a NUL byte holds no *UTF-8*
            # `uses:` line, but every second byte of a UTF-16 file is NUL and
            # the `uses:` line is sitting right there in its own encoding. It
            # was reported as neither a finding nor a gap -- a clean PASS on a
            # real skew. Decoding it rather than calling it UNSCANNED is the
            # stronger of the two repairs the reviewer offered: a BOM is an
            # explicit encoding declaration rather than a guess, so the skew
            # becomes the PIN_MISMATCH it actually is instead of a gap someone
            # has to open the file by hand to resolve.
            try:
                yield relative, raw.decode("utf-16"), None
            except UnicodeDecodeError:
                yield relative, None, "declares a UTF-16 BOM but does not decode as UTF-16"
            continue
        try:
            yield relative, raw.decode("utf-8"), None
        except UnicodeDecodeError:
            if b"\x00" in raw:
                # Git's own binary heuristic. A file with a NUL byte holds no
                # UTF-8 `uses:` line, and reporting every image in the tree
                # is the noisy check ADR 0185 says gets muted.
                continue
            yield relative, None, "is text in an encoding this scan cannot decode"


def references(root):
    """`(found, unscanned, unresolved)`: every reference, and every kind of gap.

    `unscanned` is a file this scan could not read at all; `unresolved` is a
    `uses:` key inside a file it read whose reference it could not resolve. The
    second exists because ADR 0191 sec.3's totality claim is about the *file* set,
    and a shape the recognizer cannot see is the same clean PASS on a real skew
    as a file it never opened (#1433).

    Generated headers are read as *claims* alongside the pins, never as
    instructions: adopter-controlled text is an input to this comparison, not to
    anything privileged.

    The stated ceiling: this scan stays line-based, so it names an unresolvable
    reference rather than joining continuation lines or resolving anchors to
    read one. A reference whose hub path is assembled at run time from an
    expression, and whose owner and repository therefore never appear literally
    anywhere in the tree, is outside even that -- there is no text for any scan
    of the tree to find, and no honest report beyond saying so here.

    Every comment line is a candidate header, not a fixed leading window.
    `gen-changelog-caller.sh` stamps `CONTRACT_REF` on line 13 of the ADR index
    test and emits the release caller's header below `concurrency:`, so any
    window drawn at the top of the file misses real claims. The `uses:` spans on
    a line are excluded from the header pass instead, which is what the window
    was actually buying: a commented-out pin stays one reference, not two.
    """
    found, unscanned, unresolved = [], [], []
    for relative, text, problem in _scan_files(root):
        if text is None:
            unscanned.append((relative, problem))
            continue
        is_yaml = relative.endswith((".yml", ".yaml"))
        # An anchor and a continuation line are both file-scoped in YAML, so a
        # file that never names the hub cannot carry a hub reference this scan
        # failed to read. That is what keeps the gap half quiet on the ~1500
        # third-party `uses:` keys the fleet actually has.
        names_hub = HUB_RE.search(text) is not None
        for number, line in enumerate(text.splitlines(), 1):
            matches = list(USES_RE.finditer(line))
            # Source files can quote or generate a caller using shell variables.
            # YAML references are live Actions inputs and must still be pinned.
            pins = [match for match in matches
                    if is_yaml or not SOURCE_VARIABLE_REF_RE.fullmatch(match.group("ref"))]
            key = None if matches or not names_hub else USES_KEY_RE.match(line)
            if key is not None and HUB_RE.search(line):
                unresolved.append(
                    (relative, number,
                     "is a `uses:` key naming Verjson/.github with no path@ref "
                     "this scan can read"))
            elif key is not None and OFFLINE_VALUE_RE.fullmatch(line[key.end():]):
                unresolved.append(
                    (relative, number,
                     "carries a `uses:` key whose value is not on its line, so "
                     "this line-based scan cannot tell what it references"))
            for match in pins:
                found.append((relative, "uses", match.group("ref")))
            if not line.lstrip().startswith("#"):
                continue
            spans = [match.span() for match in pins]
            for match in HEADER_RE.finditer(line):
                start = match.start("sha")
                if any(low <= start < high for low, high in spans):
                    continue
                found.append((relative, "header", match.group("sha")))
    return found, unscanned, unresolved


def read_declaration(root):
    """(version, finding). Exactly one of the two is None."""
    path = pathlib.Path(root) / DECLARATION_PATH
    if not path.is_file():
        return None, Finding("DECLARATION_MISSING",
                             f"{DECLARATION_PATH} is absent, so no contract version is declared")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, Finding("DECLARATION_UNREADABLE", f"{DECLARATION_PATH}: {error}")
    if not isinstance(document, dict) or not isinstance(document.get(DECLARATION_KEY), str):
        return None, Finding("DECLARATION_UNREADABLE",
                             f"{DECLARATION_PATH} has no string {DECLARATION_KEY!r}")
    return document[DECLARATION_KEY], None


def verify(root, releases, today: str):
    """Assert the declared version against the references the repository really has.

    The comparison is a version lookup followed by an equality on one resolved
    object id, not a blob diff of each file against the hub's default branch. The
    diff was rejected in ADR 0185 for failing open, for being the wrong
    granularity, and for reporting comment-only upstream edits as drift; none of
    those apply once there is a version to compare.
    """
    version, finding = read_declaration(root)
    found, unscanned, unresolved = references(root)
    gaps = [Finding("UNSCANNED", f"{relative} {problem}, so it cannot be shown to carry "
                                 "no contract reference")
            for relative, problem in unscanned]
    gaps += [Finding("UNRESOLVED_REFERENCE", f"{relative}:{number} {problem}")
             for relative, number, problem in unresolved]
    if finding is not None and finding.kind == "DECLARATION_MISSING" and not found:
        # No declaration and no reference is a repository that does not consume
        # the contract, not a defect. Reporting it would fire on every repository
        # in the organization, and a check that fires everywhere gets muted --
        # which is how the previous invalid drift test survived. A declaration
        # that exists and cannot be read is the other thing entirely: it is a
        # defect whether or not the tree happens to carry a reference, so it is
        # deliberately outside this guard. So is a tree with gaps in it: "no
        # reference" is a statement about a scan that finished, and a scan that
        # could not read part of the tree has not established it, so the gaps
        # are returned on their own. DECLARATION_MISSING is deliberately not
        # among them: whether this repository owes a declaration is exactly
        # what the unread files might have answered, and asserting it here
        # would fire on every repository with one oversized asset.
        return gaps
    if finding is not None:
        return [finding] + gaps
    verdict = classify(version, releases, today)
    if verdict.state in (UNKNOWN, EXPIRED):
        # EXPIRED short-circuits with UNKNOWN because expiry IS the enforcement
        # (ADR 0191 sec.5/sec.6). Falling through would let an expired contract whose
        # pins happen to agree return no findings -- a clean PASS of the refusal
        # the required-check binding exists to deliver.
        return [Finding(verdict.state, verdict.reason)] + gaps

    commit = next((r.commit for r in releases if r.version == version), "")
    if not SHA_RE.match(commit or ""):
        # `target_commitish` is a branch name for many releases. Treating one as a
        # commit would compare a pin against the string "main" and report every
        # adopter broken, so an unresolved release fails closed instead.
        return [Finding(UNKNOWN,
                        f"{version} resolved to {commit!r}, not a 40-hex commit")] + gaps

    findings = list(gaps)
    if verdict.state == DEPRECATED:
        findings.append(Finding(DEPRECATED, verdict.reason))
    if not found:
        findings.append(Finding("UNGOVERNED_DECLARATION",
                                f"{DECLARATION_PATH} declares {version} but the repository "
                                "carries no Verjson/.github reference for it to govern"))
    for relative, kind, ref in found:
        if ref == commit:
            continue
        if not SHA_RE.match(ref):
            findings.append(Finding("UNPINNED_REFERENCE",
                                    f"{relative}: {kind} names {ref!r}, which is not an "
                                    "immutable commit"))
            continue
        findings.append(Finding("PIN_MISMATCH",
                                f"{relative}: {kind} names {ref}, but the declared {version} "
                                f"is {commit}"))
    return findings


def load_releases(path: str):
    """Published contract releases, or a usage failure. Never a partial list.

    Resolve the commit by **peeling the tag**, not from `target_commitish`, and
    not from `git/ref/tags/<tag>` unpeeled either:

        gh api repos/Verjson/.github/releases --paginate \\
          --jq '.[] | [.tag_name, (.published_at | split("T")[0])] | @tsv' \\
        | while IFS=$'\\t' read -r tag published; do
            # `^{}` is load-bearing: an ANNOTATED tag's ref names the tag object,
            # whose id is 40-hex and would pass every check here while being the
            # wrong commit. Every current tag on this repository is lightweight,
            # so the unpeeled form works today by luck alone.
            commit=$(git rev-parse "refs/tags/$tag^{}")
            printf '{"version":"%s","commit":"%s","published":"%s"}\\n' \\
              "$tag" "$commit" "$published"
          done | jq -s .

    Every field is validated here rather than at the first verdict that happens
    to touch it: `published_at` is `2026-03-01T00:00:00Z` raw and the deprecation
    clock parses it as a date, and a producer built on `target_commitish` yields
    the literal string `main` for this repository's v2.0.0 and older. Both are
    producer defects, and exit 2 -- "the question could not be asked" -- is the
    honest answer to them, not a verdict computed from a broken list.
    """
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("the releases document must be a JSON array")
    releases, versions = [], set()
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError("every release entry must be an object")
        release = Release(version=str(entry.get("version", "")),
                          commit=str(entry.get("commit", "")),
                          published=str(entry.get("published", "")))
        if not SHA_RE.match(release.commit):
            raise ValueError(f"{release.version or 'a release'} has commit "
                             f"{release.commit!r}, which is not a 40-hex object id")
        try:
            datetime.date.fromisoformat(release.published)
        except ValueError as error:
            raise ValueError(f"{release.version or 'a release'} has published "
                             f"{release.published!r}, which is not a YYYY-MM-DD date") from error
        if release.version in versions:
            # First-wins would pick one commit silently, and which one it picked
            # would decide every PIN_MISMATCH in the sweep.
            raise ValueError(f"{release.version} appears more than once, so its "
                             "commit is ambiguous")
        versions.add(release.version)
        releases.append(release)
    return releases


def today_utc() -> str:
    """Today in UTC. `date.today()` is the runner's local date, and a check whose
    expiry verdict depends on the timezone of whatever host ran it is not one
    verdict."""
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _iso_date(value: str) -> str:
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from None
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("classify", "verify"):
        child = sub.add_parser(name)
        child.add_argument("--releases", required=True)
        child.add_argument("--today", type=_iso_date, default=today_utc())
        if name == "classify":
            child.add_argument("--version", required=True)
        else:
            child.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)

    try:
        releases = load_releases(args.releases)
    except (OSError, ValueError) as error:
        # Exit 2 is "the question could not be asked". Reporting a tree that was
        # never compared as conformant is the fail-open shape ADR 0185 refused.
        print(f"contract-version: could not read {args.releases}: {error}", file=sys.stderr)
        return 2

    if args.command == "classify":
        verdict = classify(args.version, releases, args.today)
        print(f"{verdict.state}\t{verdict.reason}")
        return 0 if verdict.state == SUPPORTED else 1

    try:
        findings = verify(args.repo_root, releases, args.today)
    except TreeNotEnumerable as error:
        # Same exit 2, same reason as an unreadable releases file: a tree that
        # was never enumerated was never compared, and a silent fall back to a
        # scan that finds nothing would report it as conformant.
        print(f"contract-version: could not enumerate {args.repo_root}: {error}",
              file=sys.stderr)
        return 2
    for finding in findings:
        print(f"{finding.kind}\t{finding.detail}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
