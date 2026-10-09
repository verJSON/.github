#!/usr/bin/env bash
# Adopter ref names (the default branch, a PR head branch) are adopter-controlled text.
# Every `gh api` URL that interpolates one must percent-encode it first, so that relaxing
# the `^[A-Za-z0-9._/-]+$` guards cannot silently re-arm an injection path.
#
# Two encodings exist, and they are not interchangeable:
#   query value  `…?ref=$branch_ref`   — `@uri` with `gsub("%2F"; "/")`: "/" is an ordinary
#                                        character in a query value and the API wants it
#                                        literal.
#   path segment `…/git/ref/heads/$…`  — `@uri` WITHOUT the gsub: a literal "/" there
#                                        re-addresses the resource, so a branch named
#                                        `../../x` would escape the endpoint entirely.
#
# Python callers build the same two URLs, so the same two encodings apply by position.
# `urllib.parse.quote` defaults to `safe="/"`, which is the QUERY form: `quote(v)` and
# `quote(v, safe="/")` keep "/" literal, and only `quote(v, safe="")` encodes it as %2F for
# a path segment. Those names orient a reader only — the check below evaluates each encoder
# call against the same fixtures the jq programs face, so a mis-stated mapping cannot make a
# wrong call pass.
#
# This test asserts three independent things:
#   1. behavior  — the two extractable gate-rearm blocks really encode what they read;
#   2. semantics — every encoder expression in the repository maps the fixtures correctly;
#   3. coverage  — an allowlist over the ref interpolations it can RECOGNIZE in workflows
#      and non-test shell and Python scripts, so a new unencoded site fails here instead of
#      merely being spelled differently from a denylist pattern.
#
# Read that third one narrowly, and read the ceiling below before trusting a green run.
#
# #1464 widened the anchors from two URL shapes to the ref-bearing path segments listed in
# REF_PATH_SEGMENT (`git/ref[s]/heads/`, `commits/`, `git/commits/`, `git/trees/`,
# `git/tags/`, `branches/` including `rules/branches/`, and BOTH operands of a
# `compare/A...B` range), and taught the shell anchor that `${VAR}` is the same
# interpolation as `$VAR`. That took the scan from 35 recognized sites to 77. Section 0
# measures each of those shapes against synthetic files, so a regression reports which
# shape went blind rather than simply finding fewer sites.
#
# THE CEILING — these reach a ref-bearing URL and this scan still does NOT see them:
#   * Non-brace construction in Python. `read("...?ref=" + branch)`, `"...?ref=%s" % branch`,
#     and a two-statement `url = "...?ref="` / `url += branch` build the same URL and come
#     back unseen. This is a DELIBERATE ceiling, not an oversight: every ref URL in this
#     repository is built with an f-string or `str.format`, and recognizing the others needs
#     an AST/dataflow pass rather than a line anchor -- a line anchor for `+` would be
#     evadable in exactly the way the two-shape anchor was. Keep building these URLs with
#     brace interpolation; a site that stops is a site this test stops covering.
#   * Python embedded in workflow YAML. `{EXPR}` interpolation is only extracted from `.py`
#     files, so the inline Python in `container-deployment.yml` (5 ref-bearing lines as of
#     #1464) is outside the scan.
#   * Shell positional parameters. `commits/$1` is skipped because the subject must be a
#     name; the proof for a positional is at the call site, not at the use.
#   * An interpolation split across source lines, and any ref-bearing path segment not in
#     REF_PATH_SEGMENT.
#   * Composite actions. `scanned` covers `.github/workflows/*.yml`, `scripts/*.sh` and
#     `scripts/*.py`; `.github/actions/*/action.yml` is NOT in it, so the adopter-facing
#     `commits/${HEAD_SHA}/status` read in `.github/actions/ci-eligibility/action.yml` is
#     never judged here. What covers it is `scripts/ci-gate/ci-eligibility.test.sh:55-63`,
#     which asserts byte parity between that composite script and node-ci's inline copy:
#     the guard this scan proves on the inline copy is the same bytes. That is an indirect
#     proof and it holds only while the parity assertion does.
#   * The sites in REF_SITE_ALLOWLIST -- exempted by hand, each with its reason. An
#     entry that cites a guard pins that guard's literal text, and `guard_is_live` then
#     asks whether that guard still FAILS. What it asks depends on the language:
#       - shell (a workflow `run:` block or a `.sh` script): continuations are joined
#         first -- a trailing `\`, a trailing `&&`/`||`/`|`, and a multi-line `|| { … }`
#         branch are one command -- and the tail that follows the pinned text must match
#         an ALLOW-LIST of shapes that leave the guard. Anything else -- `|| true`,
#         `|| :`, `||:`, `|| { :; }`, `|| echo skipped`, `| cat`, a trailing `&`,
#         `|| exit 0`, a bare `&&` chain with no `||` -- reads as disarmed. The allow-list
#         direction is the point: the denylist this replaced named four literals and six
#         other swallows walked straight past it (#1464 re-review).
#
#         The shapes are, exactly: an empty tail, which under `set -euo pipefail` means the
#         guard's own status is the command's; or, immediately after the final `&&`/`||`,
#         `exit N`/`return N` for N in 1-255, `fail`/`fault`/`die`/`abort …`, or a `{ … }`
#         whose body is FLAT and one of whose top-level statements is exactly one of those
#         actions. `continue` and `break` are NOT on it, in or out of a loop (see below).
#         "Exactly one of those statements" is the part that matters, and it is the second
#         thing this pin got wrong: matching the action as a SUBSTRING of the brace body is a denylist wearing
#         an allow-list's clothes, and `|| { echo "would exit 1 here"; }`,
#         `|| { ( exit 1 ); }`, `|| { false && exit 1; }`, `|| { cat <<EOF` / `exit 1`,
#         `|| exit 256`, `|| return 256`, `|| continue` and `|| break` all matched it and
#         all leave the guard standing (third #1464 re-review).
#         Quoted spans, `#` comments, `${…}` and `$(…)` are blanked by `shell_structure`
#         before the split. A `(…)` SUBSHELL is NOT -- an earlier revision of this sentence
#         said it was, and `shell_structure` has no such rule. What rejects
#         `|| { ( exit 1 ); }` is that `( exit 1 )` is not EXACTLY `exit N` as a top-level
#         statement, which is the same reason `false && exit 1` is rejected; blanking has
#         nothing to do with it (#1464 re-review round 6).
#
#         What that costs, all of it fail-CLOSED -- a live guard can read as disarmed here,
#         and none of these lets a swallow through:
#           · `exit 300` really does leave the guard (44), but the status is bounded to
#             1-255 because that bound is easier to state than "not a multiple of 256".
#           · a nested BRACE group, or a here-document, in the brace body is not flattened
#             and reads as disarmed. A REDIRECTION operator does not: `>&2`, `2>&1` and
#             `>/dev/null` are flattened and the body still reads FATAL, and it has to --
#             writing the diagnostic to stderr inside the failure branch is how this
#             repository spells a fatal `|| { … }`, in workflows and in scripts alike, and
#             the pinned live fixture below is that shape, so the stricter rule would report
#             those guards disarmed. NO COUNT of them is stated, here or in ADR 0194. The
#             figure that stood here does not survive its own predicate: "accepted fatal
#             `|| { … }` bodies carrying a `>&2`" does not pin whether the unit is the
#             accepted body, the record, or the occurrence, nor whether the `>&2` must sit
#             inside the accepted body or merely somewhere on the record, and successive
#             derivations under different readings disagreed. ADR 0194's rule -- a figure
#             returns only with a predicate that pins its unit and its scope -- is what
#             deleted the denominator there and is applied to this numerator too (#1464
#             re-review round 7). Three revisions of this line claimed the stricter rule; the code never
#             implemented it (#1464 re-review round 6, ADR 0194). A `(…)` subshell is
#             likewise flattened, so `{ ( echo x ); exit 1; }` reads FATAL -- correctly, the
#             `exit 1` is a top-level statement of the body.
#           · an action reached only through a `&&`/`||` chain inside the body is not
#             unconditionally reached. This anchor does not evaluate conditions, so it
#             cannot tell `{ false && exit 1; }` from `{ [ -n "$x" ] && exit 1; }`; both
#             read as disarmed.
#           · `shell_structure` is a lexical scan, not a shell parser. It models quotes,
#             backticks, backslashes, `${…}`, `$(…)` and word-position `#`, and nothing
#             else -- not here-documents, not `case` patterns, not quoting nested inside
#             `$(…)`. WITHIN one line, where it is unsure it blanks, which reads as NOT
#             fatal. ACROSS lines that direction reverses, and saying otherwise was a
#             fail-OPEN (#1464 re-review round 5): the scan starts every physical line
#             unquoted, so a here-document body, and a string continued onto the next line,
#             read as ORDINARY COMMANDS rather than blanking. Any caller that counts
#             structure across records must therefore detect those regions and decline.
#             Round 5 detected a here-document and an unclosed QUOTE and missed an unclosed
#             EXPANSION, which is the same blind spot: `${…}` and `$(…)` are scanned per
#             record, so one that runs off the end of its record continues onto the next as
#             ORDINARY COMMANDS. `shell_data_unclosed` now reports that too (#1464
#             re-review round 6). That decline is fail-CLOSED: a guard that genuinely
#             protects a use from inside an `else` arm containing a here-document, an
#             unclosed quote or a run-off expansion, or a record merely quoting the text
#             `<<WORD`, reads as disarmed. No site in this repository is written that way
#             today.
#           · `negated_branch_dominates` no longer decides record by record whether a
#             construct is one it must count. Its DEFAULT IS INVERTED: `arm_record_is_modelled`
#             enumerates what the record model represents and REJECTS everything else, so
#             unmodelled structure is a loud false alarm rather than an ACCEPT. That is the
#             round-6 change and the reason this list stops growing one construct per round;
#             see the block comment above that function for why enumerating the constructs
#             to count is the losing side of the same bet as enumerating bypass forms
#             (#1489, ADR 0194). Its cost is fail-CLOSED and real: any `(`, any unbalanced
#             or nested `{`, any `&` that is not `&&` or part of `>&`/`<&`/`&>`, and any
#             expansion, quote or here-document crossing a record, anywhere between the
#             guard and the use, now reads as disarmed. The one bare `)` it proves is a
#             `case` arm's pattern label, added because
#             `scripts/privileged-merge-conformance.sh:327` is a live guard whose `else` arm
#             contains a `case`. Rounds 7 and 8 proved it while any `case` was open, which
#             admitted an ordinary nested `case "$b" in y)` as a label and dropped its
#             opening event -- a fail-OPEN. It is now proved only in ARM POSITION, the two
#             places bash itself parses a word list ending in `)` as a pattern, and only for
#             a record that emits no `branch_events` at all (#1464 re-review round 9).
#             Measured on this repository, that cost moved none of the pinned counts: 77
#             sites, 144 files, 14 allowlisted sites, 11 allowlist entries, all unchanged.
#           · THE EVENT STREAM HAS THE SAME INVERTED DEFAULT (#1464 re-review round 11,
#             ADR 0196). `branch_events` used to scan silently past any command-position
#             construct it had no rule for, so "unmodelled" and "absent" produced the same
#             empty stream and absent reads as safe -- the generator of every round from 5
#             to 10. It now classifies TOTALLY over bash's reserved words, which bash itself
#             publishes as `compgen -k`, and emits `decline:<word>` for a reserved word it
#             does not positively classify; `negated_branch_dominates` reads a decline
#             BEFORE any event the same record emitted and REJECTS. `coproc` is declined
#             rather than modelled, because its optional NAME is not a command and
#             `coproc c if q; then` lost the `if` entirely. Doubled `((`/`))` is declined
#             too, because stripping the punctuation to "judge the word" manufactured
#             SPURIOUS closers out of arithmetic (`(( fi ))` emitted `fi`). Measured cost:
#             132 scanned records now decline and all 132 were ALREADY refused by
#             `arm_record_is_modelled`, so no site needed a new allowlist entry and none of
#             the counts above moved. `stream_only_declines` asserts that zero, so a later
#             widening that does cost reach reddens instead of passing quietly. This does
#             NOT make the walk correct against bash's parser; it converts an unknown
#             construct from a silent fail-open into a visible decline.
#           · `continue`/`break` are rejected outright rather than modelled. They leave a
#             guard only inside a loop, the lexical `do`/`done` count that established that
#             was ~60 lines whose last real site #1466 rewrote into an `elif`, and in
#             `slice` mode a lone `do` -- in prose, or in a here-document body -- licensed
#             them for the whole slice after it. Rejecting them is fail-closed; the count
#             was not (#1464 re-review round 4).
#       - Python: the comment check and NOTHING MORE. Every cited Python guard is a
#         sub-expression of an `if … is None:` or `require(…)`, and there is no single
#         tail shape that means "this raises" without parsing the file. `SHA.fullmatch(x)`
#         is judged by still being written, not by still failing. Five of the eleven
#         allowlist entries are in this weaker class, and so is the shell-shaped proof
#         that is really a pinned assertion STRING inside a `.py` list. The one
#         Python-shaped denylist literal that used to be here, `or True`, was dropped
#         rather than kept: see `py_guard_is_live` for why a one-entry denylist is worse
#         than an honest gap.
#         One measurement in ADR 0194 rests on `.py` files contributing no accepted fatal
#         `|| { … }` branch. Read that narrowly: three records in
#         `scripts/gen-node-ci-protected.py` DO reach `brace_body_is_fatal` and all three
#         are rejected on the CONTENT of those emitted-shell string literals, not by any
#         rule about `.py`. An edit to that generator can move it, so re-derive the figure
#         rather than assuming the language settles it.
#     Even at its strongest this is a COMMAND-level anchor, not reachability analysis, and
#     these are the ways a pin is satisfied by something that no longer guards:
#       · a guard MOVED into a branch that never runs;
#       · a guard made vacuous by editing the value it tests rather than the test itself;
#       · a terminating word redefined as a no-op. The helper names are allow-listed BY
#         NAME with no proof that they terminate, and bash lets `exit` and `return` be
#         shadowed by a function too, so this covers the whole allow-list, not just
#         `fault`.
#       · for a guard spent as the NEGATED condition of an `if`/`elif`, the pinned USE is
#         taken to be the last record of the input. In `slice` mode that is exact, because
#         `block_slice` cuts the file at the use's own line; in `whole` mode -- an allowlist
#         entry's pinned guard, read from a whole file -- there is no use to cut at, so the
#         END OF THE FILE stands in for it, and a file that happens to end inside the `else`
#         arm reads as protecting a use that may be anywhere, including in another file. No
#         allowlist entry is written that way today.
#       · that `whole`-mode residual is what is KNOWN to be open in the negated-branch class,
#         not all that is. Round 4 called it "the only fail-OPEN in that class"; round 5 then
#         found two more -- a fatal statement nested in a loop or `case` body, which
#         `branch_events` could not see, and a here-document or multi-line string in the
#         `else` arm, which inflated the depth count. Round 6 then found four more, all of
#         them inside the mechanism round 5 had just added: an `exit 1` reached only through
#         a multi-line `(…)` subshell, one that only a function DEFINITION contains, one in a
#         BACKGROUNDED `{ … } &` group, and a `${…}`/`$(…)` expansion running off the end of
#         a record in the `else` arm. All four are REJECTED, and all four are pinned as
#         fixtures with controls -- as are the four ISOLATING cases that keep each of
#         `arm_record_is_modelled`'s rules individually load-bearing.
#         Round 7 then found the exception round 6 had just added reading a label's first
#         word as a command; round 8's inertness fix was itself read into by round 9's
#         `case "$b" in y)`.
#         TEN consecutive rounds shipped a sentence of the form "this is the only ..." or
#         "the accepted set is exactly ..." about this anchor and every one was falsified
#         within a round -- round 10's was "the restart set is closed because it is read off
#         that grammar rather than collected from counterexamples", and `coproc`'s optional
#         NAME falsified it in one sitting. Enumerate what is open; do not write another one. Round 6's
#         structural answer is not that the list is now complete -- it is that the walk stops
#         ACCEPTING what is not on it. This file makes exactly TWO positive claims about the
#         walk, and both are tested rather than argued. Round 9's: a record treated as inert
#         emits no `branch_events` at all (`arm_record_is_case_label`). Round 11's: the
#         command-position classification is TOTAL over bash's reserved words, asserted
#         against `compgen -k` by `branch_events_classifies_every_reserved_word`, so a word
#         bash adds or an edit drops cannot silently rejoin a permissive default. Note what
#         the second does NOT claim: that each word is in the RIGHT bucket, that the walk
#         parses bash, or that a non-reserved word can never open a command position. It
#         claims only that none is MISSING, which is the one thing ten rounds of prose
#         closure arguments kept getting wrong. Nothing else is claimed.
#       · that same proof is positional, not dataflow: it establishes that the use LINE is
#         inside the protected arm, not that the value reaching the use is the value the
#         guard tested. A re-assignment between the guard and the use, or a different
#         variable on the use's line, still reads as protected.
#       · the pinned literal matched inside a STRING rather than as a command. The literal
#         search runs over raw text, so deleting a guard and leaving its text in an `echo`
#         or a heredoc satisfies the pin. (In `.py` files one allowlist entry is honestly
#         a pinned assertion string; in shell it is a residual, not a design.)
#     So read the shell pin as "the cited text is still written outside a comment, and the
#     continuation immediately following it is one of the shapes listed above" -- NOT as
#     "the guard still fails", which is what the first three versions of this sentence
#     claimed and could not support. Read the Python pin as "the cited check is still
#     written", and nothing beyond that.
# The recognized-site count is pinned in RECOGNIZED_REF_SITES for the same reason the
# allowlist is explicit: moving an interpolation out of a recognized shape is a way to
# lose coverage without losing a green run. SCANNED_FILES pins the OTHER half of the same
# property -- a file dropping out of the scan loses coverage just as quietly, and the file
# count was reported in the PASS line and repeated in ADR 0194 while nothing asserted it
# (#1464 re-review round 9).
# Do not read a green run here as "every ref interpolation in this repository is encoded
# correctly". Read it as "every shape this scan recognizes is".
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/gate-rearm.yml"
fixture="$root/scripts/ci-gate/fixtures/ai-review-caller-a6b3ccc.yml"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

HOSTILE_BRANCH='release#1&2'
HOSTILE_ENCODED='release%231%262'
NESTED_BRANCH='release/2026.09'
NESTED_PATH_ENCODED='release%2F2026.09'
BLOB_SHA=1111111111111111111111111111111111111111
WORKFLOW_SHA=2222222222222222222222222222222222222222

fail() { echo "FAIL: $*"; exit 1; }

mkdir "$tmp/bin"
cat >"$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$GH_CALLS"
if [ -n "${GH_STUB_STDOUT:-}" ]; then printf '%s\n' "$GH_STUB_STDOUT"; else cat "$CALLER_FILE"; fi
SH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" ACTIONS_TOKEN=actions-token TARGET_REPO=Verjson/example
export CALLER_FILE="$fixture" GH_CALLS="$tmp/gh-calls"

# --------------------------------------------------------------------------------------
# 1a. Behavior: the compatibility selector's protected caller read.
# --------------------------------------------------------------------------------------
awk '
  /          select_compatible_review_policy\(\) \{/ { found=1 }
  found && /^          }$/ { sub(/^          /, ""); print; exit }
  found { sub(/^          /, ""); print }
' "$workflow" >"$tmp/selector.sh"
[ -s "$tmp/selector.sh" ] || fail "compatibility selector is missing"
# shellcheck source=/dev/null
source "$tmp/selector.sh"
policy='{"actor":"trusted-arm","actor_permission":"automation","authority":"human","budget_usd":"auto","explicit_rereview":false,"fallback_budget_usd":"","fallback_model":"","model":"auto","pricing_version":"anthropic-native-v1","provider":"anthropic"}'

: >"$GH_CALLS"
DEFAULT_BRANCH="$HOSTILE_BRANCH" select_compatible_review_policy "$policy" >/dev/null
grep -qF "ai-review-merge.yml?ref=$HOSTILE_ENCODED" "$GH_CALLS" \
  || fail "default branch reached the caller-read query string unencoded: $(cat "$GH_CALLS")"
! grep -qF "ref=$HOSTILE_BRANCH" "$GH_CALLS" \
  || fail "raw default branch survived in the caller-read query string"

: >"$GH_CALLS"
DEFAULT_BRANCH="$NESTED_BRANCH" select_compatible_review_policy "$policy" >/dev/null
grep -qF "ai-review-merge.yml?ref=$NESTED_BRANCH" "$GH_CALLS" \
  || fail "a path-shaped default branch must keep its separators literal in a query value: $(cat "$GH_CALLS")"

# --------------------------------------------------------------------------------------
# 1b. Behavior: generalized source-bound receipt verification. Extract by explicit
#     outer-indent bounds so the label caller exercises its dynamic workflow ref.
# --------------------------------------------------------------------------------------
awk '
  !found && $0 == "                # Keep the protected caller and the workflow revision recorded by the receipt identical." { found=1 }
  found {
    line = $0
    sub(/^                /, "", line)
    print line
    if ($0 ~ /^                if ! jq -e --arg ref /) { checking_receipt=1; next }
    if (checking_receipt && $0 == "                fi") exit
  }
' "$workflow" >"$tmp/source-bound-receipt.sh"
[ -s "$tmp/source-bound-receipt.sh" ] || fail "source-bound receipt verification block is missing"
{ printf 'verify_source_bound_receipt() {\n'; cat "$tmp/source-bound-receipt.sh"; printf '  return 0\n}\n'; } \
  >"$tmp/source-bound-receipt-wrapper.sh"
# shellcheck source=/dev/null
source "$tmp/source-bound-receipt-wrapper.sh"

LABEL_CALLER_PATH='.github/workflows/ai-review-label-rearm.yml'
LABEL_WORKFLOW_ID=424242

# Exercise a complete source run and schema-2 receipt for the protected label caller.
drive_recovery() {
  # shellcheck disable=SC2034 # The sourced workflow receipt block consumes these local fixture values.
  local branch="$1" receipt_dir="$tmp/receipt" source_path source_workflow receipt_workflow_sha source_run
  # shellcheck disable=SC2034 # The extracted receipt verifier reads this local at runtime.
  source_path="$LABEL_CALLER_PATH"
  # shellcheck disable=SC2034 # The extracted receipt verifier reads this local at runtime.
  source_workflow="$LABEL_CALLER_PATH"
  # shellcheck disable=SC2034 # The extracted receipt verifier reads this local at runtime.
  receipt_workflow_sha="$WORKFLOW_SHA"
  # shellcheck disable=SC2034 # The extracted block reads this complete source-run fixture.
  source_run="$(cat <<JSON
{"id":73,"event":"pull_request_target","path":"$LABEL_CALLER_PATH","workflow_id":$LABEL_WORKFLOW_ID,"run_attempt":1,"head_sha":"$BLOB_SHA","head_repository":{"full_name":"$TARGET_REPO"},"repository":{"id":1234,"full_name":"$TARGET_REPO"},"actor":{"login":"trusted-arm"}}
JSON
)"
  rm -rf "$receipt_dir"; mkdir -p "$receipt_dir"
  cat >"$receipt_dir/receipt.json" <<JSON
{"schema":2,"workflow_sha":"$WORKFLOW_SHA","delivery_event":"labeled",
 "delivery_actor":"trusted-arm",
 "workflow_ref":"$TARGET_REPO/$LABEL_CALLER_PATH@refs/heads/$branch"}
JSON
  : >"$GH_CALLS"
  DEFAULT_BRANCH="$branch" GH_STUB_STDOUT="$BLOB_SHA" verify_source_bound_receipt \
    || fail "the source-bound receipt block rejected its fixture for branch '$branch'"
}

# The hostile branch must be percent-encoded before it reaches the dynamic caller query.
drive_recovery "$HOSTILE_BRANCH"
grep -qF "contents/$LABEL_CALLER_PATH?ref=$HOSTILE_ENCODED" "$GH_CALLS" \
  || fail "hostile default branch reached the dynamic receipt query unencoded: $(cat "$GH_CALLS")"
! grep -qF "contents/$LABEL_CALLER_PATH?ref=$HOSTILE_BRANCH" "$GH_CALLS" \
  || fail "raw hostile default branch survived in the dynamic receipt query"

# Path separators are intentional in branch refs; reserved characters still use @uri encoding.
drive_recovery "$NESTED_BRANCH"
grep -qF "contents/$LABEL_CALLER_PATH?ref=$NESTED_BRANCH" "$GH_CALLS" \
  || fail "slash-bearing default branch did not preserve path separators in the dynamic receipt query: $(cat "$GH_CALLS")"
! grep -qF "contents/$LABEL_CALLER_PATH?ref=$NESTED_PATH_ENCODED" "$GH_CALLS" \
  || fail "slash-bearing default branch was encoded contrary to the query-ref contract"

# --------------------------------------------------------------------------------------
# Shared scan: every ref interpolation in workflows and non-test shell and Python scripts.
#   query value  -> `?ref=$VAR`          (shell) or `?ref={EXPR}`          (Python)
#   path segment -> `<ref-segment>/$VAR` (shell) or `<ref-segment>/{EXPR}` (Python), where
#                   <ref-segment> is any of the ref-bearing GitHub REST path segments in
#                   REF_PATH_SEGMENT below, plus the second operand of a `compare/A...B`.
# A `.py` file is scanned for both syntaxes: it may build a URL itself, and it may also emit
# shell that does (the generated node required-workflow admission step is one).
# `*.test.sh`, `*.test.py` and `*_test.py` are excluded deliberately: their `?ref=` strings
# are stub matchers and expected-URL assertions, not privileged reads.
# `.github/actions/*/action.yml` is NOT scanned -- see the composite-action bullet in the
# ceiling above for what covers `ci-eligibility`'s copy of the same read instead.
# --------------------------------------------------------------------------------------
mapfile -t scanned < <(
  git -C "$root" ls-files -- '.github/workflows/*.yml' 'scripts/*.sh' 'scripts/*.py' \
    | grep -vE '(\.test\.(sh|py)|_test\.py)$'
)
[ "${#scanned[@]}" -gt 0 ] || fail "the ref-interpolation scan matched no files"

FUNC_HEADER='^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[(][)][[:space:]]*[({]'
PY_FUNC_HEADER='^[[:space:]]*def[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[(]'

# The GitHub REST path segments whose next component names a ref (a branch name, a tag, or
# a commit-ish). A literal "/" in any of them re-addresses the resource exactly the way it
# does in `git/refs/heads/`, so they all take the path encoding, never the query one.
# `branches` also covers `rules/branches`; `commits` also covers `git/commits`.
REF_PATH_SEGMENT='(git/refs?/heads|git/commits|git/trees|git/tags|commits|branches|compare)'
# `$name` and `${name}` are the same shell interpolation; the scan must not be evadable by
# adding braces.
SHVAR='\$\{?[A-Za-z_][A-Za-z0-9_]*\}?'

# Emits "file<TAB>line<TAB>kind<TAB>subject<TAB>syntax" for every ref interpolation in the
# repository. `syntax` is shvar for a `$name` interpolation and pyexpr for a `{expression}`
# one, because the two prove themselves differently.
ref_sites() {
  local file line text var expr
  for file in "${scanned[@]}"; do
    # Whole-line comments describe these URLs; they do not build one.
    while IFS=$'\t' read -r line text; do
      while read -r var; do
        printf '%s\t%s\tquery\t%s\tshvar\n' "$file" "$line" "${var#?ref=$}"
      done < <(grep -oE '\?ref=\$[A-Za-z_][A-Za-z0-9_]*' <<<"$text" || true)
      while read -r var; do
        var="${var##*/\$}"; var="${var#\{}"; var="${var%\}}"
        printf '%s\t%s\tpath\t%s\tshvar\n' "$file" "$line" "$var"
      done < <(grep -oE "${REF_PATH_SEGMENT}/${SHVAR}" <<<"$text" || true)
      if [[ "$text" == *compare/* ]]; then
        while read -r var; do
          var="${var##*...\$}"; var="${var#\{}"; var="${var%\}}"
          printf '%s\t%s\tpath\t%s\tshvar\n' "$file" "$line" "$var"
        done < <(grep -oE "\.\.\.${SHVAR}" <<<"$text" || true)
      fi
      [ "${file##*.}" = py ] || continue
      while IFS= read -r expr; do
        expr="${expr#\?ref=\{}"
        printf '%s\t%s\tquery\t%s\tpyexpr\n' "$file" "$line" "${expr%\}}"
      done < <(grep -oE '\?ref=\{[^}]+\}' <<<"$text" || true)
      while IFS= read -r expr; do
        expr="${expr##*/\{}"
        printf '%s\t%s\tpath\t%s\tpyexpr\n' "$file" "$line" "${expr%\}}"
      done < <(grep -oE "${REF_PATH_SEGMENT}/\{[^}]+\}" <<<"$text" || true)
      if [[ "$text" == *compare/* ]]; then
        while IFS= read -r expr; do
          expr="${expr##*...\{}"
          printf '%s\t%s\tpath\t%s\tpyexpr\n' "$file" "$line" "${expr%\}}"
        done < <(grep -oE '\.\.\.\{[^}]+\}' <<<"$text" || true)
      fi
    done < <(grep -nE '\?ref=[$\{]|'"${REF_PATH_SEGMENT}"'/[$\{]|compare/' "$root/$file" \
      | grep -vE '^[0-9]+:[[:space:]]*#' | sed 's/:/\t/' || true)
  done
}

# `$tmp/py-encoder.py` evaluates a `quote(…)` call against the stdlib binding it installs.
# A file that re-binds `quote` at module scope would therefore be judged by a function it
# never calls, so such a file is rejected outright instead of evaluated. Importing the
# stdlib function under its own name is the one binding that is not a shadow.
py_quote_unshadowed() { # $1 = file (relative to $root) -> 0 when module scope keeps `quote`
  local file="$1" line
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    [[ "$line" =~ ^from[[:space:]]+urllib\.parse[[:space:]]+import[[:space:]]+[A-Za-z0-9_,\ ]+$ ]] \
      || return 1
  done < <(grep -E '^(def|async def|class)[[:space:]]+quote[[:space:]]*[(:]|^quote[[:space:]]*[:=]|^import[[:space:]].*[[:space:]]as[[:space:]]+quote[[:space:]]*$|^from[[:space:]].*[[:space:]]import[[:space:]].*\bquote\b' \
    "$root/$file" || true)
  return 0
}

# The proof for a value must sit between the top of its enclosing function (or the top of
# the file) and its use, so a guard in a sibling block cannot vouch for it. A `.py` file
# that emits shell doubles its braces to survive `str.format`, so the slice is normalized
# back before any shell-shaped proof is matched against it.
block_slice() {
  local file="$1" line="$2" start re="$FUNC_HEADER"
  [ "${file##*.}" != py ] || re="$PY_FUNC_HEADER"
  start="$(awk -v end="$line" -v re="$re" \
    'NR <= end && $0 ~ re { s = NR } END { print (s ? s : 1) }' "$root/$file")"
  if [ "${file##*.}" = py ]; then
    sed -n "${start},${line}p" "$root/$file" | sed 's/{{/{/g; s/}}/}/g'
  else
    sed -n "${start},${line}p" "$root/$file"
  fi
}

unique_line_number() {
  local needle="$1" file="$2" count
  count="$(grep -oF -- "$needle" "$file" | wc -l || true)"
  [ "$count" -eq 1 ] || return 1
  grep -nF -- "$needle" "$file" | cut -d: -f1
}

unique_compare_definition_line() {
  local file="$1" occurrences source_contents
  source_contents="$(<"$file")"
  source_contents="${source_contents//$'\\\n'/}"
  occurrences="$(grep -oF -- 'compare_behind' <<<"$source_contents" | wc -l || true)"
  [ "$occurrences" -eq 2 ] || return 1
  unique_line_number 'compare_behind() {' "$file"
}

# Bash runs a command, not a source line, and a command is not a run of characters either.
# Three separate things have to be established before a tail can be judged:
#
#   1. Which physical lines make up one command. A guard split with a trailing `\`, or
#      chained with a trailing `&&`/`||`/`|`, or opening a `|| { … }` branch, carries its
#      failure handling on a LATER line: a per-line anchor reads the pinned text on one
#      line and never sees the `|| true` on the next. That is the hole that let node-ci's
#      continuation-form head-sha guard be neutered while this test stayed green (#1464).
#   2. Which characters of that command are shell STRUCTURE and which are data. `exit 1`
#      inside a double-quoted `echo` argument is not a command; neither is one inside a
#      `$(…)`, a `${…}`, or a comment. `shell_structure` blanks those spans, so every
#      structural decision below reads command text only.
#   3. For a guard spent as the negated condition of an `if`/`elif`, where the arms of that
#      construct begin and end, because the proof is then the use's POSITION rather than the
#      guard's own tail. That is `negated_branch_dominates`, below.
#
# `shell_structure` is a lexical scan, not a shell parser: it tracks quotes, `${…}`,
# `$(…)`, backticks, backslashes, and word-position `#`, and nothing else. Where it is
# unsure it blanks, which makes an unrecognized construct read as NOT fatal.
SHELL_STRUCTURE_AWK='
function shell_structure(s,   out, i, n, c, q, d, u) {
  n = length(s); out = ""; q = ""; u = ""
  for (i = 1; i <= n; i++) {
    c = substr(s, i, 1)
    if (q != "") { if (c == q) q = ""; out = out " "; continue }
    if (c == "\x27" || c == "\"" || c == "`") { q = c; out = out " "; continue }
    # A backslash escapes the next character -- unless it is the last one on the line,
    # where it is the continuation marker the joiner below looks for.
    if (c == "\\") { if (i < n) { out = out "  "; i++ } else { out = out "\\" }; continue }
    # `${…}` and `$(…)`/`$((…))` are data, and their braces and parens are not grouping.
    if (c == "$" && i < n && substr(s, i + 1, 1) == "{") {
      d = 0
      for (; i <= n; i++) {
        if (substr(s, i, 1) == "{") d++
        else if (substr(s, i, 1) == "}") { d--; if (d == 0) break }
      }
      # The span ran off the end of the record. Like an unclosed quote it continues onto
      # the next one, which this per-record pass then reads as ordinary commands.
      if (i > n) u = "$"
      out = out " "; continue
    }
    if (c == "$" && i < n && substr(s, i + 1, 1) == "(") {
      d = 0
      for (; i <= n; i++) {
        if (substr(s, i, 1) == "(") d++
        else if (substr(s, i, 1) == ")") { d--; if (d == 0) break }
      }
      if (i > n) u = "$"
      out = out " "; continue
    }
    # `#` opens a comment only in word position. A `#` inside a word (`release#1`) does
    # not, and neither does one inside quotes, which the branch above already consumed.
    if (c == "#" && (out == "" || substr(out, length(out), 1) ~ /[[:space:];&|()]/)) break
    out = out c
  }
  SHELL_DATA_OPEN = (q != "" ? q : u)
  return out
}
function brace_delta(st,   i, n, c, d) {
  d = 0; n = length(st)
  for (i = 1; i <= n; i++) { c = substr(st, i, 1); if (c == "{") d++; else if (c == "}") d-- }
  return d
}
'

shell_structure() { # $1 = shell text -> the same text with data spans blanked
  awk "$SHELL_STRUCTURE_AWK"'{ print shell_structure($0) }' <<<"$1"
}

# `shell_structure` reads ONE PHYSICAL LINE and starts each one unquoted, so a quote, a
# backtick, or a `${…}`/`$(…)` expansion opened on one line and closed on the next is not a
# data span to it: the opening line's remainder blanks, and every following line up to the
# closer reads as COMMANDS. An unclosed EXPANSION was missed here until #1464 re-review
# round 6, which reached a use through an `other=${msg:-` / `if …` / `}` region in an `else`
# arm. Callers that count structure across lines must know when either happened.
shell_data_unclosed() { # $1 = shell text -> 0 when some line ends inside a data span
  awk "$SHELL_STRUCTURE_AWK"'
    { shell_structure($0); if (SHELL_DATA_OPEN != "") found = 1 }
    END { exit(found ? 0 : 1) }' <<<"$1"
}

# A here-document body is the same blind spot with an explicit introducer. This matches ANY
# `<<` that is not a here-STRING (`<<<`, single-line and not one), rather than trying to
# recognize a delimiter word: `<<'9EOF'` is a legal delimiter that a `[[:alpha:]_]` class
# missed, and every such near-miss is a fail-OPEN. Over-matching here only declines.
# It is applied to the RAW record, not the structural text, for the same reason: an
# introducer inside an unclosed `$(` is blanked away before the structural text exists, and
# reading it raw also declines on a `<<WORD` that is merely quoted data. Both are refusals.
HEREDOC_INTRODUCER='(^|[^<])<<(-|[^<]|$)'

# Emits one logical line per command. A brace branch is closed by BRACE DEPTH, not by a bare `}` line: a
# closer carrying a tail (`} >&2`, `} || true`) used to leave the state machine open and
# buffer the whole remainder of the file into a single line, and a nested `}` used to
# close the body early -- both fail-closed, but both turn an unrelated edit above a guard
# into an inscrutable false positive (#1464 re-review).
#
# An unterminated branch at end of input drops the buffer -- fail-closed either way, but
# it must not be emitted as a joined line, because that line's "tail" is the rest of the
# file. `whole` (the default) also reports it on stderr; `slice` does not, because a block
# slice legitimately cuts a file mid-branch and that is not a defect in the file.
logical_lines() { # $1 = whole|slice; reads text on stdin, emits one logical line per command
  awk -v mode="${1:-whole}" "$SHELL_STRUCTURE_AWK"'
    { line = $0; sub(/[[:space:]]+$/, "", line); st = shell_structure(line) }
    brace {
      # Inside a brace body a newline IS a statement separator, unless the previous line
      # ended on an operator. Joining with a space would fuse `>&2` and `exit 1` into one
      # statement and hide the fatal one from the allow-list below.
      if (prevst ~ /(\\|&&|\|\||\||&|;|\{|\()[[:space:]]*$/) buf = buf " " line
      else buf = buf "; " line
      prevst = st
      bdepth += brace_delta(st)
      if (bdepth <= 0) { print buf; buf = ""; brace = 0 }
      next
    }
    # A YAML block-scalar introducer (`run: |`) ends in "|" without continuing a command.
    st ~ /:[[:space:]]*\|[-+0-9]*$/ { print buf line; buf = ""; next }
    st ~ /(\|\||&&)[[:space:]]*\{$/ {
      buf = buf line; prevst = st; brace = 1; bdepth = brace_delta(st); next
    }
    st ~ /\\$/ { sub(/\\$/, "", line); buf = buf line; next }
    st ~ /(&&|\|\|)$/ || st ~ /(^|[^|])\|$/ { buf = buf line " "; next }
    { print buf line; buf = "" }
    END {
      if (buf != "" && !brace) print buf
      if (brace) {
        if (mode != "slice") {
          print "logical_lines: unterminated `|| {` branch; the rest of the input was not judged" > "/dev/stderr"
        }
        exit 2
      }
    }
  '
}

# An ALLOW-LIST of what may follow a guard on its own command without disarming it. The
# denylist this replaces named four swallowing literals and missed at least six more --
# `||:`, `|| { :; }`, `|| echo skipped`, `| cat`, a trailing `&`, and `|| exit 0` all kept
# a cited guard reading as live (#1464 re-review). Enumerating swallows is a losing game.
#
# But an allow-list only fails closed if it enumerates SHAPES. Its first form matched the
# fatal action as a SUBSTRING of the brace body, which is a denylist wearing an
# allow-list's clothes: `|| { echo "would exit 1 here"; }`, `|| { ( exit 1 ); }`,
# `|| { false && exit 1; }`, `|| exit 256`, `|| return 256`, `|| continue` and `|| break`
# all matched and all leave the guard (third #1464 re-review).
# So the action must now be a WHOLE top-level statement of a flat brace body, read off the
# structural form, and the status must be a real non-zero wait status.
#
# `return N` is here alongside `exit N` because the gate-rearm receipt guards live inside
# shell functions, where a non-zero return is how the failure leaves the guard.
# `continue`/`break` are NOT here: they leave a guard only inside a loop, and the lexer that
# established loop context was itself a fail-open in `slice` mode (#1464 re-review round 4).
# The four named helpers are this repository's terminating idioms (`fail`, `fault`, `die`,
# `abort`) and are allow-listed BY NAME, not by any proof that they terminate: redefining
# one as a no-op is the same vacuous-guard ceiling the header already states.
#
# The status is bounded to 1-255, the range bash reports unchanged. `exit 256` wraps to 0
# and is a swallow; `exit 300` wraps to 44 and does leave the guard, but is rejected here
# too. That is deliberate: nothing in this repository writes it, and the bound is easier
# to state and to trust than "any N that is not a multiple of 256".
GUARD_FATAL_STATUS='0*([1-9][0-9]?|1[0-9][0-9]|2[0-4][0-9]|25[0-5])'
GUARD_FATAL_ACTION="(exit|return)[[:space:]]+${GUARD_FATAL_STATUS}"
GUARD_FATAL_HELPER_NAME='(fail|fault|die|abort)'
GUARD_FATAL_HELPER="${GUARD_FATAL_HELPER_NAME}[[:space:]][^|&]*"

guard_action_leads() { # $1 = structural text -- does it BEGIN fatally?
  [[ "$1" =~ ^[[:space:]]*${GUARD_FATAL_ACTION}([^A-Za-z0-9_]|$) ]] && return 0
  [[ "$1" =~ ^[[:space:]]*${GUARD_FATAL_HELPER} ]]
}

# The brace branch. A body counts only if it is FLAT -- no nested command group -- and one
# of its top-level statements is EXACTLY a fatal action. "Exactly" is what rejects
# `false && exit 1`: a statement reached only through a chain is not unconditionally
# reached, and this anchor does not evaluate conditions. A subshell and a quoted literal
# are already blanked by `shell_structure` before the split.
brace_body_is_fatal() { # $1 = structural text starting just after the opening `{`
  local body="$1" out="" part c i n=${#1} d=1
  for ((i = 0; i < n; i++)); do
    c="${body:i:1}"
    if [ "$c" = '{' ]; then d=$((d + 1))
    elif [ "$c" = '}' ]; then d=$((d - 1)); [ "$d" -eq 0 ] && break
    fi
    out+="$c"
  done
  [ "$d" -eq 0 ] || return 1                      # unbalanced: read as disarmed
  case "$out" in *'{'* | *'}'*) return 1 ;; esac  # a nested group is not a flat body
  # `shell_structure` does not model here-documents, so a heredoc body reads as commands:
  # `|| { cat <<EOF` / `exit 1` / `EOF` / `}` passed as fatal while printing that text.
  # A body containing a redirection operator is not flat either -- read it as disarmed.
  case "$out" in *'<<'*) return 1 ;; esac
  local IFS=';'
  for part in $out; do
    part="${part#"${part%%[![:space:]]*}"}"; part="${part%"${part##*[![:space:]]}"}"
    [[ "$part" =~ ^${GUARD_FATAL_ACTION}$ ]] && return 0
    # The helper's arguments are blanked by `shell_structure` and trimmed off the statement,
    # so match the NAME here rather than the `<name> <args>` form the whole-branch path sees.
    [[ "$part" =~ ^${GUARD_FATAL_HELPER_NAME}([[:space:]]|$) ]] && return 0
  done
  return 1
}

guard_tail_is_fatal() { # $1 = the text following the pinned guard
  # Nothing follows: the command's own non-zero status IS the command's status, and every
  # block this test reads runs under `set -euo pipefail`.
  [[ "$1" =~ ^[[:space:]]*\;?[[:space:]]*$ ]] && return 0
  local struct branch
  struct="$(shell_structure "$1")"
  # Anchored at the START of the tail, not the end: a guard may sit inside a larger group
  # (`[ -z "$head" ] || { [[ … ]] || return 2; ref_query="?ref=$head"; }`), so what matters
  # is that the continuation IMMEDIATELY following it is fatal, not what trails after that.
  # A leading `&&` chain is skipped because only the element after the FINAL `&&`/`||`
  # escapes `set -e`; `[[ … ]] && foo` with no `||` is therefore NOT fatal and is rejected.
  [[ "$struct" =~ ^[[:space:]]*(\&\&[^\|]*)*\|\|(.*)$ ]] || return 1
  branch="${BASH_REMATCH[2]}"
  branch="${branch#"${branch%%[![:space:]]*}"}"
  case "$branch" in
    '{'*) brace_body_is_fatal "${branch#\{}" ;;
    *) guard_action_leads "$branch" ;;
  esac
}

# A plain literal match for a cited guard is not enough. `grep -qF` finds the text anywhere
# in the file, so commenting the guard out, or appending a swallow to it, left this test
# green while the guard no longer guarded anything -- the same rot one level up. A guard
# counts as live only if its pinned text sits on a logical line that is not commented out
# ahead of it and whose tail is on the fatal allow-list above.
#
# This is a command-level anchor, not a reachability analysis. See the ceiling note in the
# header: a guard MOVED into a branch that never executes still satisfies this.
# A guard may also be spent as the NEGATED condition of an `if`/`elif` whose protected use
# sits in the sibling `else` arm. Nothing follows it on its own command but `; then`, so the
# tail allow-list cannot judge it, and the proof is structural instead: the arm the guard
# opens is the FAILING one, and it cannot fall through to the `else`. Establishing that needs
# lookahead past the guard's own logical line, so the records are read into an array rather
# than streamed.
#
# Deliberately narrow, and fail-closed on everything else. A positive `if <guard>; then <use>`
# is NOT accepted: its failing arm is the one that reaches the rest of the file. A negated
# branch with no `else` is not accepted either, for the same reason.
# EVERY command-position word of a part, not just its first. Taking only the first word was
# the asymmetry rounds 5 to 9 all built on top of and none of them saw (#1464 re-review
# round 10). A CLOSER is always first-word, because bash requires `;` or a newline before
# `fi`/`done`/`esac`; an OPENER is not, because another token can open the part ahead of it.
# So the first-word reading lost openers and never lost closers -- a net-negative event
# stream, which is precisely the one direction the header below names as able to fail open.
# Three legal, `bash -n`-clean, otherwise-modelled spellings each lost one level of relative
# depth that way, and each let a nested fatal statement be read at depth 0:
#   `probe || { if q; then a; fi; }`     emitted `fi`        (the `{` opened the part)
#   `if a; then if b; then c; fi; fi`    emitted `if fi fi`  (the `then` opened the part)
#   `time if q; then a; fi`              emitted `fi`        (the `time` opened the part)
#
# THE WALK DECLINES WHAT IT CANNOT POSITIVELY CLASSIFY. Rounds 5 to 10 each closed a real
# counterexample and each was followed by another fail-open in the same place, because the
# stream was PERMISSIVE: a command-position construct it had no rule for was scanned past in
# silence, so "unmodelled" and "absent" produced the same empty stream and absent reads as
# safe. Round 10's own closure argument -- "the restart set is closed because it is read off
# bash's grammar" -- was false in one word: `coproc` was a restart, but `coproc`'s OPTIONAL
# NAME is not a command, so `coproc c if q; then` read `coproc` (restart), read `c` as an
# ordinary command word, stopped, and lost the `if` that followed it. Only the unnamed
# spelling was pinned. That is the same net-negative direction, reopened one token over.
#
# So the classification below is TOTAL over bash's reserved words rather than a set of
# restarts with an implicit "everything else is an ordinary command" default. A word in
# command position is exactly one of:
#   · a RESERVED WORD this walk positively classifies -- restart, pattern-open, or stop;
#   · a reserved word it does NOT classify -- DECLINED, emitted as `decline:<word>`;
#   · anything else, which by bash's grammar IS the command name, after which the rest of
#     the part is its arguments and command position genuinely ends.
# The reserved words are a closed set bash itself publishes as `compgen -k`, and
# `branch_events_classifies_every_reserved_word` below requires every member to appear in
# exactly one bucket. A word bash adds, or one a future edit drops from a bucket, therefore
# fails that assertion instead of silently rejoining the permissive default. This is the
# same inversion `arm_record_is_modelled` already applies one layer up, pushed down into the
# stream, so an unmodelled construct is now LOUD rather than invisible.
#
# `coproc` is DECLINED rather than modelled. Modelling `coproc [NAME] command` is one more
# token rule of exactly the kind that produced ten rounds of holes; declining it costs the
# corpus nothing measurable and cannot be wrong in the fail-open direction.
#
# Parentheses are classified, not stripped. A SINGLE leading `(` opens a subshell and a
# SINGLE trailing `)` closes a `case` pattern, and those two are modelled. A DOUBLED `((` or
# `))` is arithmetic, whose contents are expressions and not commands, so blindly stripping
# the punctuation invented command-position words out of arithmetic: `if (( fi > 0 )); then
# a; fi` read `if fi fi`, a SPURIOUS closer that fires one level too shallow. Only the
# `arm_record_is_modelled` parenthesis filter stood between that and the anchor. It now
# declines at the stream, which is where a wrong stream is repaired.
#
# COMMAND POSITION RESTARTS after exactly the tokens bash's grammar lets a command follow
# with no `;` or newline between them:
#   · the list-introducing reserved words -- `if`, `elif`, `then`, `else`, `do`, `while`,
#     `until`. (`while`/`until` introduce a list and so must restart, even though `do` is
#     still the token counted for depth.)
#   · the two grouping openers, `{` and `(`. `(` is the one opener bash accepts with no
#     space after it, so it can be glued to the word it puts in command position (`(if q`).
#   · the pipeline prefixes `!`, `time`, and `time`'s own `-p`/`--` options.
#   · a `)` ENDING the word, which closes a `case` pattern and puts the arm body in command
#     position -- `a) if q; then b; fi` inside a `case` is ordinary shell.
# Classified as STOPPING command position, because the word that follows each is a NAME, a
# pattern, or a conditional expression and not a command -- `for if in …`, `select if in …`
# and `function if` are all legal and in none of them is `if` a reserved word: `for`,
# `select`, `function`, `in`, `[[`, `]]`. So are the closers `fi`, `done`, `esac` and `}`
# (`fi fi` is a syntax error). `case` is the twelfth and gets pattern state, below. An
# assignment or redirection prefix is not a reserved word at all and takes the ordinary
# command-name path (`X=1 if …` and `>f if …` are both syntax errors, so a reserved word
# can never be in command position after one).
# `case` is the one that needs state rather than a verdict: it restarts command position,
# but only after the `)` that ends its first pattern, so the scan SKIPS from `case` to that
# `)` and emits nothing in between. Without that skip `case $x in a) if q; then b; fi ;; esac`
# emitted `case fi esac` -- net-negative, the same direction as the three vectors above.
# `arm_record_is_modelled` declines that record for its bare `)` before the walk ever reads
# it, so the miss was fail-closed there; it is fixed in `branch_events` anyway, because the
# whole lesson of rounds 5 to 9 is that a net-negative stream is repaired at the stream.
# The four buckets, disjoint and together exactly `compgen -k`. They are data rather than
# `case` arms so the totality assertion can read them; see
# `branch_events_classifies_every_reserved_word`.
# Every entry is quoted: these are DATA, and shellcheck reads an unquoted reserved word in
# an array literal as a misplaced keyword (SC1010).
BRANCH_RESTART_WORDS=('if' 'elif' 'then' 'else' 'do' 'while' 'until' '{' '!' 'time')
BRANCH_PATTERN_WORDS=('case')
BRANCH_STOP_WORDS=('fi' 'done' 'esac' 'for' 'select' 'function' 'in' '[[' ']]' '}')
BRANCH_DECLINE_WORDS=('coproc')

branch_word_in() { # $1 = word, $2… = bucket -> 0 when the word is a member
  local needle="$1" w; shift
  for w in "$@"; do [ "$w" = "$needle" ] && return 0; done
  return 1
}

branch_events() { # $1 = structural text -> EVERY command-position event, or a `decline:<word>`
  local st="$1" part rest word raw lead trail at_command prev trailing in_pattern
  st="${st//&&/;}"; st="${st//||/;}"; st="${st//|/;}"; st="${st//&/;}"
  local IFS=';'
  for part in $st; do
    rest="$part"; at_command=1; prev=''; in_pattern=0
    while [ "$at_command" -eq 1 ]; do
      rest="${rest#"${rest%%[![:space:]]*}"}"
      [ -n "$rest" ] || break
      word="${rest%%[[:space:]]*}"; rest="${rest#"$word"}"; raw="$word"
      # Count the grouping punctuation instead of stripping it away. ONE leading `(` opens a
      # subshell and ONE trailing `)` closes a `case` pattern; a DOUBLED `((`/`))` is an
      # arithmetic command whose contents are expressions, and stripping it manufactured
      # command-position words out of arithmetic (`(( fi ))` emitted a spurious `fi`).
      lead=0; trail=0
      while [ "${word#\(}" != "$word" ]; do word="${word#\(}"; lead=$((lead + 1)); done
      while [ "${word%\)}" != "$word" ]; do word="${word%\)}"; trail=$((trail + 1)); done
      if [ "$lead" -gt 1 ] || [ "$trail" -gt 1 ]; then
        printf 'decline:%s\n' "$raw"; at_command=0; continue
      fi
      trailing="$trail"
      # Between `case` and the `)` that ends its first pattern nothing is a command: the
      # subject word, `in`, and the pattern itself are all data. `case if in a) ;; esac` is
      # legal bash in which `if` is a PATTERN, so emitting it here would invent an opener.
      if [ "$in_pattern" -eq 1 ]; then
        [ "$trailing" -eq 1 ] && in_pattern=0
        prev="$word"; continue
      fi
      # Nothing but parentheses: still command position, and no word to judge.
      [ -n "$word" ] || { prev='('; continue; }
      case "$word" in
        if | fi | else | elif) printf '%s\n' "$word" ;;
        # `do`/`done` and `case`/`esac` open and close a compound statement exactly as
        # `if`/`fi` do. They were invisible here, so a fatal statement nested in a loop or a
        # `case` arm read as a statement of the `then` arm itself and the guard ACCEPTed with
        # the use reached on an empty loop list (#1464 re-review round 5). `do` is counted
        # rather than `while`/`for`/`until`/`select`, because `do` is the token that always
        # pairs with `done`, in both the same-line and the split spelling.
        'do' | 'done' | 'case' | 'esac') printf '%s\n' "$word" ;;
      esac
      # A `)` ended the word, so a `case` pattern just closed and the arm body is in command
      # position whatever the word itself was. Otherwise the word decides, and the decision
      # is total over the reserved words: restart, open a pattern, stop, or DECLINE.
      if [ "$trailing" -eq 0 ]; then
        if branch_word_in "$word" "${BRANCH_PATTERN_WORDS[@]}"; then
          in_pattern=1; prev="$word"; continue
        elif branch_word_in "$word" "${BRANCH_RESTART_WORDS[@]}"; then
          :
        elif branch_word_in "$word" "${BRANCH_DECLINE_WORDS[@]}"; then
          # `coproc [NAME] command`: the optional NAME is not a command, so neither scanning
          # past it nor restarting on it is sound. Refuse the record instead of modelling it.
          printf 'decline:%s\n' "$raw"; at_command=0
        elif branch_word_in "$word" "${BRANCH_STOP_WORDS[@]}"; then
          at_command=0
        else
          # `time [-p] [--] pipeline`, and only right after `time` or another of its options.
          case "$word" in
            -p | --) case "$prev" in time | -p | --) ;; *) at_command=0 ;; esac ;;
            # Not a reserved word, so by bash's grammar it IS the command name and the rest
            # of the part is its arguments. This is the one place command position really
            # ends, and it is a positive classification rather than a fallthrough.
            *) at_command=0 ;;
          esac
        fi
      fi
      prev="$word"
    done
  done
}

guard_opens_negated_branch() { # $1 = text before the guard, $2 = text after it
  [[ "$1" =~ ^[[:space:]]*(el)?if[[:space:]]+\![[:space:]]*$ ]] || return 1
  [[ "$2" =~ ^[[:space:]]*\;?[[:space:]]*then[[:space:]]*$ ]]
}

# One top-level statement of the `then` arm, judged by the SAME two notions the tail
# allow-list uses -- no third idea of what "terminates" means, and in particular `exit 0` is
# not one of them here either.
arm_statement_is_fatal() { # $1 = structural text of one top-level `then`-arm statement
  local st="${1#"${1%%[![:space:]]*}"}"
  case "$st" in
    '{'*) brace_body_is_fatal "${st#\{}" ;;
    *) guard_action_leads "$st" ;;
  esac
}

# Walk forward from the guard's logical line to the PINNED USE, which is the LAST record of
# the input: `block_slice` cuts the file at the use's own line, so in `slice` mode the end of
# the records is the use itself. Relative depth 0 is the construct the guard opened.
#
# Existence of an `else` is NOT the question, and answering it was this anchor's own
# fail-open (#1464 re-review round 4): seven shapes had one and still ran the use with an
# unchecked value -- a non-terminating `then` arm with the use after `fi`, two empty arms, a
# `then` arm of `exit 0`, the use in the `else` AND again after `fi`, the construct inside a
# loop, a `then` arm calling a helper nothing proves terminates, and the `elif` form of the
# first. Worse, it separated two cases that are the same program: a vacuous `else` was
# accepted where the pinned no-`else` fixture below was rejected.
#
# Two things can make the guard protect the use, and both are checked, because neither
# subsumes the other:
#   · the use lies inside the `else`/`elif` EXTENT -- the arm entered only when `! guard`
#     was false. This is how `scripts/privileged-merge-conformance.sh:327` is written, and
#     its `then` arm only counts a failure and falls through, so nothing else establishes it.
#   · the `then` arm TERMINATES, so a use below `fi` is reached only when the guard held.
#     `if ! guard; then echo …; exit 1; fi` is the ordinary spelling and read as dead before.
# A depth-0 `fi` means the construct closed ABOVE the use, so from there only the second can
# apply. Running out of records still inside the `then` arm proves nothing.
# THE DEFAULT IS INVERTED HERE, and that is the whole of round 6's fix.
#
# Every round of this review from 1 to 6 closed the previous round's fail-OPEN by teaching
# this walk one more construct, and every one shipped the next fail-open inside the very
# mechanism that closed it -- always the same shape, a shell construct the record model does
# not represent. Round 4 added the `else`/termination distinction; round 5 added `do`/`done`
# and `case`/`esac` to the depth counter and a here-document/unclosed-quote decline; round 6
# then found `(…)` subshells, `{…}` groups, function bodies and a `${…}` running off the end
# of a record unaccounted for in exactly those additions. Adding those four to the counter is
# round 7's fail-open. Enumerating the constructs that must be counted fails open for the
# same reason enumerating bypass forms does -- the set is not closed, and a line-oriented
# model cannot bound it (#1489; ADR 0194 reached the same conclusion for a different
# sub-problem).
#
# So the walk no longer accepts a record it merely failed to recognize. It recognizes a
# small, explicitly enumerated set of shapes, and DECLINES everything else. Unmodelled
# structure produces a REJECT -- a loud false alarm, fixed later by proving one more shape --
# and never an ACCEPT. What is listed below is therefore not "the constructs that disarm a
# guard"; it is the frontier of what this model claims to represent.
#
# Read that frontier as LEXICAL, and no wider. What `arm_record_is_modelled` refuses is a
# record whose TEXT carries structure the depth model cannot pair; a record whose text is an
# ordinary simple command is walked past, because walking past ordinary commands is the whole
# job. So a command that BUILDS control flow at RUN TIME is outside this model and is not
# refused: `eval "fi"` is read as a call to `eval` and walked past, and so is `source x.sh`.
# That residual is stated rather than defended against -- `eval "fi"` is not a realistic
# vector, since bash reports a syntax error and `eval` returns 2, so it closes nothing.
#
# NO COMPLETENESS CLAIM IS MADE HERE. Three earlier revisions of this paragraph named the
# accepted-but-unrepresented set exactly, each narrower than the last, and each was falsified
# by the next round -- most recently by `case "$b" in y)`, a record squarely inside the
# lexical frontier whose run-time effect IS its text and which was accepted, unrepresented,
# and fail-OPEN anyway (#1464 re-review round 9). A sentence rewritten four times to survive
# successive counterexamples is the wrong sentence, so it is gone rather than narrowed again.
#
# What this model does guarantee is ONE property, and `arm_inertness_emits_no_events` pins it
# over the scan's own files rather than over a fixture list:
#
#     A RECORD THIS WALK TREATS AS INERT EMITS NO `branch_events` AT ALL.
#
# The property stands on its own, and NO directional argument is offered for it any more.
# Rounds 8 and 9 both justified it with "dropping an OPENING event is the only direction in
# which a dropped event can fail open", reasoning that a dropped closer raises the relative
# depth and that a dropped `else`/`elif` leaves `in_then` set so the walk runs out of records
# still inside the `then` arm and proves nothing. That argument named only ONE of the walk's
# two exits. The other is `fi) [ "$d" -eq 0 ] && return "$arm_terminates"`: with `in_then`
# wrongly still 1, a fatal statement in the `ELSE` arm sets `arm_terminates=0` and that `fi`
# ACCEPTs. Patching `branch_events` to drop only its `else|elif` emission turns
# `if ! guard; then echo warn; else exit 1; fi` / use -- correctly `dead`, because the `then`
# arm falls through -- into `live`. So dropping `else`/`elif` is a fail-OPEN direction too,
# and the sentence was false rather than merely incomplete (#1464 re-review round 10).
# It is DELETED, not narrowed for a third time. What remains is the property itself:
# "a label is a label -- it opens nothing and closes nothing" is a claim this model can state
# once and test, whereas "which drops happen to be fail-closed" is a claim it would have to
# re-derive on every future edit, and has twice re-derived wrongly. Everything outside that
# one property is UNCLAIMED.
# The one record shape whose bare `)` is NOT a group: a `case` arm's pattern label, with the
# `;;` that ends an arm. Only when the WHOLE record is the label -- `) || echo swallowed`
# does not match it, and a `(` with no `)` cannot. `( exit 1 )` DOES match it; an earlier
# revision of this sentence said otherwise and the fixture added alongside it says the
# opposite outright (#1464 re-review round 7). `;&` and `;;&` are deliberately NOT in the
# terminator: a record carrying either has a bare `&` and declines at the `&` rule below
# before this shape is ever consulted, so listing them made the model claim a spelling it
# never accepts (#1464 re-review round 7).
# `scripts/privileged-merge-conformance.sh:327` is the live guard that needs the label: the
# use it protects sits in an `else` arm containing a `case` over the compare status.
CASE_ARM_TERMINATOR=';;'
CASE_ARM_LABEL="^[[:space:]]*[(]?[^()]*[)][[:space:]]*(${CASE_ARM_TERMINATOR})?[[:space:]]*\$"
# A record that ENDS an arm, and therefore puts the walk back in arm position. `;;` only:
# `;&` and `;;&` carry a bare `&` and are declined before this is consulted.
CASE_ARM_ENDS=";;[[:space:]]*\$"

# A record the model reads as a `case` arm pattern label. It is a LABEL, not a command: it
# opens nothing, closes nothing, and runs nothing, so the exception grants exactly that one
# property and no other. `negated_branch_dominates` therefore treats such a record as INERT
# instead of feeding it to `branch_events`.
#
# TWO conditions, and neither is a shape rule, because four consecutive rounds of shape rules
# each shipped the next fail-open inside the mechanism that closed the previous one.
#
# 1. ARM POSITION, not "a `case` is open somewhere". `case … in` and `;;` are the only two
#    places bash itself will parse a word list ending in `)` as a pattern; anywhere else in
#    an arm a `)` is a group. Rounds 7 and 8 asked only whether some `case` was open, which
#    admitted `case "$b" in y)` -- an ordinary nested `case` written on one line, in the
#    middle of an arm, `bash -n` clean -- as a label. Its `case` event was then dropped, the
#    guarded `fi` fired one level too shallow, and a guard whose `then` arm falls through
#    read as live (#1464 re-review round 9). This is parser state rather than another shape,
#    which is why it is not the fifth entry in that series: in arm position `)` IS a label to
#    bash, and outside it `)` is NOT, and those are the two things being asked.
#
# 2. THE RECORD MUST EMIT NO `branch_events` AT ALL. This is the guarantee the header states,
#    enforced where inertness is granted rather than argued about afterwards. It is what
#    makes "a label opens nothing and closes nothing" true by construction instead of true by
#    a survey of shapes: a label-shaped record that would emit an event is simply not a label
#    here, and falls to `arm_record_is_modelled`'s bare-parenthesis refusal -- a REJECT, the
#    inverted default, never an ACCEPT. It closes round 9's vector a second time, and it also
#    retires the round-7 exception for `do )`, `if )`, `case )` and `do|while )` labels: those
#    now DECLINE rather than going inert. That is the measured cost of the guarantee, and it
#    is paid deliberately -- see the fixtures below, and the cost measured over the scanned
#    files in `arm_inertness_emits_no_events`.
#
# Note what is NOT relied on any more. Round 7 justified the exception with "`case` raises `d`
# and `case_depth` together and `esac` lowers both, so `case_depth > 0` implies `d > 0`, and a
# label can never be read at relative depth 0." That was FALSE: `fi` and `done` lower `d`
# without lowering `case_depth`, so `probe || { if q; then a; fi; }` inside an arm emits a
# bare `fi` and leaves `d` at 0 with a `case` still open (#1464 re-review round 9). The claim
# is deleted rather than repaired, because nothing needs it: an inert record contributes no
# statement and no event, so what relative depth it is read at grants it nothing either way.
arm_record_is_case_label() { # $1 = structural text, $2 = 1 when an arm label is expected
  [ "${2:-0}" -eq 1 ] || return 1
  [[ "$1" =~ $CASE_ARM_LABEL ]] || return 1
  [ -z "$(branch_events "$1")" ]
}

arm_record_is_modelled() { # $1 = raw record, $2 = structural text, $3 = 1 when in arm position
  # Data regions the per-record structural pass cannot carry across records: a here-document
  # body, and a quote, backtick or expansion left open at the end of a record, all read as
  # ordinary commands on the records that follow (#1464 re-review rounds 5 and 6).
  [[ "$1" =~ $HEREDOC_INTRODUCER ]] && return 1
  shell_data_unclosed "$1" && return 1
  local st="$2" amp opens closes
  # Any grouping the depth counter does not pair: a `(…)` subshell, a `((…))` command, a
  # function definition header `name() {`, and a `case` pattern's bare `)`. `$(…)` and
  # `${…}` are blanked before this, so a parenthesis surviving here opens or closes a group.
  # A subshell is the round-6 vector twice over -- `( exit 1 ) || echo …` exits the subshell,
  # and `cleanup() { exit 1; }` only DEFINES an exit -- and both read as fatal at depth 0.
  if [[ "$st" == *'('* || "$st" == *')'* ]]; then
    arm_record_is_case_label "$st" "${3:-0}" || return 1
  fi
  # A `&` that is not `&&`. A backgrounded `{ …; exit 1; } &` exits a subshell, not this
  # shell, and reads as fatal at depth 0. `>&`, `<&` and `&>` are descriptor-duplicating
  # redirections, not control operators, and are the one `&` this model does represent --
  # `echo … >&2` is how this repository writes an error, and the pinned live fixture below
  # is that shape.
  amp="${st//&&/}"; amp="${amp//>&/}"; amp="${amp//<&/}"; amp="${amp//&>/}"
  case "$amp" in *'&'*) return 1 ;; esac
  # Braces. The ONLY brace shape this analysis can prove is the single FLAT group that
  # `brace_body_is_fatal` reads, so at most one may open on a record and it must close on
  # the same record. A `{` left open spans records the depth counter does not pair, which is
  # both the backgrounded-group vector and the function-body one.
  opens="${st//[^\{]/}"; closes="${st//[^\}]/}"
  [ "${#opens}" -le 1 ] && [ "${#opens}" -eq "${#closes}" ]
}

negated_branch_dominates() { # $1 = index of the guard's record in GUARD_RECORDS
  local i d=0 event events struct in_then=1 arm_terminates=1 depth_at_start expecting_label=0
  for ((i = $1 + 1; i < ${#GUARD_RECORDS[@]}; i++)); do
    struct="$(shell_structure "${GUARD_RECORDS[i]}")"
    # The inverted default: a record this walk cannot account for ENDS the walk as a REJECT.
    # Modelling these regions is shell parsing. Refusing them is not.
    arm_record_is_modelled "${GUARD_RECORDS[i]}" "$struct" "$expecting_label" || return 1
    # The same default inside the event stream. A `decline:` is read BEFORE any event this
    # record also emitted, so a record that both closes a construct and carries an
    # unclassified command-position word cannot return through its `fi` first.
    events="$(branch_events "$struct")"
    case "$events" in *decline:*) return 1 ;; esac
    # A record admitted as a `case` arm pattern label is INERT: it contributes no statement
    # to the arm and no event to the depth count. `arm_record_is_case_label` grants that only
    # in arm position and only to a record that emits nothing, so the `continue` below cannot
    # discard an event -- which is the property `arm_inertness_emits_no_events` pins.
    # An empty arm (`x) ;;`) puts the walk straight back into arm position.
    if arm_record_is_case_label "$struct" "$expecting_label"; then
      if [[ "$struct" =~ $CASE_ARM_ENDS ]]; then expecting_label=1; else expecting_label=0; fi
      continue
    fi
    # `;;` ends an arm wherever it appears, so the next record is in arm position again.
    # It is read before the events below, because `esac` on a later record clears it again.
    [[ "$struct" =~ $CASE_ARM_ENDS ]] && expecting_label=1
    depth_at_start=$d
    # Only a statement of the arm ITSELF is unconditionally reached: one nested inside a
    # further branch is not, which is the same reason `{ false && exit 1; }` is rejected.
    if [ "$in_then" -eq 1 ] && [ "$depth_at_start" -eq 0 ] \
      && arm_statement_is_fatal "$struct"; then
      arm_terminates=0
    fi
    while IFS= read -r event; do
      case "$event" in
        if | do) d=$((d + 1)) ;;
        # A `case` puts the walk in arm position: the next record bash will accept is a
        # pattern label. Only this record's own `case` does, because a record carrying both
        # the `case` and its first label has a bare `)` outside arm position and was already
        # declined above.
        'case') d=$((d + 1)); expecting_label=1 ;;
        # `arm_terminates` is already 0-for-yes, so it IS the answer below the construct.
        fi) [ "$d" -eq 0 ] && return "$arm_terminates"; d=$((d - 1)) ;;
        # A `done`/`esac` closing at depth 0 would close a construct this walk never saw
        # opened, so the depth model has lost the file. Decline rather than guess.
        done) [ "$d" -eq 0 ] && return 1; d=$((d - 1)) ;;
        # `esac` leaves arm position: what follows is the enclosing arm's body, not a label.
        'esac') [ "$d" -eq 0 ] && return 1; d=$((d - 1)); expecting_label=0 ;;
        else | elif) [ "$d" -eq 0 ] && in_then=0 ;;
      esac
    done < <(printf '%s\n' "$events")
  done
  # The records ran out without closing the construct. Inside the `else`/`elif` arm that is
  # the use sitting in the protected extent; still inside the `then` arm it proves nothing.
  [ "$in_then" -eq 0 ]
}

guard_live_literal_records() { # $1 = literal proof text; reads GUARD_RECORDS
  local line i
  for ((i = 0; i < ${#GUARD_RECORDS[@]}; i++)); do
    line="${GUARD_RECORDS[i]}"
    case "$line" in *"$1"*) ;; *) continue ;; esac
    # Anything opening a comment ahead of the pinned text disarms the whole line.
    case "${line%%"$1"*}" in *'#'*) continue ;; esac
    guard_tail_is_fatal "${line#*"$1"}" && return 0
    guard_opens_negated_branch "${line%%"$1"*}" "${line#*"$1"}" || continue
    negated_branch_dominates "$i" && return 0
  done
  return 1
}

guard_live_literal() { # $1 = literal proof text, $2 = whole|slice; reads the text on stdin
  local -a GUARD_RECORDS=()
  mapfile -t GUARD_RECORDS < <(logical_lines "${2:-whole}")
  guard_live_literal_records "$1"
}

guard_live_re_records() { # $1 = ERE whose match is the proof; reads GUARD_RECORDS
  local line match i
  for ((i = 0; i < ${#GUARD_RECORDS[@]}; i++)); do
    line="${GUARD_RECORDS[i]}"
    [[ "$line" =~ $1 ]] || continue
    match="${BASH_REMATCH[0]}"
    case "${line%%"$match"*}" in *'#'*) continue ;; esac
    guard_tail_is_fatal "${line#*"$match"}" && return 0
    guard_opens_negated_branch "${line%%"$match"*}" "${line#*"$match"}" || continue
    negated_branch_dominates "$i" && return 0
  done
  return 1
}

guard_live_re() { # $1 = ERE whose match is the proof, $2 = whole|slice; reads the text on stdin
  local -a GUARD_RECORDS=()
  mapfile -t GUARD_RECORDS < <(logical_lines "${2:-whole}")
  guard_live_re_records "$1"
}

# Python has no single tail shape that means "this raises": every cited Python guard is a
# sub-expression of an `if … is None:` test or a `require(…)` call, so there is nothing to
# allow-list without parsing the file. A Python guard therefore gets the comment check and
# NOTHING MORE. This is the weaker of the two anchors, deliberately and visibly so; the
# header's ceiling says which entries it covers.
#
# The denylist this replaced also carried one Python-shaped literal, `or True`. It is NOT
# kept here. A one-entry denylist catches `or True` and nothing adjacent to it -- `or 1`,
# `or (lambda: True)()`, a `require` redefined above -- while reading like protection, and
# that gap is the whole reason the shell side stopped denylisting. Dropping it is a
# deliberate, named loss of one narrow case in exchange for a claim that is true.
py_guard_is_live() { # $1 = file, $2 = literal guard text
  local line
  while IFS= read -r line; do
    case "${line%%"$2"*}" in *'#'*) continue ;; esac
    return 0
  done < <(grep -F -- "$2" "$1")
  return 1
}

guard_is_live() { # $1 = file, $2 = literal guard text
  if [ "${1##*.}" = py ]; then
    py_guard_is_live "$1" "$2"
    return
  fi
  guard_live_literal "$2" <"$1"
}

# A 40-hex-constrained value needs no encoding: the constraint already excludes every
# character that could change the meaning of the URL. Three spellings count, and in a shell
# file each is held to the same liveness rule as a cited guard -- this is the anchor for the
# head-sha constraint #1464 added to node-ci, so it may not be the weaker of the two.
#
# In a `.py` file the shell-shaped proof may be a pinned assertion STRING rather than a
# command (`scripts/cli-projects-package-surface-ruleset.py:169` is one), whose tail is
# Python list punctuation and never a `||` continuation. Those get the comment check only,
# the same weaker anchor `py_guard_is_live` applies, and the header ceiling says so.
sha_constrained() { # $1 = variable name, $2 = block slice, $3 = the file it came from
  local var="$1"
  if [ "${3##*.}" = py ]; then
    grep -qF "[[ \"\$$var\" =~ ^[0-9a-f]{40}\$ ]]" <<<"$2" && return 0
    grep -qE "(^|[[:space:]])${var}[:=][[:space:]]*[\"']?[0-9a-f]{40}[\"']?[[:space:]]*\$" <<<"$2" && return 0
    grep -qE "^[[:space:]]*$var=\"\\\$\(jq -er .*\^\[0-9a-f\]\{40\}\\\$" <<<"$2" && return 0
    return 1
  fi
  # One logical-line pass feeds every spelling. Repeating it per pattern re-lexed
  # the same multi-thousand-line workflow slice for every site (#1736).
  local -a GUARD_RECORDS=()
  mapfile -t GUARD_RECORDS < <(logical_lines slice <<<"$2")
  guard_live_literal_records "[[ \"\$$var\" =~ ^[0-9a-f]{40}\$ ]]" && return 0
  guard_live_re_records "(^|[[:space:]])${var}[:=][[:space:]]*[\"']?[0-9a-f]{40}[\"']?([[:space:]]|\$)" && return 0
  # The same 40-hex constraint spelled inside the jq program that produced the value.
  # `jq -er` exits non-zero when `select` drops the value, and `set -euo pipefail` at the
  # top of every one of these blocks turns that into an abort, so the constraint is as
  # load-bearing as the `[[ … =~ ]]` form above -- provided the assignment itself is not
  # the thing that swallows, which is why the match runs to the closing `)"`.
  guard_live_re_records "^[[:space:]]*$var=\"[\$][(]jq -er .*\^\[0-9a-f\][{]40[}][\$].*[)]\"" && return 0
  return 1
}


# --------------------------------------------------------------------------------------
# 0. The recognizer's own coverage, measured rather than described. Each case below is a
#    shape that reached a ref-bearing URL position while this scan reported green (#1464).
#    They run against synthetic files, not the repository, so a real site being fixed or
#    moved cannot quietly retire one.
# --------------------------------------------------------------------------------------
mkdir -p "$tmp/fx"
recognizer_sees() { # $1 = label, $2 = filename, $3 = expected "kind<TAB>subject", $4… = lines
  local label="$1" name="$2" expected="$3"; shift 3
  printf '%s\n' "$@" >"$tmp/fx/$name"
  # Redirected, not pipe-fed: `grep -q` exits on its first match and would SIGPIPE a
  # still-writing producer (#1430, #1445).
  grep -qxF "$expected" < <( ( root="$tmp/fx"; scanned=("$name"); ref_sites ) | cut -f3,4 ) \
    || fail "the ref scan is blind to $label"
}

recognizer_sees 'a ref in a commits/ path segment (shell)' anchor-commits.sh \
  $'path\tdefault_branch' 'gh api "repos/$REPO/commits/$default_branch"'
recognizer_sees 'a ref in a commits/ path segment (Python)' anchor-commits.py \
  $'path\tdefault_branch' 'run_gh(f"repos/{repo}/commits/{default_branch}")'
recognizer_sees 'a ref in a git/commits/ path segment' anchor-git-commits.py \
  $'path\thead_sha' 'client.request("GET", f"repos/{repo}/git/commits/{head_sha}")'
recognizer_sees 'a ref in a git/trees/ path segment' anchor-git-trees.py \
  $'path\tref' 'gh("api", f"repos/{HUB}/git/trees/{ref}?recursive=1")'
recognizer_sees 'a ref in a git/tags/ path segment' anchor-git-tags.py \
  $'path\ttag' 'api.call("GET", f"{repo}/git/tags/{tag}", token)'
recognizer_sees 'a ref in a branches/ path segment' anchor-branches.sh \
  $'path\tbranch' 'gh api "repos/$ORG/$repo/branches/$branch"'
recognizer_sees 'a ref in a rules/branches/ path segment' anchor-rules-branches.py \
  $'path\tbase' 'f"repos/{repo}/rules/branches/{base}?per_page=100"'
recognizer_sees 'the LEFT operand of a compare/ range' anchor-compare-left.sh \
  $'path\tbase_ref' 'gh api "repos/$REPO/compare/$base_ref...$head_sha"'
recognizer_sees 'the RIGHT operand of a compare/ range' anchor-compare-right.sh \
  $'path\thead_sha' 'gh api "repos/$REPO/compare/$base_ref...$head_sha"'
recognizer_sees 'the RIGHT operand of a compare/ range (Python)' anchor-compare-right.py \
  $'path\tcurrent' 'gh_json(f"repos/{repo}/compare/{previous}...{current}")'
recognizer_sees 'a ${braced} shell interpolation' anchor-braced.sh \
  $'path\tHEAD_SHA' 'gh api "repos/${GITHUB_REPOSITORY}/commits/${HEAD_SHA}/status"'

# A module-scope `quote` re-binding is rejected outright rather than evaluated: the
# encoder sandbox binds the stdlib `quote`, so a file that ships its own would be judged by
# a function it never calls.
cat >"$tmp/fx/shadowed.py" <<'PYFX'
def quote(value, safe="/"):
    return value


def read(repo, branch):
    return gh(f"repos/{repo}/git/refs/heads/{quote(branch, safe='')}")
PYFX
( root="$tmp/fx"; py_quote_unshadowed shadowed.py ) \
  && fail "a module-scope 'quote' re-binding was accepted as the stdlib encoder"
cat >"$tmp/fx/unshadowed.py" <<'PYFX'
from urllib.parse import quote


def read(repo, branch):
    return gh(f"repos/{repo}/git/refs/heads/{quote(branch, safe='')}")
PYFX
( root="$tmp/fx"; py_quote_unshadowed unshadowed.py ) \
  || fail "the stdlib 'from urllib.parse import quote' was mistaken for a shadow"

# The guard-liveness anchor's own coverage. Every `dead` tail below kept a cited guard
# reading as live while it no longer failed (#1464 re-review): the anchor used to carry a
# DENYLIST of four swallowing literals, and `||:`, `|| { :; }`, `|| echo skipped`, `| cat`,
# a trailing `&`, and `|| exit 0` all walked past it. Enumerating swallows is a losing
# game, so the anchor now allow-lists the tails that DO fail and rejects everything else.
# These cases run against synthetic files, so fixing a real guard cannot retire one.
# --------------------------------------------------------------------------------------
# THE INVARIANT, asserted as a property of the MECHANISM rather than as one more fixture
# list. Eight rounds of this review each enumerated one more shape and each shipped the next
# fail-open inside the mechanism that closed the previous one, so the ninth states what the
# walk guarantees and tests that instead:
#
#     A RECORD THIS WALK TREATS AS INERT EMITS NO `branch_events` AT ALL.
#
# Why that is the right property, rather than the ninth shape: it is a claim about what a
# label IS, and it needs no supporting claim about which discards this year's control flow
# happens to survive. Rounds 8 and 9 wrote that supporting claim anyway -- "a discarded
# OPENING event is the only direction in which the discard can fail open" -- and it is FALSE.
# It named only the walk's run-out-of-records exit and missed its `fi` exit, where a discarded
# `else`/`elif` leaves `in_then` set, a fatal statement in the ELSE arm sets
# `arm_terminates=0`, and `fi) [ "$d" -eq 0 ] && return "$arm_terminates"` ACCEPTs; the
# header records the two-line program that demonstrates it (#1464 re-review round 10). The
# directional argument is deleted from both places rather than narrowed a third time. "No
# event at all" is asserted here on its own account.
#
# The corpus is the scan's own files, not a fixture list, and it has teeth: it contains
# records that are arm-label-SHAPED and do emit events -- `.github/workflows/gate-rearm.yml`
# writes `case "$EVENT_ACTION" in labeled|…) ;;`, which is round 9's own vector. Deleting the
# emit-nothing clause from `arm_record_is_case_label` therefore turns this test red on real
# repository content. `WITNESSES` pins a floor under that so repository churn cannot quietly
# empty the corpus and leave the assertion vacuously true, and the synthetic witnesses below
# carry the same teeth independently of any file.
#
# `.py` files are excluded: the shell record model is never applied to them (`py_guard_is_live`
# takes the weaker comment-only anchor), so their text is not a record corpus for this walk.
# READ THIS AS A MUTATION DETECTOR, not as a check that can fire on unmutated code. Its two
# conditions are `arm_record_is_case_label` and "emits an event", and the first ALREADY
# requires the second to be false, so on this file as written `violations` is necessarily 0.
# That is the point and not a defect: replacing the emit-nothing clause at
# `arm_record_is_case_label` with `true` makes it exit 1 with 5 errors on real repository
# content. The assertion that the corpus can still supply such content is the separate
# `inert_witnesses` floor below; this function is what turns that content into a failure.
arm_label_is_inert_violations() { # $1 = file; prints every record that breaks the invariant
  local rec struct
  while IFS= read -r rec; do
    # Sound prefilter: `CASE_ARM_LABEL` needs a `)`, and blanking only ever replaces a
    # character with a space, so a record with no `)` cannot acquire one.
    case "$rec" in *')'*) ;; *) continue ;; esac
    struct="$(shell_structure "$rec")"
    # Arm position is the most PERMISSIVE state the walk can be in, so asking with it set
    # covers every state the walk could reach at this record.
    arm_record_is_case_label "$struct" 1 || continue
    [ -n "$(branch_events "$struct")" ] && printf '%s\n' "$rec"
  done < <(logical_lines slice <"$1" 2>/dev/null)
  return 0
}

# THE MEASURED COST OF THE INVERSION, kept measured. Declining what the walk cannot
# classify is only affordable while the corpus does not actually write those constructs, and
# "it was free when we shipped it" is a claim that rots silently. This reports, per record,
# whether the STREAM declines a record that `arm_record_is_modelled` would otherwise have
# ACCEPTED -- i.e. a refusal the anchor did not already make one layer up, and therefore real
# lost reach rather than a second refusal of the same record.
# Sound prefilter, derived from the buckets rather than hand-listed: a decline is emitted
# only for a doubled `((`/`))` or for a member of `BRANCH_DECLINE_WORDS`, and blanking only
# ever replaces a character with a space, so a record containing none of those substrings
# cannot acquire one. Deriving it from the bucket means adding a decline word cannot leave
# the filter behind.
branch_record_may_decline() { # $1 = raw record
  local w
  case "$1" in *'(('*) return 0 ;; *'))'*) return 0 ;; esac
  for w in "${BRANCH_DECLINE_WORDS[@]}"; do
    case "$1" in *"$w"*) return 0 ;; esac
  done
  return 1
}

stream_only_declines() { # $1 = file; counts records the stream declines that the record model accepts
  local rec struct n=0
  while IFS= read -r rec; do
    branch_record_may_decline "$rec" || continue
    struct="$(shell_structure "$rec")"
    case "$(branch_events "$struct")" in *decline:*) ;; *) continue ;; esac
    arm_record_is_modelled "$rec" "$struct" 1 && n=$((n + 1))
  done < <(logical_lines slice <"$1" 2>/dev/null)
  printf '%s\n' "$n"
}

# Every record the stream declines, whatever the record model says. The floor below uses it
# so the zero above cannot be zero merely because nothing in the corpus declines at all.
stream_declines() { # $1 = file; counts records whose event stream carries a decline
  local rec n=0
  while IFS= read -r rec; do
    branch_record_may_decline "$rec" || continue
    case "$(branch_events "$(shell_structure "$rec")")" in *decline:*) n=$((n + 1)) ;; esac
  done < <(logical_lines slice <"$1" 2>/dev/null)
  printf '%s\n' "$n"
}

arm_label_shaped_emitters() { # $1 = file; counts records that are label-SHAPED and do emit
  local rec struct n=0
  while IFS= read -r rec; do
    case "$rec" in *')'*) ;; *) continue ;; esac
    struct="$(shell_structure "$rec")"
    [[ "$struct" =~ $CASE_ARM_LABEL ]] || continue
    [ -n "$(branch_events "$struct")" ] && n=$((n + 1))
  done < <(logical_lines slice <"$1" 2>/dev/null)
  printf '%s\n' "$n"
}

inert_violations=0
inert_witnesses=0
declines=0
costly_declines=0
for scan_file in "${scanned[@]}"; do
  case "$scan_file" in *.py) continue ;; esac
  while IFS= read -r offender; do
    [ -n "$offender" ] || continue
    inert_violations=$((inert_violations + 1))
    echo "::error::an inert arm label emitted a branch event: $scan_file :: $offender"
  done < <(arm_label_is_inert_violations "$root/$scan_file")
  inert_witnesses=$((inert_witnesses + "$(arm_label_shaped_emitters "$root/$scan_file")"))
  declines=$((declines + "$(stream_declines "$root/$scan_file")"))
  costly_declines=$((costly_declines + "$(stream_only_declines "$root/$scan_file")"))
done
[ "$inert_violations" -eq 0 ] \
  || fail "$inert_violations record(s) in the scanned files were treated as inert while emitting a branch event"
# The corpus must keep at least one record that could break the invariant, or the assertion
# above stops being evidence of anything.
[ "$inert_witnesses" -ge 1 ] \
  || fail "no scanned file contains an arm-label-shaped record that emits a branch event; the invariant test above is now vacuous"

# Measured when the inversion landed: 132 of the scanned records carry a decline, and every
# one of them was ALREADY refused by `arm_record_is_modelled`'s parenthesis rule -- they are
# embedded `jq` and `awk` program bodies that `logical_lines` reads as records. So the
# inversion cost nothing: no site needed a new allowlist entry and none of this file's
# published counts moved. That is a fact about today's corpus, not a property, which is why
# it is asserted rather than written down.
#
# If this fires, the stream is refusing something the record model accepts, and the anchor
# has lost reach it used to have. Do NOT raise it to make the run green: a rising number is
# the signal the brief for this change asked to see before it could be papered over, and the
# answer is to classify the construct positively or to decide deliberately that the reach is
# not worth having.
[ "$costly_declines" -eq 0 ] \
  || fail "$costly_declines scanned record(s) are declined by the event stream but accepted by arm_record_is_modelled; the inversion is now costing the anchor reach it previously had"
# ... and the zero above must be a zero about something. A corpus with no declining records
# at all would satisfy it vacuously.
[ "$declines" -ge 1 ] \
  || fail "no scanned record carries a branch_events decline; the cost assertion above is now vacuous"

# The same property on records chosen to break it, so the test does not depend on repository
# churn keeping a witness alive. Each must be label-SHAPED, must EMIT, and must therefore be
# declined -- and `arm_record_is_modelled` must then refuse it outright, because a record
# carrying a bare `)` that is not a label is exactly what the inverted default rejects.
for witness in \
  'case "$b" in y)' \
  'case "$EVENT_ACTION" in labeled|edited) ;;' \
  'do )' \
  'if )' \
  'do|while )' \
  'esac )' \
  'fi )' \
  'else )'; do
  witness_struct="$(shell_structure "$witness")"
  [[ "$witness_struct" =~ $CASE_ARM_LABEL ]] \
    || fail "invariant witness is not arm-label-shaped, so it proves nothing: $witness"
  [ -n "$(branch_events "$witness_struct")" ] \
    || fail "invariant witness emits no branch event, so it proves nothing: $witness"
  ! arm_record_is_case_label "$witness_struct" 1 \
    || fail "an emitting record was treated as an inert arm label: $witness"
  ! arm_record_is_modelled "$witness" "$witness_struct" 1 \
    || fail "a declined arm label was still walked past instead of ending the walk: $witness"
done

# Arm POSITION, asserted on the predicate rather than through a whole program: the same
# record is a label where bash would parse one and a bare group anywhere else. This is what
# round 7's `case_depth > 0` could not say, and it is the half of the fix that keeps the
# exception accurate instead of merely narrow.
for positioned in '  a)' '  "") ;;' '  ahead|identical) ;;' '  *)' '  ( exit 1 )'; do
  positioned_struct="$(shell_structure "$positioned")"
  arm_record_is_case_label "$positioned_struct" 1 \
    || fail "a record in arm position was not read as a label: $positioned"
  ! arm_record_is_case_label "$positioned_struct" 0 \
    || fail "a record outside arm position was read as a label: $positioned"
done

HEX_GUARD='[[ "$head_sha" =~ ^[0-9a-f]{40}$ ]]'

guard_tail_case() { # $1 = live|dead, $2 = label, $3… = the guard's physical lines
  local expect="$1" label="$2"; shift 2
  printf '%s\n' "$@" >"$tmp/fx/guard.sh"
  if guard_is_live "$tmp/fx/guard.sh" "$HEX_GUARD"; then
    [ "$expect" = live ] || fail "the cited-guard anchor accepted a disarmed guard: $label"
  else
    [ "$expect" = dead ] || fail "the cited-guard anchor rejected a live guard: $label"
  fi
  # The inline 40-hex proof reads the same shapes out of a block slice, and the two must
  # not disagree: the guard this PR added to node-ci is judged by the inline path.
  if sha_constrained head_sha "$(printf '%s\n' "$@")" "$tmp/fx/guard.sh"; then
    [ "$expect" = live ] || fail "the inline 40-hex proof accepted a disarmed guard: $label"
  else
    [ "$expect" = dead ] || fail "the inline 40-hex proof rejected a live guard: $label"
  fi
}

guard_tail_case live 'a same-line `|| { …; exit 1; }`' \
  "$HEX_GUARD"' || { echo "::error::current PR head is unavailable"; exit 1; }'
guard_tail_case live 'the line-continuation form node-ci uses' \
  "$HEX_GUARD"' \' \
  '  || { echo "::error::head-sha is not a 40-hex object name"; exit 1; }'
guard_tail_case live 'an && chain closing in `|| exit 1`' \
  "$HEX_GUARD"' &&' \
  '  [[ "$head_sha" != /* ]] || exit 1'
guard_tail_case dead 'the guard commented out' \
  '# '"$HEX_GUARD"' || { echo "::error::x"; exit 1; }'
guard_tail_case dead 'a `|| true` on the continuation line, not the pinned one' \
  "$HEX_GUARD"' \' \
  '  || true'
for swallow in '|| true' '|| :' '||:' '|| { :; }' '|| echo skipped' '| cat' '&' '|| exit 0'; do
  guard_tail_case dead "a \`$swallow\` tail" "$HEX_GUARD $swallow"
done

# The rest of the fatal allow-list, pinned so a later narrowing cannot drop a shape this
# repository actually writes -- and its near-misses, pinned so widening it stays deliberate.
guard_tail_case live 'a `|| return 2` inside a function' \
  "$HEX_GUARD"' || return 2'
guard_tail_case live 'a guard nested in a larger group, fatal branch first' \
  '[ -z "$head_sha" ] || { '"$HEX_GUARD"' || return 2; ref_query="?ref=$head_sha"; }'
guard_tail_case live 'a `|| fault …` named terminating helper' \
  "$HEX_GUARD"' || fault 1 "could not resolve a head SHA"'
guard_tail_case live 'a multi-line `|| {` failure branch' \
  "$HEX_GUARD"' || {' \
  '  echo "::error::could not resolve a head SHA" >&2' \
  '  exit 1' \
  '}'
# A guard spent as the NEGATED condition of an `if`/`elif`, whose protected use sits in the
# sibling `else`. Nothing follows the guard on its own command but `; then`, so the fatal-tail
# allow-list above cannot see it -- and main's #1466 rewrote
# `scripts/privileged-merge-conformance.sh:327` from a `|| { …; continue; }` into exactly this
# shape, which made this scan report a live guard as dead at four call sites (#1464 re-review
# round 4). The domination is structural: the failing arm cannot fall through to the `else`.
# Each fixture ENDS at the use, because `block_slice` does: it cuts the file at the use's
# own line, so the closing `fi` of a construct the use sits inside is not in the slice. A
# fixture that trails a `fi` past the use would be asking a different question.
guard_tail_case live 'a negated `elif` whose protected use is in the `else`' \
  'if [ "${#pins[@]}" -ne 1 ]; then' \
  '  failures=$((failures + 1))' \
  'elif ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  gh api "repos/$repository/commits/$head_sha"'
guard_tail_case live 'a negated `if` whose protected use is in the `else`' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  gh api "repos/$repository/commits/$head_sha"'
# The other way the guard reaches a use: the `then` arm leaves, so a use BELOW `fi` runs
# only when the guard held. This ordinary spelling read as dead until round 4.
guard_tail_case live 'a negated `if` whose `then` arm exits, with the use after `fi`' \
  'if ! '"$HEX_GUARD"'; then' \
  '  echo "::error::head sha is not a 40-hex object name" >&2' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# Fail-closed boundaries of that shape, pinned so widening it stays deliberate. Without an
# `else` the failing arm falls straight through to the use, so the branch proves nothing.
guard_tail_case dead 'a negated `if` with no `else`, which falls through to the use' \
  'if ! '"$HEX_GUARD"'; then' \
  '  echo "::warning::head sha looks wrong"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# ... and neither does giving that same program a VACUOUS `else`. Accepting it while
# rejecting the fixture directly above was this anchor's round-4 fail-open: the two are the
# same program, and an `else` that exists is not an `else` the use is in. The shapes below
# are the distinct mechanisms behind the seven that got through; `if`-with-non-terminating-
# arm, two empty arms, and the construct wrapped in a `while` are restatements of the first
# and are not pinned separately.
guard_tail_case dead 'a vacuous `else`, which is the no-`else` program above' \
  'if ! '"$HEX_GUARD"'; then' \
  '  echo "::warning::head sha looks wrong"' \
  'else' \
  '  :' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a negated `elif` whose use is after `fi`, not in the `else`' \
  'if [ -z "$head_sha" ]; then' \
  '  :' \
  'elif ! '"$HEX_GUARD"'; then' \
  '  :' \
  'else' \
  '  :' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a use in the `else` AND again after `fi`, judged at the second' \
  'if ! '"$HEX_GUARD"'; then' \
  '  :' \
  'else' \
  '  gh api "repos/$repository/commits/$head_sha"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# The `then` arm has to leave by the SAME notion the tail allow-list uses. `exit 0` is the
# swallow that list rejects, and an unlisted helper name is one nothing here proves
# terminates; accepting either below `fi` would contradict the cases above.
guard_tail_case dead 'a `then` arm of `exit 0`, the swallow the tail allow-list rejects' \
  'if ! '"$HEX_GUARD"'; then' \
  '  exit 0' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a `then` arm calling an unlisted helper, which may return' \
  'if ! '"$HEX_GUARD"'; then' \
  '  notice "head sha looks wrong"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a negated `if` whose `else` belongs to a nested branch' \
  'if ! '"$HEX_GUARD"'; then' \
  '  if [ -n "$head_sha" ]; then' \
  '    echo "::warning::x"' \
  '  else' \
  '    echo "::warning::y"' \
  '  fi' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# A fatal statement nested in a LOOP or a `case` arm is not a statement of the `then` arm:
# the loop may run zero times and the `case` may match no pattern, so the use below `fi` is
# reached with an unchecked value. `branch_events` tracked only `if`/`fi`, so `do`/`done` and
# `case`/`esac` were invisible and all three shapes ACCEPTed (#1464 re-review round 5).
#
# Each is paired with a CONTROL that moves the SAME fatal statement out of the nested body to
# the arm's own top level and must stay live. Without it a `dead` verdict proves nothing: the
# first `case` shape tried here was rejected only because `a) exit 1 ;;` does not BEGIN with a
# fatal action, which would have pinned the wrong cause.
guard_tail_case dead 'a fatal statement inside a `while` body in the `then` arm' \
  'if ! '"$HEX_GUARD"'; then' \
  '  while read -r line; do' \
  '    exit 1' \
  '  done' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case live 'the same `exit 1` below `done`, at the arm top level' \
  'if ! '"$HEX_GUARD"'; then' \
  '  while read -r line; do' \
  '    echo "::warning::$line"' \
  '  done' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a fatal statement inside a `for` body in the `then` arm' \
  'if ! '"$HEX_GUARD"'; then' \
  '  for candidate in $refs; do' \
  '    exit 1' \
  '  done' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case live 'the same `exit 1` below that `done`, at the arm top level' \
  'if ! '"$HEX_GUARD"'; then' \
  '  for candidate in $refs; do' \
  '    echo "::warning::$candidate"' \
  '  done' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a fatal statement inside a `case` arm in the `then` arm' \
  'if ! '"$HEX_GUARD"'; then' \
  '  case "$head_sha" in' \
  '    "")' \
  '      exit 1' \
  '      ;;' \
  '  esac' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# Round 6's inversion rejected this shape outright -- a `case` arm's pattern label carries a
# bare `)` and the record model did not represent it. It is back to LIVE because
# `scripts/privileged-merge-conformance.sh:327` is a real guard written this way, so the
# label was PROVEN as a shape (`CASE_ARM_LABEL`) rather than the rejection being widened
# away. The two fixtures below it pin what that proof does NOT extend to.
guard_tail_case live 'the same `exit 1` below `esac`, at the arm top level' \
  'if ! '"$HEX_GUARD"'; then' \
  '  case "$head_sha" in' \
  '    "")' \
  '      echo "::warning::empty"' \
  '      ;;' \
  '  esac' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'

# `shell_structure` reads one physical line at a time, so a here-document body and a string
# continued onto the next line read as COMMANDS. A bare `if` in either, inside the `else` arm,
# raised the depth, consumed the real `fi` one level too deep, and left the walk inside what
# looked like the protected arm -- while the use sat below `fi` with a `then` arm that only
# counts and falls through. Both ACCEPTed (#1464 re-review round 5). The anchor cannot model
# those regions, so it detects and declines them; the CONTROLs pin that the decline is caused
# by the unmodellable region and not by the surrounding shape, which stays live.
guard_tail_case dead 'a bare `if` inside a here-document in the `else` arm' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  cat <<EOF' \
  'if this were code it would open a branch; then' \
  'EOF' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a bare `if` inside a string continued onto the next line' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  message="head_sha was rejected' \
  'if this were code it would open a branch; then' \
  '"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case live 'the same shape with neither, whose use IS in the `else` extent' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  gh api "repos/$repository/commits/$head_sha"'
# Two near-misses of the introducer match itself, found by probing the first form of it and
# pinned so a later narrowing cannot reopen them. Both ACCEPTed while it read the STRUCTURAL
# text and tried to recognize a delimiter WORD.
guard_tail_case dead 'a here-document introducer blanked away inside an unclosed `$(`' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  summary=$(cat <<EOF' \
  'if this were code it would open a branch; then' \
  'EOF' \
  '  )' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a here-document delimiter that does not begin with a letter' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  "  cat <<'9EOF'" \
  'if this were code it would open a branch; then' \
  '9EOF' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case live 'a here-STRING below `fi`, which is single-line and not a here-document' \
  'if ! '"$HEX_GUARD"'; then' \
  '  exit 1' \
  'fi' \
  'read -r probe <<<"$head_sha"' \
  'gh api "repos/$repository/commits/$head_sha"'

# ROUND 6. The arm walk now DECLINES a record it cannot account for, instead of walking
# past it (`arm_record_is_modelled`). Each vector below reached the use while the anchor
# reported a live guard, and each is the same shape the four rounds before it were: a
# grouping or data construct the record model does not represent, read at relative depth 0
# and taken for a statement of the arm itself.
#
# Each is paired with a CONTROL that keeps the legitimate spelling of the same idea alive,
# so a later narrowing cannot be mistaken for the inversion doing its job.
guard_tail_case dead 'an `exit 1` that only leaves a multi-line `( … )` subshell' \
  'if ! '"$HEX_GUARD"'; then' \
  '  (' \
  '    exit 1' \
  '  ) || echo "swallowed"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'an `exit 1` inside a function DEFINITION, which only defines it' \
  'if ! '"$HEX_GUARD"'; then' \
  '  cleanup() {' \
  '    exit 1' \
  '  }' \
  '  echo "::warning::registered a cleanup"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'an `exit 1` in a BACKGROUNDED group, which exits a subshell' \
  'if ! '"$HEX_GUARD"'; then' \
  '  {' \
  '    exit 1' \
  '  } &' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case live 'the same `exit 1` as a plain statement of the arm' \
  'if ! '"$HEX_GUARD"'; then' \
  '  echo "::error::head sha is not a 40-hex object name" >&2' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# The data half of the same round: `shell_structure` scans `${…}` and `$(…)` per record, so
# one that runs off the end of its record continued onto the next records as COMMANDS -- the
# same blind spot as an unclosed quote, which round 5 detected and this one did not. A bare
# `if` inside the region then consumed the real `fi` one level too deep and the walk ended
# still reading the use as inside the protected `else` extent.
guard_tail_case dead 'a bare `if` inside a `${…}` left open at the end of a record' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  other=${msg:-' \
  'if this were code it would open a branch; then' \
  '}' \
  '  gh api "repos/$repository/commits/$head_sha"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'the same with a `$(…)` left open instead' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  other=$(printf "%s" "x' \
  'if this were code it would open a branch; then' \
  ')' \
  '  gh api "repos/$repository/commits/$head_sha"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case live 'the same `else` extent with every expansion closing on its record' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  other="${msg:-none}"' \
  '  gh api "repos/$repository/commits/$head_sha"'
# `arm_record_is_modelled` states four rules, and the four vectors above are each caught by
# more than one of them. These four are the ISOLATING cases, probed by mutating each rule
# alone: without them a later edit could delete any one rule and keep this file green, which
# is how a rule that reads like protection stops being any.
guard_tail_case dead 'a `${…}` run-off whose closer record has BALANCED braces' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  other=${msg:-' \
  'if this were code it would open a branch; then' \
  'x} ${z}{' \
  '  gh api "repos/$repository/commits/$head_sha"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a backgrounded SIMPLE command, which no brace or paren rule sees' \
  'if ! '"$HEX_GUARD"'; then' \
  '  exit 1 &' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a bare `{` group record, whose imbalance is the only signal' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  {' \
  'if this were code it would open a branch; then' \
  '  }' \
  '  gh api "repos/$repository/commits/$head_sha"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a bare `(` record inside an OPEN `case`, which is not a label' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  case "$head_sha" in' \
  '    a)' \
  '      (' \
  'if this were code it would open a branch; then' \
  '      ;;' \
  '  esac' \
  '  gh api "repos/$repository/commits/$head_sha"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'

# `CASE_ARM_LABEL` is the ONE bare `)` this model proves, and only while a `case` is open.
# These pin that it does not become a general licence for a parenthesis.
guard_tail_case dead 'a single-line `( exit 1 )` subshell, which matches the label shape' \
  'if ! '"$HEX_GUARD"'; then' \
  '  ( exit 1 )' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a label-shaped record with no `case` open above it' \
  'if ! '"$HEX_GUARD"'; then' \
  '  bogus)' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'

# ROUND 7. Admitting the label was not the leak; still READING it was. `branch_events` takes
# each `;`-part's first word, so an arm label whose first word is a depth keyword -- `do )`,
# `if )`, `case )`, or an alternation like `do|while )` -- opened a construct that never
# closes. The guarded `fi` then fired at relative depth 1 rather than 0, the walk ran off the
# end of the records with `in_then=0`, and this program ACCEPTed while its `then` arm only
# counts a failure and falls through to the use. A label is a LABEL, so it now contributes no
# event at all (`arm_record_is_case_label`). The alternative -- a list of words a label may
# not begin with -- is the enumeration this whole mechanism exists to stop.
guard_tail_case dead 'a `case` arm label whose first word is the `do` keyword' \
  'if ! '"$HEX_GUARD"'; then' \
  '  failures=$((failures + 1))' \
  'else' \
  '  case "$mode" in' \
  '    do )' \
  '      note=1' \
  '      ;;' \
  '  esac' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# Round 7 kept this shape LIVE as its control: the same label above an `exit 1` that really
# is a statement of the `then` arm. Round 9 gives it up. `arm_record_is_case_label` now
# requires a label to emit no `branch_events` at all, and `do )` emits `do`, so this record
# declines and the walk REJECTs. That is the measured price of the guarantee, and it is
# recorded here as a fail-CLOSED false negative rather than hidden: a `do )` / `if )` /
# `case )` arm label is a shape no file in this repository writes, and the alternative is
# arguing every round about which dropped events happen to be harmless.
guard_tail_case dead 'a `do )` label above an `exit 1`: fail-CLOSED, and the price of the guarantee' \
  'if ! '"$HEX_GUARD"'; then' \
  '  case "$mode" in' \
  '    do )' \
  '      note=1' \
  '      ;;' \
  '  esac' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'

# ROUND 9. Rounds 7 and 8 asked only whether SOME `case` was open. An ordinary nested `case`
# written on one line -- `case "$b" in y)` -- matches the arm-label shape, so its `case` event
# was dropped, the guarded `fi` fired one level too shallow, the walk ran out of records with
# `in_then=0`, and this guard read as LIVE while its `then` arm falls through whenever `q`
# fails (#1464 re-review round 9). `bash -n` clean. `.github/workflows/gate-rearm.yml` writes
# that exact spelling, so it is repository content and not a contrived shape.
#
# The fix is arm POSITION -- `case … in` and `;;` are the only two places bash will parse a
# word list ending in `)` as a pattern -- plus the emit-nothing requirement above. Either one
# alone closes this vector; both are kept, because the first is what makes the exception
# accurate and the second is what makes the guarantee hold by construction.
# `;;` puts the walk back in arm position, so a SECOND arm is still read. Without that, only
# the first label of each `case` is recognized and every later one declines -- fail-closed,
# but it silently retires the shape `scripts/privileged-merge-conformance.sh:336` writes.
guard_tail_case live 'a two-arm `case` above a terminating statement of the arm' \
  'if ! '"$HEX_GUARD"'; then' \
  '  case "$mode" in' \
  '    a)' \
  '      note=1' \
  '      ;;' \
  '    b)' \
  '      note=2' \
  '      ;;' \
  '  esac' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a nested `case … in PAT)` on ONE line, whose `case` event was dropped' \
  'if ! '"$HEX_GUARD"'; then' \
  '  if q; then' \
  '    case "$a" in' \
  '      x)' \
  '        case "$b" in y)' \
  '          note=1' \
  '          ;;' \
  '        esac' \
  '        ;;' \
  '    esac' \
  '    exit 1' \
  '  fi' \
  '  failures=$((failures + 1))' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# The same program with the nested `case` split over two lines. It was already REJECTed, and
# pinning it says the one-line spelling was accepted by the LABEL reading and by nothing else.
guard_tail_case dead 'the same program with the nested `case` split over two lines' \
  'if ! '"$HEX_GUARD"'; then' \
  '  if q; then' \
  '    case "$a" in' \
  '      x)' \
  '        case "$b" in' \
  '          y)' \
  '          note=1' \
  '          ;;' \
  '        esac' \
  '        ;;' \
  '    esac' \
  '    exit 1' \
  '  fi' \
  '  failures=$((failures + 1))' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# The control: a genuinely live guard whose `then` arm terminates below a nested `case` in the
# split spelling. Arm-position tracking must not cost this, or the fix is a rejection widening
# wearing a parser's clothes.
guard_tail_case live 'a terminating arm below a nested `case` in the split spelling' \
  'if ! '"$HEX_GUARD"'; then' \
  '  case "$a" in' \
  '    x)' \
  '      case "$b" in' \
  '        y)' \
  '          note=1' \
  '          ;;' \
  '      esac' \
  '      ;;' \
  '  esac' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# `esac` LEAVES arm position, and this is the input that distinguishes that assignment. An
# earlier revision of the header called it unpinnable, reasoning that a distinguishing record
# would have to be arm-label-SHAPED, legal directly after `esac`, and read as fatal by
# `arm_statement_is_fatal`. The third condition was the wrong one: `( probe )` is label-shaped
# and legal there, and `arm_statement_is_fatal` does NOT call it fatal, yet it distinguishes
# anyway -- because the mechanism is not the statement check. With `expecting_label=0` in
# place the record is not a label, so it falls to `arm_record_is_modelled`'s bare-parenthesis
# refusal and the walk REJECTs; without it the record goes INERT and is skipped, the `exit 1`
# below is read at relative depth 0, and the `fi` ACCEPTs. The guard really is live here --
# the `then` arm exits unconditionally -- so `dead` is a fail-CLOSED cost of the paren
# refusal, and it is the cost that makes the assignment testable (#1464 re-review round 10).
guard_tail_case dead 'a `( probe )` directly after `esac`, which is no longer a label' \
  'if ! '"$HEX_GUARD"'; then' \
  '  case "$x" in' \
  '    a) ;;' \
  '  esac' \
  '  ( probe )' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# The control: the same program with the paren record removed stays LIVE, so the `dead` above
# is caused by the record directly after `esac` and not by the `case` or the arm shape.
guard_tail_case live 'the same program without the record after `esac`' \
  'if ! '"$HEX_GUARD"'; then' \
  '  case "$x" in' \
  '    a) ;;' \
  '  esac' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
# Round 7 argued the label exception needed no statement rule because "`case_depth > 0`
# implies `d > 0`". This program falsifies that: `probe || { if q; then a; fi; }` emits a bare
# `fi` -- `branch_events` reads the `{`-part's first word as `{`, never as `if` -- so `d` drops
# to 0 with a `case` still open, and `( exit 1 )` is then read as an inert label at relative
# depth 0. The consequence is fail-CLOSED, so this fixture pins the outcome and the deleted
# claim pins the reasoning: inertness grants nothing at ANY depth, which is why no depth
# invariant is needed (#1464 re-review round 9).
# Inertness has two halves, and until round 9 only the event half was pinned: with the
# emit-nothing requirement in place, deleting the skip ENTIRELY left every other assertion in
# this file green. It is load-bearing because a label is otherwise also read as a STATEMENT,
# and `fail )` is a legal arm label that `arm_statement_is_fatal` calls terminating. The two
# bare `fi`s below are round 9's blocker-2 fact put to work: each `{ if q; then y; fi; }`
# emits `fi` and no `if`, so `d` reaches 0 with the `case` still open, the `fail )` label is
# read at relative depth 0, and the third one returns at `fi` with `arm_terminates=0`. This
# guard's `then` arm only counts a failure and falls through, so that ACCEPT is a fail-open.
guard_tail_case dead 'a `fail )` arm label read as a terminating statement of the arm' \
  'if ! '"$HEX_GUARD"'; then' \
  '  if r; then' \
  '    case "$x" in' \
  '      a)' \
  '        probe || { if q; then y; fi; }' \
  '        probe || { if q; then y; fi; }' \
  '        ;;' \
  '      fail )' \
  '        probe || { if q; then y; fi; }' \
  '        ;;' \
  '    esac' \
  '  fi' \
  '  failures=$((failures + 1))' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a bare `fi` that drops `d` to 0 with a `case` still open' \
  'if ! '"$HEX_GUARD"'; then' \
  '  case "$mode" in' \
  '    a)' \
  '      probe || { if q; then a; fi; }' \
  '      ( exit 1 )' \
  '      ;;' \
  '  esac' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'

# ROUND 10, and one level below every round from 5 to 9. Those all patched the label and
# arm-position logic that READS the event stream; the stream itself under-counted openers.
# `branch_events` took each part's FIRST word, and a closer is always first-word -- bash
# requires `;` or a newline before `fi`/`done`/`esac` -- while an opener is not, because
# another token can open the part ahead of it. So the loss was one-directional, net-negative,
# and pointed in exactly the direction the header names as the only way to fail open.
#
# The stream is pinned directly first. Testing only the programs below would leave a future
# edit free to restore the asymmetry anywhere the fixtures happen not to reach, which is what
# let the same defect survive five rounds of fixtures layered on top of it.
branch_events_case() { # $1 = label, $2 = one record, $3 = its expected event stream
  local got
  got="$(branch_events "$(shell_structure "$2")" | tr '\n' ' ')"
  got="${got% }"
  [ "$got" = "$3" ] || fail "branch_events read '$2' as [$got], not [$3]: $1"
  bash -n <<<"$2" 2>/dev/null || fail "the pinned record is not legal bash: $2"
}
# The openers a first-word reading loses, one per token that can open a part ahead of them.
# THE CLOSURE ARGUMENT, MADE CHECKABLE. Every round from 5 to 10 ended with prose asserting
# its set was closed, and every one of those was falsified by the next round -- round 10's
# "read off that grammar rather than collected from counterexamples" missed `coproc`'s NAME.
# Prose cannot carry this claim, so the claim is an assertion instead: bash publishes its own
# reserved words as `compgen -k`, and the four buckets must partition exactly that set. A
# word bash adds, or one an edit drops from a bucket, reddens HERE rather than silently
# rejoining a permissive default -- which is the whole difference between this round and the
# ten before it. What it does NOT prove is that each word is in the RIGHT bucket; that is
# what the individual cases below are for. It proves only that none is missing.
branch_events_classifies_every_reserved_word() {
  local -a classified=("${BRANCH_RESTART_WORDS[@]}" "${BRANCH_PATTERN_WORDS[@]}" \
    "${BRANCH_STOP_WORDS[@]}" "${BRANCH_DECLINE_WORDS[@]}")
  local word seen=''
  # Disjoint: a word in two buckets means one of them is not doing what it says.
  for word in "${classified[@]}"; do
    case " $seen " in *" $word "*) fail "reserved word '$word' is in two branch_events buckets" ;; esac
    seen="$seen $word"
  done
  # Total: every reserved word bash knows is classified.
  while IFS= read -r word; do
    case " $seen " in
      *" $word "*) ;;
      *) fail "bash reserved word '$word' has no branch_events classification; command position after it is unmodelled" ;;
    esac
  done < <(compgen -k)
  # Nothing invented: a bucket entry that is not a reserved word would be dead weight the
  # totality check above cannot see.
  local known; known="$(compgen -k | tr '\n' ' ')"
  for word in "${classified[@]}"; do
    case " $known " in
      *" $word "*) ;;
      *) fail "'$word' is classified by branch_events but is not a bash reserved word" ;;
    esac
  done
}
branch_events_classifies_every_reserved_word

branch_events_case 'a `{` group opening the part' \
  'probe || { if q; then a; fi; }' 'if fi'
branch_events_case 'a `then` opening the part' \
  'if a; then if b; then c; fi; fi' 'if if fi fi'
branch_events_case 'an `if` introducing a list, opening the part for a second `if`' \
  'if if a; then b; fi; then c; fi' 'if if fi fi'
branch_events_case 'a `time` prefix opening the part' \
  'time if q; then a; fi' 'if fi'
branch_events_case 'a `time -p --` option run' \
  'time -p -- if q; then a; fi' 'if fi'
branch_events_case 'a `!` pipeline negation' \
  '! if q; then a; fi' 'if fi'
branch_events_case 'a `(` glued to the word it puts in command position' \
  '(if q; then a; fi)' 'if fi'
branch_events_case 'an `else` opening the part' \
  'if a; then b; else if c; then d; fi; fi' 'if else if fi fi'
branch_events_case 'a `while` introducing a list, with `do` still the counted token' \
  'while if q; then a; fi; do b; done' 'if fi do done'
branch_events_case 'an `until` introducing a list' \
  'until if q; then a; fi; do b; done' 'if fi do done'
branch_events_case 'a `do` opening the part' \
  'for x in 1; do if q; then a; fi; done' 'do if fi done'
branch_events_case 'an `elif` introducing a list' \
  'if a; then b; elif if c; then d; fi; then e; fi' 'if elif if fi fi'
branch_events_case 'a `case` arm label putting its body in command position' \
  'case $x in a) if q; then b; fi ;; esac' 'case if fi esac'
# `coproc` was pinned here as a RESTART, asserting that the `if` after an unnamed `coproc`
# is counted. That assertion is DELETED, not narrowed, and what it proved is gone with it:
# the walk no longer counts openers inside a `coproc`'s command at all. The loss is
# one-directional -- a `coproc` record now REJECTS the walk instead of being modelled -- so
# it costs reach, never safety, and it buys the `coproc NAME` spelling the old rule could
# not see. Both spellings are pinned now, because pinning only one is what let round 10's
# closure argument stand.
branch_events_case 'an unnamed `coproc`, declined rather than modelled' \
  'coproc if q; then a; fi' 'decline:coproc fi'
branch_events_case 'a `coproc NAME`, whose NAME is not a command' \
  'coproc c if q; then a; fi' 'decline:coproc fi'
# ... and the words after which a reserved word is NOT one, so the scan must stop. Each of
# these is legal bash in which `if` is an ordinary NAME or pattern, so emitting it would be
# a spurious OPENER -- the fail-CLOSED direction, but a false alarm all the same.
# Arithmetic, whose contents are expressions and not commands. The old walk STRIPPED the
# grouping punctuation to "judge the word", which manufactured a command-position word out
# of the expression: `(( fi ))` emitted a bare `fi` -- a SPURIOUS CLOSER, which fires the
# guarded return one level too shallow. Nothing in the stream caught it; only
# `arm_record_is_modelled`'s parenthesis filter did, one layer up. Counting the punctuation
# instead of stripping it declines these without a rule naming `fi`, `done` or `esac`, which
# is the test that the inversion did the work rather than a fourth special case.
for arith_closer in 'if (( fi > 0 )); then a; fi' '(( done ))' '(( esac ))'; do
  bash -n <<<"$arith_closer" \
    || fail "the arithmetic spurious-closer vector is not legal bash: $arith_closer"
done
branch_events_case 'an arithmetic command holding a closer word' \
  'if (( fi > 0 )); then a; fi' 'if decline:(( fi'
branch_events_case 'a bare arithmetic `done`' '(( done ))' 'decline:(('
branch_events_case 'a bare arithmetic `esac`' '(( esac ))' 'decline:(('
branch_events_case 'a single-paren subshell, still modelled' '( if q; then a; fi )' 'if fi'

branch_events_case 'a `case` subject word, not a command' 'case if in a) ;; esac' 'case esac'
branch_events_case 'a `for` loop variable named `if`' 'for if in 1; do :; done' 'do done'
branch_events_case 'a `select` loop variable named `if`' 'select if in 1; do :; done' 'do done'
branch_events_case 'a function named `if`' 'function if { :; }' ''
branch_events_case 'a reserved word as an ARGUMENT' 'echo if a then b fi' ''
branch_events_case 'a reserved word inside a `[` test' '[ "$x" = if ] || exit 1' ''
# `X=1 if …` and `>f if …` are both syntax errors, so a reserved word can never sit in
# command position after an assignment or a redirection prefix. Pinned as the reason those
# two are absent from the restart set rather than forgotten from it.
for prefix_not_a_restart in 'X=1 if a; then b; fi' '>/dev/null if a; then b; fi'; do
  bash -n <<<"$prefix_not_a_restart" 2>/dev/null \
    && fail "an assignment or redirection prefix now accepts a reserved word: $prefix_not_a_restart"
  branch_events_case 'no restart after a prefix that cannot precede a reserved word' \
    "${prefix_not_a_restart%%;*}" ''
done

# The programs the lost opener let through. Each loses ONE level of relative depth on the
# marked record, so the `exit 1` nested inside `if outer` is read at relative depth 0, sets
# `arm_terminates=0`, and the construct's real `fi` returns it -- an ACCEPT for a `then` arm
# that falls straight through to the warning below it and on past `fi` to the use. This is
# round 5's vector reopened through a different door, and all three were `bash -n` clean and
# `arm_record_is_modelled`-clean while they walked past.
#
# Each is paired with a CONTROL that keeps the same construct in a genuinely terminating arm
# alive, so counting more openers cannot be mistaken for a rejection widening.
guard_tail_case dead 'a `{` group whose `if` was lost, dropping one level of depth' \
  'if ! '"$HEX_GUARD"'; then' \
  '  if outer; then' \
  '    probe || { if q; then a; fi; }' \
  '    exit 1' \
  '  fi' \
  '  echo "::warning::head sha looks wrong"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case live 'the same `{` group above an `exit 1` at the arm top level' \
  'if ! '"$HEX_GUARD"'; then' \
  '  probe || { if q; then a; fi; }' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a one-line nested `if` whose second `if` was lost' \
  'if ! '"$HEX_GUARD"'; then' \
  '  if outer; then' \
  '    if a; then if b; then c; fi; fi' \
  '    exit 1' \
  '  fi' \
  '  echo "::warning::head sha looks wrong"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case live 'the same one-line nested `if` above an `exit 1` at the arm top level' \
  'if ! '"$HEX_GUARD"'; then' \
  '  if a; then if b; then c; fi; fi' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case dead 'a `time` prefix whose `if` was lost' \
  'if ! '"$HEX_GUARD"'; then' \
  '  if outer; then' \
  '    time if q; then a; fi' \
  '    exit 1' \
  '  fi' \
  '  echo "::warning::head sha looks wrong"' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'
guard_tail_case live 'the same `time` prefix above an `exit 1` at the arm top level' \
  'if ! '"$HEX_GUARD"'; then' \
  '  time if q; then a; fi' \
  '  exit 1' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'

# ROUND 11. Round 10 said its restart set was "closed because it is read off that grammar
# rather than collected from counterexamples". It was not: `coproc` is in the set, but
# `coproc`'s OPTIONAL NAME is not a command, so the scan reads `coproc` (restart), reads the
# name as an ordinary command word, and stops. Every later command-position word of that
# part is then invisible -- the exact net-negative direction round 10 claimed to have closed.
# `coproc c if q; then` emits nothing where `coproc if q; then` emits `if`, and only the
# unnamed spelling was pinned. `bash -n` clean, and clean through every existing filter:
# no unpaired paren, no bare `&`, no unbalanced brace, no here-document.
guard_tail_case dead 'a `coproc NAME` whose following `if` was lost entirely' \
  'if ! '"$HEX_GUARD"'; then' \
  '  coproc c if q; then' \
  '    exit 1' \
  '  fi' \
  'fi' \
  'gh api "repos/$repository/commits/$head_sha"'

guard_tail_case dead 'a POSITIVE `if` condition, whose failing arm is the unguarded one' \
  'if '"$HEX_GUARD"'; then' \
  '  gh api "repos/$repository/commits/$head_sha"' \
  'fi'

guard_tail_case dead 'an && chain with no || at all: set -e exempts the failing element' \
  "$HEX_GUARD"' && echo ok'
guard_tail_case dead 'an unlisted helper name, which nothing here proves terminates' \
  "$HEX_GUARD"' || notice "head sha looks wrong"'
guard_tail_case dead 'a `|| { … }` branch whose body never leaves' \
  "$HEX_GUARD"' || { echo "::warning::head sha looks wrong"; }'

# The allow-list's SECOND fail-open, and the reason the brace branch is no longer a
# substring match over the body (third #1464 re-review). Every tail below matched
# `[{][^}]*[^A-Za-z_](exit|return)[[:space:]]+[1-9][0-9]*…[}]` and every one of them
# leaves the guard standing. An allow-list that does not reason about command position is
# a denylist wearing an allow-list's clothes, so these are pinned permanently.
guard_tail_case dead 'a `exit 1` that is only ever echoed, never run' \
  "$HEX_GUARD"' || { echo "would exit 1 here"; }'
guard_tail_case dead 'an `exit 1` that leaves a subshell and nothing else' \
  "$HEX_GUARD"' || { ( exit 1 ); }'
guard_tail_case dead 'an `exit 1` that is unreachable behind a false `&&`' \
  "$HEX_GUARD"' || { false && exit 1; }'
# Two fail-closed false negatives fixed in the same pass (#1464 re-review round 4): a
# zero-padded status, which bash reports unpadded, and a named terminating helper reached as
# a statement of a brace body rather than as the whole branch.
guard_tail_case live 'an `|| exit 01`, which bash exits 1 from' \
  "$HEX_GUARD"' || exit 01'
guard_tail_case live 'a named terminating helper inside a `|| { … }` body' \
  "$HEX_GUARD"' || { echo "::error::bad pin"; fail "head sha is not a 40-hex object name"; }'
guard_tail_case dead 'an `|| exit 0256`, which still wraps to a 0 wait status' \
  "$HEX_GUARD"' || exit 0256'
guard_tail_case dead 'an `|| exit 256`, which wraps to a 0 wait status' \
  "$HEX_GUARD"' || exit 256'
guard_tail_case dead 'an `|| return 256`, which wraps to a 0 wait status' \
  "$HEX_GUARD"' || return 256'
# `continue`/`break` are not on the allow-list at all, in or out of a loop. They were, and
# the loop-depth lexer that made them safe outside one was ~60 lines whose only remaining
# consumer was its own fixture: main's #1466 rewrote `privileged-merge-conformance.sh:314`,
# the one real site, into an `elif`. Forcing that lexer to report depth 0 everywhere on the
# merged tree failed nothing but those fixtures. Deleting it also deletes the fail-open it
# carried in `slice` mode, where a lone `do` -- in prose, or in a here-document body --
# licensed `continue`/`break` for everything after it in the slice (#1464 re-review round 4).
guard_tail_case dead 'an `|| continue`, whose enclosing loop this anchor does not model' \
  'while IFS= read -r repository; do' \
  '  '"$HEX_GUARD"' || { echo "::error::bad pin"; continue; }' \
  '  gh api "repos/$repository/commits/$head_sha"' \
  'done < <(printf "")'
guard_tail_case dead 'an `|| break`, for the same reason' \
  'for repository in a b; do' \
  '  '"$HEX_GUARD"' || break' \
  'done'
guard_tail_case dead 'an `exit 1` that is only ever printed by a here-document' \
  "$HEX_GUARD"' || {' \
  '  cat <<EOF' \
  'exit 1' \
  'EOF' \
  '}'
guard_tail_case dead 'a nested group in the brace body, which this anchor does not flatten' \
  "$HEX_GUARD"' || {' \
  '  handler() {' \
  '    :' \
  '  }' \
  '  exit 1' \
  '}'

# `logical_lines` closes a `|| {` branch by brace DEPTH. Closing on a bare `}` line alone
# left a closer carrying a tail (`} >&2`, `} || true`) open forever -- buffering the whole
# rest of the file into one logical line -- and let a nested `}` close the body early.
# Both are fail-CLOSED, so neither was a bypass; both turn an unrelated edit anywhere
# above a guard into an inscrutable false positive (#1464 re-review).
guard_tail_case live 'a `} >&2` closer above the guard, which must not swallow the file' \
  'maybe || {' \
  '  echo x' \
  '} >&2' \
  "$HEX_GUARD"' || exit 1' \
  'echo later'
guard_tail_case live 'a nested `}` above the guard, which must not close the branch early' \
  'maybe || {' \
  '  handler() {' \
  '    :' \
  '  }' \
  '  echo x' \
  '}' \
  "$HEX_GUARD"' || exit 1'
# A trailing `\` inside a COMMENT is not a continuation: joining it prefixed the next
# line with `#` and read a live guard as commented out (#1464 re-review).
guard_tail_case live 'a comment ending in a backslash above the guard' \
  '# the head sha is pinned below \' \
  "$HEX_GUARD"' || exit 1'

# An input that ends inside a `|| {` branch drops the buffer rather than emitting it as a
# joined line whose "tail" is the rest of the file. Fail-closed, and in `whole` mode it
# says so on stderr instead of reporting a silent green.
printf '%s\n' "$HEX_GUARD"' || exit 1' 'maybe || {' '  echo x' >"$tmp/fx/guard.sh"
guard_is_live "$tmp/fx/guard.sh" "$HEX_GUARD" 2>/dev/null \
  || fail "a live guard above an unterminated branch was not judged"
printf '%s\n' 'maybe || {' '  echo x' "$HEX_GUARD"' || exit 1' >"$tmp/fx/guard.sh"
guard_is_live "$tmp/fx/guard.sh" "$HEX_GUARD" 2>/dev/null \
  && fail "a guard inside an unterminated branch was judged live"
grep -q 'unterminated' < <(guard_is_live "$tmp/fx/guard.sh" "$HEX_GUARD" 2>&1 >/dev/null) \
  || fail "an unterminated branch was swallowed without saying so"


# A command whose own non-zero status ends the step needs no continuation at all; that is
# how the repo-hygiene entry is written. Swallowing it still has to redden.
BARE_GUARD='resolved="$(git -C .repo-hygiene rev-parse HEAD)"'
printf '%s\n' "$BARE_GUARD" >"$tmp/fx/guard.sh"
guard_is_live "$tmp/fx/guard.sh" "$BARE_GUARD" \
  || fail "the cited-guard anchor rejected a bare command whose own status ends the step"
printf '%s\n' "$BARE_GUARD"' || true' >"$tmp/fx/guard.sh"
guard_is_live "$tmp/fx/guard.sh" "$BARE_GUARD" \
  && fail "the cited-guard anchor accepted a bare command whose failure is swallowed"

# --------------------------------------------------------------------------------------
# 2. Semantics: evaluate every encoder expression this repository actually ships.
# --------------------------------------------------------------------------------------
# $1 = block slice, $2 = variable -> "<jq --arg name><TAB><jq program>" for its @uri
# assignment. The `--arg` name is captured rather than assumed: this repository spells it
# `branch`, `value` and `r`, and a program is only evaluable with the name it actually binds.
encoder_program() {
  sed -nE "s/^[[:space:]]*$2=\"\\\$\(jq -rn --arg ([A-Za-z_][A-Za-z0-9_]*) \"[^\"]*\" '(.*)'\)\"\$/\1\t\2/p" <<<"$1" \
    | grep '@uri' || true
}

shell_assignment_count() { # $1 = shell variable, $2 = source slice -> assignment count
  local variable="$1" source="$2"
  [[ "$variable" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return 2
  sed '/^[[:space:]]*#/d' <<<"$source" \
    | grep -oE "(^|[[:space:];|&])${variable}[[:space:]]*(\\+?=)" \
    | wc -l
}

ref_locked_after_encoding() {
  awk '
    /^[[:space:]]*base_ref_path=/ && /@uri/ {
      if (getline next_line > 0 && next_line ~ /^[[:space:]]*readonly[[:space:]]+base_ref_path[[:space:]]*$/) {
        found = 1
      } else {
        exit 1
      }
    }
    END { exit !found }
  ' <<<"$1"
}

# Keep the ref gate sensitive to the direct, conditional, and AND-list clobbers observed
# in #1508. Full-line comments do not count as executable assignments.
encoded_assignment='base_ref_path="encoded"'
comment_fixture="$(printf '%s\n' "$encoded_assignment" '# base_ref_path=comment')"
[ "$(shell_assignment_count base_ref_path "$comment_fixture")" -eq 1 ] \
  || fail "a comment was counted as a shell assignment"
ref_locked_after_encoding $'base_ref_path="encoded @uri"\nreadonly base_ref_path' \
  || fail "the encoded ref is not locked immediately after assignment"
if ref_locked_after_encoding $'base_ref_path="encoded @uri"\nread base_ref_path\nreadonly base_ref_path'; then
  fail "a command can run between encoding the ref and locking it"
fi
for clobber in \
  'base_ref_path="$base_ref"' \
  'if [ -z "$base_ref_path" ]; then base_ref_path="$base_ref"; fi' \
  '[ -z "$base_ref_path" ] && base_ref_path="$base_ref"'; do
  assignments="$(shell_assignment_count base_ref_path "$(printf '%s\n' "$encoded_assignment" "$clobber")")"
  [ "$assignments" -eq 2 ] \
    || fail "the ref-assignment scan missed a clobber form: $clobber"
done

# A Python encoder proves itself the same way a jq program does: the call this repository
# actually ships is evaluated, with only its first argument replaced by the fixture. Nothing
# here pattern-matches `safe=`, so a novel spelling is judged by what it produces.
cat >"$tmp/py-encoder.py" <<'PYSRC'
import ast
import sys
import urllib.parse

expression, value = sys.argv[1], sys.argv[2]
call = ast.parse(expression, mode="eval").body
if not isinstance(call, ast.Call) or not call.args:
    raise SystemExit(f"not a percent-encoding call: {expression}")
name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
if name != "quote":
    raise SystemExit(f"not a percent-encoding call: {expression}")
call.args[0] = ast.Constant(value)
namespace = {"urllib": urllib, "quote": urllib.parse.quote}
print(eval(compile(ast.fix_missing_locations(ast.Expression(call)), "<encoder>", "eval"), namespace))
PYSRC

py_encoder_expression() { # $1 = block slice, $2 = interpolated expression -> its quote(…) call
  local slice="$1" subject="$2"
  if [[ "$subject" == *quote\(* ]]; then printf '%s\n' "$subject"; return 0; fi
  [[ "$subject" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return 0
  sed -nE "s/^[[:space:]]*${subject}[[:space:]]*=[[:space:]]*((urllib\.parse\.)?quote\(.*\))[[:space:]]*\$/\1/p" \
    <<<"$slice" | tail -1
}

assert_py_encoder() { # $1 = label, $2 = python expression, $3 = query|path
  local label="$1" expression="$2" mode="$3" got
  got="$(python3 "$tmp/py-encoder.py" "$expression" "$HOSTILE_BRANCH")" \
    || fail "$label does not evaluate as a percent-encoding call: $expression"
  [ "$got" = "$HOSTILE_ENCODED" ] \
    || fail "$label encodes '$HOSTILE_BRANCH' as '$got', expected '$HOSTILE_ENCODED'"
  got="$(python3 "$tmp/py-encoder.py" "$expression" "$NESTED_BRANCH")" \
    || fail "$label does not evaluate as a percent-encoding call: $expression"
  case "$mode" in
    query)
      [ "$got" = "$NESTED_BRANCH" ] \
        || fail "$label builds a query value and must keep '/' literal, got '$got'" ;;
    path)
      [ "$got" = "$NESTED_PATH_ENCODED" ] \
        || fail "$label builds a path segment and must encode '/' as %2F, got '$got'" ;;
  esac
}

# The Python counterpart of the shell `=~ ^[0-9a-f]{40}$` guard, in the two spellings this
# repository uses. A module-level SHA_PATTERN must itself be the 40-hex pattern.
py_hex_constrained() { # $1 = file, $2 = block slice, $3 = identifier
  local file="$1" slice="$2" var="$3" hex='\[0-9a-f\]\{40\}'
  # A dotted attribute path (`args.deployment_commit`) is as matchable as a bare name: the
  # grep below is a literal comparison against the text the file actually ships.
  [[ "$var" =~ ^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$ ]] || return 1
  grep -qE "re\.fullmatch\(r?['\"][^,]*${hex}[^,]*['\"],[[:space:]]*${var}([^A-Za-z0-9_]|$)" \
    <<<"$slice" && return 0
  grep -qE "SHA_PATTERN\.fullmatch\(${var}([^A-Za-z0-9_]|$)" <<<"$slice" || return 1
  grep -qE "^SHA_PATTERN = re\.compile\(r['\"]${hex}['\"]\)$" "$root/$file"
}

assert_encoder() { # $1 = label, $2 = jq program, $3 = query|path, $4 = jq --arg name
  local label="$1" program="$2" mode="$3" arg="$4" got
  got="$(jq -rn --arg "$arg" "$HOSTILE_BRANCH" "$program")"
  [ "$got" = "$HOSTILE_ENCODED" ] \
    || fail "$label encodes '$HOSTILE_BRANCH' as '$got', expected '$HOSTILE_ENCODED'"
  got="$(jq -rn --arg "$arg" "$NESTED_BRANCH" "$program")"
  case "$mode" in
    query)
      [ "$got" = "$NESTED_BRANCH" ] \
        || fail "$label builds a query value and must keep '/' literal, got '$got'" ;;
    path)
      [ "$got" = "$NESTED_PATH_ENCODED" ] \
        || fail "$label builds a path segment and must encode '/' as %2F, got '$got'" ;;
  esac
}

# --------------------------------------------------------------------------------------
# 3. Coverage: the allowlist. Each site proves its value safe, or this test fails.
#
# A site whose proof this scan cannot see at the point of use is named here, once, with the
# reason it is safe anyway. This is a record, not an escape hatch: an entry is keyed by file
# and interpolated subject, every entry must match at least one live site (a stale one fails
# below), and a NEW site that is not listed still fails.
#
# An entry is four tab-separated fields:
#
#     file <TAB> subject <TAB> guard-file <TAB> guard-text
#
# and the last two are the part that keeps the record honest. An entry that says "another
# step already constrains this" is a claim about code somewhere else, and that code can be
# edited away while this gate keeps printing PASS -- deleting the 40-hex assert from
# gate-rearm.yml, or the `SHA.fullmatch` from container_deployment_transport.py, used to
# leave this test green. So an entry that cites a guard PINS it: the literal guard text is
# grepped for below, in the idiom scripts/gen-container-deployment.sh uses for its own
# pinned contract text, and removing the guard reddens THIS gate.
#
# Three reason classes appear here, and nothing else should be added without one:
#   sha-guarded  -- the value is a 40-hex object name whose constraint is established in
#                   another step, another function, or at the caller, outside this scan's
#                   block slice. Cites and pins that constraint.
#   sha-supplied -- the value is an object name supplied by GitHub itself, so there is no
#                   repository-local guard to pin; inventing one would be ceremony. Only a
#                   value that never passes through adopter text belongs here.
#   ref          -- the value is a ref name carrying a stated non-encoding guard at its
#                   source.
# --------------------------------------------------------------------------------------
REF_SITE_ALLOWLIST=(
  # sha-supplied: `.headRefOid` from `gh pr view` on the PR this run is gating -- GitHub
  # answers a 40-hex OID or the `// ""` default, and no adopter text reaches it. Nothing
  # repository-local constrains it, so there is nothing to pin: it is here for the same
  # reason node-ci's `github.sha` would be, not because a check lives elsewhere.
  $'.github/workflows/ai-review-merge.yml\thead_sha\t\t'
  # sha-guarded: same value, but gate-rearm asserts the pattern itself, in the job that
  # resolves it -- a different step from the read this scan is looking at.
  $'.github/workflows/gate-rearm.yml\thead_sha\t.github/workflows/gate-rearm.yml\t[[ "$head_sha" =~ ^[0-9a-f]{40}$ ]]'
  # sha-guarded: `git rev-parse HEAD` in the checkout this step just made; git answers a
  # 40-hex object name or fails, and the step runs under `set -euo pipefail`.
  $'.github/workflows/repo-hygiene.yml\tresolved\t.github/workflows/repo-hygiene.yml\tresolved="$(git -C .repo-hygiene rev-parse HEAD)"'
  # sha-guarded: validated via `validate_workflow_identity`, whose `re.fullmatch` on the
  # 40-hex pattern lives in that function rather than in `build_receipt`'s own block.
  $'scripts/container_deployment_review_producer.py\targs.producer_commit\tscripts/container_deployment_review_producer.py\tre.fullmatch(r"[0-9a-f]{40}", contract_ref) is None'
  # sha-guarded: `SHA.fullmatch(target['sha'])` on the line above, spelled through
  # `.get()` at the use. Same value, different expression, so the literal match cannot
  # join them up.
  $'scripts/container_deployment_transport.py\ttarget.get(\'sha\')\tscripts/container_deployment_transport.py\tSHA.fullmatch(target[\'sha\'])'
  # sha-guarded: the same pattern inside the probe validator, a different function from
  # the transport call.
  $'scripts/container_deployment_transport.py\tprobe[\'workflowCommit\']\tscripts/container_deployment_transport.py\tSHA.fullmatch(probe[\'workflowCommit\'])'
  # sha-guarded: `hub_tree`'s own parameter. Its two callers pass the literal "main" and a
  # value USES_RE extracted under a 40-hex pattern, so the constraint is at the caller.
  $'scripts/fleet-contract-inventory.py\tref\tscripts/fleet-contract-inventory.py\t(?P<sha>[0-9a-f]{40})'
  # sha-guarded: `require_sha` is the 40-hex boundary check for this value, but it is a
  # call rather than an inline pattern, so the scan cannot read it as one.
  $'scripts/renovate-changelog.py\thead_sha\tscripts/renovate-changelog.py\trequire_sha(planned["head_sha"], "plan.head_sha")'
  # `rules/branches/` was settled on 2026-09-26 (#1470): measured against a temporary
  # ruleset targeting `release/2026.09` in Verjson/demo-repository, the endpoint resolves
  # the same rules for `release%2F2026.09` and `release/2026.09`, so the path form is the
  # strictly safer spelling. assert-mergeable-head.sh and verify-arm-receipt.sh now
  # encode their base ref with `@uri` like every other rules/branches/ reader, and the
  # scan below pins that: no allowlist entry remains for either site.
)

allowlisted_hits=()
ref_site_allowlisted() { # $1 = file, $2 = subject
  local entry entry_file entry_subject
  for entry in "${REF_SITE_ALLOWLIST[@]}"; do
    IFS=$'\t' read -r entry_file entry_subject _ _ <<<"$entry"
    [ "$entry_file" = "$1" ] && [ "$entry_subject" = "$2" ] || continue
    allowlisted_hits+=("$entry")
    return 0
  done
  return 1
}
sites=0
encoders=0
py_sites=0
py_encoders=0
while IFS=$'\t' read -r file line kind var syntax; do
  sites=$((sites + 1))
  slice="$(block_slice "$file" "$line")"
  if [ "$syntax" = pyexpr ]; then
    py_sites=$((py_sites + 1))
    label="$file:$line {$var}"
    py_quote_unshadowed "$file" \
      || fail "$file re-binds 'quote' at module scope; its encoder calls cannot be judged by the stdlib one"
    expression="$(py_encoder_expression "$slice" "$var")"
    if [ -n "$expression" ]; then
      py_encoders=$((py_encoders + 1))
      assert_py_encoder "$label" "$expression" "$kind"
      continue
    fi
    py_hex_constrained "$file" "$slice" "$var" && continue
    ref_site_allowlisted "$file" "$var" && continue
    fail "$label reaches a gh api $kind position without a percent-encoding or a 40-hex constraint in its block"
  fi
  label="$file:$line \$$var"
  jq_arg=""; program=""
  IFS=$'\t' read -r jq_arg program < <(encoder_program "$slice" "$var") || true
  if [ -n "${program:-}" ]; then
    encoders=$((encoders + 1))
    if [ "$file" = ".github/workflows/ai-review-merge.yml" ] && [ "$var" = "base_ref_path" ]; then
      assignments="$(shell_assignment_count "$var" "$slice")"
      [ "$assignments" -eq 1 ] \
        || fail "$label is assigned $assignments times before its URL use; require one encoded assignment"
      ref_locked_after_encoding "$slice" \
        || fail "$label is not made readonly immediately after encoding"
    fi
    assert_encoder "$label" "$program" "$kind" "$jq_arg"
    continue
  fi
  sha_constrained "$var" "$slice" "$file" && continue
  ref_site_allowlisted "$file" "$var" && continue
  fail "$label reaches a gh api $kind position without a percent-encoding or a 40-hex constraint in its block"
done < <(ref_sites)

# Exercise the actual workflow function with the repository's `gh` stub. This checks the
# value sent to the API, not just a matching encoder line in the source slice.
compare_workflow=".github/workflows/ai-review-merge.yml"
cat >"$tmp/duplicate-compare-functions.yml" <<'EOF'
  compare_behind() {
    raw="$(gh api "repos/$REPO/compare/$base_ref_path...$head_sha")"
  }
  compare_behind() {
    raw="$(gh api \
      "repos/$REPO/compare/$base_ref...$head_sha")"
  }
  behind="$(compare_behind)" && break
EOF
if unique_compare_definition_line "$tmp/duplicate-compare-functions.yml" >/dev/null; then
  fail "duplicate compare_behind fixture was accepted"
fi
printf '%s\n' 'compare_behind() { :; }; compare_behind() { :; }' \
  >"$tmp/same-line-compare-functions.yml"
if unique_line_number 'compare_behind() {' "$tmp/same-line-compare-functions.yml" >/dev/null; then
  fail "same-line duplicate compare_behind fixture was accepted"
fi
cat >"$tmp/alternate-compare-function.yml" <<'EOF'
  compare_behind() {
    :
  }
  function compare_behind {
    raw="$(gh api \
      "repos/$REPO/compare/$base_ref...$head_sha")"
  }
  behind="$(compare_behind)" && break
EOF
if unique_compare_definition_line "$tmp/alternate-compare-function.yml" >/dev/null; then
  fail "the Bash function-keyword duplicate fixture was accepted"
fi
cat >"$tmp/continued-compare-function.yml" <<'EOF'
  compare_behind() {
    :
  }
  function compare_\
behind {
    raw="$(gh api \
      "repos/$REPO/compare/$base_ref...$head_sha")"
  }
  behind="$(compare_behind)" && break
EOF
bash -n "$tmp/continued-compare-function.yml" \
  || fail "the line-continuation duplicate fixture was not valid Bash"
if unique_compare_definition_line "$tmp/continued-compare-function.yml" >/dev/null; then
  fail "the line-continuation duplicate compare_behind fixture was accepted"
fi

workflow="$root/$compare_workflow"
compare_function_line="$(unique_compare_definition_line "$workflow")" \
  || fail "expected exactly one compare_behind function in ai-review-merge.yml"
compare_url_line="$(unique_line_number 'raw="$(gh api "repos/$REPO/compare/$base_ref_path...$head_sha"' "$workflow")" \
  || fail "expected exactly one compare API call in ai-review-merge.yml"
compare_call_line="$(unique_line_number 'behind="$(compare_behind)" && break' "$workflow")" \
  || fail "expected exactly one production compare_behind call in ai-review-merge.yml"
[ "$compare_function_line" -lt "$compare_url_line" ] \
  && [ "$compare_url_line" -lt "$compare_call_line" ] \
  || fail "the compare function, API call, and production invocation are out of order"
block_slice "$compare_workflow" "$compare_url_line" >"$tmp/compare-behind.sh"
compare_slice_header="$(sed -n '1s/^[[:space:]]*//p' "$tmp/compare-behind.sh")"
[ "$compare_slice_header" = 'compare_behind() {' ] \
  || fail "the runtime test did not extract the unique production compare_behind function"
printf '}\n' >>"$tmp/compare-behind.sh"
bash -n "$tmp/compare-behind.sh" || fail "the extracted compare_behind function is not valid Bash"
# shellcheck source=/dev/null
source "$tmp/compare-behind.sh"

: >"$GH_CALLS"
REPO="$TARGET_REPO" base_ref="$HOSTILE_BRANCH" head_sha="$BLOB_SHA" GH_STUB_STDOUT=0 \
  compare_behind >/dev/null || fail "compare_behind failed for the hostile ref fixture"
grep -qF "compare/$HOSTILE_ENCODED...$BLOB_SHA" "$GH_CALLS" \
  || fail "the actual compare API call did not use the encoded ref: $(cat "$GH_CALLS")"
! grep -qF "compare/$HOSTILE_BRANCH...$BLOB_SHA" "$GH_CALLS" \
  || fail "the actual compare API call used the unencoded ref"

: >"$GH_CALLS"
REPO="$TARGET_REPO" base_ref="$NESTED_BRANCH" head_sha="$BLOB_SHA" GH_STUB_STDOUT=0 \
  compare_behind >/dev/null || fail "compare_behind failed for the slash-bearing ref fixture"
grep -qF "compare/$NESTED_PATH_ENCODED...$BLOB_SHA" "$GH_CALLS" \
  || fail "the actual compare API call did not encode a slash-bearing ref: $(cat "$GH_CALLS")"

# The compare_behind ref segment is single-assignment and readonly after encoding. The
# exact function slice is checked at the URL use, so a later direct or guarded assignment
# cannot replace the encoded value while the gate remains green. The readonly declaration
# also prevents indirect shell writes from changing the value at runtime.
#
# Corollary, found while trying to harden the other direction: the recognizer accepts
# the encoding only as the ENTIRE assignment. Appending a guard to it --
# `base_ref_path="$(jq … @uri)" || return 1` -- makes this gate FAIL the very line it
# is meant to bless. So the encoding assignment is deliberately left bare; it still
# fails closed, because an empty result yields a `compare/…` path that 404s, so `gh api`
# exits non-zero and the `|| return 1` on that same line fires. (The `[[ =~ ^[0-9]+$ ]]`
# below is the second net, reached only when `gh api` exits 0 with a non-numeric body.)
# Do not "fix" that by widening the recognizer.

# Floors, not targets. They exist so that a recognizer regression -- an anchor dropped, a
# grep that stops matching -- shows up as "the scan stopped reaching the repository" rather
# than as a quieter green run. Raise them when the recognizer widens; never lower one to
# accommodate a scan that found less.
# Pinned, not slack: a refactor that moves an interpolation OUT of a recognized shape
# -- string concatenation instead of an f-string brace, a `compare/` prefix hoisted
# into its own variable -- lowers this count while every remaining site still
# passes, so a loose floor lets coverage drain away under a green run. Raise it
# deliberately, the way the allowlist is edited; never lower it to accommodate a
# scan that found less.
RECOGNIZED_REF_SITES=77
[ "$sites" -ge "$RECOGNIZED_REF_SITES" ] \
  || fail "the ref-interpolation scan recognized $sites sites, below the pinned $RECOGNIZED_REF_SITES; a ref interpolation moved out of a shape this scan can see"
# `git ls-files` over the three globs, less the `*.test.sh`/`*.test.py`/`*_test.py`
# exclusion: 57 `.py` + 38 `.sh` + 49 `.yml`. The naive per-extension totals are 144, 157
# and 49, so the breakdown reproduces only AFTER the exclusion, which is why it is asserted
# here rather than described in prose.
SCANNED_FILES=144
[ "${#scanned[@]}" -ge "$SCANNED_FILES" ] \
  || fail "the ref-interpolation scan covered ${#scanned[@]} files, below the pinned $SCANNED_FILES; a file carrying ref interpolations dropped out of the scan"
[ "$encoders" -ge 16 ] || fail "only $encoders encoder expressions were exercised; the semantics check is not reaching the fixed sites"
[ "$py_sites" -ge 18 ] || fail "the ref-interpolation scan found only $py_sites Python sites; it is not reaching the Python callers"
[ "$py_encoders" -ge 6 ] || fail "only $py_encoders Python encoder calls were exercised; the semantics check is not reaching them"

# A stale allowlist entry is a silent hole: it would keep vouching for a site that has
# moved, been renamed, or been fixed, and would quietly cover a future site that happens to
# reuse the name. Every entry must have been consulted by a live site.
for entry in "${REF_SITE_ALLOWLIST[@]}"; do
  # Redirected, not pipe-fed: `grep -q` exits on its first match and would SIGPIPE a
  # still-writing producer (#1430, #1445).
  grep -qxF "$entry" < <(printf '%s\n' "${allowlisted_hits[@]:-}") \
    || fail "stale ref-site allowlist entry, no site matched it: ${entry//$'\t'/ }"

  # An entry that cites a guard pins its literal text, so removing the guard reddens HERE
  # instead of quietly turning the entry into a vouch for a value nothing constrains.
  IFS=$'\t' read -r entry_file entry_subject guard_file guard_text <<<"$entry"
  [ -n "$guard_file" ] || continue
  label="ref-site allowlist entry $entry_file \$$entry_subject"
  [ -f "$root/$guard_file" ] \
    || fail "$label cites a guard file that is missing: $guard_file"
  guard_is_live "$root/$guard_file" "$guard_text" \
    || fail "$label cites a guard that is gone, commented out, or neutered in $guard_file: $guard_text"
done

# "files", not "workflows": `scanned` is three globs, and only the `.github/workflows/*.yml`
# third of it is workflows. Calling the whole count workflows overstated what the number is.
echo "PASS: $sites ref interpolations ($py_sites of them Python) across ${#scanned[@]} files -- workflows"
echo "      and non-test scripts -- are percent-encoded or 40-hex-constrained, and all $encoders jq"
echo "      and $py_encoders Python encoder expressions keep '/' literal in a query value and"
echo "      encode it as %2F in a path segment"
echo "      ${#allowlisted_hits[@]} sites are covered by ${#REF_SITE_ALLOWLIST[@]} allowlist entries with a stated reason instead"
