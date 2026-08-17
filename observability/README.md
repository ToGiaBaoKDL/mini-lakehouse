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
generated `pours/`); durable data lives in the named Docker volumes. The UI is reachable at
`http://127.0.0.1:8082` on the host only; OTLP ingest ports 4317/4318 bind to 127.0.0.1. The UI
will move behind Cloudflare Tunnel/Access as `observe.tgblab.io.vn` in a later phase.

### Upgrades

Bump the reviewed digest pins in `casting.yaml`, re-forge locally with the same Foundry version,
commit the updated `casting.yaml.lock`, and merge. Never patch files under `pours/` or restart
containers with an altered compose; `foundryctl cast` is the only mutation path.

### Retention

Retention is application state in SigNoz's metastore (enforced through ClickHouse table TTLs), not
casting configuration and not a Terraform resource. After first deployment set it once through the
same settings API the UI uses (Settings → Workspace → Retention Controls): logs 7 days, traces
7 days, metrics 30 days. The self-hosted defaults are 15 days logs/traces and 30 days metrics; do
not rely on them.

### Backup and restore

SigNoz telemetry is disposable; S3 remains the durable record of task logs and database backups.
The ClickHouse volume is the only state worth protecting against a full-host loss. Before any
SigNoz or host upgrade, snapshot the volumes:

```bash
for volume in signoz-telemetrystore-0-0-data signoz-metastore-postgres-0-data \
    signoz-telemetrykeeper-0-data; do
  docker run --rm -v "$volume":/data -v "$HOME/signoz-backup":/backup alpine \
    tar -czf "/backup/$volume.tar.gz" -C /data .
done
```

Restore stops the stack, replaces the volume contents, and re-casts. A restore drill belongs to the
upgrade checklist; dev accepts losing telemetry if the drill is not run.

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

The agent joins the Foundry-created `signoz-network` and the shared `lakehouse-metadata` network
(both external network dependencies, deploy order matters) and exports to the already-host-bound
`signoz-ingester:4317`; it never binds host ports. It is also the single OTLP gateway for
applications: Airflow exports metrics and traces over `lakehouse-metadata`, and the ArXiv Inspector
exports traces over `signoz-network`, both to `signoz-collection-agent:4317`. Secrets for the
`postgresql` receiver are supplied at deploy time from Secrets Manager through the
`metadata-postgres` identity — never stored in config or compose.

Signals collected:

- `hostmetrics` (host root mounted read-only at `/hostfs`, virtual mounts excluded): CPU, RAM,
  load, filesystem, disk IO, paging, network, process counts.
- `docker_stats` via the read-only Docker socket, with compose project/service labels promoted to
  metric attributes.
- `postgresql` metrics for the shared metadata server through a login role granted only the
  built-in `pg_monitor` privilege.
- Container logs through `receiver_creator` + `docker_observer`: resource attributes
  (`container.id`, `container.name`, `container.image.name`) come from Docker metadata with no
  regex, and an allowlist rule keeps only long-lived compose services (Airflow, Lightdash,
  Inspector, metadata-postgres, cloudflare). Ephemeral dbt/OCR task containers, SigNoz's own
  containers, and the collector itself are never tailed, which avoids ingestion loops and log
  duplication against S3-backed Airflow task logs.
- Airflow native OpenTelemetry metrics and traces (scheduler heartbeat, DAG/task durations,
  failures) and the `/var/log/lakehouse/metadata-backup-audit.log` parsed as JSON (written by the
  metadata backup script; see the plan doc).

Log policy: redaction strips bearer tokens, basic-auth connection strings, AWS key IDs, and common
`password|token|secret|api_key|cookie|authorization` pairings before export; Docker's
`json-file` rotation (20m × 3) remains the short-term buffer. Collector container runs as root
only to read 0600 docker log files and the socket, with `no-new-privileges` and read-only mounts.
All resource attributes carry `deployment.environment=dev` through `OTEL_RESOURCE_ATTRIBUTES`.

Liveness: the `health_check` extension answers `GET /` on `:13133` in-container, and the
container healthcheck invokes the distroless binary's `validate` subcommand (the contrib image
ships no shell or curl), so the SigNoz stack and Docker both detect a wedged collector.
