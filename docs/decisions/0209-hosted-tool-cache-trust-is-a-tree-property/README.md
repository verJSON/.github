# 0209 — Hosted tool-cache trust is a property of the admitted tree, not its root component

- **Date:** 2026-09-26
- **Status:** Accepted
- **Issue:** [Verjson/.github#1599](https://github.com/Verjson/.github/issues/1599)
- **Related:** [ADR 0097](../0097-secretless-trusted-ref-node-validation/README.md), PR #1603, PR #1607
- **Category:** protected CI sandbox trust boundary (sensitive class)

## Context

`node-ci-protected.yml` runs a PR-controlled script plan inside a bubblewrap sandbox and
bind-mounts the setup-node tool prefix read-only into it. Before the mount, the generated
plan step validates the trusted tool root (`RUNNER_TOOL_CACHE`), every PATH component
under it, the resolved `npm`/`node` executables, and the whole mounted tree: each entry must
be owned by an allowlisted uid, and by default must carry no group- or world-write bit.

PR #1603 admitted the GitHub-hosted convention for the *root only*: `/opt/hostedtoolcache`
at exactly mode `0777`, uid 0 or 1001, gid 0. Descendants stayed strict. That fixed the
first failure (`trusted setup-node tool root has unsafe ownership mode`) and immediately
surfaced the next one on `verjson-customer-lifecycle` PR #71 (run 36075510940):
`setup-node lexical PATH ancestry has unsafe ownership mode`.

A probe workflow run on `ubuntu-latest` from `Verjson/demo-repository`
(run 36278983435, branch deleted afterwards) measured the actual shape:

| Path | uid | gid | mode |
| --- | --- | --- | --- |
| `/opt/hostedtoolcache` | 1001 | 0 | 0777 |
| `/opt/hostedtoolcache/node` and every descendant | 1001 | 1000 | 0777 |

Every one of the 2204 directories, 9549 regular files, and 6 symlinks under
`/opt/hostedtoolcache/node` is uid 1001 (`runner`), gid 1000 (`packer`), mode 0777.
setup-node resolves Node from `/opt/hostedtoolcache/node/<version>/x64`, so no
per-descendant unwritable requirement can pass on a hosted runner. Every hosted
protected type-surface caller (`verjson-authn`, `verjson-customer-lifecycle`) was red.

## Decision

When the trusted tool root is the admitted hosted convention (the exact root check from
#1603 is unchanged), the write-bit requirement is relaxed for **every resolved path under
that root**: PATH ancestry, the `npm`/`node` executables, and the mounted tool prefix tree.
The relaxation is a single derived flag, `trusted_tool_tree_requires_unwritable`, applied
uniformly at each validation site. Every other trusted root stays strict.

What still fails closed under the hosted convention:

- ownership by any uid outside `(0, 1001)` anywhere in the tree;
- a symlink that escapes the mounted prefix to anything other than a root-owned,
  unwritable path under `/usr`;
- special files inside the tree;
- a root that does not match the convention exactly (a root-owned `0755` root with
  world-writable descendants is rejected even at the hosted path);
- the post-validation identity re-check before the namespace bind (`st_dev`, `st_ino`,
  `st_uid`, mode of each tool prefix).

## Rationale

The trust anchor for a world-writable tree owned by the job's own uid was never the write
bit. The Actions runner executes every step as uid 1001, so a tree owned by uid 1001 is
writable by the job regardless of mode; #1603 already accepted that by admitting uid 1001.
What makes the tree trustworthy is ordering: nothing PR-controlled runs before this
validation (`npm ci --ignore-scripts`, no lifecycle scripts, checkout only), the tree is
validated and then bind-mounted read-only into the sandbox, and the identity re-check
detects a prefix swap between validation and mount. The write bit is unexploited
capability, not evidence of tampering — the same argument the plan already applied to
the Microsoft-shipped `pwsh` tree under `/opt`.

Alternatives considered:

- **Copy the resolved Node tree into a root-owned, unwritable location first.** Rejected:
  the copy would be made by uid 1001 (the only identity the job has), so it cannot be
  root-owned without privilege escalation, and it adds a multi-hundred-megabyte copy to
  every run for no additional trust.
- **Exempt directories but keep files strict.** Rejected by measurement: files and
  symlinks carry the same 0777 mode as directories on the image.
- **Route protected type-surface work to self-hosted runners only.** Rejected: it removes
  the hosted lane the protected callers were designed to use and does not fix the check.

## Consequences

- `scripts/gen-node-ci-protected.py` and the generated `node-ci-protected.yml` no longer
  carry an `allow_writable_root` parameter; the derived tree flag replaces it.
- `scripts/ci-gate/node-ci-required-identity.test.py` models the measured hosted shape
  (`tool_tree_mode`, `tool_tree_gid`, `foreign_entry` harness parameters) and asserts:
  the measured shape passes; a foreign owner inside the admitted tree is rejected at each
  relaxed site (PATH ancestry, the selected executable, the mounted tree walk); a strict
  root with world-writable descendants at the hosted path is rejected. Verified in a
  privileged container as an unprivileged sudo-capable user: the new test fails on `main`
  with the production error and the full 22-test suite passes on this change.
- Consumers pinned at or before `fff8891` must repin to the merge SHA of this change to
  recover their hosted protected type-surface lane.
- If GitHub changes the image convention, the exact-root admission fails closed again and
  this decision must be revisited with a fresh probe rather than a wider exemption.

## 2026-09-27 amendment: the hosted sandbox is provisioned for every protected lane

**Status:** Accepted; #1423.

Activating the cli-projects required workflow (ruleset 21830367 at `ebe574b`) produced
the first hosted run of a protected lane that declares neither a type surface nor
compatibility ranges (verjson-cli-projects#142, run 36283749779). Both lanes failed
closed with `verified bubblewrap namespace boundary is unavailable`: the protected
script plan requires `/usr/bin/bwrap` unconditionally, but the `Provision trusted
compatibility sandbox` step ran on GitHub-hosted runners only when
`protected-type-surface-declaration-path` or `secretless-compatibility-ranges` was set.
The authn and customer-lifecycle callers pass a declaration path, which is why they
were green at the same SHA and the defect stayed hidden.

The provisioning gate now matches the consumers: on `runner.environment ==
'github-hosted'` the sandbox is provisioned for every lane that will execute the
script plan (`secretless-ci-script-plan` or `secretless-nested-manifests` set) or the
compatibility lanes (declaration path or compatibility ranges set), under the same
`should-run` and secretless-PR/trusted-ref conditions. A lane that passes none of the
four does not pay for provisioning it would never use. Self-hosted runners are
unchanged because they carry the sandbox as a fleet property. Nothing about what the
sandbox admits changes; only when it is present.

## 2026-09-28 amendment: prewarm the pinned changelog engine for protected plans

**Status:** Accepted; [#1646](https://github.com/Verjson/.github/issues/1646).

Protected consumer plans may invoke the generated `scripts/render-next.sh`, whose
engine is pinned by `CONTRACT_REF` and `CONTRACT_SHA256`. A networkless protected
plan cannot use the renderer's fetch fallback, and an empty cache made the exact
`test:release` plan fail before it could validate the consumer. Keep that plan and
the sandbox's network boundary unchanged.

Before sandbox entry, the generated workflow reads only the two literal pin
declarations as data; it never executes the consumer renderer on the host. It
fetches only `scripts/changelog.py` from the canonical `.github` repository at the
validated 40-hex ref, verifies the bytes against the declared SHA-256, and writes
the verified file into a job-scoped `RUNNER_TEMP` cache. The protected plan
revalidates the exact one-file cache tree and digest, opens the cache root without
following symlinks, rechecks its identity, and bind-mounts it read-only alongside
the existing trusted tool prefixes. The renderer can therefore read the same
verified engine in its existing offline sandbox, without granting it network or
host write access.

The behavioral coverage in `scripts/ci-gate/node-ci-required-identity.test.py`
exercises malformed pins, digest mismatch, non-execution of the renderer during
warm-up, and a real bubblewrap write-denial probe for the mounted cache.

## 2026-10-01 amendment: invoke the npm CLI from the validated tool tree

**Status:** Accepted; [#1669](https://github.com/Verjson/.github/issues/1669).

The Node 26 setup-node layout places npm's CLI under
`lib/node_modules/npm/bin/npm-cli.js`, while its launcher under `bin` still
looks for `bin/node_modules/npm/bin/npm-prefix.js`. Resolve the CLI only from
the validated tool prefixes and run it with the validated Node executable.
Protected execution fails closed when the CLI is ambiguous and uses the
validated npm launcher only for older or synthetic layouts without a CLI file.
This keeps execution inside the existing trusted, read-only tool prefix and
does not add network or credential access.

The Node 26 path is covered by
`scripts/ci-gate/node-ci-secretless-consumer.test.sh` and the protected
`scripts/ci-gate/node-ci-required-identity.test.py` harness.

The secretless lifecycle entrypoint starts through a trusted Python bootstrap that closes inherited bind-source descriptors before npm or Corepack runs. Regression coverage attempts writes to host command files through every visible directory descriptor under real Bubblewrap.
