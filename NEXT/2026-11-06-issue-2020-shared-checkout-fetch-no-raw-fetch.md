---
date: 2026-11-06
issue: 2020
impact: patch
title: Replace shared_checkout_fetch's raw git fetch pass-through with a typed branch fetch
---
`scripts/shared_checkout_fetch.py` no longer passes caller arguments straight to `git fetch`. An argument such as `--upload-pack=<command>` would have run that command. Its only caller, the hub changelog gate's base-branch fallback (`scripts/ci-gate/hub-changelog-validate.sh`), now uses a typed `fetch-branch <repo> <branch>` operation. That operation accepts only a valid branch name, which also excludes globs such as `*`, and builds its single-branch refspec itself under the shared lock. Tests prove the program rejects the old operation before git runs and that a pattern is refused (#2020). The object-name pattern is now documented as accepting a full 40-digit SHA-1 or 64-digit SHA-256 name and refusing abbreviations (#2019).
