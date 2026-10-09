# Immutable container candidate adoption

Adoption publishes an attested candidate from `main`; it does not create a stable
release or deploy anything. Commit a reviewed `container-candidate.json` with the
repository, its exact `ghcr.io/<owner>` namespace, `nextStableVersion`, and the
complete image, variant, and platform matrix. A derived image names `baseVariant`;
the candidate manifest binds it to the base index digest produced by that run.
GHCR remains the canonical build and provenance source. Omit `registryDestinations`
to keep GHCR-only behavior, or add the reviewed Google Artifact Registry OIDC
destination shown below. The publisher builds each image once and copies the full
multi-platform index without converting its digest.

```json
{
  "registryDestinations": [
    {"provider": "ghcr", "namespace": "ghcr.io/<owner>"},
    {
      "provider": "gar",
      "namespace": "<location>-docker.pkg.dev/<project>/<repository>",
      "workloadIdentityProvider": "projects/<project-number>/locations/global/workloadIdentityPools/<pool>/providers/<provider>",
      "serviceAccount": "<publisher>@<project>.iam.gserviceaccount.com",
      "candidateRetentionDays": 88
    }
  ]
}
```

The GHCR destination must be first and match `registryNamespace`. GAR must name a
Docker repository and a narrowly configured Workload Identity provider and service
account; only the trusted publisher job receives `id-token: write`. `candidateRetentionDays`
is required for GAR and may be 1 through 88 days. The canonical GHCR default is 88 days.
Candidate manifests record each destination's verified digest and expiry from the
source run start. Promotion checks the GHCR expiry and fails once it has passed; it
does not rebuild or substitute another digest. Nexus remains unavailable until the
upstream multi-platform OIDC publisher contract is ready.

New candidate manifests use schema v4. GAR receipts bind the registry's observed
index provenance referrer and per-platform SBOM referrers to the attestation records,
and the validator checks every destination timestamp. Historical v2 and v3 candidates
remain readable, but must be rebuilt with the updated publisher before promotion.

At one immutable `Verjson/.github` commit, acquire
`scripts/gen-container-candidate.sh`, then generate and commit all four outputs:

```sh
scripts/gen-container-candidate.sh workflow <contract-sha> container-candidate.json > .github/workflows/container-candidate.yml
scripts/gen-container-candidate.sh validator <contract-sha> container-candidate.json > scripts/container_release_manifest.py
scripts/gen-container-candidate.sh destination-helper <contract-sha> container-candidate.json > scripts/container_registry_destinations.py
scripts/gen-container-candidate.sh contract-test <contract-sha> container-candidate.json > scripts/container-candidate-contract.test.sh
chmod +x scripts/container_release_manifest.py scripts/container_registry_destinations.py scripts/container-candidate-contract.test.sh
```

The generated caller binds pull-request validation to `container-candidate.yml`
and trusted publication to `container-candidate-publish.yml` at the same immutable
SHA. Do not collapse the two calls or grant publication permissions to validation:
GitHub validates the complete reusable graph before evaluating runtime conditions.
Each image's `provenance.builderIdentity` must name the publishing entrypoint,
`Verjson/.github/.github/workflows/container-candidate-publish.yml@<contract-sha>`.
Regenerate the workflow, validator, destination helper, and contract test together
when changing the contract SHA, and run the generated test in CI.

Default-branch pushes publish commit-addressed and
`<nextStableVersion>-rc.<run_id>.<run_attempt>` identities and retain a complete
candidate manifest. Downstream automation consumes its digests, never its tags.

## Private Node packages

List every exact private scoped package in `privateNodePackages`. The trusted
publication entrypoint validates the allowlist against the selected lockfile,
accepts only canonical registry URLs with exact integrity, and runs installation
with lifecycle scripts disabled. The resulting `node_modules` tree is bound to
the workflow run, attempt, and lockfile digest. Trusted build jobs restore it by
an exact, unguessable cache key and fail on a miss. BuildKit receives the tree as
the named context `verjson_node_modules`; it never receives the acquisition token
or npm configuration.

Pull-request validation never acquires private packages or receives
`NODE_AUTH_TOKEN`. When `privateNodePackages` is non-empty, the read-only entrypoint
reports that it skipped the candidate Docker builds; the trusted publisher builds
the candidate from the protected default branch after merge. For a public-only
configuration, pull-request builds remain credential-free and receive an empty
`verjson_node_modules` context. Private-package adoption can therefore use one
reviewed PR without making its PR-controlled Dockerfile able to read package
contents.

The optional `packageManager` field accepts `npm` (the default, with
`package-lock.json`) or `pnpm` (with `pnpm-lock.yaml` version 9.0 and an
integrity-pinned `packageManager` field in `package.json`). Registry scopes derive
from the exact names in `privateNodePackages`; do not add a repository `.npmrc` or
a parallel npm lockfile for a pnpm project. Bundled npm lock entries marked
`inBundle: true` are covered by the integrity-pinned containing tarball and are
not downloaded separately. The planner still rejects ordinary entries without
exact registry URLs and integrity.

An absent `privateNodePackages` field preserves pull-request builds with an empty
dependency context. Fork pull requests cannot use package credentials. Unapproved
packages, non-registry URLs, stale integrity, or project-controlled npm
configuration fail closed. This contract is separate from the repository
changelog contract; continue using
`Verjson/.github/scripts/gen-changelog-caller.sh` for the changelog workflow,
renderer, and contract test at one immutable SHA. Do not replace or hand-edit
generated artifacts.
