# Pipeline execution and operations

Prefect has three deployments:

- `github-archive-hourly`: one hour of ingestion followed by source freshness and dbt build.
- `github-archive-backfill`: an inclusive UTC range, with one dbt build after all hours load.
- `iceberg-maintenance-weekly`: compaction, snapshot expiry, and orphan-file cleanup on an explicit
  allowlist of owned tables.

The hourly flow has deployment concurrency one with `ENQUEUE`, avoiding overlap between ingestion
and transformation. The local process worker is packaged in the same image as the flow code; there
are no host-only imports or runtime pip installs.

The Compose overlay creates or reconciles the process work pool in a one-shot bootstrap service
before starting the worker and registering deployments. Re-running the overlay is therefore
non-interactive and idempotent.

Start the orchestration module with the data plane:

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
