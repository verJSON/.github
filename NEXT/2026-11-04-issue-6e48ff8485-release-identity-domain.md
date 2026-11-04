---
date: 2026-11-04
id: 6e48ff8485
impact: patch
title: Author release commits under the verjson.com domain
---
The reusable changelog release now commits and tags as
`release@verjson.com`, and the release App canary as
`release-canary@verjson.com`, instead of the `verjson.dev` addresses the
organization does not use. `changelog-release-permissions.test.sh` pins the
release identity. A caller keeps the old identity until it repins to a
contract commit that carries this change, which includes this repository's
own `release.yml`.
