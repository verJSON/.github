# PR #411 exact-head merge adjudication (run 18): head 27ae92f024

Run `arb-verjson-cli-pr411-27ae92f024-20261125-01`, round 3 of 3. **Status: APPROVED_WITH_CONSTRAINTS. Merge decision: APPROVED** for head `27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f` on base `8d1fc2c54cdcb3d4ea4694e081c003520676d7a4` (`main`, confirmed by fetch, PR `baseRefOid` and merge base). The chair was non-voting. Runs 1–17 were not approved.

The owner's decision rule is met for every candidate in the final round, and the chair states so here as the owner requires: claude (required, verified) ACCEPTs all ten candidates, and at least 2 of the 3 advisory members ACCEPT each one. cursor-agent and agy accept all ten; codex accepts seven and holds REVISE on B2, L3 and M1.

No kind (a) item remains. claude adjudicated every advisory REVISE in a later ballot and dismissed each as a merge blocker with a stated reason. codex's round-3 REVISE restates the L3 finding claude adjudicated in round 3. It is retained dissent, because there is no later ballot. Only kind (b) debt and out-of-scope items remain.

This board grants no merge, push, deploy or ADR authority. Merge stays subject to the org guard: exact head, required checks, approvals, and `--match-head-commit 27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f`.

ADR-style fence SHA-256 `c6c28f504d8972930e12670bcdd7b9989c5787fbe18cb5d59fa291e996dbfad7`. Final-round validator slate SHA-256 `dee0534fd632ec6593a567ec88919a1ca00a86358bb549f264fe8fc88d98f577` (ledger schema version 2). Validator: `validate_ledger.py ledger-final.json --state run-cont2.sqlite` prints "ARB ledger valid" and exits 0. The round-1 and round-2 ledgers also validate against their state files with exit 0.

```canonical-slate-json
{"adjudications":[{"adjudicated_by":"claude","confirmed_round_3":"ACCEPT","finding":"L1 (codex round 1, B1: issuer accepts an empty interior DNS label)","outcome":"dismissed as merge blocker (kind (b))","reason":"Dismissed as a merge blocker: this is kind (b) under the validator contract. Compose cannot misread SSO_AUTHORITY, because '$' is rejected (src/secrets/render.ts:247) and the value is a plain environment string (:311). Traefik never sees it. Vaultwarden's discovery against a host that cannot resolve fails, so SSO login is refused and no access widens. That is the same fail-closed availability class as the rulings in run-12 E2, run-13 F1 and N1. With a bootstrap host, the failure appears at first-owner enrollment, before anyone depends on the vault. With an open re-render, the operator must confirm a fresh SSO login after an issuer change, and the break-glass at :605 restores password access. Record as debt with the fix in my B1 concern.","round":2},{"adjudicated_by":"claude","confirmed_round_3":"ACCEPT","finding":"L2 (codex round 1, B2: force-recreate statement wrong for in-place writes)","outcome":"dismissed as merge blocker (kind (b))","reason":"Dismissed as a merge blocker: kind (b). The sentence at src/secrets/render.ts:588 overstates the delay: backup.sh stops the containers (src/secrets/scripts.ts:50) and then runs 'docker compose up -d' in cleanup, and that start rereads a changed traefik.yml or client secret. The worst outcome shown is codex's scenario: a client secret staged early is loaded by the backup restart before the identity provider accepts it. That is SSO unavailability, which fails closed (run-15 H4, run-16 J5). A restart that loads traefik.yml does not widen ingress, because traefik.yml is identical for bootstrap and open renders and the access rules live in routes.yml, which the text already says reloads at once. The chair's fix needs one correction: Docker re-resolves a bind-mount source at every container start, so a new-inode write is also picked up by a plain restart, not only by a force-recreate. The cleanup 'up -d' also applies a changed compose.yaml, and the archive would then hold the new files. The backup must therefore come before any change to the install directory, not only before a change to mounted files.","round":2},{"adjudicated_by":"claude","finding":"L3 (codex round 1, B2: saved-config remedy order)","outcome":"dismissed as merge blocker (kind (b))","reason":"Dismissed as a merge blocker: kind (b). The only product path that writes /data/config.json is the admin page, which applies its settings at once, so an override found by the check at src/secrets/render.ts:590 is already in effect, and a restart neither creates nor widens it. Codex's harmful ordering needs a config.json copied by hand into the running volume after startup. That act is outside every remaining instruction: the isolated restore at :610 deletes config.json first, and live restore is forbidden. Even then, the check runs after every change and the fix is prompt removal. Reordering the remedy, so the file is copied out and removed before any restart, is the right debt fix.","round":2},{"adjudicated_by":"claude","finding":"codex round-2 REVISE on B2, L3, M1 (same saved-config sentence, rebuttal that back up can mean backup.sh)","outcome":"dismissed as merge blocker (kind (b))","reason":"Adjudication of codex's round-2 rebuttal: dismissed as a merge blocker, kind (b). I weighed the reading that 'back up' at src/secrets/render.ts:590 means running backup.sh. Even so, backup.sh only stops and restarts the existing containers. It changes neither ingress nor the routes.yml allowlist. Ingress is public only if it was already open, and Vaultwarden restarts with the same /data it served before the backup. Through the product, /data/config.json is created only by saving in the admin page. Vaultwarden applies that save to the running process at once, so any SSO_ONLY=false override the check finds is already serving password login. The restart neither creates nor widens it. The harm only arises in codex's scenario: a config.json copied by hand into the live volume after startup and never loaded. No remaining instruction leads there. The isolated restore at :610 deletes config.json before start, and restore over the live host is forbidden at :588 and :610. I accept codex's point that 'back up' is ambiguous and that the remedy is in the wrong order; that is wording debt. Fix as in D1: copy config.json to a root-only location outside the install directory. Remove it. Force-recreate Vaultwarden with sudo docker compose up -d --force-recreate --wait vaultwarden. Repeat the test ! -e check and, with SSO-only, the password-grant refusal. Assert this order at tests/secrets-render.test.ts:595.","round":3},{"adjudicated_by":"none (round limit)","finding":"codex round-3 REVISE on B2, L3, M1","outcome":"retained dissent; it restates the L3 finding and scenario claude adjudicated in round 3 with the round-2 rebuttal before it, and adds no new scenario; no later ballot exists under max_rounds 3","reason":"chair note","round":null}],"charter_rules":"kind (a)/(b) calibration, scope rule and validator contract unchanged; owner option (c) scope; advisory membership and supermajority; max 3 rounds with round-2 refusal rule; file prompt transport","decision_rule":"owner rule (2026-11-24): claude required; codex, cursor-agent, agy advisory; each candidate needs claude ACCEPT and >=2 of 3 advisory ACCEPT; every advisory REVISE/REJECT adjudicated by claude in a later ballot; chair-enforced (ledger v2 sees claude as the only required member, rule unanimous over required); upstream support verJSON/verjson-agents#556 not yet landed","engine_state":{"round_1":"run.sqlite (engine round 1, closed APPROVED on claude alone)","round_2":"run-cont.sqlite (engine round 1 of continuation)","round_3":"run-cont2.sqlite (engine round 1 of continuation)"},"kind_a_blocking":[],"kind_a_disputed":["L3 (codex, advisory; dismissed by claude)"],"kind_b_debt":["L1 (src/secrets/render.ts:246-264, issuer check): In src/secrets/render.ts:246-264, reject an issuer hostname that is not an IP literal and has an empty label. Keep IP literals. Add parser tests: 'https://login..example.com/realms/team' is rejected and a valid multi-label issuer is accepted.","L2 (src/secrets/render.ts:588, bind-mount sentence): At src/secrets/render.ts:588 replace the sentence with: 'Compose does not recreate a container when only a bind-mounted file changes, but every container start reads the current files: a changed traefik.yml or client secret takes effect at that container's next start, including the stop and start backup.sh performs, and an in-place write to routes.yml takes effect at once. Run backup.sh and keep the current files before changing any file in <installDir>, because backup.sh restarts the stack and archives the files then in place.' Assert at tests/secrets-render.test.ts:505.","L3 (src/secrets/render.ts:590, saved-config remedy; codex holds kind (a), retained dissent): At src/secrets/render.ts:590 replace the sentence with: 'If the file exists, copy it to a root-only location outside <installDir>, remove it, then run sudo docker compose up -d --force-recreate --wait vaultwarden, repeat the check, and with SSO-only confirm the password grant is refused.' Extend tests/secrets-render.test.ts:595. codex's alternative, recorded for the owner: Require owner-supervised handling of a failed saved-config check. Verify public ingress is closed before any restart. Preserve config.json in a root-only location outside the install directory using a file copy that leaves services running, then remove the override and verify its absence before restarting Vaultwarden. Keep public ingress closed until the file's absence and, for an SSO-only deployment, password-grant refusal are verified after restart. Assert the sequence at tests/secrets-render.test.ts:595.","D1 (1) src/secrets/render.ts:588 and :605: 'keep the current files in a root-owned 0700 directory outside the install directory, and a fresh backup.sh archive'.","D1 (2) src/secrets/render.ts:610: list 'docker compose cp vaultwarden/. vaultwarden:/data', 'docker compose cp redis/. redis:/data' and 'docker compose cp letsencrypt/. traefik:/letsencrypt'; assert all three in tests/secrets-render.test.ts:505.","D1 (3) ADR 0038 break-glass bullet: append '(no apply procedure is rendered; see Apply, rollback and recovery above; owner-supervised until #412)'.","D1 (4) Record the bootstrap-enrollment, break-glass and isolated-restore conversion scope in #412's body and its CLAUDE.md index line, or in a separate issue CLAUDE.md points to.","Tests: add parser tests for L1 (rejected 'https://login..example.com/realms/team', accepted multi-label issuer, IPv4 and IPv6 literals) and wording assertions for L2 and L3 at tests/secrets-render.test.ts:505 and :595."],"ledger_status":"APPROVED","max_rounds":3,"member_identity":{"agy":"unverified; JSON response carries no served-model field","claude":"verified claude-opus-5-5 (response modelUsage, all three rounds)","codex":"unverified; requested gpt-6.2-sol; codex exec output names no served model","cursor-agent":"unverified; JSON result carries no served-model field"},"merge_decision":"APPROVED","merge_decision_basis":"in round 3 (slate revision 3) every candidate has claude ACCEPT and at least 2 of 3 advisory ACCEPTs (cursor-agent and agy accept all; codex accepts all but B2, L3 and M1); claude dismissed L1-L3 as merge blockers (kind (b)) in round 2 and confirmed the L3 dismissal against codex's round-2 rebuttal in round 3; only kind (b) debt and out-of-scope items remain; codex retains a kind (a) dissent on L3","next_action":"RUN_VERIFICATION","out_of_scope_items":["verjson-cli#412: tested apply.sh, rollback.sh and recover.sh carrying the run-17 K1-K6 requirements (owner option (c)); the absence of a rendered procedure is out of scope.","Process: record the CI run on head 27ae92f024 and the host README sha256 on #410; merge stays subject to the org guard (assert-mergeable-head.sh, gh pr merge --match-head-commit 27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f).","verJSON/verjson-agents#556: native advisory-member and supermajority support in the ARB engine (not yet landed)."],"prompt_transport":{"agy":"one-line argument to read the 0400 workspace copy, all rounds","claude":"prompt file on stdin (claude -p), all rounds","codex":"prompt file on stdin (codex exec -), all rounds","cursor-agent":"rounds 1-2: one-line argument to read the 0400 workspace copy (three calls returned narration before the JSON: malformed_ballot, not repaired); round-2 re-request and round 3: identical prompt file on stdin after a one-line stdin probe"},"round_slate_sha256":{"r1":"79234de5e288fd64d4796c9ffe4a889878020817953eb3bd4f543ec0eadd813d","r2":"31ecc6b8a3e00ee4f7fbcfe1606c171a057f7b180b7789cfdaea686729ab8ed2","r3":"dee0534fd632ec6593a567ec88919a1ca00a86358bb549f264fe8fc88d98f577"},"rounds_used":3,"run_id":"arb-verjson-cli-pr411-27ae92f024-20261125-01","schema_version":2,"source_pins":[{"id":"pr-head","ref":"feat/410-vaultwarden-lifecycle (PR #411)","repo":"verJSON/verjson-cli","role":"primary","sha":"27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f"},{"id":"prior-head","ref":"previous PR #411 head (run arb-verjson-cli-pr411-91964270bc-20261124-01, not approved)","repo":"verJSON/verjson-cli","role":"baseline","sha":"91964270bc5997fe696dc9bdd764ace8d2a9eb2e"},{"id":"main-base","ref":"main","repo":"verJSON/verjson-cli","role":"baseline","sha":"8d1fc2c54cdcb3d4ea4694e081c003520676d7a4"}],"status":"APPROVED_WITH_CONSTRAINTS","subjects":[{"base_sha":"8d1fc2c54cdcb3d4ea4694e081c003520676d7a4","head_sha":"27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f","merge_decision":"APPROVED","pr":411,"repo":"verJSON/verjson-cli"}],"supermajority_met":true,"tally_final_round":{"B1":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"B2":{"advisory":{"agy":"ACCEPT","codex":"REVISE","cursor-agent":"ACCEPT"},"advisory_accepts":2,"claude":"ACCEPT","supermajority_met":true},"B3":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"B4":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"D1":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"L1":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"L2":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"L3":{"advisory":{"agy":"ACCEPT","codex":"REVISE","cursor-agent":"ACCEPT"},"advisory_accepts":2,"claude":"ACCEPT","supermajority_met":true},"M1":{"advisory":{"agy":"ACCEPT","codex":"REVISE","cursor-agent":"ACCEPT"},"advisory_accepts":2,"claude":"ACCEPT","supermajority_met":true},"N1":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true}},"validator_slate_sha256":"dee0534fd632ec6593a567ec88919a1ca00a86358bb549f264fe8fc88d98f577"}
```

## Decision rule and how it was enforced

Owner direction (2026-11-24, session-attested and bound in `charter.question`):
- claude is the only required member; codex, cursor-agent and agy are advisory.
- A candidate is approved only with claude ACCEPT **and** at least 2 of the 3 advisory ACCEPTs.
- Every advisory REVISE or REJECT is adjudicated by claude in a later ballot.

The ledger v2 engine sees only `required_members: ["claude"]`, so it cannot express this rule. The chair enforced it from the recorded advisory ballots in `all-ballots.json`, each of which is bound to its raw-response sha256 in the run state. Upstream support is verJSON/verjson-agents#556, which has **not** landed.

A consequence of that limitation: the engine treated each submitted round as final once claude accepted, and closed the state file. Rounds 2 and 3 therefore ran in continuation state files, each started at engine round 1, with `charter.board_round` recording the board round:

| Board round | Slate revision | State file | Slate SHA-256 |
|---|---|---|---|
| 1 | 1 | `run.sqlite` | `79234de5e288fd64d4796c9ffe4a889878020817953eb3bd4f543ec0eadd813d` |
| 2 | 2 | `run-cont.sqlite` | `31ecc6b8a3e00ee4f7fbcfe1606c171a057f7b180b7789cfdaea686729ab8ed2` |
| 3 | 3 | `run-cont2.sqlite` | `dee0534fd632ec6593a567ec88919a1ca00a86358bb549f264fe8fc88d98f577` |

Advisory dissent is tracked by the chair here, not in the engine's `retained_dissent`, which is empty because the engine sees no advisory dissent.

## Adjudications (claude, required member)

- **L1 (codex round 1, B1: issuer accepts an empty interior DNS label)** — adjudicated by claude in round 2: dismissed as merge blocker (kind (b)). Reason: Dismissed as a merge blocker: this is kind (b) under the validator contract. Compose cannot misread SSO_AUTHORITY, because '$' is rejected (src/secrets/render.ts:247) and the value is a plain environment string (:311). Traefik never sees it. Vaultwarden's discovery against a host that cannot resolve fails, so SSO login is refused and no access widens. That is the same fail-closed availability class as the rulings in run-12 E2, run-13 F1 and N1. With a bootstrap host, the failure appears at first-owner enrollment, before anyone depends on the vault. With an open re-render, the operator must confirm a fresh SSO login after an issuer change, and the break-glass at :605 restores password access. Record as debt with the fix in my B1 concern.
- **L2 (codex round 1, B2: force-recreate statement wrong for in-place writes)** — adjudicated by claude in round 2: dismissed as merge blocker (kind (b)). Reason: Dismissed as a merge blocker: kind (b). The sentence at src/secrets/render.ts:588 overstates the delay: backup.sh stops the containers (src/secrets/scripts.ts:50) and then runs 'docker compose up -d' in cleanup, and that start rereads a changed traefik.yml or client secret. The worst outcome shown is codex's scenario: a client secret staged early is loaded by the backup restart before the identity provider accepts it. That is SSO unavailability, which fails closed (run-15 H4, run-16 J5). A restart that loads traefik.yml does not widen ingress, because traefik.yml is identical for bootstrap and open renders and the access rules live in routes.yml, which the text already says reloads at once. The chair's fix needs one correction: Docker re-resolves a bind-mount source at every container start, so a new-inode write is also picked up by a plain restart, not only by a force-recreate. The cleanup 'up -d' also applies a changed compose.yaml, and the archive would then hold the new files. The backup must therefore come before any change to the install directory, not only before a change to mounted files.
- **L3 (codex round 1, B2: saved-config remedy order)** — adjudicated by claude in round 2: dismissed as merge blocker (kind (b)). Reason: Dismissed as a merge blocker: kind (b). The only product path that writes /data/config.json is the admin page, which applies its settings at once, so an override found by the check at src/secrets/render.ts:590 is already in effect, and a restart neither creates nor widens it. Codex's harmful ordering needs a config.json copied by hand into the running volume after startup. That act is outside every remaining instruction: the isolated restore at :610 deletes config.json first, and live restore is forbidden. Even then, the check runs after every change and the fix is prompt removal. Reordering the remedy, so the file is copied out and removed before any restart, is the right debt fix.
- **codex round-2 REVISE on B2, L3, M1 (same saved-config sentence, rebuttal that back up can mean backup.sh)** — adjudicated by claude in round 3: dismissed as merge blocker (kind (b)). Reason: Adjudication of codex's round-2 rebuttal: dismissed as a merge blocker, kind (b). I weighed the reading that 'back up' at src/secrets/render.ts:590 means running backup.sh. Even so, backup.sh only stops and restarts the existing containers. It changes neither ingress nor the routes.yml allowlist. Ingress is public only if it was already open, and Vaultwarden restarts with the same /data it served before the backup. Through the product, /data/config.json is created only by saving in the admin page. Vaultwarden applies that save to the running process at once, so any SSO_ONLY=false override the check finds is already serving password login. The restart neither creates nor widens it. The harm only arises in codex's scenario: a config.json copied by hand into the live volume after startup and never loaded. No remaining instruction leads there. The isolated restore at :610 deletes config.json before start, and restore over the live host is forbidden at :588 and :610. I accept codex's point that 'back up' is ambiguous and that the remedy is in the wrong order; that is wording debt. Fix as in D1: copy config.json to a root-only location outside the install directory. Remove it. Force-recreate Vaultwarden with sudo docker compose up -d --force-recreate --wait vaultwarden. Repeat the test ! -e check and, with SSO-only, the password-grant refusal. Assert this order at tests/secrets-render.test.ts:595.
- **codex round-3 REVISE on B2, L3, M1** — adjudicated by none (round limit): retained dissent; it restates the L3 finding and scenario claude adjudicated in round 3 with the round-2 rebuttal before it, and adds no new scenario; no later ballot exists under max_rounds 3.

## Kind (b) debt for a follow-up issue linked from #410 (non-blocking)

1. L1 (src/secrets/render.ts:246-264, issuer check): In src/secrets/render.ts:246-264, reject an issuer hostname that is not an IP literal and has an empty label. Keep IP literals. Add parser tests: 'https://login..example.com/realms/team' is rejected and a valid multi-label issuer is accepted.
2. L2 (src/secrets/render.ts:588, bind-mount sentence): At src/secrets/render.ts:588 replace the sentence with: 'Compose does not recreate a container when only a bind-mounted file changes, but every container start reads the current files: a changed traefik.yml or client secret takes effect at that container's next start, including the stop and start backup.sh performs, and an in-place write to routes.yml takes effect at once. Run backup.sh and keep the current files before changing any file in <installDir>, because backup.sh restarts the stack and archives the files then in place.' Assert at tests/secrets-render.test.ts:505.
3. L3 (src/secrets/render.ts:590, saved-config remedy; codex holds kind (a), retained dissent): At src/secrets/render.ts:590 replace the sentence with: 'If the file exists, copy it to a root-only location outside <installDir>, remove it, then run sudo docker compose up -d --force-recreate --wait vaultwarden, repeat the check, and with SSO-only confirm the password grant is refused.' Extend tests/secrets-render.test.ts:595. codex's alternative, recorded for the owner: Require owner-supervised handling of a failed saved-config check. Verify public ingress is closed before any restart. Preserve config.json in a root-only location outside the install directory using a file copy that leaves services running, then remove the override and verify its absence before restarting Vaultwarden. Keep public ingress closed until the file's absence and, for an SSO-only deployment, password-grant refusal are verified after restart. Assert the sequence at tests/secrets-render.test.ts:595.
4. D1 (1) src/secrets/render.ts:588 and :605: 'keep the current files in a root-owned 0700 directory outside the install directory, and a fresh backup.sh archive'.
5. D1 (2) src/secrets/render.ts:610: list 'docker compose cp vaultwarden/. vaultwarden:/data', 'docker compose cp redis/. redis:/data' and 'docker compose cp letsencrypt/. traefik:/letsencrypt'; assert all three in tests/secrets-render.test.ts:505.
6. D1 (3) ADR 0038 break-glass bullet: append '(no apply procedure is rendered; see Apply, rollback and recovery above; owner-supervised until #412)'.
7. D1 (4) Record the bootstrap-enrollment, break-glass and isolated-restore conversion scope in #412's body and its CLAUDE.md index line, or in a separate issue CLAUDE.md points to.
8. Tests: add parser tests for L1 (rejected 'https://login..example.com/realms/team', accepted multi-label issuer, IPv4 and IPv6 literals) and wording assertions for L2 and L3 at tests/secrets-render.test.ts:505 and :595.

## Out of scope

- verjson-cli#412: tested apply.sh, rollback.sh and recover.sh carrying the run-17 K1-K6 requirements (owner option (c)); the absence of a rendered procedure is out of scope.
- Process: record the CI run on head 27ae92f024 and the host README sha256 on #410; merge stays subject to the org guard (assert-mergeable-head.sh, gh pr merge --match-head-commit 27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f).
- verJSON/verjson-agents#556: native advisory-member and supermajority support in the ARB engine (not yet landed).

## Retained dissent

**codex (advisory), round 3: REVISE on B2, L3 and M1** (`src/secrets/render.ts:590`; test `tests/secrets-render.test.ts:595`).

Reason: Kind (a). The check at src/secrets/render.ts:590 applies whenever config.json exists. If a legacy file was copied into the volume after Vaultwarden started, its override can still be inactive when the operator finds it. Reading 'back up' as the bundle's backup.sh is reasonable because the same README recommends that script for a consistent snapshot. Its cleanup at src/secrets/scripts.ts:50 starts Vaultwarden and Traefik before the operator removes the file. On an open-ingress host, an override of SSO_ONLY to false can then make password login publicly reachable for eligible accounts. The admin page's immediate-apply behavior addresses files written by that page. The prescribed response still creates exposure when the check finds an inactive file.

Correction requested: Require owner-supervised handling of a failed saved-config check. Verify public ingress is closed before any restart. Preserve config.json in a root-only location outside the install directory using a file copy that leaves services running, then remove the override and verify its absence before restarting Vaultwarden. Keep public ingress closed until the file's absence and, for an SSO-only deployment, password-grant refusal are verified after restart. Assert the sequence at tests/secrets-render.test.ts:595.

Raw response sha256 `fcaa5f5dc77487b18d7ca5f220629a78f4e48219eec6882bfb311afacee4d6ff`. claude dismissed the same finding in rounds 2 and 3. cursor-agent and agy classed it kind (b) in both rounds. The owner may override this classification.

## B1 · secrets render code is correct and secure at the head

Apart from L1 as adjudicated, no correctness or security defect remains in src/secrets at the head for the full diff 8d1fc2c..27ae92f024; run 1-16 code findings stay fixed, including run-12 E1 (email local parts are dot-atom and at most 64 octets, domain labels are non-empty without leading or trailing hyphens, and IDNA xn-- top-level labels are accepted in either case) and run-10 Q1: managementCidr is validated by value (canonicalized through URL, IPv4-embedded IPv6 forms rejected by their expanded groups, and a BlockList rejecting unspecified, this-network, reserved/broadcast, loopback, link-local and multicast addresses) and the canonical form is rendered; NAT64 and 6to4 spellings remain accepted and fail closed. The renderer pins DATA_FOLDER and CONFIG_FILE to the paths checked by the apply and restore instructions. It renders an empty signup domain whitelist and DISABLE_ADMIN_TOKEN=false. Bootstrap can supply a temporary ADMIN_TOKEN through the host env file for authenticated admin access. The operator reported the expected live environment, an absent saved config, a refused password grant and disabled /admin at the c8ff6d6 apply. The chair verified locally that the head renders the same compose.yaml, routes.yml, traefik.yml, render-options.json and backup.sh as c8ff6d6, and that only the generated README.md differs from that render. Interactive SSO, invitation acceptance through SSO and event-log entries remain owner-pending.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=no valid ballot) |
|---|---|
| 1 (rev 1) | claude:A / codex:R / cursor-agent:— / agy:A |
| 2 (rev 2) | claude:A / codex:A / cursor-agent:A / agy:A |
| 3 (rev 3) | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**.

- [src/secrets/render.ts:58](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L58)
- [src/secrets/render.ts:64](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L64)
- [src/secrets/render.ts:89](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L89)
- [src/secrets/render.ts:96](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L96)
- [src/secrets/render.ts:107](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L107)
- [src/secrets/render.ts:165](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L165)
- [src/secrets/render.ts:166](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L166)
- [src/secrets/render.ts:100](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L100)

## B2 · ADR 0038, README, NEXT and the generated README faithfully describe the code at the head

Apart from L2 and L3 as adjudicated, every behavioral statement in ADR 0038, the README secrets section, the NEXT fragment and the generated README matches the code at the head, and no statement that remains would lead an operator into an unsafe action. Run-1 D3 through run-16 J6 stay fixed as runtime and parser behavior. Owner decision after run 17 (option (c)): the generated README no longer renders any open-ingress apply, rollback or emergency-recovery procedure and says so; it states that Traefik reloads routes.yml as soon as it changes, so copying routes.yml into place exposes the new routes at once; that any such change must keep the current files and a fresh backup.sh archive and must not use the restore steps on the live host; that routes.yml and traefik.yml are single-file bind mounts that follow the file's inode, so a tool that writes a new file (mv, sed -i, many editors) leaves Traefik serving the previous content; that Compose does not recreate a container when only a bind-mounted file changes (Traefik keeps a changed traefik.yml, and Vaultwarden a changed client secret, until force-recreated); and that after an image digest change starting the previous images against possibly migrated data is unsafe. The isolated-host restore copies each saved directory with a trailing /. source (for example docker compose cp vaultwarden/. vaultwarden:/data), and its 'except the emergency recovery' exception is gone, so it reads 'Never restore over the only live database.' The SSO-only break-glass 'apply the bundle' now adds '(no apply procedure is rendered; keep the current files and a fresh backup.sh archive)'. The 'after every apply' saved-config check now reads 'after every change to a running host'. ADR 0038 records the withdrawal, the move to tested scripts (#412) and that updates to an enrolled host are owner-supervised until then; NEXT, the root README and the operations record (summary only) are aligned; CLAUDE.md adds a scoped rule that new live-host procedures with more than three stateful steps, a lock, a rollback branch or a destructive recovery ship as tested scripts. Vaultwarden runtime statements stay labeled external and owner-pending.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=no valid ballot) |
|---|---|
| 1 (rev 1) | claude:A / codex:R / cursor-agent:— / agy:A |
| 2 (rev 2) | claude:A / codex:R / cursor-agent:A / agy:A |
| 3 (rev 3) | claude:A / codex:R / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 2/3 (codex REVISE, cursor-agent ACCEPT, agy ACCEPT); **condition met**.

- [docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md:33](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md#L33)
- [docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md:44](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md#L44)
- [docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md:46](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md#L46)
- [docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md:48](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md#L48)
- [docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md:50](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md#L50)
- [docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md:58](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md#L58)
- [docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md:84](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md#L84)
- [README.md:302](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/README.md#L302)

## L1 · Advisory finding (codex, round 1, B1): the issuer validator accepts an HTTPS URL with an empty interior DNS label; chair classification kind (b)

Confirmed by the chair: parseSecretsOptions accepts vaultwardenSsoAuthority 'https://login..example.com/realms/team' (WHATWG URL canonicalization keeps the empty label). codex's scenario: rendered as SSO_AUTHORITY with SSO_ONLY=true, the unresolvable issuer locks users out of browser and client login. Chair classification: kind (b) under the validator contract — no concrete misinterpretation by Compose, Traefik or Vaultwarden is shown (Vaultwarden's OIDC discovery fails against an unresolvable host and SSO login is refused, which fails closed with no access widened), consistent with the run-12 E2, run-13 F1 and later availability rulings and with N1 (IP-literal issuers); the identity-provider outage break-glass at src/secrets/render.ts:605 restores password access. Fix: in the issuer check, reject a hostname with an empty label (split on '.', every label non-empty), keep canonical IP literals, and add parser tests for 'https://login..example.com/realms/team' (rejected) and a valid multi-label issuer (accepted). ADJUDICATION (required member claude; advisory members vote too): ACCEPT means the advisory REVISE is dismissed as a merge blocker for the stated reason and recorded as kind (b) debt with the fix above; REVISE means you uphold it as kind (a), which blocks merge (state the concrete unsafe scenario and the correction).

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=no valid ballot) |
|---|---|
| 1 (rev 1) | claude:— / codex:— / cursor-agent:— / agy:— |
| 2 (rev 2) | claude:A / codex:A / cursor-agent:A / agy:A |
| 3 (rev 3) | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**.

- [src/secrets/render.ts:260](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L260)
- [src/secrets/render.ts:311](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L311)

## L2 · Advisory finding (codex, round 1, B2): the bind-mount fact says a changed traefik.yml or client secret waits for a force-recreate, but an in-place write is loaded by any restart, including backup.sh's; chair classification kind (b)

Confirmed from the text and documented container behavior: a file written in place (same inode) is visible in the container at once, and a plain stop/start reloads it; Vaultwarden reads SSO_CLIENT_SECRET_FILE and Traefik its static traefik.yml at startup; backup.sh stops and restarts the existing containers without recreating them. So the sentence 'Traefik keeps a changed traefik.yml, and Vaultwarden a changed client secret, until that container is force-recreated' is exact only for a file written as a new inode. codex's scenario: an operator writes a new OIDC client secret in place, then runs backup.sh as the text requires before activating the secret at the identity provider; the backup restart loads it and SSO-only users are locked out. Chair classification: kind (b) — the consequence is fail-closed SSO unavailability with no access widened (run-15 H4, run-16 J5 availability rulings); a staged traefik.yml from the same render does not widen ingress because routes.yml carries the access rules and is stated to reload at once. Fix: at src/secrets/render.ts:588 say 'Compose does not recreate a container when only a bind-mounted file changes: a file copied over in place is read at the container's next start, including the restart backup.sh performs, while a file written as a new file (mv, sed -i, many editors) is seen only after the container is force-recreated; take the backup before changing any mounted file'; extend tests/secrets-render.test.ts:505. ADJUDICATION (required member claude; advisory members vote too): ACCEPT means the advisory REVISE is dismissed as a merge blocker for the stated reason and recorded as kind (b) debt with the fix above; REVISE means you uphold it as kind (a), which blocks merge (state the concrete unsafe scenario and the correction).

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=no valid ballot) |
|---|---|
| 1 (rev 1) | claude:— / codex:— / cursor-agent:— / agy:— |
| 2 (rev 2) | claude:A / codex:A / cursor-agent:A / agy:A |
| 3 (rev 3) | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**.

- [src/secrets/render.ts:588](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L588)
- [src/secrets/scripts.ts:50](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/scripts.ts#L50)

## L3 · Advisory finding (codex, rounds 1 and 2, B2/L3): the saved-config paragraph's 'back up, remove it and recreate Vaultwarden' could restart Vaultwarden with a saved override before it is removed; claude dismissed it as kind (b) in round 2; codex holds kind (a)

Confirmed text: 'If the file exists, back up, remove it and recreate Vaultwarden.' codex's scenario: a legacy config.json copied into the running volume after Vaultwarden started, overriding SSO_ONLY to false; running backup.sh as the 'back up' restarts Vaultwarden, which loads the override and serves password login publicly until the operator removes it. Chair classification: kind (b) — the scenario requires someone to write config.json into the live volume by hand (the admin page, the only product path that writes it, applies its settings at once, so the override is already in effect when the check finds the file, and removing it promptly is the remedy); 'back up' names the file in context, not backup.sh. Fix: at src/secrets/render.ts:590 say 'If the file exists, copy it to a root-only location, remove it, then force-recreate Vaultwarden (sudo docker compose up -d --force-recreate --wait vaultwarden) and repeat the check and the password-grant refusal'; extend the saved-config test. Round 2: claude dismissed it as a merge blocker (kind (b)): the only product path that writes /data/config.json is the admin page, which applies its settings at once, so an override found by the check is already in effect and a restart neither creates nor widens it; codex's harmful ordering needs a config.json copied by hand into the running volume after startup, which is outside every remaining instruction (the isolated restore deletes config.json first, and live restore is forbidden); the fix is reordering the remedy. cursor-agent and agy agreed (kind (b)). codex held kind (a) in round 2: an operator can read 'back up' as backup.sh, whose cleanup starts Vaultwarden and Traefik before the file is removed, and if the file overrides SSO_ONLY to false an eligible account can log in by password through public ingress; the admin page's immediate-apply behavior does not address a hand-copied file. ROUND-3 ADJUDICATION (required member claude): ACCEPT confirms the dismissal (kind (b), fix as in D1: copy config.json to a root-only location outside the install directory, remove it, force-recreate Vaultwarden, repeat the check and, with SSO-only, the password-grant refusal) after weighing codex's round-2 rebuttal; REVISE upholds it as kind (a) and blocks merge. Advisory members: ACCEPT agrees with kind (b); REVISE holds kind (a).

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=no valid ballot) |
|---|---|
| 1 (rev 1) | claude:— / codex:— / cursor-agent:— / agy:— |
| 2 (rev 2) | claude:A / codex:R / cursor-agent:A / agy:A |
| 3 (rev 3) | claude:A / codex:R / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 2/3 (codex REVISE, cursor-agent ACCEPT, agy ACCEPT); **condition met**.

- [src/secrets/render.ts:590](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L590)

## B3 · src/ carries no organization-specific deployment values

No organization hostname, email, IP, stack name, install path, realm or IdP client registration is hard-coded in src/secrets at the head; deployment values come only from arguments or a JSON options file. Remaining literals are generic product defaults.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=no valid ballot) |
|---|---|
| 1 (rev 1) | claude:A / codex:A / cursor-agent:— / agy:A |
| 2 (rev 2) | claude:A / codex:A / cursor-agent:A / agy:A |
| 3 (rev 3) | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**.

- [src/secrets/render.ts:53](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L53)
- [src/secrets/render.ts:57](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L57)
- [src/secrets/render.ts:62](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L62)
- [src/secrets/render.ts:236](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L236)
- [tests/secrets-render.test.ts:367](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/tests/secrets-render.test.ts#L367)

## B4 · Tests prove the behaviors, including every prior-run fix

The chair reports 157/157 secrets tests and 987/987 CLI and 48/48 cli-schema tests (npm test exit 0) at this head (the count fell because the prose-procedure tests were removed with the procedure); against the prior source (91964270bc) 2 of 157 fail. In scratch copies of the head source, each of these injected defects fails the withdrawal test at tests/secrets-render.test.ts:505: removing the trailing /. restore wording, removing 'must not use the restore steps below on the live host', removing '(mv, sed -i, many editors)', and restoring the 'except the emergency recovery' exception. The tests cover rendered values, parser behavior and instructions; host behavior remains integration evidence.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=no valid ballot) |
|---|---|
| 1 (rev 1) | claude:A / codex:A / cursor-agent:— / agy:A |
| 2 (rev 2) | claude:A / codex:A / cursor-agent:A / agy:A |
| 3 (rev 3) | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**.

- [tests/secrets-render.test.ts:260](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/tests/secrets-render.test.ts#L260)
- [tests/secrets-render.test.ts:227](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/tests/secrets-render.test.ts#L227)
- [tests/secrets-render.test.ts:574](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/tests/secrets-render.test.ts#L574)
- [tests/secrets-render.test.ts:581](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/tests/secrets-render.test.ts#L581)
- [tests/secrets-render.test.ts:588](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/tests/secrets-render.test.ts#L588)
- [tests/secrets-render.test.ts:602](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/tests/secrets-render.test.ts#L602)
- [tests/secrets-render.test.ts:608](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/tests/secrets-render.test.ts#L608)
- [tests/secrets-render.test.ts:617](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/tests/secrets-render.test.ts#L617)

## N1 · Known nits are non-blocking

The renderer accepts canonical HTTPS issuers with IP-literal hosts. ACME email shares the validator exercised by the organization-creator dollar-sign tests. The bundle leaves the listed sharing, rate-limit, push, and WebSocket settings unset. Operators can supply settings supported by the pinned Vaultwarden version through the host env file. Unset supported settings use that version's defaults when no saved configuration overrides them. Sharing policy is outside ADR 0038. These points do not block merge.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=no valid ballot) |
|---|---|
| 1 (rev 1) | claude:A / codex:A / cursor-agent:— / agy:A |
| 2 (rev 2) | claude:A / codex:A / cursor-agent:A / agy:A |
| 3 (rev 3) | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**.

- [src/secrets/render.ts:260](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L260)
- [src/secrets/render.ts:89](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L89)
- [src/secrets/render.ts:448](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L448)

## D1 · Kind (b) low-risk debt and recorded process items: non-blocking, for a follow-up issue linked from #410

These change no runtime file and lead to no unsafe operator action; they do not block merge and are tracked with these exact fixes (claude, round 1): (1) at src/secrets/render.ts:588 and :605 say 'keep the current files in a root-owned 0700 directory outside the install directory, and a fresh backup.sh archive'; (2) at src/secrets/render.ts:610 list all three copies: 'docker compose cp vaultwarden/. vaultwarden:/data', 'docker compose cp redis/. redis:/data', 'docker compose cp letsencrypt/. traefik:/letsencrypt', asserted in tests/secrets-render.test.ts:505; (3) at the ADR 0038 break-glass bullet append '(no apply procedure is rendered; see Apply, rollback and recovery above; owner-supervised until #412)'; (4) add the bootstrap-enrollment, break-glass and isolated-restore conversion to #412's body and its CLAUDE.md index line, or a separate issue that CLAUDE.md points to; (5) L1-L3 fixes if claude dismisses them as blockers. codex's round-1 B4 concern is resolved by chair evidence: on Docker 29.9.3 / Compose, 'docker compose cp src/. svc:/data' into a created, never-started service with a named volume placed the files directly under /data, and they were present at startup. Process items (claude): the CI run on head 27ae92f024 and the host README sha256 on #410 are recorded outside this board; merge stays subject to the org guard (assert-mergeable-head.sh, --match-head-commit 27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f). Out of scope: #412 scripts (run-17 K1-K6).

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=no valid ballot) |
|---|---|
| 1 (rev 1) | claude:— / codex:— / cursor-agent:— / agy:— |
| 2 (rev 2) | claude:A / codex:A / cursor-agent:A / agy:A |
| 3 (rev 3) | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**.

- [src/secrets/render.ts:588](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L588)
- [src/secrets/render.ts:610](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L610)
- [docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md:84](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/docs/decisions/0038-vaultwarden-member-lifecycle-policy/README.md#L84)

## M1 · PR #411 at head 27ae92f024 is acceptable to merge if claude dismisses L1-L3 as blockers and the supermajority condition is met

Under the charter calibration and scope, PR #411 at exactly 27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f is acceptable to merge provided claude dismisses L1, L2 and L3 as merge blockers (kind (b), fixes in D1); if claude upholds any of them as kind (a), the head is not acceptable. Approval of this and every other candidate requires claude ACCEPT and at least 2 of the 3 advisory members (codex, cursor-agent, agy) ACCEPT in the final round, with every advisory REVISE or REJECT adjudicated by claude; the chair states in the decision record whether that condition was met. Merge stays subject to the org merge guard (exact head, required checks, approvals). The operations record's owner-pending host checks (interactive SSO, invitation through SSO, event-log entries) remain post-merge verification. This acceptance does not carry to any other head.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=no valid ballot) |
|---|---|
| 1 (rev 1) | claude:A / codex:R / cursor-agent:— / agy:A |
| 2 (rev 2) | claude:A / codex:R / cursor-agent:A / agy:A |
| 3 (rev 3) | claude:A / codex:R / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 2/3 (codex REVISE, cursor-agent ACCEPT, agy ACCEPT); **condition met**.

- [src/secrets/render.ts:588](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L588)
- [tests/secrets-render.test.ts:505](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/tests/secrets-render.test.ts#L505)
- [src/secrets/render.ts:107](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L107)
- [src/secrets/render.ts:605](https://github.com/verJSON/verjson-cli/blob/27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f/src/secrets/render.ts#L605)

## Members, identity and transport

| Member | Role | Identity | Prompt transport |
|---|---|---|---|
| claude | required | verified claude-opus-5-5 (response modelUsage, all three rounds) | prompt file on stdin (claude -p), all rounds |
| codex | advisory | unverified; requested gpt-6.2-sol; codex exec output names no served model | prompt file on stdin (codex exec -), all rounds |
| cursor-agent | advisory | unverified; JSON result carries no served-model field | rounds 1-2: one-line argument to read the 0400 workspace copy (three calls returned narration before the JSON: malformed_ballot, not repaired); round-2 re-request and round 3: identical prompt file on stdin after a one-line stdin probe |
| agy | advisory | unverified; JSON response carries no served-model field | one-line argument to read the 0400 workspace copy, all rounds |

- **Prompt files:** each round's prompt was a 0600 file in the 0700 run directory: round 1 151,020 bytes, round 2 144,001 bytes, round 3 91,487 bytes. No prompt was passed as a single CLI argument.
- **Workspace copies:** the copies in `ws/` were 0400.
- **cursor-agent failures:** in round 1, both calls failed as `malformed_ballot` (narration before the JSON), so cursor-agent has no round-1 vote. Round 2's first call failed the same way. After a one-line probe showed cursor-agent reads stdin, the identical round-2 prompt file was re-sent on stdin and recorded; round 3 used stdin too.
- **No repairs:** no ballot was repaired.

## Charter

- **Question:** is PR #411 at exact head 27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f acceptable to merge? Scope is the full diff 8d1fc2c..27ae92f024, with the delta from 91964270bc (664b049, 80bc572, 42daee5, 66524b3, ddc4ac5, 27ae92f) highlighted.
- **Rules:**
  - the kind (a)/(b) calibration, scope rule and validator contract, all unchanged;
  - owner option (c): run-17 K1–K6 go to #412, and a missing procedure is out of scope; a finding is in scope only if remaining text would lead an operator into an unsafe action, or it is a runtime or parser defect;
  - the advisory membership and supermajority rule;
  - max 3 rounds with the round-2 refusal rule;
  - file prompt transport.

  All of these are in `charter.question`, so the slate digest binds them.
- **Head provenance:** `git fetch`, then `git rev-parse HEAD` and `origin/feat/410-vaultwarden-lifecycle`, plus `gh pr view 411` `headRefOid`, all return `27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f`, at charter time and again after round 3.
- **Disclosure:**
  - **Every round:** the full head `src/secrets/render.ts` and `backupScript`, plus a citation packet in rounds 1–2.
  - **Rounds 1–2:** the `src/` and docs delta, ADR 0038, NEXT, the root README and CLAUDE.md.
  - **Round 1 only:** the `tests/` delta.
  - **Operations material:** a chair summary only, with no IP addresses, emails, issuer URL, host inventory or backup names. The only email-shaped strings sent were `example.com` test fixtures from the test delta.
  - **Secrets and access:** no secrets were sent, and members ran read-only.

## Evidence

Coordinator facts, stated as given:
- The host README was replaced with this head's render, recorded on #410.
- An independent reviewer verified that the runtime files are byte-identical to the 91964270 render for 5 option sets.
- An independent code-reviewer approved 27ae92f after 3 rounds.

Chair-verified locally, with no host access:
- **Tests:** full `npm test` passes 987/987 CLI and 48/48 cli-schema, exit 0. The secrets tests pass 157/157, and 2 of 157 fail against 91964270bc's source.
- **Defect injection:** each of four injected defects fails `tests/secrets-render.test.ts:505`: removing the trailing-`/.` wording, removing the live-host restore prohibition, removing the "(mv, sed -i, many editors)" clause, or restoring the old emergency exception.
- **Runtime files:** for the committed options, with and without `--no-vaultwarden-sso-only`, the head renders all five runtime files byte-identically to c8ff6d6 and to 91964270bc.
- **Restore copy:** on Docker 29.9.3 with Compose, `docker compose cp src/. svc:/data` into a created, never-started service with a named volume put the files directly under `/data`.
- **L1:** `parseSecretsOptions` accepts `https://login..example.com/realms/team`.
- **Not executed:** the L2 and L3 container and admin-page behavior is stated from documentation.

## Observations and limits

- **Engine support:** advisory membership needs engine support (#556). Until then, every closing round is "APPROVED" to the engine and the chair must enforce the rule. That forced two continuation state files in this run.
- **cursor-agent:** it narrates when reading a file through its tools, so stdin is the reliable transport for it.
- **Post-merge checks:** the owner-pending interactive SSO, invitation-through-SSO and event-log checks remain post-merge verification.
- **Process metrics:** rounds started at 2026-11-25T01:44:35Z, 2026-11-25T01:53:47Z and 2026-11-25T01:58:04Z. There were 12 member calls plus 2 re-requests: 11 recorded and 3 malformed (all cursor-agent). claude cost USD 1.40, 1.18 and 0.90. Human ratification is pending with the verjson-cli repository owner.

## Standards grounding
- status: verified
- reason: work_start_baseline
- config_origin: installed
- config_provenance: installer-default
- source: verJSON/verjson-docs:governance/architecture-review-context.json@main
- config_sha: 57932dc678cb089a99c19e6eb3aba78329933eab
- manifest_sha256: 08b326728b4ac727a68560147d9bd4ff57feebbf2bcb1dddb7943ecd08c81922 (resolver-manifest-v1)
- binding_sha256: 4554b9c6ed5c684b5cfcd7ab5bb89ce31e3ef0e2ad41e8ce4825c0b1283c4ce9 (resolver-binding-v1)
- canonical_complete: true
- baseline_eligible: true
- eligibility_reason: work_start_baseline
- work_started_at: 2026-11-24T18:46:31Z
- baseline_observed_at: 2026-11-24T18:46:24Z
- sources:
  - engineering-standards VerJSON/verjson-agents@e0510d2f906fd404c623d5ff6995f42093381652:CANON.md [git_commit, available]
  - code-style VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:agents/code-style.md [git_commit, available]
  - ci-tiers VerJSON/.github@04fc2ec904492fcc0c8f6eadfe1091df6c682715:docs/ci-workflow-tiers.md [git_commit, available]
  - node-workflows VerJSON/.github@04fc2ec904492fcc0c8f6eadfe1091df6c682715:docs/node-workflows.md [git_commit, available]
  - protected-reviews VerJSON/.github@04fc2ec904492fcc0c8f6eadfe1091df6c682715:docs/ruleset-conformance.md [git_commit, available]
  - merge-admission-decision VerJSON/.github@04fc2ec904492fcc0c8f6eadfe1091df6c682715:docs/decisions/0257-merge-admission-requires-the-exact-head-review-receipt/README.md [git_commit, available]
  - merge-admission-runbook VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:agents/merge-admission.md [git_commit, available]
  - delivery-workflow VerJSON/verjson-agents@e0510d2f906fd404c623d5ff6995f42093381652:rules/workflow.md [git_commit, available]
  - infrastructure-overview VerJSON/.github@04fc2ec904492fcc0c8f6eadfe1091df6c682715:docs/infrastructure/README.md [git_commit, available]
  - runner-routing VerJSON/.github@04fc2ec904492fcc0c8f6eadfe1091df6c682715:docs/runner-routing.md [git_commit, available]
  - runner-lanes-decision VerJSON/.github@04fc2ec904492fcc0c8f6eadfe1091df6c682715:docs/decisions/0040-runner-lanes-and-admission-axes/README.md [git_commit, available]
  - platform-composition VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:architecture/platform-composition.md [git_commit, available]
  - capability-catalog VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:catalog/packages.md [git_commit, available]
  - security-practices VerJSON/verjson-agents@e0510d2f906fd404c623d5ff6995f42093381652:rules/security.md [git_commit, available]
  - security-policy VerJSON/.github@04fc2ec904492fcc0c8f6eadfe1091df6c682715:SECURITY.md [git_commit, available]
  - dependency-principles VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:architecture/dependency-principles.md [git_commit, available]
  - ownership-boundaries VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:architecture/ownership-boundaries.md [git_commit, available]
  - decision-placement VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:governance/decision-placement.md [git_commit, available]
  - agent-message-contracts-decision VerJSON/verjson-agents@e0510d2f906fd404c623d5ff6995f42093381652:docs/decisions/0036-versioned-schema-contracts-for-agent-messages/README.md [git_commit, available]
  - agent-message-contracts-adoption VerJSON/verjson-agents@e0510d2f906fd404c623d5ff6995f42093381652:contracts/agent-messages/adoption.json [git_commit, available]
  - agile-ai-manifesto VerJSON/agile-ai-manifesto@15d91cb19be0b3889165b7a7ca7bb905afbcd831:public/index.html [git_commit, available]
  - tda-review-guide VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:governance/technical-design-authority.md [git_commit, available]
- gaps: none
