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
