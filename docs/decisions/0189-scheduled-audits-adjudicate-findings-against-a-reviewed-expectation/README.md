# 0189 — Scheduled audits adjudicate findings against a reviewed expectation

- **Date:** 2026-09-17
- **Issue:** Verjson/.github#1409
- **PR:** #1412
- **Category:** scheduled control reporting

## Context

`.github/workflows/org-ruleset-conformance.yml` schedules the authorization-arm
audit (#1404, #1406). It ships red on purpose: `Verjson/verjson-agents` carries
`verjson-core-checks=enforced` with `verjson-stack=none`, so the arm governs it
with no deterministic required CI behind it (#1401, #1407).

A permanently-red scheduled job reproduces the exact failure this line of work
exists to end — an audit failure nobody saw. Once red is expected, a genuinely
new finding and the known one are the same signal, and the only way to tell them
apart is to open the run and read the log.

#1409 proposed filing or updating a tracking issue per failure. Two other shapes
were available: a `config/`-held expectation compared on each run, and a job
summary. The issue-per-finding mechanism was rejected as the primary signal
because it puts the control's memory outside version control, where it drifts and
is never reviewed, and because it would require granting `issues: write` to a job
that already holds `ORG_ADMIN_TOKEN` across ~95 repositories.

## Decision

A scheduled audit's verdict is the **comparison of its findings against a
reviewed expectation**, not its raw exit status.

1. `scripts/audit-finding-state.py` runs the audit unchanged, extracts each
   `ERROR: ` finding, and fingerprints it with SHA-256 over the exact text.
2. `config/audit-expected-findings.json` records the findings currently
   acknowledged, keyed by audit name. Each entry carries the finding text, its
   digest, the tracking issue, a reason, and an expiry. Changing what is quiet is
   a reviewed pull-request diff against that file.
3. Verdicts and exit statuses:
   - `match` (exit 0) — the observed findings are exactly the recorded ones.
   - `drift` (exit 1) — a finding appeared that is not recorded, a recorded one
     stopped reproducing, or a recorded acknowledgement expired.
   - `undetermined` (exit 2) — the comparison could not be made.
4. Every verdict, including `undetermined`, renders its classified digests into
   `GITHUB_STEP_SUMMARY`.

### What keeps this from failing open

ADR 0024's rule is that absence is never green, and a waiver file is exactly
where "absence reads as conformance" would creep back in. The mechanism is
constrained so that a quiet run is a positive statement rather than a missing
one:

- The comparison is over the **exact** finding text. Any change to it — one more
  repository in the sample, a different count, a different failure entirely —
  produces a different digest and is loud.
- A recorded finding that stops reproducing is **also** drift. The file cannot
  accumulate acknowledgements nobody revisits.
- Each entry expires. An acknowledgement that outlives the work it points at goes
  loud on its own.
- Each entry's digest must equal SHA-256 of its own recorded text, so a waiver
  cannot quietly cover a finding other than the one a reviewer reads.
- An audit that exits non-zero without reporting a finding, an audit that exits
  zero while reporting one, a missing or malformed expectation file, and an audit
  name with no recorded expectation all exit 2. None of them can report a match.
- An audit that exits with a status it does not declare exits 2 as well. Those
  two guards constrain findings against zero and non-zero exits but neither
  constrains *which* non-zero: an audit that printed the recorded finding and was
  then killed satisfied both and adjudicated `match`, exit 0. A wrapped audit
  therefore declares its statuses with `--expect-status` (defaulting to 0 and 1),
  and anything else is a crash rather than a verdict.
- Findings are read from the audit's **stderr** only. Its stdout is passed
  through to this process's stderr, keeping the audit's own payload in the job
  log while leaving stdout for the adjudicator's JSON. A sibling audit adopting
  this mechanism must report findings on stderr.
- The committed expectation file is validated by `scripts/audit_finding_state_test.py`
  in the `platform` CI group, so a malformed edit fails in the pull request rather
  than in the next scheduled run.

### Privilege boundary

The adjudicated finding text is adopter-controlled: it embeds organization
repository names. It is only hashed, compared, and rendered as literal text. It
is never executed, never interpolated into a shell command, and never emitted at
the start of a line, so it cannot become an Actions workflow command. Control
characters are replaced before rendering. Both the audit command and the
expectation path are repository-controlled arguments fixed in the workflow, and
`scripts/ci-gate/privileged-scheduled-workflows.test.py` rejects redirecting
either to an inherited or untrusted path.

## Consequences

- The scheduled run is green while the known `verjson-agents` finding persists
  and flips red the moment the finding set changes. That is the signal a human
  can read without opening the run.
- The conformance defect itself is **not** hidden: it stays recorded, with its
  tracking issue and expiry, in a file that appears in every diff of `config/`.
  Resolving #1401 / #1407 is what removes the entry, and until it is removed the
  file states the organization is knowingly non-conformant.
- The adjudicator is audit-agnostic. The sibling scheduled jobs (#1409 notes they
  share the convention) can adopt it by adding an `audits` key and wrapping their
  command; no change to this script is required.
- The expiry date is a maintenance obligation: it must be resolved or
  deliberately re-reviewed before it lapses. That cost is the point.

## 2026-10-18 amendment: a truncated finding carries a digest of every item

"What keeps this from failing open" says any change to the finding text is loud.
That held only for the part of the text a reader can see. The authorization-arm
audit renders a list finding as a count, the first eight items, and `(plus N more)`.
A change that kept the count and touched only unsampled items therefore produced
identical text, the same fingerprint, and a `match`. For example, one adopter
regenerates while another loses a caller. Review of #1839 found the hole while
re-recording the #1810 finding, which has 20 items and a sample of 8.

`sampled_items` in `scripts/ai-review-required-workflow-audit.py` appends
`items=sha256:<hex>`. The hex is the SHA-256 of the full item list, sorted and encoded
as compact JSON. Both truncating renderers use this helper: `concise_findings` for
missing-item lists (sample of 8) and `concise_mismatches` for image mismatches (sample of
4), which also become finding text. The sample stays short for a reader, while the fingerprint the
adjudicator compares now covers every item. The adjudicator is unchanged: it still
hashes the exact text, so a sibling audit adopting this mechanism must likewise make
its finding text a function of every item it reports. Changing the format changed
every recorded arm fingerprint once. The single recorded entry was re-recorded in
the same pull request. Tests show that a same-count swap among unsampled items or
unsampled mismatches changes the finding, and that the digest does not depend on item order.

## 2026-10-21 amendment: a recorded item set that shrinks is an improvement, not drift

The exact-text comparison read every improvement as a regression. Each adopter that
regenerated its callers changed the #1810 finding's count and digest, so the
scheduled `arm-audit` job went red for good news: once on 2026-10-17 (re-recorded by
hand in #1839) and again on 2026-10-21 (run 39619145007, `missing=17` against a
recorded `missing=20`). More regenerations were landing the same week, so another
hand re-record would have reddened again within days.

An expectation entry may now also record its **item set**: `finding_class`, the
finding text's prefix up to its item summary, and `items`, the full sorted list the
text's `items=sha256:` digest covers. The adjudicator validates both against the
recorded text, so the list a reviewer reads is the list the digest commits to. An
entry without them is compared by exact text, as before.

The producing audit states its item sets positively. Beside its findings, its stdout
report carries `finding_classes`: every class it evaluated, with that class's sorted
items, **including the empty ones**. Each finding reaches the adjudicator on its own
`ERROR:` line, so each class's finding is adjudicated on its own; the suspension
finding [ADR 0254](../0254-audit-recognizes-the-main-protection-suspension/README.md)
adds belongs to no class and is still compared by exact text.

For an active item-set entry whose exact text did not reproduce:

- If the observed set for the same class, in the same audit, is a **strict subset**
  of the recorded set, the entry is `improved`. The verdict stays `match` (exit 0).
  The run emits a `::notice` annotation, and the step summary carries the
  replacement entry to re-record.
- If the observed set is **empty**, the entry is `resolved`. That reads as "no
  finding": exit 0, with a notice to remove the expectation.
- Any other outcome is drift, exactly as before. That covers any item not in the
  recorded set, the same set rendered differently, a different finding class, and a
  class the report does not state.

### What keeps this from failing open

- Relaxation needs positive evidence. A run with no structured report (stdout that
  is not a JSON object carrying `finding_classes`, or an audit that aborted before
  reporting) falls back to exact text. Absence never reads as an improvement.
- Evidence that is present but malformed or contradictory is `undetermined` (exit 2).
  That covers a non-list, an unknown key, a nameless class, items that are not
  sorted unique strings or that carry control characters, a class reported twice, a
  non-empty class with no finding line or more than one, a finding whose
  `items=sha256:` digest is not its class's items, and an empty class that still has
  a finding.
- An expired entry is never relaxed. A recorded item set must be non-empty, and one
  class may carry at most one recorded item set per audit.
- The notice is built only from integers and hex digests this process computed, so
  no audit- or adopter-controlled text becomes a workflow command.

### Trade-off

A resolved item-set entry no longer fails the run, so the "a recorded finding that
stops reproducing is drift" rule now applies only to exact-text entries. The
entry's expiry is what still forces the revisit, and the notice names the entry to
remove. A shrinking set can also mean the audit is checking less (for example, a
repository leaving the armed cohort). The notice and the report's
`armed_repositories` count keep that visible, but they no longer page. This was
chosen over a count-only tolerance, which would let a same-count swap pass, and over
re-recording on every change. A daily improvement treadmill trains reviewers to
re-record without reading, which is the failure the mechanism exists to prevent.
