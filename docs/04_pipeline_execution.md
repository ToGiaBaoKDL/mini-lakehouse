# Pipeline execution and operations

Prefect has three deployments:

- `etl_github_archive_hourly`: one hour of ingestion followed by source freshness and dbt build.
- `etl_github_archive_backfill`: an inclusive UTC range, with one dbt build after all hours load;
  historical backfills intentionally do not run the real-time freshness gate.
- `gov_iceberg_maintenance`: policy-driven compaction, manifest rewrite, snapshot expiry, and
  orphan-file cleanup for discovered Iceberg tables.

Deployable files live in the top-level `orchestration/` directory and follow
`[job_type]_[description].py`. Each file contains the tasks used by its flow; the directory is not
a Python package and has no `__init__.py`.

The hourly flow has deployment concurrency one with `ENQUEUE`, avoiding overlap between ingestion
and transformation. The local process worker is packaged in the same image as the flow code; there
are no host-only imports or runtime pip installs.

Landing is append-only and idempotent by UTC source hour. A retry first uses Iceberg partition/file
metadata to count an existing hour; it does not read the full table. The immutable compressed
archive is retained beside the Iceberg table, and a missing archive for an already-loaded hour is
treated as an integrity error. A backfill loads all requested hours before rebuilding tables once.

Maintenance policy definitions are stored as metadata at the Data Platform-owned Polaris
`curated` root and attached to the `landing`, `curated`, and `analytics` roots so child namespaces
inherit them. No fourth bucket or location-less pseudo-domain is introduced. Polaris stores and
resolves policy metadata; the Prefect governance flow discovers every table, fetches its applicable
policies, validates their typed content, and executes the matching Trino procedures. This removes
table-name allowlists while retaining explicit scope and safe retention thresholds.

The Compose overlay creates or reconciles the process work pool in a one-shot bootstrap service
before starting the worker and registering deployments. Re-running the overlay is therefore
non-interactive and idempotent.

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
