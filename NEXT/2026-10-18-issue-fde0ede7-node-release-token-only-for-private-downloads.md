---
date: 2026-10-18
id: fde0ede7
impact: patch
title: Give the node-release package token only to exact private package downloads
---

Security fix: `node-release` no longer gives `NODE_AUTH_TOKEN` to `npm ci --ignore-scripts`. Under npm 10, which current Node 18, 20, and 22 releases bundle, that command still ran `prepare` for linked and directory dependencies with the token. Under every npm, a git dependency's preparation passed the token to a child `npm install` that read the dependency's own `.npmrc`. The credentialed step now only runs `npm cache add` for each locked `npm.pkg.github.com` download URL and checks the cached content against the lockfile's sha512 integrity. Then `npm ci --prefer-offline` installs and runs every lifecycle script without the token. When private downloads exist, acquisition refuses npm majors other than 10 and 11, and both steps share one explicit npm cache under `RUNNER_TEMP`. Private lock entries need lockfile version 2 or 3, an exact download URL, and sha512 integrity; a version 1 lockfile with no private packages still installs. A git dependency whose preparation needs private packages now fails instead of receiving the token. See ADR 0220's 2026-10-18 amendment.

`scripts/node-release-credential-isolation.test.py` now runs the workflow's own steps under pinned, integrity-checked npm 10.9.9 and 11.19.1 instead of the runner's npm. Its verdict therefore no longer depends on which runner executes it.
