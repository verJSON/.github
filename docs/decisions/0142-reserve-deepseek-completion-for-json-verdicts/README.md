# 0142 — Reserve DeepSeek completion for JSON verdicts

- **Date:** 2026-08-26
- **Issue:** [Verjson/.github#1110](https://github.com/Verjson/.github/issues/1110)
- **Category:** merge-gate AI review policy — **sensitive class**
- **Status:** Accepted
- **Amends:** [ADR 0090](../0090-human-first-opt-in-ai-review/README.md)
- **Supersedes:** ADR 0090's 2026-08-13 provider-local thinking-mode amendment

## Context

The merge gate asks DeepSeek for one tool-free JSON verdict. Thinking mode shares the
bounded completion with private reasoning that the gate cannot admit as a verdict. In
run `33002497981`, Pro produced reasoning without verdict content until the bounded
transport timeout, then Flash completed with an empty content string. Both exact-head
passes were correctly consumed without reusable evidence.

The gate needs a deterministic request policy that reserves the completion envelope
for the only accepted result: the JSON verdict. Provider tuning fields that apply to
reasoning mode do not strengthen that contract and can make the request internally
inconsistent.

## Decision

Every DeepSeek review request explicitly sets `thinking.type` to `disabled`, retains
JSON-object response mode, remains tool-free, and omits `reasoning_effort` and
`temperature`. This policy applies identically to Pro and Flash.

The stream parser rejects any non-empty `reasoning_content` even when the provider
accepts the non-thinking request. A successful stream still requires exactly the
requested model, one stopped assistant choice, a terminal marker, complete usage and
cache evidence, a budget-conformant cost, and non-empty schema-valid JSON verdict
content. Empty completed content remains a typed `json_decode` extraction failure with
content-free diagnostics.

Exact-head authorization, the cumulative two-pass cap, streaming bounds, immutable
pricing policy, provenance, replay retention, and fail-closed publication remain
unchanged.

## Consequences

- Output budget is available to the required JSON object instead of provider reasoning.
- Unexpected reasoning is evidence that the provider did not honor the reviewed request
  policy, so the pass fails closed rather than silently discarding it.
- Disabling reasoning may reduce depth on complex changes. The independent review gates
  and human path remain available; changing this provider policy requires another
  reviewed decision.

## Rollback

Disable DeepSeek selection while investigating provider compatibility. Do not restore
thinking mode without a new ADR that preserves bounded verdict production and the
existing exact-head and cost controls.

## 2026-09-09 — Stop cancelled and superseded provider work

[Issue #1278](https://github.com/Verjson/.github/issues/1278) reproduced a cancelled
Pro request in `verjson-ai#470` starting Flash against the previous commit. The
fallback returned a verdict, but exact-head authorization correctly rejected it.

Both fallback reservation and invocation require `!cancelled()`. This still admits
fallback after a failed or inconclusive primary in an active run, while cancellation
cannot create another paid attempt. Both DeepSeek steps re-read the current PR head
immediately before invoking the provider; stale heads skip and failed lookups stop.
The lookup uses the workflow token, which is removed from the provider child's
environment. Existing reservation accounting and exact-head authorization remain
controlling; a head change after the last read can still invalidate an in-flight
request, and neither a consumed reservation nor a stale verdict is recycled.

Each provider step has a five-minute wall-clock ceiling. This bounds even a stream
that keeps delivering bytes without finishing, independently of socket idle timeout
and the existing cost/output limits. A timed-out primary may use the already-bounded
fallback only while the run remains active and its head remains current. Cancellation
never implies a successful review or permission for a third automatic pass.

`scripts/ci-gate/deepseek-cancellation.test.py` checks cancellation/failure conditions
and executes the actual provider step scripts against stubbed GitHub and provider
boundaries, including head changes between reservation and invocation and failed
lookups. This corrects orchestration around the existing bounded-review decision;
it does not restore thinking mode or relax verdict verification.

### 2026-11-15 — Identical pre-token failures are not fallback verdicts (#2124)

The 2026-09-09 rule still admits fallback after a failed or inconclusive primary
that reached a completed response, and after a model-specific HTTP rejection.
It does not admit fallback after an identical pre-token HTTP or transport
failure. The non-secret status classification and the neutral
`provider_transport` advisory are the 2026-11-15 amendment to
[ADR 0090](../0090-human-first-opt-in-ai-review/README.md). Real verdict
fail-closed behavior is unchanged.
