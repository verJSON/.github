#!/usr/bin/env python3
"""Compare GitHub organization and repository identities (#1779).

GitHub owner and repository names are case-insensitive identities, and the API
reports their current display case, which an organization rename changes: the
`Verjson` login became `verJSON` on 2026-10-02. An identity therefore compares by
case-folding the whole `owner` or `owner/name` string; a different owner or
repository never matches.

Case folding is sound only over ASCII: U+212A KELVIN SIGN folds to `k` and
U+017F LATIN SMALL LETTER LONG S folds to `s`. Both the observed and the
configured value must use GitHub's name charset, so a non-ASCII spelling is an
invalid identity, never a match.
"""

from __future__ import annotations

import re


_NAME = r"[A-Za-z0-9._-]+"
ORGANIZATION = re.compile(_NAME)
REPOSITORY = re.compile(rf"{_NAME}/{_NAME}")


def _identity(value: object, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        return None
    return value.casefold()


def organization_identity(value: object) -> str | None:
    """The comparable identity of an organization login, or None when invalid."""
    return _identity(value, ORGANIZATION)


def repository_identity(value: object) -> str | None:
    """The comparable identity of an `owner/name` repository, or None when invalid."""
    return _identity(value, REPOSITORY)


def same_organization(observed: object, configured: object) -> bool:
    identity = organization_identity(configured)
    return identity is not None and organization_identity(observed) == identity


def same_repository(observed: object, configured: object) -> bool:
    identity = repository_identity(configured)
    return identity is not None and repository_identity(observed) == identity


def same_repositories(observed: list[object], configured: list[object]) -> bool:
    """Whether two repository lists name the same identities, each exactly as often."""
    observed_identities = [repository_identity(value) for value in observed]
    configured_identities = [repository_identity(value) for value in configured]
    return (
        None not in observed_identities
        and None not in configured_identities
        and sorted(observed_identities) == sorted(configured_identities)
    )


def strip_repository_url_prefix(value: object, prefix: str, repository: str) -> str | None:
    """Return what follows `prefix` in `value`, or None when `value` lacks it.

    `prefix` embeds `repository` as one `/<owner>/<name>/` path segment pair. Only
    that pair compares as a repository identity; every other character of the
    prefix, and the remainder, stays exact.
    """
    marker = f"/{repository}/"
    if repository_identity(repository) is None or not isinstance(value, str):
        return None
    # `str.count` skips overlapping matches: `/a/b/a/b/` holds `/a/b/` twice.
    start = prefix.find(marker)
    if start < 0 or prefix.find(marker, start + 1) >= 0:
        return None
    before, after = prefix[:start], prefix[start + len(marker):]
    if not value.startswith(f"{before}/"):
        return None
    owner, _, rest = value[len(before) + 1:].partition("/")
    name, separator, rest = rest.partition("/")
    if not separator or not same_repository(f"{owner}/{name}", repository) or not rest.startswith(after):
        return None
    return rest[len(after):]
