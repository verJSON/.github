# Compatibility lane isolation proposal

Status: **proposed; not an active CI contract**. Part of [#1966](https://github.com/VerJSON/.github/issues/1966). The current workflow and [ADR 0141](decisions/0141-swap-verified-compatibility-packages-without-graph-resolution/README.md) remain effective until a reviewed replacement is adopted. This proposal does not authorize enabling privileged promotion or changing required checks.

## Problem

The current trusted package policy authorizes package names and ranges, while the caller chooses the compatibility script. A successful caller script cannot establish that independently required assertions ran. A single invocation can also run inner lanes against writable shared workspace data; a prior lane can change an input read by a later lane. The compatibility sandbox currently inherits the host network namespace. Artifact provenance and read-only dependency mounts do not establish independent fixture provenance, assertion coverage, or isolation between those inner lanes.

## Proposed boundary

1. A protected, versioned policy selects an independently maintained harness and fixture source by immutable identity and specifies the complete required lane set. The trusted workflow obtains the policy without relying on candidate-controlled files. Trusted configuration independently enables the workflow and requires its check for the applicable candidate; an omitted request, disabled workflow, changed caller or ref, or skipped required check must not erase the obligation. Candidate requests may narrow neither the required set nor the harness, assertions, network profile, or fixture identity. Absence, ambiguity, incompatible versions, or extra unsupported fields fail closed. A digest supplied by the candidate is not independent authorization.
2. Before candidate execution, a trusted supervisor resolves and verifies the policy, candidate kind, exact candidate commit and tree, base commit, harness and fixture identities, and each acquired package's name, version and artifact digest. It records their bindings to the workflow, run and attempt. The integration gate must bind that evidence to the actual integration candidate and base, including a merge-group candidate when a queue is used; a changed candidate or base requires fresh applicable evidence. Verification must establish source authority as well as byte equality; a content hash alone does not establish who authorized it. Each required lane has one unique identity and exactly one terminal result for that run.
3. The supervisor stages fresh source, dependencies and verified artifact bytes for each lane. Candidate processes cannot write other lanes' inputs, the policy, supervisor, harness, provenance, or final result. Each lane has separate writable output and temporary storage; cleanup and interrupted execution cannot turn missing results into success. A read-only mount alone is insufficient if another process can mutate the underlying host path while the lane runs.
4. The trusted harness exercises the candidate under a bounded protocol, with a supervisor and harness authority separated from candidate processes and files. Candidate code cannot access or forge the control or result channel, kill the trusted harness, or run inside its authority-bearing process. A candidate-produced file or exit status is evidence of candidate behavior, not an authoritative assertion that the harness ran. The supervisor accepts only a complete result from the selected trusted harness, binds it to the verified inputs, and fails on omitted, duplicate, skipped, timed-out, interrupted or ambiguous lanes. Candidate code receives no privileged credentials.
5. Each lane uses an isolated network namespace. Where tests require loopback, a private loopback service is allowed only within that namespace; host loopback and other host network access must remain unavailable. When a trusted service is required, the supervisor must provision and identify it, verify its health, and prevent candidate-controlled service impersonation. Probe this behavior on each supported runner profile and fail closed if the boundary cannot be established. A local probe on 2026-10-31 successfully connected a namespace-local loopback pair and could not connect to a host listener; it does not establish hosted or other-runner support.

The existing `verjson-ci` source execution boundary provides a relevant read-only source and network-namespace implementation, but its current interface does not establish fixture authority, trusted compatibility assertions, or multi-lane completeness. Reuse requires a reviewed integration and evidence for those additional properties.

The existing Bubblewrap supervisor also exposes an experimental opt-in elapsed
deadline. It begins before launch and withholds success if the deadline passes
before cleanup and result processing finish; cleanup has separate bounds and can
finish after the cutoff. Existing callers retain their prior behavior. A timeout
is an infrastructure failure with cleanup conservatively
unverified. A cancellation observed before the deadline is reported as an
interruption; one observed during timeout cleanup is retained on the timeout
failure. The enclosing plan still owns the final cancellation acceptance cutoff.
The deadline alone does not authenticate a harness or prove PID-namespace cleanup;
the real-profile probe and integration remain necessary before use for admission.

An optional, unwired supervisor notification exposes a provisional pinned PID
namespace and supervisor pidfd before launch. The notice revokes its own handles
when supervision ends; borrowed duplicates belong to the consumer and remain open
until it closes them. A consumer must check pidfd liveness and reject prelaunch,
aborted, or stale notices. The notice does not authenticate a socket peer or
authorize a lane. Queue and notice locks require trusted, bounded in-process
consumers; the deadline does not bound a stalled holder of either lock.
In an unautomated local Linux 6.18 WSL2 probe, a socketpair created before `fork` reported
the creator through `SO_PEERCRED` and `SO_PEERPIDFD` even when the child sent
the message. These credentials therefore do not identify every subsequent
sender when a descriptor is inherited or transferred. The controller must
establish and protect the connection and descriptor lifecycle, in addition to
checking peer and supervisor identities. This probe does not establish the
behavior of every supported kernel or an accepted-connection implementation.

In a separate local Linux 6.18 WSL2 socketpair probe, `SO_PASSCRED` and
`recvmsg` reported the child PID for bytes written through an inherited
descriptor; a second probe received queued parent and child bytes with their
respective credentials. This supports investigating credential-bearing reads,
but does not establish a sender-authentication mechanism. The receiver must
handle missing or truncated credentials, PID-namespace translation, PID reuse,
credential-forging capabilities, concurrent writers and descriptor sharing.
The probes did not exercise Bubblewrap or a hosted runner. The current relay
uses `recv`, does not inspect per-message credentials and remains unauthenticated.

An experimental, unwired chunk reader observes `SCM_CREDENTIALS` against a
live pinned sender pidfd and a deadline. It rejects EOF, unexpected ancillary
data, truncation and mismatched credentials, and attempts to close all received
descriptors. Its caller must enable `SO_PASSCRED` before any sender can enqueue
data, ensure the socket credential and trusted procfs PID and UID namespace
views agree, retain and recheck the pidfd through final acceptance, and control
socket ownership. The helper does not prove harness provenance, prevent
capability-based credential forgery, authenticate complete frames or identify
who closed a shared socket. Cancellation must be queued; asynchronous exceptions
or concurrent descriptor manipulation can interrupt descriptor cleanup. It is
not connected to the relay or admission path.
An opt-in direct Bubblewrap probe exercises a live sender in a separate PID
namespace. This does not exercise the trusted supervisor, authenticate harness
code, or establish the namespace and socket lifecycle needed for admission.
An opt-in supervisor fixture additionally exercises a cooperating sender with
the pinned connector and chunk reader together; its result is evidence only on
the runner profile where it is actually executed.
The fixture also checks a forked writer that inherits the connection while the
original connector remains alive. It does not establish resistance to descriptor
transfer from other namespaces, capability-based forgery, or arbitrary writers.

An experimental, unwired connector-evidence helper pins the kernel-recorded
connector with `SO_PEERPIDFD`, checks its UID and pidfd identity, and compares
its live PID namespace with a live pinned supervisor. It rejects unavailable or
inconsistent evidence. The caller must establish that the supplied procfs root
is the trusted host view; the helper checks procfs type and root, not mount
provenance. Its caller owns the returned pidfd and must retain and check both
pidfds through final acceptance. This observation does not authenticate harness
code, launch, a subsequent writer, or lane completion, and cannot authorize
admission. Its descriptor handoff requires queued cancellation rather than
asynchronously raised exceptions, which can interrupt ownership transfer.
An isolated public [canary run](https://github.com/test-agent-verjson/tbd-queue-canary-20261029165528-c5edae32/actions/runs/41166094673)
checked out commit `a6d6bf37f4acddac129fb4d58eb42e028d804a2b` and passed the receipt and
connector suites with the canonical hosted sandbox provisioner. One probe matched
an accepted connector to its supervisor's pinned PID namespace through a
read-only socket mount; a separate deadline probe verified that its supervisor
namespace differed from the host. These observations do not establish
descriptor-transfer safety, harness provenance, the full coordinator, or
production admission.

An experimental, unwired launch-handoff reader accepts one attempt- and
nonce-bound Unix seqpacket from a pinned live supervisor and checks the received
pidfd against a live direct child in the supervisor's PID namespace. It rejects
extra packets and ambiguous descriptors. The caller must establish a trusted
host procfs, aligned credential namespaces, an independently pinned supervisor,
exclusive child-reaping ownership, and a pre-execution gate; the helper does
not create or acknowledge that gate or select the intended child. It does not
prove interpreter, harness or executed-byte provenance, controller isolation,
or cleanup. The caller must retain and recheck the child and supervisor pidfds
through the acceptance cutoff. Socket ownership, cancellation, and descriptor handoff require
trusted bounded consumers without asynchronously raised exceptions. This is
not an admission or launch authorization.
The one-shot reader waits for socket EOF to reject queued extra packets; EOF is
not authenticated. A forked holder can half-close the shared socket, so the
gate must prevent that ownership transfer rather than treating EOF as proof
that the supervisor ended the exchange.

## Controller candidates

An experimental, unwired `scripts/ci-gate/compatibility_lane_evidence.py` compares
an independently supplied plan and terminal results as an unordered complete
lane set. It binds the declared policy, GitHub.com owner and repository IDs,
candidate and base, run, workflow revision, and lane inputs. A `matching`
result means only that the supplied records match; `readyForApply` is always
false. The source identifiers are opaque labels that a future trusted controller
must independently resolve and authenticate. The comparator does not authenticate
records, compute or verify digests, establish a sandbox, run assertions, or prove
that the supplied candidate and base are current. It is not an active check or
an authorization to merge. Its version 1 is an experimental wire shape, not an
adopted organization contract, and currently covers GitHub.com only.

An experimental, unwired lane relay forwards one versioned request and one
response between controller-owned Unix stream sockets. Each sender half-closes
its write side after its single bounded frame; the relay checks the complete
stream, the frame shape and the controller-supplied nonce before forwarding.
It closes its sockets and withholds success on malformed, incomplete, late or
ambiguous exchanges. The future controller must generate a unique nonce and
bind it to the authorized lane and attempt. The candidate response is untrusted
input to the harness, not an assertion or completion receipt. The relay does not
authenticate either peer, launch or isolate processes, protect a harness result
channel, verify cleanup, or establish admission; it always reports
`readyForApply: false`.


An independently enforced controller must execute the protected complete lane plan; candidate-authored calls and their outputs cannot be its authority. One candidate is a generalized organization-required workflow and trusted supervisor, building on the existing [CLI package-surface required workflow](../.github/workflows/cli-projects-package-surface-required.yml) and [ADR 0154](decisions/0154-authenticate-required-workflow-identity-overrides/README.md). Another is a separately controlled App that executes the plan and publishes an App-bound required check. A new App is not presumed necessary: select between these only after demonstrating the required enforcement and candidate/base binding on the intended GitHub plan and runner. Existing examples are repository-specific and do not establish cross-organization or merge-group support.

On 2026-10-31 UTC, a [public canary run](https://github.com/test-agent-verjson/tbd-queue-canary-20261029165528-c5edae32/actions/runs/41153346595) invoked one reusable workflow twice. Both executions received the same caller workflow reference and reusable job name, while their `job.check_run_id` values differed. GitHub's jobs API associated each ID with the run, attempt, head SHA and a different displayed job name. This permits correlation of those observed executions, not authorization of the caller-controlled names, proof that every required call occurred, or proof of required-workflow enforcement. The probe used `workflow_dispatch`; pull-request, merge-group and private-repository behavior remain unverified. Its temporary workflow files were removed after the run.

The existing [ADR 0226](decisions/0226-superseded-cancelled-check-runs-do-not-block-admission/README.md) notes that Actions checks share an App identity. A same-name check from that App is therefore not by itself evidence that the intended required workflow ran. Test same-name spoofing, omission and changed workflow pins against the actual protected integration before selecting an enforcement mechanism.

## Adoption and verification

Define and review the policy and harness trust roots, complete plan format, fixture acquisition, downgrade behavior and result protocol before binding them to the reusable workflow. Retain compatibility for current callers through an explicitly tested migration; do not silently treat legacy caller scripts as independently verified assertions. A changed required check needs evidence of both enforcement and adopter rollout.

Tests must exercise a candidate that disables or skips the required workflow, alters its caller or ref, reuses evidence for a stale base or different merge-group candidate, omits assertions, omits or duplicates a lane, substitutes a fixture, harness or trusted service, forges a result or kills the harness, changes another lane's source, dependency, artifact or result, and attempts host networking. Also cover unsupported isolation, malformed policy and provenance, timeout, interruption, and cleanup. Unit tests and local namespace probes do not prove the hosted runner profile or live adoption; exercise the actual pinned workflow and runner before making that claim.

Independent architecture review remains advisory. The prior review did not establish verified member identity or ARB consensus. Before making this proposal effective, complete the applicable architecture decision, adversarial review, tests, review protections and migration checks; preserve dissent and assess the communication schema used by that board.
