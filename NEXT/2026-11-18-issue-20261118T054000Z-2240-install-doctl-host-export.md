---
date: 2026-11-18
id: 20261118T054000Z
impact: patch
title: Install doctl for host-export dry-run on ubuntu
---
Install pinned `digitalocean/action-doctl` (`no_auth: true`, doctl `2.6.0`) on
the protected container-deployment `dry-run` and `deploy` jobs so
`runner-host-evidence` can execute `doctl projects list` after GitHub binding
(.github#2240; dry-run 44245632322 ENOENT).

Part of VerJSON/.github#629 / verJSON/verjson-git-runners#197.
