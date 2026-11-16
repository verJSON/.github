# Merge-admission runbook

Organization ruleset `merge-admission-required` requires the `merge-admission`
check, bound to App `4528902` (the AI review App; ADR 0260 Decision 2), on the
exact head before a non-bypass principal can merge. The producer posts that
check from the default branch
([ADR 0260](decisions/0260-native-merge-admission-through-a-producer-posted-check/README.md)).
`scripts/assert-mergeable-head.sh` Gate R reads the same bound-App SUCCESS.

Canary scope is `repository_id` `1269388380` (`Verjson/.github`). Wider
enrollment uses the `verjson-merge-admission=enforced` property with paused
repositories excluded; that path stays out of this canary close.

## C9 non-bypass canary principal

Decision 7 canaries that press Merge or enable auto-merge must use a principal
that is **not** on the ruleset bypass list:

| Role | Account | Notes |
| --- | --- | --- |
| Canary actor (member, not owner) | `lightningleap` | Not OrganizationAdmin; not Renovate (2740); not release App (4583107); not merge App (4693283); not the AI review App (4528902). |
| Owner bypass (C7 only) | OrganizationAdmin owners (`AceVentura7`, `manishb27`, `PanchalKaran967`, `pyousefi`) | Allowed to merge during a producer outage; do not remove OrganizationAdmin from the bypass list before #1964. |

Do **not** put App `4528902` on the bypass list (C6).

## After a ruleset edit or arm rotation

A ruleset edit moves `updated_at`. Heads that already carry `merge-admission`
SUCCESS stay green until the next producer run sees the new required set; still
re-sweep so every open non-draft head is re-evaluated promptly:

```bash
gh workflow run "Merge admission" --repo Verjson/.github
# Consumer (generated caller):
gh workflow run "Merge admission" --repo <owner>/<repo>
```

Confirm a new bound-App `merge-admission` check on each head after the dispatch,
then re-read Gate R:

```bash
bash scripts/assert-mergeable-head.sh Verjson/.github <pr>
```

Record the dispatch on the change that edited the ruleset (or on #2031 for the
canary enablement).

## Deactivating the ruleset (rollback)

1. Set enforcement to `disabled` (do not delete until the canary is abandoned):

   ```bash
   gh api --method PUT orgs/Verjson/rulesets/<ruleset_id> \
     --input - <<'EOF'
   {"enforcement":"disabled"}
   EOF
   ```

   Prefer the GitHub UI ruleset editor if the PUT body must carry the full
   ruleset document your token's API version requires.

2. Re-run `python3 scripts/org-ruleset-conformance.py` and confirm the full
   receipt prints `enforcement=disabled`.

3. Leave the producer, trigger, and outage monitor in place so re-enablement
   does not require re-shipping callers.

## Closing a foreign same-head pull request

Two pull requests can share a head SHA. Admission is per head: a SUCCESS admits
every pull request at that SHA. If a foreign pull request must not merge:

1. Close it, or push a distinct head on that branch.
2. Dispatch `Merge admission` so the producer re-lists open pull requests at the
   SHA and refuses (or leaves in progress) when the remaining set is empty or
   fails Gate R.

Never satisfy the required context with a same-named GitHub Actions check; Gate
R and the ruleset bind the context to App `4528902`.

## C7 recovery (producer outage)

When the producer cannot post a superseding check (missing client id, environment
admission failure, token mint failure, Actions outage), an earlier SUCCESS can
stand for as long as the outage lasts. The
[Merge admission outage](../.github/workflows/merge-admission-outage.yml) workflow
fails on the default branch when that window is silent.

Recovery:

1. Fix the producer (restore `ai-review-app`, App client id, billing, or workflow
   `state=active`).
2. Dispatch `Merge admission` and wait for a fresh bound-App check on each head.
3. If autonomous merges must land during the outage, an **OrganizationAdmin**
   owner merges via the explicit bypass path. Do **not** remove
   OrganizationAdmin from the bypass list before #1964. Do **not** add the AI
   review App to the bypass list.

## REST and hosted-minute budgets (canary)

Measured on `Verjson/.github` on 2026-11-16 (read-only):

- **REST.** About 56 `Merge admission` runs in the prior hour (3 schedule, 53
  `workflow_run`; 36 success, 20 cancelled displaced-pending) with 1 open
  non-draft pull request. Estimated job-token use ≈ 720 calls/hour against the
  Team plan ≈ 1000/hour shared repository budget. **Fits; no skip-if-fresh
  mitigation and no extra read-only App token.**
- **Hosted minutes.** This canary is public, so GitHub-hosted minutes for the
  15-minute sweep and per-head evaluate on `CI_LANE_UNTRUSTED` / ephemeral
  hosted fallback are not billed. Before enrolling a **private** consumer
  (verjson-agents, verjson-ai) without a self-hosted untrusted lane, keep
  estimated private hosted use under the organization private-minute ceiling:
  roughly `96 sweeps/day × (plan ≈ 2 min + N heads × evaluate ≈ 2 min)`. With
  N ≤ 5 that is on the order of 1.5k minutes/month per private repository; raise
  the ceiling or attach a self-hosted untrusted lane before exceeding it. No
  private enrollment in the #2031 canary close.

## Conformance

`scripts/org-ruleset-conformance.py` prints the merge-admission receipt only on
exit 0. After the ruleset exists, that receipt also requires C8 enrollment
scope (canary `repository_id` or the enrollment property with paused exclusion,
and caller/trigger presence on each scoped default branch). See
[ruleset-conformance.md](ruleset-conformance.md).
