# Mini Lakehouse

A local-first, production-shaped lakehouse for GitHub Archive data. The stack uses Apache
Polaris as the single `prod` catalog, Apache Iceberg for all persisted tables, Trino for SQL,
dbt for transformation, Prefect for orchestration, and uv for Python packaging.

## Architecture

```text
GitHub Archive API
        │
        ▼
s3://landing/
  ├── api/github_archive/raw/year=.../month=.../day=.../hour=.../*.json.gz
  └── github_archive_events_raw/         prod.landing.github_archive_events_raw
        │
        ▼ Trino curation: validate → normalize → idempotent MERGE
s3://curated/github/                     prod."curated.github"
  ├── events/
  ├── actors_current/
  └── repositories_current/
        │
        ▼ dbt/analytics: ephemeral staging → intermediate → marts
s3://analytics/engineering/              prod."analytics.engineering"
  ├── fct_repository_activity_daily/
  └── fct_contributor_activity_daily/
```

The three physical buckets are lifecycle/security boundaries. They are intentionally not the
same thing as dbt's modeling layers. Managed table locations are derived from the namespace
location and logical table name; application code and dbt models do not repeat physical paths.

| Contract | Owner | Rules |
|---|---|---|
| Landing | Data Platform | Immutable archive plus one atomic Iceberg partition per source hour |
| Curated GitHub | Data Platform | Stable IDs, deduplication, conformed facts and dimensions |
| Engineering marts | Engineering Analytics | Public business metrics at documented grains |

## Quick start

Requirements: Docker with Compose v2 and [uv](https://docs.astral.sh/uv/).
Compose modules consistently follow `compose.<module>.yaml`: `core` is the required data plane,
while `prefect` is the optional orchestration overlay. The BI plane is intentionally absent until
Lightdash is added as a real service.

```bash
cp .env.example .env
uv sync --frozen --all-extras --all-groups
uv run lakehouse validate
docker compose -f compose.core.yaml up -d --build
```

The core data plane exposes only loopback ports:

- MinIO API/console: `localhost:9000` / `localhost:9001`
- Polaris API/management health: `localhost:8181` / `localhost:8182`
- Trino: `localhost:8080`

Ingest and curate the previous complete UTC hour, then run the same phased analytics build used by
Prefect:

```bash
uv run lakehouse ingest github-archive
uv run lakehouse curate github
uv run dbt source freshness \
  --project-dir dbt/analytics --profiles-dir dbt/analytics \
  --selector github_sources
uv run dbt test \
  --project-dir dbt/analytics --profiles-dir dbt/analytics \
  --selector github_sources --indirect-selection cautious
uv run dbt run \
  --project-dir dbt/analytics --profiles-dir dbt/analytics \
  --selector engineering_marts --threads 1
uv run dbt test \
  --project-dir dbt/analytics --profiles-dir dbt/analytics \
  --selector engineering_marts
```

Add the orchestration control plane when you need scheduled deployments:

```bash
docker compose -f compose.core.yaml -f compose.prefect.yaml up -d --build
```

Prefect is then available at `localhost:4200`. BI services are not deployed by the current Compose
stack. When Lightdash is added, it
should consume only public analytics marts and dbt metadata, not landing or curated tables directly.

The ingestion deployment runs hourly at minute 15 UTC. A single transformation deployment runs at
minute 30, curates the corresponding archive hour, validates source freshness, tests curated
sources, writes analytics marts serially for Iceberg transaction safety, then runs mart tests.
Prefect task/flow failures and flow success can be sent to Slack and Gmail using the
`LAKEHOUSE_NOTIFICATIONS__*` settings documented in
[pipeline operations](docs/04_pipeline_execution.md). Channels are disabled unless configured.

## Development checks

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -m "not integration"
uv run dbt parse --project-dir dbt/analytics --profiles-dir dbt/analytics
docker compose -f compose.core.yaml config --quiet
docker compose -f compose.core.yaml -f compose.prefect.yaml config --quiet
```

Run read-only integration tests after the stack is healthy:

```bash
RUN_LAKEHOUSE_INTEGRATION=1 uv run pytest -m integration
```

See [docs/00_overview.md](docs/00_overview.md) for boundaries and
[docs/04_pipeline_execution.md](docs/04_pipeline_execution.md) for operations. Source and policy
onboarding rules live beside the desired state in [contracts/README.md](contracts/README.md).
The ownership matrix and safe policy migration procedure are documented in
[docs/06_contracts_operations.md](docs/06_contracts_operations.md).
