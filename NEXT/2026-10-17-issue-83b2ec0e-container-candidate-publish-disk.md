---
date: 2026-10-17
id: 83b2ec0e
impact: patch
title: Reclaim hosted disk and fail fast before container candidate image builds
---
The container candidate publisher now reclaims the large preinstalled hosted-image toolchains its image builds never use (Android SDK, .NET, GHC, CodeQL, Swift, PowerShell) before restoring node_modules, but only on GitHub-hosted runners, and it refuses to start an image build unless the Docker data filesystem holds twice the measured node_modules build context plus a 4 GiB margin, failing with an explicit required-versus-available error instead of an opaque `ENOSPC` mid-build.

terptechpub/self-publish-ai-app's push-to-main candidate publication had failed on every run since 2026-10-04 (runs 39000509900, 39001337204): a 3.97 GB node_modules transferred as a named build context and copied into a layer left about 11 GB free when the build began, and the adopter's `npm rebuild` install scripts exhausted the disk. Pull-request validation builds with an empty node_modules context (#1679), so it stayed green and could not observe this path. The guard fails closed when `du`, `df`, or `docker info` fail or return anything other than a bare byte count.
