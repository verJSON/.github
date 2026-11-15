# 0267 — Container acquisition accepts a pinned gitlink file dependency

- **Date:** 2026-11-15
- **Status:** Accepted
- **Issue:** [Verjson/.github#2087](https://github.com/Verjson/.github/issues/2087)
- **Category:** secrets and CI supply chain (sensitive class)

## Context

`container-candidate-publish.yml` installs private npm packages with `NODE_AUTH_TOKEN` and `npm ci --ignore-scripts`. [ADR 0220](../0220-confine-release-credentials-to-dependency-acquisition/README.md) keeps that step from reaching a git dependency or an npm 10 link `prepare` by rejecting every lock entry that lacks an exact registry tarball and sha512 integrity. A `link: true` entry is skipped, and the link's target is rejected.

Services that follow xops-docs ADR 0002 depend on an internal package as `file:<path>`, where `<path>` is a git submodule pinned in the published commit. The lock target has a name and version and no tarball. Publication stops before install. Checking the submodule out would still leave a symlink and no `dist/`, because the credentialed install ignores scripts and the image build context ignores `**/dist`.

## Decision

npm acquisition may admit a `file:` dependency only when all of the following hold. pnpm acquisition is unchanged.

1. The lock entry is the target of one `node_modules` link whose `resolved` path is that entry. `package.json` depends on the package name through exactly `file:<path>` in `dependencies`, `devDependencies`, or `optionalDependencies`. The entry carries no tarball URL, integrity, or `inBundle` flag.
2. `<path>` is a gitlink (mode 160000) in the published commit, declared once in `.gitmodules`, and the checkout's `HEAD` is that commit. Every `.gitmodules` URL is `https://github.com/<owner>/<repo>.git` for the publishing repository owner. The path is a directory, not a symlink.
3. Submodules are fetched only when such a link exists, one level deep, with `secrets.submodules-token` when it is set and the job token otherwise. `persist-credentials` stays false. The fetch runs only after the URL check.
4. The credentialed install stays `npm ci --ignore-scripts`. When a gitlink is admitted, npm must be 11 or newer, which is the release that honors `--ignore-scripts` for linked `prepare` (ADR 0220). The lock digest is recorded before that install.
5. After the credentialed step exits, one later step with `NODE_AUTH_TOKEN`, `NPM_TOKEN`, and `GITHUB_TOKEN` empty runs `npm ci --no-audit --no-fund` in each admitted gitlink. That is the only build command. It is not taken from caller configuration. `npm ci` runs the package's own `prepare`, which is how the pinned package builds `dist/`. The gitlink must contain its own `package.json` and `package-lock.json`.
6. Each `node_modules` symlink to an admitted gitlink is then replaced by a copy of that package. The copy excludes `node_modules` and `.git` and is rejected if it contains a symlink. The sealed tree is what the image receives, so `dist/` is inside `node_modules` rather than a Docker-ignored path in the source context.

Generated callers that already pass `NODE_AUTH_TOKEN` also pass `submodules-token: ${{ secrets.SUBMODULES_TOKEN }}`. Acquisition still runs only when `privateNodePackages` is non-empty. A repository whose lock has a gitlink and no approved private package does not enter this job.

## Consequences

- A published tree can contain one or more pinned same-owner gitlink `file:` dependencies. Git dependencies and every other unresolved entry are still rejected.
- The package token is not in the step that builds the gitlink. The submodule token is not in the step that holds the package token, and it is not written to Git config for later steps.
- The gitlink's dependency lifecycle scripts run without the package token. They can still change the workspace before the tree is sealed. That is the build this decision allows.
- Adopters receive the behavior by regenerating the caller at a contract commit that contains it. This decision does not publish that release.

## Rollback

Restore the previous acquisition job and stop passing `submodules-token`. Lock entries without a registry tarball and sha512 integrity are rejected again, including pinned gitlink targets.
