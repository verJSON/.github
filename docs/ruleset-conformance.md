# Organization ruleset release conformance

The scheduled audit and the local production check use the reviewed policy from
the same checkout as `scripts/org-ruleset-conformance.py`:

```bash
python3 scripts/org-ruleset-conformance.py
```

Do not set a policy-path environment variable or pass `--test-policy` outside the
isolated unit suite. The scheduled workflow is event-SHA-bound and its semantic
contract requires the exact no-argument command.

## What a conformant result proves

The audit reads every organization ruleset detail, then requires the configured
release App actor in `always` mode when both of these exact predicates hold:

- `target` is `branch`; and
- `conditions.ref_name.include` contains the literal `~DEFAULT_BRANCH` token.

The result does not claim semantic coverage for `~ALL` or explicit refs such as
`refs/heads/main`. Those selectors can affect a default branch, but an explicit
name is not necessarily the default across every repository selected by an
organization rule. Authors use `~DEFAULT_BRANCH` for the canonical contract. A
reviewer must separately assess any `~ALL` or explicit-ref rule that applies to a
releasable repository and preserve the release bypass when applicable.

The audit separately requires that every `required_status_checks` context in
**any** organization ruleset — not only the `~DEFAULT_BRANCH` ones — declares
`integration_id` equal to the policy's `required_check_producer_app_id`. An
unbound context can be satisfied by a status published from any App that reuses
its name, which is the provenance property ADR 0184's Gate A depends on. Both
findings are reported together, so neither masks the other. See ADR0190.

The program has no mutation path. It calls only paginated GitHub REST GETs and
does not print API response bodies, credential data, secret values, or variable
values. Exit `0` means the exact-token policy conforms, exit `1` is policy drift,
and exit `2` means the audit could not establish trustworthy API or schema state.

The package type-surface audit (`package-type-surface-ruleset.py audit`) runs
even when the release-authorization audit fails.

## Sensitive-path required-reviewer rulesets

The same `audit` job also runs, even after an earlier audit in it fails:

```bash
python3 -I scripts/required-reviewer-ruleset.py check
```

It fails the job when a reviewed `*-sensitive-path-admin-review` ruleset
(`config/required-reviewer-rulesets.json`) is absent, differs from the reviewed
image, shares its name with another live ruleset, or when a live ruleset with
that suffix is not declared (exit `1`), and when it cannot read trustworthy state
(exit `2`). It issues only GETs. See ADR 0210's 2026-10-20 amendment.

## Credential boundary

As of 2026-08-15, the organization Actions secret-name inventory contains no
dedicated Administration-read ruleset credential. `ORG_ADMIN_TOKEN` is the
reviewed residual binding; the wrapper still fixes every API call to explicit
GET. Replace it only after an `.github`-scoped credential with organization
Administration read proves both the paginated list and every detail request, and
update the scheduled-workflow and secret-scope contracts in the same change.

## Verification

Run the isolated behavior and privileged-workflow contracts:

```bash
python3 scripts/org_ruleset_conformance_test.py
python3 scripts/ci-gate/privileged-scheduled-workflows.test.py
```

Before any separately authorized live repair, capture the complete ruleset
preimage. Add only the required release App actor, then compare and verify every
other actor, rule, condition, target, and enforcement field byte-for-byte. This
repository audit neither authorizes nor performs that mutation.
