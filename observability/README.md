# Observability

SigNoz is the private OpenTelemetry observability plane for the lakehouse: host, container,
service, Airflow, and data-pipeline signals. It is never a workflow dependency: its outage must not
block ingestion, transformation, OCR, BI, or durable task logging.

## SigNoz runtime

Source of truth is the Foundry casting:

| Path | Purpose |
|---|---|
| `observability/signoz/casting.yaml` | Foundry installation spec: pinned image digests and platform patches |
| `observability/signoz/casting.yaml.lock` | Committed forge output; CI fails on lock drift |
| `observability/signoz/deploy/deploy` | Host-side delivery: pinned Foundry install, cast, health wait |
| `observability/signoz/pours/` | Generated compose output — gitignored, never edited |
| `.github/workflows/deploy-signoz.yml` | Delivery path: forge validation on the runner, cast on the host |

### Deployment

Merge to `main` (or manual dispatch of the protected `dev` environment) triggers the workflow:

1. The runner downloads a checksum-pinned `foundryctl`, runs `forge`, compares the generated lock
   with the committed one, validates the compose, and asserts the reviewed localhost bindings and
   resource limits.
2. The workflow connects through Tailscale, transfers the casting bundle, and runs the deploy
   script, which installs the pinned Foundry binary on the host, syncs the casting file, runs
   `foundryctl cast`, and waits for `127.0.0.1:8082/api/v1/health`.

Runtime state lives on the host under `~/.config/lakehouse/<env>/signoz/` (casting file, lock,
generated `pours/`); durable data lives in the named Docker volumes. The UI is reachable over Tailscale
at `http://tgbao-dev-services:8082`, locally on the host at `http://127.0.0.1:8082`, and behind Cloudflare
Tunnel/Access as `https://observe.tgblab.io.vn`; OTLP ingest ports 4317/4318 bind to 127.0.0.1.

### Upgrades

Bump the reviewed digest pins in `casting.yaml`, re-forge locally with the same Foundry version,
commit the updated `casting.yaml.lock`, and merge. Never patch files under `pours/` or restart
containers with an altered compose; `foundryctl cast` is the only mutation path.

### Retention

Retention is application state in SigNoz's metastore (enforced through ClickHouse table TTLs), not
casting configuration and not a Terraform resource. After first deployment set it once through the
same settings API the UI uses (Settings → Workspace → Retention Controls): logs 7 days, traces
7 days, metrics 30 days. These match the Foundry Docker defaults today, but keeping the values
explicit makes an upgrade's retention behavior reviewable.

### Backup and restore

SigNoz telemetry is disposable; S3 remains the durable record of task logs and database backups.
Before host or SigNoz upgrades, use an OCI boot-volume backup/snapshot for a crash-consistent
recovery point. Do not archive a live ClickHouse Docker volume with `tar`: that is not an
application-consistent ClickHouse backup. Dev accepts rebuilding SigNoz and losing retained
telemetry when no infrastructure snapshot exists.

## Collection agent

One official OpenTelemetry Collector Contrib agent runs as the compose project
`signoz-collection-agent` on the Docker host and is delivered together with the stack by
`deploy-signoz.yml`:

| Path | Purpose |
|---|---|
| `observability/signoz/collector/config.yaml` | Receivers, OTTL redaction, and exporters |
| `observability/signoz/collector/compose.yaml` | Hardened agent runtime (0.25 CPU / 512M, ro mounts) |
| `observability/signoz/collector/image` | Reviewed digest-pinned contrib image |
| `observability/signoz/collector/deploy` | Host-side validate-and-run script |

The agent joins the Foundry-created `signoz-network`, shared `lakehouse-metadata`, and host-owned
`lakehouse-observability` application network. The shared host reconcile action creates the latter
idempotently before any component deploy, removing cross-workflow ordering races. It exports to
`signoz-ingester:4317` and exposes only its health endpoint on host loopback (`127.0.0.1:13133`).
It is the single OTLP gateway for applications over `lakehouse-observability`, so workloads do not
need access to the SigNoz datastore network. Secrets for the
`postgresql` receiver are supplied at deploy time from Secrets Manager through the
`metadata-postgres` identity — never stored in config or compose.

Signals collected:

- `hostmetrics` (host root mounted read-only at `/hostfs`, virtual mounts excluded): CPU, RAM,
  load, filesystem, disk IO, paging, network, process counts.
- `docker_stats` via the read-only Docker socket, with compose project/service labels promoted to
  metric attributes.
- `postgresql` metrics for the shared metadata server through a login role granted only the
  built-in `pg_monitor` privilege. The receiver uses its beta per-database connection pool
  (`max_open=4`); the role-wide connection limit is 12 for the three monitored databases.
- Container logs through `receiver_creator` + `docker_observer`: resource attributes
  (`container.id`, `container.name`, `container.image.name`) come from Docker metadata with no
  regex, and an allowlist rule keeps only long-lived compose services (Airflow, Lightdash,
  Inspector, metadata-postgres, cloudflare). Ephemeral dbt/OCR task containers, SigNoz's own
  containers, and the collector itself are never tailed, which avoids ingestion loops and log
  duplication against S3-backed Airflow task logs.
- Airflow native OpenTelemetry metrics and traces (scheduler heartbeat, DAG/task durations,
  failures) and the `/var/log/lakehouse/metadata-backup-audit.log` parsed as JSON (written by the
  metadata backup script; see the plan doc).
- Private application health probes over the telemetry network and public TLS-expiry probes. The
  latter intentionally ignore HTTP status because Cloudflare Access redirects unauthenticated
  requests to its login flow.

Log policy: structured OpenTelemetry DEBUG/INFO records are dropped before export; legacy records
without a severity remain eligible because discarding them would be guesswork. Long-lived Docker
services also emit warning-or-higher at their source. Redaction strips bearer tokens, basic-auth
connection strings, AWS key IDs, and common
`password|token|secret|api_key|cookie|authorization` pairings before export. The same redaction
rules apply to span attribute values (e.g. botocore auto-instrumentation signed URLs redact the
SigV4 `x-amz-credential`/`x-amz-signature`/`x-amz-security-token` query parameters). Docker's
`json-file` rotation (20m × 3) remains the short-term buffer. Collector container runs as root
only to read 0600 docker log files and the socket, with `no-new-privileges` and read-only mounts.
All resource attributes carry `deployment.environment=dev` through `OTEL_RESOURCE_ATTRIBUTES`.

Durability: container-log and backup-audit file positions are checkpointed through the
`file_storage` extension into the named `collection-agent-file-storage` volume, so restarts and
redeploys resume where they stopped instead of skipping the backlog with `start_at: end`.

Liveness: the `health_check` extension answers `GET /` on host loopback port `13133`, and the
container healthcheck invokes the distroless binary's `validate` subcommand (the contrib image
ships no shell or curl), so the SigNoz stack and Docker both detect a wedged collector.

## Dashboards and alerts as code

Dashboards and alert rules exist only as Terraform (`observability/signoz/terraform/`), managed by
the official SigNoz provider (`signoz/signoz`, pinned `~> 0.1.4`, requires SigNoz ≥ v0.133.0). The
pinned `casting.yaml` must stay at v0.135.0 or later for the typed dashboard schema. Directory
layout:

| File | Content |
|---|---|
| `versions.tf`, `backend.tf` | Provider pin and the shared versioned S3 backend (`lakehouse/signoz/dev/terraform.tfstate`) |
| `dashboards_host.tf` | Host Overview: CPU, memory, load, filesystem, disk and network IO |
| `dashboards_containers.tf` | Containers Overview: CPU/memory/network per compose service and per container |
| `dashboards_airflow.tf` | Airflow: scheduler heartbeat, task outcomes, DAG durations, pool state, recent traces |
| `dashboards_postgres.tf` | Metadata PostgreSQL: backends, commits/rollbacks, size, row operations |
| `dashboards_backup.tf` | Metadata backup status parsed from the backup audit JSON lines |
| `alerts.tf` | Disk 70/80/90%, absent-metric rules for ingest liveness, origin/TLS probes, backup failed/missing |

Panel and rule queries use the builder query language over the exact metric names emitted by the
collection agent (`system.*`, `container.*`, `postgresql.*`, `airflow.*`) and the parsed fields of
the backup audit log, identified by the stable resource attribute
`service.name=lakehouse-metadata-backup` rather than a filename.

Applying requires a SigNoz service-account API key stored in AWS Secrets Manager
(`lakehouse/dev/signoz/ci`) or exported as `SIGNOZ_ACCESS_TOKEN` (create one in
Settings → Service Accounts → Keys). CD automatically applies dashboards and alert rules
on merge to `main` via `deploy-signoz.yml`. For manual operator apply:

```sh
export AWS_PROFILE=tgbao-dev
export SIGNOZ_ACCESS_TOKEN=...
make signoz-init signoz-plan signoz-apply
```

`AWS_PROFILE` is needed only for the S3 Terraform backend and AWS APIs. The SigNoz provider itself
uses `SIGNOZ_ACCESS_TOKEN`; GitHub Actions obtains AWS credentials with OIDC instead of a profile.

Notification channels (Slack/SMTP) are not provider resources: create the operator channel once in
the UI, then set `TF_VAR_signoz_alert_channels='["channel-name"]'` so every rule threshold
references it. Until it is set, thresholds carry no channel list and the default UI route applies.
Never edit entities Terraform manages through the UI — drift only reaches the state through
`terraform import`/refresh.

CD automatically reconciles dashboards and alert rules on deploy. CI covers the root through the
shared `terraform-fmt` and `terraform-validate` make targets (`make check`).
