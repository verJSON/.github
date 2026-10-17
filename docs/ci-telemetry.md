# CI telemetry for the AI review/merge gate

The org-wide AI review/merge gate (`.github/workflows/ai-review-merge.yml`)
emits bounded, best-effort telemetry about each review through the shared
`verjson-observability` OTLP pipeline.

## Design

- The `ai-review` job builds one `CiTelemetryPayload` (the schema defined in
  `verjson-observability`) from runtime facts it already owns.
- It is emitted with the `Verjson/verjson-observability` composite action
  (`@v0.7.2`), which runs `emit-ci-telemetry.cjs` and exports OTLP metrics to
  the shared collector. There is no bespoke HTTP/curl path.
- Every telemetry step is `continue-on-error: true` and `if: always()`, so a
  telemetry fault can **never** fail or block the gate.
- It is **dormant until an endpoint exists**: the composite action no-ops when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is unset. Provision that secret (plus
  `OTEL_EXPORTER_OTLP_HEADERS` for auth) to turn it on.

## What is emitted (the `ai-review` payload)

Schema-conformant `CiTelemetryPayload` fields: `repository`, `workflow_name`,
`job_name`, `event_name`, `lane`, `review_type`, `lane_reason`,
`model_selected`, `budget_usd`, `dependency_major`, `verdict_blocking`,
`findings_count`, `action_conclusion`, `execution_file_present`,
`execution_file_parse_ok`, `total_cost_usd`, `total_turns`.

Numeric/status fields sourced from Claude's `execution_file` are extracted
defensively by `scripts/ci-telemetry/parse-claude-execution.sh` (present/parse
flags, `num_turns`, `total_cost_usd`, `subtype`) — never the transcript itself.

## What is explicitly excluded

Prompt text, diff text, PR review/findings text, raw Claude transcript, exact
error text, PR title/body, and the full file list. Only bounded numeric and
enumerated fields leave the runner.

## Provisioning the OTLP exporter (how to turn telemetry on)

Telemetry is dormant until two secrets exist. **But setting them is step 2 —
step 1 is making the collector reachable from the runners.** Read the
reachability section first.

### 1. Reachability prerequisite (do this first)

The shared collector is deployed **in-cluster** by `verjson-infra`'s
`ObservabilityStack` (namespace `observability`, service `otel-collector`), and
is **ClusterIP-only**:

- OTLP HTTP: `http://otel-collector.observability.svc.cluster.local:4318`
- OTLP gRPC: `http://otel-collector.observability.svc.cluster.local:4317`

That `*.svc.cluster.local` name resolves **only inside the Kubernetes cluster**.
The AI-gate jobs run on the **self-hosted GCP runners**, which are
docker-compose containers on GCE VMs (`Verjson/github-runner-docker-compose`) —
**outside** the cluster. They cannot resolve cluster DNS or route to a ClusterIP,
so the in-cluster URL will not work from CI as-is.

Pick one path to bridge that gap (infra work in `verjson-infra`, not a secret):

1. **Expose the collector on an internal LoadBalancer** (recommended). Add an
   internal-LB `Service` (GCP annotation
   `networking.gke.io/load-balancer-type: "Internal"`) for `otel-collector`'s
   `4318` port, and ensure the runners' VMs are on the same VPC/subnet. Then the
   endpoint is `http://<internal-lb-ip-or-dns>:4318`.
2. **Move the runners in-cluster** (actions-runner-controller pods). Then the
   ClusterIP DNS endpoint above works directly with no exposure change.
3. Any equivalent VPC-routable path (peering + `ExternalName`, etc.).

Until one exists, leave the secrets unset — the gate stays green and telemetry
no-ops.

### 2. Set the secrets

Only the gate emits, so scoping the secrets to `Verjson/.github` is sufficient
and least-privilege (use `--org Verjson` if you want them org-wide later):

```bash
# Base OTLP/HTTP URL of the reachable collector (no path — the SDK appends
# /v1/metrics). Use the HTTP receiver (4318), not gRPC.
gh secret set OTEL_EXPORTER_OTLP_ENDPOINT --repo Verjson/.github \
  --body 'http://<collector-host>:4318'

# Optional auth: newline-separated `Name: value` headers.
printf 'Authorization: Bearer %s\n' "$TOKEN" | \
  gh secret set OTEL_EXPORTER_OTLP_HEADERS --repo Verjson/.github
```

- Use **HTTP (4318)**, not gRPC — the composite action's CLI speaks OTLP/HTTP.
- Prefer **TLS** (`https://…`) if the path crosses anything untrusted; rotate the
  bearer token by re-setting `OTEL_EXPORTER_OTLP_HEADERS`.
- Omit `OTEL_EXPORTER_OTLP_HEADERS` if the internal LB is on a trusted network
  and the collector is unauthenticated.

### 3. Verify

Trigger a gate run (open a trivial PR, or `workflow_dispatch` the gate), then
confirm arrival at the collector:

```bash
kubectl -n observability logs deploy/otel-collector | grep -i verjson.cicd
```

or look for `verjson.cicd.*` metrics in Grafana. The `Emit ai-review telemetry`
step logs a warning (never fails) if export is rejected.

## Extending

`classify` and `ai-merge` telemetry can be added the same way (build a
`CiTelemetryPayload`, emit via the composite action, keep every step
`continue-on-error`). Only `ai-review` is wired today to keep the surface
minimal.

## actions-ci runtime records

Separate from the AI-review payload, `scripts/actions-ci-group.sh` produces
per-row runtime records for the pull-based collector in
[verjson-observability#271](https://github.com/Verjson/verjson-observability/issues/271)
([ADR 0225](decisions/0225-bound-actions-ci-command-runtimes/README.md),
amendment of 2026-10-17). Nothing is pushed from the runner.

- **Row line (job log).** `actions-ci command group=<g> status=<n> elapsed=<s>s
  [budget=<s>s] command=<cmd>`, unchanged in format. Replayed command output is
  wrapped in a random-token `::stop-commands::` block and every replayed line is
  prefixed with `| `, so output can neither execute a workflow command nor store
  a line that matches a row or a group marker.
- **Consumer contract for the job log.** Split stored logs on LF only, tolerating
  a trailing CR, and accept a producer line only when it carries the runner's
  timestamp prefix (`YYYY-MM-DDTHH:MM:SS.fffffffZ `). The replay prefixes after
  LF and bare CR, the line ends the Actions runner honors, and not after VT,
  FF, NEL, LS, PS, or FS/GS/RS. A consumer that splits on those (Python
  `str.splitlines()`, for example) can see a forged row at column 0.
- **JSONL (artifact).** Each `shell-test-groups` cell uploads
  `actions-ci-runtime-records-<run_id>-<attempt>-<group>` (seven-day retention,
  uploaded even when the cell fails or is cancelled). One object per row, in
  manifest order. The `docs` and `locale-collation` groups run outside that
  matrix and produce no records. A records path that cannot be written produces
  one `::warning::` and does not fail the cell, so treat a record count that
  disagrees with the logged `command_count` as unreadable input:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | `1` |
| `group`, `command` | string | Matrix group and manifest command |
| `status` | integer, or `"missing"` / `"invalid"` | Exit status as on the row line; a string when the worker left no usable result |
| `elapsed_seconds` | integer or null | Row wall clock as on the row line |
| `budget_seconds` | integer or null | Null on push runs, which have no budget |
| `started_at`, `ended_at` | UTC `YYYY-MM-DDTHH:MM:SSZ` | When the runner launched and reported the row |
| `timed_out` | boolean or null | Stopped at its budget |
| `left_background` | boolean or null | The runner terminated processes the row left behind |
| `runner_name`, `runner_environment` | string or null | `RUNNER_NAME`, `RUNNER_ENVIRONMENT`; null outside Actions |

**Integrity limits.** Rows run as the same user as the runner. The records path
is not exported to rows and the runner rewrites the file from memory after every
row, which discards accidental or output-borne writes, but a deliberately
malicious row could still tamper with the file or the job log, and on pull
requests the runner itself comes from the pull request head. No HMAC is added:
an undisclosed key cannot be verified, and a disclosed one is readable by the
same user. The collector must instead require each record to agree with its row
line and the record count to equal the logged `command_count`, and fail closed
on any disagreement.
