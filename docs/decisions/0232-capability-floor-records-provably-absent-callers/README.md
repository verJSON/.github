# 0232 — Capability-floor discovery records a provably absent caller as a finding

- **Date:** 2026-10-15
- **Status:** Accepted
- **Issue:** [verJSON/.github#1777](https://github.com/verJSON/.github/issues/1777)
- **Supersedes in part:** [ADR 0135](../0135-observe-live-generated-caller-capability-floors/README.md) — "missing files" as a discovery-terminating condition

## Context

ADR 0135 made a missing allowlisted caller fatal, so that a caller removed without
a coordinated allowlist update could not silently shrink coverage. In practice the
opposite happened. `verjson-authn` deliberately deleted its local `gate-rearm.yml`
on 2026-09-02 (verjson-authn#283, its ADR 0053): the organization ruleset already
injects the arm, and a local caller on the same events armed every pull request
twice. ADR 0094's 2026-08-12 amendment recognizes this ruleset-created arm with no
local caller as a valid installation shape. The allowlist was not updated, and the
scheduled observation then failed on every run for six weeks. During that time it
observed nothing for any of the seven repositories, which is a larger loss of
coverage than the one removed file.

A contents-API `404` for the caller cannot prove absence on its own. GitHub also
returns `404` for a repository the token cannot see, so treating a `404` as absence
would turn a scope or installation failure into a quiet finding.

## Decision

- Discovery lists `.github/workflows` once per repository through the contents API
  at the resolved immutable source commit. A caller is **absent** only when that
  listing succeeds, is a well-formed array whose entries each carry a string `path`,
  holds fewer than the API's 1,000-entry directory limit, and does not contain the
  caller's path.
- An absent caller produces an `allowlisted-caller-absent` record in the
  `absent_callers` receipt array, with `callers_absent` as its count. It also
  produces a workflow `::warning::` annotation that names the repository as
  non-conforming. Discovery then continues with the remaining callers and
  repositories, and the absent caller contributes no pin.
- A `404` is never interpreted as absence. Every failed read stays fatal and writes
  no receipt. That covers repository metadata, the default-branch commit, the
  listing (`404`, `403`, `5xx`, malformed JSON, a non-array body, a malformed entry,
  or a listing at the limit), and a listed caller that then fails to read.
- The reviewed allowlist remains the single statement of expected callers. Every
  allowlisted caller is expected, so its absence is a non-conformance finding: the
  consumer drifted or the allowlist is stale. When a repository legitimately does
  not carry a caller, the allowlist is corrected. This change corrects the
  `verjson-authn` entry to its generated `ai-review-label-rearm.yml`, which calls
  the same canonical `gate-rearm.yml`.

## Consequences

- One consumer's missing caller no longer blinds the observation for the whole
  fleet, and the absence stays visible as an annotation and a receipt.
- An absent caller is still not a workflow failure. It has the same standing as a
  stale pin under ADR 0135: a report finding that an owner adjudicates. Coverage
  loss now shows up as a named finding on each run, rather than as a red run that
  hides every other repository.
- Each repository costs one more contents read per run, which stays inside the
  existing exact-scope, contents-read token.
- A consumer with 1,000 or more entries under `.github/workflows` fails closed
  until discovery moves to the Git Trees API.
