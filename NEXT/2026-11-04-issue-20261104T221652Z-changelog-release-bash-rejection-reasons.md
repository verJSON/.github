---
date: 2026-11-04
id: 20261104T221652Z
refs: 1994
impact: patch
title: Changelog-release Bash negative tests name the guard that must reject them
---

The negative tests in the Bash files of the `changelog-release-1` through
`changelog-release-4` CI groups, other than the container contract files, now assert
why they were rejected: the `::error::` line, the engine's `changelog:` line, the
generator's message, or the shape checker's report, together with the exit status the
script defines. Before this, any nonzero exit passed, so a guard could stop working
while an earlier, unrelated check kept the test green. The release caller's shape
checker had five such cases: with the named check removed, another report still
rejected the mutation. Two of those mutations also rewrote the publish job's token,
and are now bounded to the verify job. Each case now asserts the report of the
invariant it names. No case had reached the check that the publish job's
private-dependency read token is not `GITHUB_TOKEN`; a new case does.

Some guards now say why they rejected. Their accept/reject behavior and the
generator's output are unchanged:

- `gen-changelog-caller.sh`: an unknown mode, a missing `--only-package-dir` value,
  and either order of mixing `--package-dir` with `--only-package-dir` print their
  own line before the shared usage text.
- `node-release.yml`: a GitHub Release inspection that returns malformed JSON or
  bytes that are not UTF-8 now says so, instead of ending in a Python traceback.

`exact-release-packages.test.py` asserts the new package-directory messages, and
`gen-adr-index.test.sh` and `render-next.test.sh` were brought under the same rule.

Part of #1994. `changelog-caller-contract.test.sh` and the container contract files are
left to later legs.

The generator and `gen-adr-index.test.sh` edits move two runner-isolation backlog
digests, `capability-floor-observe.yml` `observe` and `release.yml` `verify`. Their
unresolved reasons are unchanged, so only the digests are refreshed.
