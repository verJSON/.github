# 0231 — A review caller bootstrap may move canonical reusable CI pins to its contract revision

- **Date:** 2026-10-14
- **Status:** Accepted
- **Issue:** [verJSON/.github#1696](https://github.com/verJSON/.github/issues/1696)
- **Extends:** [ADR 0227](../0227-arm-bootstraps-stale-review-callers/README.md)
- **Category:** Merge-gate authorization (sensitive class)

## Context

ADR 0227 lets the authorization arm approve the one pull request a stale default-branch
review caller cannot authorize: the byte-exact generated caller update, its pin mirrors,
and one generated fragment. It refuses every other file under `.github/`.

verjson-cli shows the lane is still not enough. verjson-cli#318 is a correct ADR 0227
bootstrap to `ff21f300`, and its `AI review authorization` check passes through the
bootstrap classifier. Its required `ci / build-test` fails, because the default branch
still pins `node-ci.yml@ab93610d` in `.github/workflows/ci.yml:21`. That revision predates
the GitHub Packages registry-URL casing repair (`19eed78e`, #1699, and `65529002`) and
rejects the lockfile with `registry exact-version provenance has an invalid GitHub
Packages URL` (run 38587736434). verjson-cli#302 repairs that pin and passes CI, but the
stale review caller reviews it and fails closed. Neither pull request can merge, and
every further pull request in the repository meets the same pair of blockers.

The CI caller cannot simply move first or second:

- A pull request that moves only the CI pin is reviewed by the stale caller, which fails.
- A bootstrap that leaves the CI pin alone fails `ci / build-test`.
- verjson-cli's `tests/ci-contract.test.ts` binds the node-ci *and* actionlint callers to
  one `canonicalNodeCiPin`. In a disposable worktree at #318's head, the contract test
  passes unchanged (24 of 24). Moving `ci.yml` alone fails 3 of 24, and moving `ci.yml`
  with its mirror fails the actionlint assertion (1 of 24). Moving `ci.yml`,
  `actionlint.yml`, and the mirror passes 34 of 34 across both contract test files. The
  required build-test runs that suite.
- These callers are hand-written. Neither verjson-cli's nor verjson-payments' `ci.yml`
  carries a generator header, and no canonical generator emits them. The one generator
  that renders a node-ci caller, `scripts/gen-node-required-workflow.py`, serves only the
  CLI Projects admission contract (ADR 0212). A byte-exact "generated" proof like the
  review caller's therefore does not exist for these files.

## Decision

The bootstrap classifier also admits modified workflow files at
`.github/workflows/<name>.yml` or `.yaml`, with an exact-case path and no subdirectory,
when a line-by-line proof shows that the file only moves allowlisted canonical reusable CI
pins to the bootstrap's contract revision:

1. **Same shape.** Both sides must decode as UTF-8 and contain no line break other than
   `\n` and `\r\n`. A lone `\r`, U+0085, U+2028, or U+2029 refuses the caller. YAML
   breaks lines at a lone `\r`, and YAML 1.1 parsers at the other three, while the proof
   splits on `\n` only. Without this guard, `# c<CR>run-name: |` reads to the proof as one
   comment line, but YAML sees a block scalar header, whose text the proof would then take
   for a job. Base and head then split on `\n` into the same number of lines. Lines are
   compared pairwise, so nothing can be appended, removed, or reordered.
2. **Reference lines.** Every changed line that is not a comment line (rule 3) must, on
   the base side, be exactly
   `    uses: <owner>/<repo>/.github/workflows/<file>@<40-hex>`, indented four spaces. It
   may be followed only by whitespace, an optional YAML comment, and an optional `\r`, so
   a revision that runs on into a branch name is not a pin. Structurally, the nearest
   earlier non-blank, non-comment line indented fewer than four spaces must be a bare
   two-space job key (`  <id>:`), and the nearest earlier such line at column zero must be
   `jobs:`. Either may be followed by whitespace and a comment. A `#` directly after the
   colon (`jobs:#x`) is refused, because YAML reads that as a plain scalar, not a key. This approximates "a job's reusable workflow
   call" without a YAML parser, and it matches every known caller (verjson-cli `ci.yml`
   and `actionlint.yml`, verjson-payments and verjson-cli-cloud `ci.yml`). A caller written
   with other indentation is refused, which fails closed. The head line must equal the base line with
   only those 40 hex digits replaced by the contract revision. The owner and repository
   must be the canonical `Verjson/.github`, compared case-insensitively as GitHub resolves
   it. The workflow must be in the closed allowlist `REUSABLE_CI`.
3. **Comment lines.** A changed line whose base side *starts* with optional whitespace
   and `#` must equal the base line with each revision moved in rule 2 replaced by the
   contract. Free text changes, or a pin that moves without its `uses:` line, are refused.
   This is a lexical test, not a YAML one. It covers YAML comments, and also `#`-led lines
   inside block scalars such as `run: |`, where they are usually shell comments. A line
   with `#` anywhere later, such as `run: curl …/<pin>/x.sh | bash # pinned`, is not a
   comment line.
4. **Contract revision.** The new revision is the review caller's contract revision, so
   it carries every ADR 0227 check: it contains `MINIMUM_SAFE_REVIEW`, descends from the
   stale review pin, and is on canonical `main`. It must also descend from every reusable
   CI revision it replaces, so the lane never downgrades CI.
5. **Pin mirrors.** Each moved reusable CI revision joins ADR 0227's token swap. Any
   modified regular file outside `.github/` that is not action metadata may replace that
   revision with the contract revision, with no other change. The same rule already lets
   it swap the review caller pin and digest. In practice these files are consumer
   contract tests, but the proof does not know that. Any such file qualifies, including a
   script that embeds the pin in a URL.

Each workflow file is proven on its own. Two callers that both satisfy the rule are
admitted, and the pull request is refused if any one caller changes anything else. All
other ADR 0227 rules still apply unchanged. The review caller must itself be a generated
stale-to-contract update, so a CI pin bump without one is not a bootstrap. At most one
file may be added, and it must be the generated fragment. Deletions, renames, mode
changes, action metadata, and other `.github/` paths are refused. The 32-path and 256 KiB
limits still apply, and every API failure leaves the ordinary dispatch in place. The
completion step re-runs the same proof, so it re-proves the companions as well.

`REUSABLE_CI` starts as `node-ci.yml` and `actionlint.yml`:

- **`node-ci.yml`** is the required CI whose stale pin blocks verjson-cli.
- **`actionlint.yml`** is included because verjson-cli's contract test binds it to the
  same pin. Without it, the node-ci repair cannot pass the required build-test. It has no
  publishing or secret-bearing surface beyond read-only linting.
- **`node-ci-protected.yml`** is excluded. Its callers are generated
  (`scripts/gen-node-ci-protected.py`) and carry the protected lane's trust semantics. No
  stale consumer is known to need it, and a generated caller deserves a generated proof.
- **`node-release.yml`**, deployment, and other publishing workflows are excluded. Moving
  a credential-bearing release path must never ride a model-free lane.

A workflow joins the list only with evidence that a bootstrap cannot pass its required
checks without it, recorded by amending this ADR.

## Why this is safe

The reviewed artifact is canonical code at a canonical revision that the arm already
trusts for the review caller itself. A pin-only change cannot alter repository-specific
behavior. Every input, secret mapping, permission, trigger, job, and runner label stays
byte-identical to the protected default branch, which was reviewed when it landed. The
only thing that changes is which canonical `main` commit runs, and that commit is newer
than both the stale CI pin and `MINIMUM_SAFE_REVIEW`. Without this lane, a repository
whose stale review caller and stale CI pin both block can never recover without bypassing
the gate.

## Threat model

| Threat | Control |
| --- | --- |
| Arbitrary workflow edit rides the lane | Every changed line must be a reference or comment whose only change is the 40-hex revision. Equal line count rules out added jobs. Inputs, permissions, secrets, triggers, and free text are refused. |
| Retarget to another repository, a fork, or a branch or tag | Each head line must equal its base line except for the revision token, which must be the contract. The base owner and repository must be canonical, and the revision must be exactly 40 hex digits followed only by whitespace or a comment. |
| Pin to an arbitrary or older revision | The new revision is the contract, which must be on canonical `main`, contain `MINIMUM_SAFE_REVIEW`, and descend from every replaced CI revision. |
| Moving a credential-bearing or release workflow without review | Closed allowlist: only `node-ci.yml` and `actionlint.yml`. |
| Lookalike paths | Exact-case `.github/workflows/<file>` only. `.GitHub/…`, subdirectories, and `action.yml` fall through to the existing refusal. |
| A non-bootstrap pull request uses the lane | The ADR 0227 caller and stale-revision proof still gates eligibility. |
| A line break the proof does not split on hides YAML structure inside an apparent comment | Rule 1 refuses any caller containing a lone `\r`, U+0085, U+2028, or U+2029, or any caller that is not UTF-8. |
| A pin-shaped or `#`-led line inside a block scalar, or a pin inside a non-test file outside `.github/`, has an older canonical revision swapped to the contract | Accepted residual. The swap only replaces a canonical revision that the contract descends from with a canonical `main` revision. The rule 2 structure checks and the rule 3 start-of-line anchor refuse the single-line shapes tested: `with:` values, `run:` commands ending in a comment, checkout `ref:` inputs, and block scalar text under a two-space key. They are lexical, not a YAML parse, so the next row remains. This is the same class of change ADR 0227 already accepts for the review pin in companion files. |
| A multi-line quoted or flow scalar opened anywhere above the line, including a top-level `run-name:` or `env:` at column 0 (for example `run-name: "deploy`, `PIN: "a`, or `env: {`), continues across a four-space `uses: …@<pin>` line, which the proof reads as a job's reusable workflow call | Accepted residual. The swapped token becomes part of that scalar's value. The swap is bounded the same way as the previous row, to an ancestor canonical revision becoming canonical `main`. It can only occur if the protected default branch already contains such a construct, because every unchanged byte must equal the base. |

## Alternatives rejected

- **Owner merges past the red required check.** This bypasses the gate, leaves `main`
  with a failing required context, and teaches a recovery path that does not scale to the
  next stale consumer.
- **Admit arbitrary workflow edits in a bootstrap.** That turns a model-free approval
  into a general workflow-change approval.
- **Regenerate `ci.yml` with a generator, as the review caller is.** No generator exists
  for these hand-written callers (verified: no generator header in verjson-cli or
  verjson-payments), and one would erase legitimate repository-specific inputs.
- **Move the CI pin in a separate earlier pull request.** The stale review caller reviews
  it and fails closed, which is the deadlock itself.
- **Admit any canonical reusable workflow.** Release and deployment callers carry
  credentials and publishing authority. The allowlist grows only with evidence.

## Consequences

- verjson-cli can recover in one pull request: #318 plus the `ci.yml` and `actionlint.yml`
  pin moves and the `canonicalNodeCiPin` mirror, all at `ff21f300`. Under the arm
  revision that carries this change, the classifier admits it.
- **Operator steps (owner-mediated, not performed by this change):** as with ADR 0227,
  the lane takes effect only once organization ruleset 20722935's `gate-rearm.yml`
  revision is rotated to this change's merge SHA and read back.
- A consumer that pins a reusable CI workflow outside the allowlist still needs an
  ordinary pull request after its bootstrap, reviewed by the now-current caller.

## Verification

`python3 scripts/ci-gate/review-caller-bootstrap.test.py` runs the real classification
step against stubbed GitHub data. It covers seven admitted companion shapes and 30
refusals. Each refusal except the layered "no review caller update" case has a mutation
proof that drops or loosens exactly its guard and shows the case is then wrongly
admitted. A further proof shows that refusing every `\r` would wrongly refuse a CRLF
caller. Independent review found three mutants the first version of the suite did not
kill: overwriting rather than accumulating the moved CI revisions, `COMMENT.match`
becoming `.search`, and a widened comment marker. Each now has a refusal case that kills
it, and the job-level structure checks came out of the same review. A second review
showed that a lone `\r` (and NEL, LS, PS) could hide a block scalar header, or a `run:`
step, behind an apparent comment. That finding produced the rule 1 line-break guard and
the whitespace-before-`#` requirement on `jobs:` and job keys, each with a refusal case
and a killed mutant. The guard runs on both base and head, which is intentionally redundant:
unchanged bytes must equal the base and the only permitted changes are hex swaps, so a
foreign line break or invalid UTF-8 on one side is present on the other, and no case can
kill a base-only or head-only mutant. While
the line-count guard's proof was being written, it first passed vacuously, because a base
with a final newline exposes appended lines to the per-line rule. The case now uses a
base without a final newline, the one shape where only that guard can refuse. The real
verjson-cli trees (`2d818377` and #318's `d9657189`, plus the three pin moves) were run
through the same step, with canonical ancestry taken from local git. It reports
`eligible=true`, stale `68b823b1`, and contract `ff21f300`. The live default-branch callers of
verjson-cli (`ci.yml`, `actionlint.yml`), verjson-payments, and verjson-cli-cloud, each
moved to the contract, are all admitted by the narrowed rule.
