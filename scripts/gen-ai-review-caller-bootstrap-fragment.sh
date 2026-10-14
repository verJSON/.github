#!/usr/bin/env bash
# Generate the one changelog fragment a stale review caller bootstrap may add.
#
# Usage:
#   scripts/gen-ai-review-caller-bootstrap-fragment.sh <YYYY-MM-DD> <identity> \
#     <stale-contract-sha> <new-contract-sha> \
#     > "NEXT/<YYYY-MM-DD>-issue-<identity>-ai-review-caller-bootstrap.md"
#
# <identity> is an issue number, a UTC timestamp (YYYYMMDDTHHMMSSZ), or a 6-12
# character lowercase hexadecimal ID. The organization authorization arm renders
# these exact bytes from the fragment's filename and admits no other text in a
# bootstrap pull request (ADR 0227), so never edit the output.
set -euo pipefail

usage() {
  echo "usage: gen-ai-review-caller-bootstrap-fragment.sh <YYYY-MM-DD> <issue-number|YYYYMMDDTHHMMSSZ|hex-id> <stale-sha> <new-sha>" >&2
  exit 2
}

[ "$#" -eq 4 ] || usage
date="$1"
identity="$2"
stale="$3"
contract="$4"
[[ "$date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && [ "$(date -u -d "$date" +%F 2>/dev/null)" = "$date" ] || usage
[[ "$identity" =~ ^([1-9][0-9]{0,8}|[0-9]{8}T[0-9]{6}Z|[0-9a-f]{6,12})$ ]] || usage
[[ "$stale" =~ ^[0-9a-f]{40}$ ]] && [[ "$contract" =~ ^[0-9a-f]{40}$ ]] && [ "$stale" != "$contract" ] || usage
key=id
[[ "$identity" =~ ^[1-9][0-9]*$ ]] && key=issue

printf -- '---\ndate: %s\n%s: %s\nimpact: patch\ntitle: Bootstrap the AI review caller to a current canonical revision\n---\n\n' \
  "$date" "$key" "$identity"
# The backticks are Markdown code spans in the fragment, not command substitution.
# shellcheck disable=SC2016
printf 'Regenerate `.github/workflows/ai-review-merge.yml` at canonical Verjson/.github revision `%s`, replacing `%s`, whose receipt verifier cannot authorize its own update. The organization authorization arm verified this byte-exact generated change under Verjson/.github ADR 0227.\n' \
  "$contract" "$stale"
