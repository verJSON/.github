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
tmpfs root and its mounts, except `/proc`, `/sys` and the read-only `/usr` trees
(`/usr`, `/bin`, `/lib`, `/lib64`), and fails on any socket other than the Postgres
socket, whatever its name. A trusted host-side check covers the skipped trees: it
resolves each listening socket path in the host's `/proc/net/unix`, including symlinks,
`..` and a merged `/usr`, and fails on one under those trees or on a relative one. It
cannot see listeners in other network namespaces or sockets renamed after `bind()`. The
check fails while
any endpoint answers, and also when one times out, reports a busy or other
non-definitive error, or cannot be parsed or dialed. Only an immediate refusal from the
kernel counts as unreachable.

`docker info` starts every installed CLI plugin before it contacts a daemon. Its run time
therefore depended on runner load and intermittently exceeded the probe's 3-second bound
(runs 41766639219 and 41772455924). The `TimeoutExpired` then failed
`hosted-compatibility-tests` with no code cause. In a local sandbox of the same shape, the
whole check answers in about 40 ms. It reports a bind-mounted daemon socket as reachable,
and it finds one placed at an unusual path. Skipping `/usr` is an accepted residual,
because walking the hosted image's toolchains would put back a runner-dependent run time.
The walk also skips directories the candidate cannot search; a directory it can search
but not list fails the check, as does a `/proc/net/unix` row the host check cannot
parse. The file's unit tests also run outside GitHub Actions now; the hosted class still
skips itself. Part of #1999.
