#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 5 ]; then
  echo 'usage: release-push-guard.sh <branch> <dispatch-ref> <dispatch-sha> <release-sha> <tag>' >&2
  exit 2
fi
branch=$1
dispatch_ref=$2
expected=$3
release=$4
tag=$5

if [[ ! "$expected" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || [[ ! "$release" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
  echo 'dispatch and release commits must be full lowercase object IDs' >&2
  exit 1
fi
git check-ref-format "refs/heads/$branch" >/dev/null
git check-ref-format "refs/tags/$tag" >/dev/null
if [ "$dispatch_ref" != "refs/heads/$branch" ]; then
  echo 'dispatch ref must name the default branch' >&2
  exit 1
fi
export GIT_NO_REPLACE_OBJECTS=1
if [ "$(git cat-file -t "$expected")" != commit ] || [ "$(git cat-file -t "$release")" != commit ]; then
  echo 'dispatch and release IDs must identify commits' >&2
  exit 1
fi
mapfile -t parents < <(git cat-file -p "$release" | awk '/^$/ {exit} /^parent / {print $2}')
if [ "${#parents[@]}" -ne 1 ] || [ "${parents[0]}" != "$expected" ]; then
  echo 'release commit must have the dispatch commit as its sole parent' >&2
  exit 1
fi
tag_oid=$(git rev-parse --verify "refs/tags/$tag")
if [ "$(git cat-file -t "$tag_oid")" != tag ]; then
  echo 'release tag must be annotated' >&2
  exit 1
fi
mapfile -t tag_header < <(git cat-file -p "$tag_oid" | awk 'NR <= 3 {print}')
if [ "${#tag_header[@]}" -ne 3 ] || [ "${tag_header[0]}" != "object $release" ] || [ "${tag_header[1]}" != 'type commit' ] || [ "${tag_header[2]}" != "tag $tag" ]; then
  echo 'release tag object must name the tag and directly target the release commit' >&2
  exit 1
fi

# The sole-parent assertion proves this specific update is a fast-forward. The
# ref-specific lease supplies compare-and-swap against the dispatch SHA; it does
# not authorize an update to any other branch or force an existing tag.
git -c core.hooksPath=/dev/null push --atomic --no-follow-tags \
  --force-with-lease="refs/heads/$branch:$expected" origin \
  "$release:refs/heads/$branch" "$tag_oid:refs/tags/$tag"
