---
date: 2026-11-04
issue: 1999
impact: patch
title: Postgres sandbox acceptance dials Docker endpoints instead of timing the CLI
---

The hosted Postgres sandbox acceptance no longer runs `docker info` inside the
Bubblewrap sandbox to prove the candidate cannot reach a Docker daemon. It dials each
endpoint a daemon could be reached through: the system and rootless sockets,
`DOCKER_HOST`, and every configured Docker context. It also walks the sandbox's own
mounts and fails on any socket other than the Postgres socket, whatever its name. Which
mounts it walks is derived from the same Bubblewrap argument list the sandbox is built
from: `/proc` and every read-only bind of a host distribution tree (`/usr`, `/bin`,
`/lib`, `/lib64`, and the node root, which is `/usr/local` on hosted images) are skipped,
and a trusted host-side check covers exactly those bind sources instead. It resolves each
listening socket path in the host's `/proc/net/unix`, including symlinks, `..` and a
merged `/usr`, and fails on one under those sources or on a relative one. It cannot see
listeners in other network namespaces or sockets renamed after `bind()`. The walk stops
after 100,000 entries and fails closed, and an option the mount parser does not know, or
a mount made over a skipped tree, fails the check rather than being guessed. The check fails while
any endpoint answers, and also when one times out, reports a busy or other
non-definitive error, or cannot be parsed or dialed. Only an immediate refusal from the
kernel counts as unreachable.

`docker info` starts every installed CLI plugin before it contacts a daemon. Its run time
therefore depended on runner load and intermittently exceeded the probe's 3-second bound
(runs 41766639219 and 41772455924). The `TimeoutExpired` then failed
`hosted-compatibility-tests` with no code cause. In a local sandbox of the same shape, the
whole check answers in about 40 ms. It reports a bind-mounted daemon socket as reachable,
and it finds one placed at an unusual path. Skipping the distribution binds is an accepted
residual: the first hosted run walked the node root, `/usr/local`, and timed the candidate
out. A skipped 200,000-entry tree now costs the same as an empty one, and a walked one
stops at the limit in under half a second.
The walk also skips directories the candidate cannot search; a directory it can search
but not list fails the check, as does a `/proc/net/unix` row the host check cannot
parse. The file's unit tests also run outside GitHub Actions now; the hosted class still
skips itself. Part of #1999.
