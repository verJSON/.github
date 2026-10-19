---
date: 2026-10-19
id: b9d18280
impact: patch
title: Make the release-caller npm trace recorder detect the credential reliably
---

The release-caller credential-isolation test's npm trace recorder no longer misrecords a credentialed npm call as credential-free. It checked the environment through a pipe into an early-exiting reader under `pipefail`; with a large environment the writer died of SIGPIPE, the check read false, and a real exposure could be undercounted. It now matches the environment without a pipe, and the test first proves the recorder reports the credential present, and absent, across repeated runs under a large environment.
