---
date: 2026-10-10
issue: 1749
impact: patch
title: ci(node): separate npm config files and isolate inline Python in secretless CI
---
The secretless lifecycle rebuild gives npm distinct, empty user and global config files, so npm 11 no longer rejects one `/dev/null` path loaded for both roles. The sandbox bootstrap closes the host descriptors Bubblewrap leaves open before it creates `/tmp/npm-config/{user,global}.npmrc` (directory 0700, files 0600) on the sandbox's fresh `/tmp`, sets both `NPM_CONFIG_*` and `npm_config_*` spellings, and leaves the umask lifecycle scripts inherit unchanged. Every inline Python interpreter in `node-ci.yml` and the generated `node-ci-protected.yml` now runs isolated from the checkout with `-I`, enforced by a registered workflow contract with an empty exception list. (#1749; ADR 0086)
