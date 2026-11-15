# 0090 — Keep human approval available when AI review is opted in

- **Date:** 2026-08-10
- **Status:** Accepted
- **Issue:** [Verjson/.github#732](https://github.com/Verjson/.github/issues/732)
- **Supersedes:** [ADR 0080](../0080-one-automatic-paid-ai-review-per-head/README.md)
- **Amends:** [ADR 0079](../0079-head-bound-ai-authorization-and-native-auto-merge/README.md), [ADR 0082](../0082-receipt-bound-provider-budget-policy/README.md)

## Context

The required AI workflow coupled merge availability to both its own implementation and
an external model provider. Run `31450341729` failed before review because trusted
classifier code referenced an unset shell variable. Repair PR #730 then needed an
administrator bootstrap because the broken workflow was its own required gate. After
that repair, run `31451246712` reached OpenAI but returned HTTP 429 without a verdict.
PR #727 had green deterministic CI and an independent review but remained blocked.

AI review can add evidence, but provider or workflow availability is not review
evidence. GitHub branch protection already requires current-head approval, CODEOWNERS,
resolved threads, linear history, and deterministic `shell-tests`. Those controls must
remain sufficient for an authorized human merge.

DeepSeek publishes an OpenAI-compatible Chat Completions API and JSON-output mode. Its
2026-08-10 price table lists, per million tokens, `deepseek-v4-pro` at $0.003625 cached
input, $0.435 cache-miss input, and $0.87 output; `deepseek-v4-flash` at $0.0028 cached
input, $0.14 cache-miss input, and $0.28 output. The table warns that rates may change,
so live pricing cannot be caller-controlled data.

## Decision

`AI_REVIEW_AUTHORITY` is an exact enum with safe default `human`:

- `human`: AI may post advisory comments but cannot approve or merge;
- `ai-approve`: a non-blocking opted-in verdict may mint the dedicated App approval;
- `ai-merge`: that exact App approval may also dispatch trusted terminal promotion.

Every unknown value fails before model spend. Authority joins provider, model, budgets,
pricing version, requesting actor, and permission in the immutable exact-head receipt.
The required workflow evaluates successfully when AI is skipped, blocking, rate-limited,
or inconclusive; none of those outcomes mints App approval. GitHub's ordinary human
approval rule therefore remains available in every mode. `hold`, `DO NOT MERGE`, draft,
stale-head, and malformed-policy checks remain terminal.

Model execution requires the normalized `ai-review` label or an explicit `re-review`.
The paid label requires maintain or admin permission. Later head events re-resolve the
label actor and current permission before creating another receipt; an unauthorized
label is removed when possible and remains fail-closed when cleanup fails. Unrequested
code uses the no-model human path. AI publishes comments only: even a blocking finding
never submits `CHANGES_REQUESTED`. Only the dedicated App may submit approval, and only
after the receipt grants `ai-approve` or `ai-merge`.

The `deepseek-v4-2026-08-10` policy permits exactly two passes: Pro with a $5.00 ceiling,
then Flash with its own $5.00 ceiling only when Pro produced no usable verdict. Any
usable blocking or non-blocking Pro verdict terminates the cascade; there is no third
automatic attempt. Complete serialized request bytes are conservatively priced as
cache-miss tokens before the call. Provider-reported cache hits, misses, prompt tokens,
completion tokens, exact model, single stopped choice, tool absence, and local verdict
semantics are validated after the call. Missing cache detail is priced entirely at the
cache-miss rate. A pricing change requires a new reviewed pricing-version allowlist.

`DEEPSEEK_API_KEY` is step-scoped and selected only for `Verjson/.github`. Generated
reusable callers declare the secret, but consumer repositories receive no organization
grant unless separately reviewed.

### 2026-08-13 amendment — observable thinking streams and provider-local fallback policy

The Pro pass explicitly enables DeepSeek thinking mode, fixes the supported
`reasoning_effort` to `high`, and uses temperature `0.2`; the Flash fallback
explicitly enables thinking but does not inherit Pro-only effort or temperature.
The budget-derived `max_tokens` remains capped at 65,536 and JSON-object,
tool-free, exact-model, usage, and cost validation are unchanged. Streaming emits
flushed progress at most every 30 seconds or 1 MiB, plus completion, using only
elapsed time and numeric counters. Verdict, reasoning, prompt, diff, key, payload,
and exception details never enter telemetry.

When a completed DeepSeek response passes exact-model, usage, cost, and JSON
transport extraction but later fails canonical source validation or publication,
the gate may retain a one-day diagnostic replay. The bundle contains a
schema-aware sanitized verdict object, projected validated numeric usage and
bounds, the exact reviewed head/repository/PR/pass/sensitive
classification/check identity, the trusted validator revision, and SHA-256
digests of the receipt policy and bounded inputs. Documented verdict fields,
including evidence, may quote reviewed source. Unknown provider field names and
values collapse to one fixed unknown-field key/value sentinel. The bundle is explicitly
non-authorizing and non-cacheable; no workflow consumes or downloads it. An
operator reproduces canonical validation by executing the validator from a
trusted `Verjson/.github` checkout at `trusted_review_sha` against a separate
target repository checkout at `reviewed_head`:

```bash
trusted_checkout=/path/to/trusted-review
target_checkout=/path/to/target-repository

git -C "$trusted_checkout" checkout "$(jq -r .provenance.trusted_review_sha replay.json)"
git -C "$target_checkout" checkout "$(jq -r .provenance.reviewed_head replay.json)"

VERDICT="$(jq -c .response.verdict replay.json)" \
SENSITIVE="$(jq -r .provenance.sensitive replay.json)" \
REVIEW_PASS="$(jq -r .provenance.review_pass replay.json)" \
REVIEW_REPOSITORY="$target_checkout" \
REVIEWED_HEAD_SHA="$(jq -r .provenance.reviewed_head replay.json)" \
GITHUB_OUTPUT="$(mktemp)" \
python3 "$trusted_checkout/scripts/ci-gate/review-verdict.py"
```

Raw SSE, reasoning, prompt, metadata, and diff input files, keys, headers,
unknown provider names and values, and exception details are never persisted, and an
incomplete transport or successful end-to-end review creates no uploaded replay.

A repository provider override is authoritative over inherited organization
fallback variables. Selecting Anthropic or OpenAI therefore normalizes stale
DeepSeek fallback model and budget values to empty before provider validation and
receipt generation; selecting DeepSeek still requires the exact Pro-to-Flash
cascade. This preserves receipt determinism while allowing a repository to choose
a supported single-pass provider without first clearing organization-wide
DeepSeek defaults.

### 2026-08-16 amendment — neutral advisory check runs and explicit approval success (#858)

The dedicated `AI review authorization` check run uses `neutral` whenever trusted
deterministic policy completed but the dedicated App did not persist an exact-head
approval. Its title and summary name the actual advisory state: blocking,
inconclusive, skipped, superseded, non-blocking without authority, or App approval
unavailable. `success` is reserved for a non-blocking verdict whose exact-head App
approval was persisted and verified. Trust failures still conclude `failure`.

This changes presentation, not authority. GitHub documents that a
[required status check may be successful, skipped, or neutral](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches#require-status-checks-before-merging)
before a protected branch can change. The completion job therefore accepts both
`neutral` and `success`, preserving the authorized human path. Terminal AI promotion
still requires `ai_authorized=true`, which is emitted only after the exact-head App
approval is read back and verified. A neutral blocking or inconclusive advisory can
satisfy check availability, but cannot mint an approval or dispatch a merge.

### 2026-11-15 amendment — identical pre-token failures do not consume the fallback pass (#2124)

Run `43737657166` on PR #2123, and the same pattern on #2116, failed DeepSeek SSE
with `HTTPError` before any completion tokens. The gate recorded only
`error_type=HTTPError`, classified the empty result as a semantic verdict, and
reserved Flash. Both exact-head passes were consumed with no verdict.

Pass 1 stays reserved because the slot is taken before invocation. Automatic
pass 2 is not reserved when that failure would repeat on Flash: HTTP auth
(`401`/`403`), quota (`402`/`429`), upstream (`408` and `5xx`), or a pre-token
transport error that never received an HTTP status. Model-specific HTTP classes
(`404`, `400`/`422`, and any other status) still follow the Pro-to-Flash
cascade. Logs and the advisory comment record the status code, status family
(`4xx`/`5xx`/`other`/`none`), and failure class. Response bodies, headers,
URLs, and exception text stay redacted.

The authorization check stays `neutral`, including the distinct
`provider_transport` advisory. No outcome mints App approval. Human approval
remains available. `re-review` is still required for another paid attempt and
does not raise the two-pass ceiling. A real unusable verdict, including empty
completed content, still fail-closes and may cascade.

The GitHub-visible contract is:

| Review state | Check conclusion | Meaning |
| --- | --- | --- |
| Blocking, inconclusive, skipped, superseded, provider transport, or human-only non-blocking | `neutral` | AI did not authorize the head; human approval remains available |
| Exact-head App approval persisted and verified | `success` | AI approval exists for this exact head |
| Receipt, trusted-head, or deterministic-policy failure | `failure` | Authorization evaluation failed closed |

## Consequences

- Deterministic CI and human review can authorize merge during any AI outage.
- Model spend is opt-in, bounded to two separately capped calls, and receipt-auditable.
- `ai-approve` cannot dispatch merge; `human` cannot mint any App approval.
- Retry and privileged-promotion boundaries independently decode the receipt and reject
  terminal merge unless its authority is exactly `ai-merge`.
- AI findings are evidence for reviewers, not an unreviewable merge veto.
- Existing six-field receipt envelopes decode as legacy human authority during rollout;
  new nine-field envelopes bind authority and fallback policy explicitly.
- Enabling AI approval or merge is a sensitive organization-variable change and keeps a
  human-reviewed rollout and rollback gate.
