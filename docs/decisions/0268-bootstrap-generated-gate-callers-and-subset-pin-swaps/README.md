# 0268 — A review-caller bootstrap may move generated gate callers and a subset of mirrored pins

- **Date:** 2026-11-15
- **Status:** Accepted
- **Issue:** [verJSON/.github#1843](https://github.com/verJSON/.github/issues/1843)
- **Extends:** [ADR 0227](../0227-arm-bootstraps-stale-review-callers/README.md), [ADR 0231](../0231-bootstrap-moves-reusable-ci-pins/README.md)
- **Category:** Merge-gate authorization (sensitive class)

## Context

ADR 0227 admits one pull request a stale default-branch review caller cannot
authorize: the generated `ai-review-merge.yml` update, pin mirrors that replace
every stale token, and one generated fragment. ADR 0231 also lets that pull
request move allowlisted reusable CI pins. Both rules still leave a consumer
wedged when one contract test uses the same stale SHA for callers the lane can
move and for callers it cannot.

`tests/ci-workflow.test.ts` in that shape names `2d1ea177` for the review
caller, node CI, the gate re-arm callers, and the terminal merge callers. The
mirror rule required every occurrence of that SHA to become the contract SHA.
Moving only the review caller then fails the test on the unmoved pins. Leaving
the test unswapped fails it on the review caller. Moving node CI with the lane
is a separate problem: the contract's sandbox can reject the consumer's current
harness, and a bootstrap may not edit `scripts/`.

The other generated callers (`gate-rearm.yml`, `ai-review-label-rearm.yml`,
`ai-privileged-merge.yml`, and `ai-promotion-retry.yml`) were not admitted at
all. A hand-written edit of those privileged workflows must not ride the
model-free lane, so a pin-only diff is the wrong proof. They need the same
byte-exact generator proof ADR 0227 uses for the review caller.

## Decision

On the same stale-review bootstrap, the classifier also admits those four
generated callers when each is modified in place and both sides equal the
generator output for the bootstrap's stale and contract revisions.

The embedded proof does not execute a generator fetched from those revisions.
It embeds the templates whose emitted bytes changed, and it selects the
template by ancestry on `Verjson/.github`:

- `gate-rearm.yml` is precomposed at and after `e7db320e`, composed at and
  after `ae70e4ef`, and guarded at and after `635aca97`.
- `ai-review-label-rearm.yml` is the lifecycle caller at and after `e7db320e`
  and the label caller at and after `0f8aead1`.
- `ai-privileged-merge.yml` and `ai-promotion-retry.yml` use the template
  introduced at `0bc98a81`. Required checks, workflow names, and optional
  runner labels are recovered from the protected base and must reproduce both
  sides. Those parameters cannot change.

A revision older than the template that applies to it is refused. The head of
a current contract is the current template; the base of `68b823b1` or
`2d1ea177` is the template that revision's generator emitted. Unknown or
hand-edited bytes fail closed.

A companion outside `.github/`, and not action metadata, may replace any
non-empty subset of the admitted tokens: the stale review SHA, the review
caller digest, each moved reusable CI SHA, and the digest of each moved
generated caller. Every other byte stays equal to the base. A consumer can
therefore move the five generated callers, keep `ci.yml` on the stale pin, and
swap only the matching test occurrences.

Reusable CI pin rules are unchanged. The review caller update, the stale-pin
proof, the single generated fragment, and the path and size limits still gate
the pull request. The completion step re-runs this proof.

## Why this is safe

Each generated caller is accepted only when its bytes are the generator's, so
the lane cannot approve a hand-written privileged workflow or a change to
required checks, workflow names, or runner labels. The template epoch is the
generator that actually existed at that revision, so a newer template cannot
be presented as the output of an older revision. A companion diff still
contains nothing except substitutions of tokens the proof has already bound to
the contract revision. Leaving some occurrences unchanged is how a shared pin
survives for the caller that did not move; it does not authorize any other
edit.

## Threat model

| Threat | Control |
| --- | --- |
| A hand-edited privileged or re-arm workflow rides the lane | Both sides must equal the embedded generator for the revision's template epoch. |
| Required checks, retry workflow names, or runner labels change | Parameters are taken from the protected base and must reproduce the head. |
| A template from a later generator is claimed at an older revision | Ancestry selects the template. A revision that predates every recognized template is refused. |
| A companion rewrites prose while swapping some pins | Bytes outside a substituted token must match the base. |
| A companion swaps a SHA the bootstrap did not move | Only the review SHA, moved reusable CI SHAs, and the digests of moved generated callers are tokens. An unmoved distinct pin is not a token. |
| A shared SHA is swapped in a non-test file outside `.github/` | Accepted residual, the same class ADR 0227 already accepts. The substitution is still only stale-to-contract. |

## Consequences

- A consumer whose contract test mirrors one stale SHA across generated callers
  and node CI can bootstrap the five generated callers, leave `ci.yml` unmoved,
  and swap only the occurrences that moved.
- **Operator steps (owner-mediated, not performed by this change):** the lane
  takes effect only once organization ruleset 20722935's `gate-rearm.yml`
  revision is rotated to this change's merge SHA and read back.

## Verification

`python3 scripts/ci-gate/review-caller-bootstrap.test.py` runs the real
classification step against stubbed GitHub data. It admits a `68b823b1`
bootstrap that moves all four generated callers and swaps only some shared
SHA occurrences, and a `2d1ea177` bootstrap whose gate and label callers cross
template epochs. It refuses a hand-edited gate or label caller, a privileged
caller whose required checks change, a retry caller whose workflow names
change, a guarded template at a revision that does not contain that template,
and a companion that changes any byte other than an admitted token. Each of
those refusals has a mutation proof. The embedded templates match
`scripts/gen-gate-rearm-caller.sh`, `scripts/gen-ai-review-label-rearm-caller.sh`,
and `scripts/gen-privileged-merge-caller.sh` at the boundary revisions above.
The ancestry of those boundaries was checked with `git merge-base --is-ancestor`
against `68b823b1`, `2d1ea177`, and `HEAD`.
