# Pipeline execution and operations

Prefect has two deployments:

- `etl_github_archive`: scheduled without parameters to ingest the previous complete UTC hour;
  custom runs accept `start_hour` and optional `end_hour` for an inclusive backfill. Historical
  custom runs intentionally do not run the real-time freshness gate.
- `gov_iceberg_maintenance`: policy-driven compaction, manifest rewrite, snapshot expiry, and
  orphan-file cleanup for discovered Iceberg tables.

Deployable files live in `orchestration/flows/` and follow `[job_type]_[description].py`, making
that directory the Prefect equivalent of Airflow's `dags/`. Each flow and its source-owned tasks
stay together. Only cross-DAG dbt/retry mechanics live in `orchestration/utils`, and Prefect
integrations in `orchestration/plugins`. There is no private task sidecar, catch-all `tasks.py`, or
`__init__.py` in orchestration.

The ingestion deployment has concurrency one with `ENQUEUE`, avoiding overlap between its schedule
and custom backfill runs. The local process worker is packaged in the same image as the flow code;
there are no host-only imports or runtime pip installs.

Landing is idempotent by UTC source hour. Raw object creation uses the S3 conditional-create
primitive and can never overwrite an existing archive. The Iceberg repository checks only
partition/file metadata, then commits a new hour with dynamic partition overwrite. A normal rerun
is a no-op; retries or concurrent writers cannot accumulate duplicate rows through append. A
missing archive for an already-loaded hour is an integrity error. Backfill is bounded by its
requested start/end hours and rebuilds dbt tables once after ingestion completes.

A normal scheduled/manual trigger uses no parameters. A one-hour replay supplies only
`start_hour`; an inclusive backfill supplies both parameters:

```bash
prefect deployment run etl_github_archive/etl_github_archive
prefect deployment run etl_github_archive/etl_github_archive \
  --param start_hour=2026-07-21T04:00:00Z
prefect deployment run etl_github_archive/etl_github_archive \
  --param start_hour=2026-07-21T04:00:00Z \
  --param end_hour=2026-07-21T05:00:00Z
```

The same fields are exposed by Prefect's custom-run form. Ingestion task retries use delays of
30/120/300 seconds and retry only transient transport/server failures. If a terminal flow run is
retried, Prefect preserves its parameters; already committed hours resolve as metadata-only no-op,
so the retry cannot append duplicates.

Maintenance policy definitions are stored as typed YAML contracts and reconciled as metadata at
the Data Platform-owned Polaris
`curated` root and attached to the `landing`, `curated`, and `analytics` roots so child namespaces
inherit them. No fourth bucket or location-less pseudo-domain is introduced. Polaris stores and
resolves policy metadata; the Prefect governance flow discovers every table, fetches its applicable
policies, validates their typed content, and executes the matching Trino procedures. Tables are
processed in bounded concurrent batches and failures are summarized only after the remaining
tables have run. This removes table-name allowlists while retaining explicit scope and safe
retention thresholds.

The Compose overlay creates or reconciles the process work pool in a one-shot bootstrap service
before starting the worker and registering deployments. Re-running the overlay is therefore
non-interactive and idempotent.

Every task has a failure hook; every flow has running, success, failure, cancellation, and crash
hooks. Configure Slack Bot API and Gmail App Password delivery in `.env`:

```dotenv
LAKEHOUSE_NOTIFICATIONS__SLACK_BOT_TOKEN=xoxb-...
LAKEHOUSE_NOTIFICATIONS__SLACK_CHANNEL_ID=C0123456789
LAKEHOUSE_NOTIFICATIONS__GMAIL_SENDER=lakehouse-alerts@gmail.com
LAKEHOUSE_NOTIFICATIONS__GMAIL_APP_PASSWORD=google-app-password
LAKEHOUSE_NOTIFICATIONS__GMAIL_RECIPIENTS=["data-platform@example.com"]
```

The Slack bot needs `chat:write` and must be a member of the configured channel. A flow-running
hook creates one colored parent message and stores its `thread_ts` as a Prefect Variable keyed by
flow-run ID. Task failures reply inside that thread; terminal flow state updates the parent to
green or red and removes the variable. Gmail sends styled HTML for flow success/failure and each
task failure. It intentionally skips start and task-success mail to prevent alert fatigue.

Both channels include environment, Prefect state, run ID, detail, and a Prefect deep link. Gmail
requires a Google App Password rather than the account password. Channel delivery is independent
and best-effort: Slack/Gmail outages are logged but never mask the pipeline's original state. With
no complete channel set, notifications are disabled for local development.

Start the orchestration overlay with the data plane:

```bash
docker compose -f compose.core.yaml -f compose.prefect.yaml up -d --build
```

Register deployments manually after editing `prefect.yaml`:

```bash
PREFECT_API_URL=http://localhost:4200/api uv run prefect deploy --all
```

Useful health commands:

```bash
docker compose -f compose.core.yaml -f compose.prefect.yaml ps
curl --fail http://localhost:8182/q/health/ready
curl --fail http://localhost:8080/v1/info
curl --fail http://localhost:4200/api/health
uv run dbt debug --project-dir dbt_project --profiles-dir dbt_project
```

To remove the local platform and all local named-volume data:

```bash
docker compose -f compose.core.yaml -f compose.prefect.yaml down --volumes
```

This is destructive and should never be used against shared or cloud buckets.
