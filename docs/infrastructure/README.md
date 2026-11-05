# Shared infrastructure services

This directory lists user-facing endpoints for shared verJSON development
services. An endpoint being listed does not grant access or make its contents
public; obtain an account and repository permissions from the platform team.

| Environment | Services | Endpoint reference |
| --- | --- | --- |
| Nuremberg development stack | GitLab CE, Nexus Repository Community Edition, npm, Docker, PyPI, S3 | [Service endpoints and client settings](netcup-nuremberg.md); [backup and restore runbook](netcup-stack-backup-restore.md) |

## Documentation ownership

The verJSON platform team maintains this directory in `Verjson/.github` because
the services are shared across repositories. Deployment implementation belongs
to [Verjson/verjson-cli](https://github.com/Verjson/verjson-cli).

When an endpoint or client setting changes, update its environment document in
the same delivery, include a new `NEXT/` fragment, and record the verification
date and the checks actually performed. Distinguish a reachable web page from a
successful authenticated publish/install test. Track unfinished provisioning in
the issue tracker rather than presenting a planned endpoint as operational.

This repository is public. Keep credentials, credential-storage locations,
administrator access procedures, private host inventory, kubeconfigs, and
personal workstation paths in private operational documentation or the secret
manager. Do not attach raw deployment manifests or command output containing
those details to public issues or pull requests.

These endpoints do not change the organization's existing CI, publishing, or
package-retention contracts. Adopting a different package source in a consumer
repository remains a separate change.
