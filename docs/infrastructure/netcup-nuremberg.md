# Nuremberg development services

Service endpoint and registry checks last verified: **2026-09-07**.

## Endpoints

Concrete hostnames and ports are kept in the private operational record
(`verJSON/verjson-cli`, `docs/operations/netcup-nuremberg.md`). This public reference uses
placeholders: `<gitlab-host>`, `<ssh-port>`, `<nexus-host>`, `<nexus-docker-host>` and
`<s3-host>`.

| Service | Endpoint | Verified state |
| --- | --- | --- |
| GitLab CE | `https://<gitlab-host>` | Sign-in page returns HTTP 200 |
| GitLab REST API | `https://<gitlab-host>/api/v4` | API base; authenticated API use not tested |
| GitLab SSH clone | `ssh://git@<gitlab-host>:<ssh-port>/<group>/<repository>.git` | Configured port; clone not tested |
| Nexus UI | `https://<nexus-host>` | HTTP 200 |
| Nexus REST API | `https://<nexus-host>/service/rest/v1` | Authenticated repository inventory queried successfully |
| npm hosted registry | `https://<nexus-host>/repository/npm-hosted/` | Authenticated npm publish/download verified; anonymous reads denied |
| Docker registry | `<nexus-docker-host>` | Docker login/push/pull verified; anonymous pulls denied |
| PyPI publishing | `https://<nexus-host>/repository/pypi-hosted/` | Authenticated Twine upload verified |
| PyPI package index | `https://<nexus-host>/repository/pypi-hosted/simple/` | Authenticated pip download verified; anonymous reads denied |
| S3 API | `https://<s3-host>` | `/minio/health/live` returns HTTP 200 |

HTTPS checks use normal certificate validation. Health checks establish
reachability only; they do not verify authentication, persistence, or backups.
Backups and restore drills follow
[ADR 0256](../decisions/0256-netcup-stack-backup-and-disaster-recovery/README.md)
and the [backup and restore runbook](netcup-stack-backup-restore.md).

## Migration status snapshot (2026-10-02, 23:13 UTC)

A read-only Kubernetes deployment inventory, queried through the Netcup
administration host at the time above, showed GitLab CE, Nexus CE, MinIO, and
both GitLab Runner deployments at 1/1 ready. This establishes workload
readiness only; this inventory did not check runner job execution or DNS, probe
service endpoints, exercise an isolated runner canary, or publish a
multi-platform OCI index. Follow [verjson-ci issue #30](https://github.com/Verjson/verjson-ci/issues/30)
for the remaining migration acceptance work.

## Registry setup status

The owner authorized acceptance of the Nexus Community Edition EULA on
2026-09-07. Acceptance is confirmed, and `npm-hosted`, `docker-hosted`, and
`pypi-hosted` are online on Nexus Community Edition 3.83.0-08 with S3-backed
storage. PyPI publishing and its package index are two interfaces to the same
`pypi-hosted` repository.

All three hosted registries require authentication for reads and writes and
use the `ALLOW_ONCE` write policy. Publish a new version instead of replacing
an existing one. Docker and npm authentication realms are enabled. Existing
default Maven and NuGet repositories retain their previous anonymous read
access; that access does not extend to these hosted registries.

Registry readiness and consumer integration remain coordinated in
[#1264](https://github.com/Verjson/.github/issues/1264). Documentation work began
in [#1266](https://github.com/Verjson/.github/issues/1266). The generated
bootstrap includes the authenticated Nexus registry configuration. That code
change did not validate a live deployment; environment rollout and endpoint
verification remain part of #1264.

## Client settings

Use a dedicated publishing account with permissions for the intended
repository. Supply credentials through your local credential store or CI secret
mechanism; do not commit them with these examples.

### npm

For packages in the `@verjson` scope, a project `.npmrc` can contain:

```ini
@verjson:registry=https://<nexus-host>/repository/npm-hosted/
```

For an individual publish, select the hosted registry explicitly:

```sh
npm publish --registry=https://<nexus-host>/repository/npm-hosted/
```

This is a hosted repository, not an npmjs proxy or group. It does not provide
arbitrary public npm dependencies. Nexus supports npm's legacy login flow with
the enabled npm authentication realm; use an account supplied by the platform
team and keep the resulting credentials in your user configuration.

```sh
npm login --auth-type=legacy \
  --registry=https://<nexus-host>/repository/npm-hosted/
```

### Docker

```sh
docker login <nexus-docker-host>
docker tag example:1.0.0 <nexus-docker-host>/example:1.0.0
docker push <nexus-docker-host>/example:1.0.0
docker pull <nexus-docker-host>/example:1.0.0
```

Use the registry hostname without a `/repository/docker-hosted/` path. The
external connection uses HTTPS on port 443.

### Python / PyPI

Publish distribution files using Twine:

```sh
python -m twine upload \
  --repository-url https://<nexus-host>/repository/pypi-hosted/ \
  dist/*
```

Install from the simple package index:

```sh
python -m pip install \
  --index-url https://<nexus-host>/repository/pypi-hosted/simple/ \
  your-package
```

The hosted index contains uploaded packages only. Use `--index-url` for an
explicit package source; adding a private registry as `--extra-index-url` can
allow a public package with the same name to be selected. Dependency resolution
that combines public and private sources requires a separately configured and
reviewed proxy/group strategy.

## Verification and updates

The 2026-09-07 proof used a temporary account restricted to browse, read, add,
and edit in these three repositories. Its attempt to list users was denied.

| Check | Result |
| --- | --- |
| `npm publish` followed by `npm pack` from the hosted registry | Downloaded tarball matched the published bytes |
| Twine upload followed by `pip download --no-deps` through the simple index | Downloaded wheel matched the published bytes |
| Docker login, build, push, local image removal, and pull | Pulled image ID matched the original |
| npm and PyPI duplicate-version publication | Rejected |
| Anonymous package metadata/index reads and Docker manifest pull | Denied |
| Test cleanup | Component inventory empty in all three hosted repositories; temporary account and role removed |

Nexus search indexing briefly lagged component deletion; a follow-up component
inventory confirmed cleanup. The test did not prove Docker overwrite rejection,
cross-repository dependency resolution, backup/restore, availability, subscriber
isolation, token expiry, or the CI credential-broker integration. No persistent
publisher account was created by this proof. These results are a development
registry check, not completion of the broader
[Nexus distribution proof](../decisions/0160-self-host-paid-package-distribution-on-nexus/README.md).

Check HTTPS reachability without bypassing certificate validation. Registry
verification should include an authenticated upload and download of a uniquely
named test package/image, an integrity check, and removal of only those test
artifacts. Record which of these checks passed when updating the status above.
Do not infer successful publishing from the Nexus UI being reachable.
