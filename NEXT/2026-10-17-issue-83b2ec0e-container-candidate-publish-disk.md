---
date: 2026-10-17
id: 83b2ec0e
impact: patch
title: Reclaim hosted disk and fail fast before container candidate image builds
---
On GitHub-hosted runners, the container candidate publisher now removes the large preinstalled hosted-image toolchains its image builds never use (Android SDK, .NET, GHC, CodeQL, Swift, PowerShell) on every row a publish job may build, with or without private Node packages. It also refuses to start an image build unless the Docker data filesystem holds twice the measured node_modules context plus a 4 GiB margin. If it doesn't, the build fails with an explicit required-versus-available error instead of an opaque `ENOSPC` mid-build.

terptechpub/self-publish-ai-app's push-to-main candidate publication had failed on every run since 2026-10-04 (runs 39000509900, 39001337204). A 3.97 GB node_modules was transferred as a named build context and copied into a layer. That left about 11 GB free when the build began, and the adopter's `npm rebuild` install scripts and the layer export exhausted the disk. Pull-request validation builds with an empty node_modules context (#1679), so it stayed green and could not observe this path.

The free-space check is a necessary floor, not a guarantee. The evidence runs ran out of disk after the transfer, during install scripts and layer export, whose demand the publisher cannot predict. A build that passes the floor can still exhaust the disk. The check fails closed when `du`, `df`, or `docker info` fails or returns anything other than a single byte count. [ADR 0078](../docs/decisions/0078-container-release-and-runner-deployment-contract/README.md) carries the dated amendment.
