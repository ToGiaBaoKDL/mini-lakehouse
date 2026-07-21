# Mini Lakehouse

A local-first, production-shaped lakehouse for GitHub Archive data. The stack uses Apache
Polaris as the single `prod` catalog, Apache Iceberg for all persisted tables, Trino for SQL,
dbt for transformation, Prefect for orchestration, and uv for Python packaging.

## Architecture

```text
GitHub Archive API
        │
        ▼
s3://landing/api/github_archive/
  ├── raw/year=.../month=.../day=.../hour=.../*.json.gz
  └── events_raw                         prod."landing.api.github_archive"
        │
        ▼ dbt: staging → intermediate
s3://curated/                     prod."curated.github.internal" (private)
  ├── stg_github_archive__events (view)
  └── int_github__events_enriched (table)
        │
        ▼ dbt: GitHub domain marts
                                  prod."curated.github" (public)
  ├── fct_github_events
  ├── dim_github_actors
  └── dim_github_repositories
        │
        ▼ dbt: domain marts
s3://analytics/                   prod."analytics.engineering"
  ├── fct_repository_activity_daily
  └── fct_contributor_activity_daily
```

The three physical buckets are lifecycle/security boundaries. They are intentionally not the
same thing as dbt's modeling layers.

| Contract | Owner | Rules |
|---|---|---|
| Landing | Data Platform | Immutable archive plus one atomic Iceberg partition per source hour |
| Curated GitHub | Data Platform | Stable IDs, deduplication, conformed facts and dimensions |
| Engineering marts | Engineering Analytics | Public business metrics at documented grains |

## Quick start

Requirements: Docker with Compose v2 and [uv](https://docs.astral.sh/uv/).
Compose modules consistently follow `compose.<module>.yaml`: `core` is the required data plane,
while `prefect` and `dashboard` are optional overlays.

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

Ingest the previous complete UTC hour and build the dbt project:

```bash
uv run lakehouse ingest github-archive
uv run dbt build --project-dir dbt_project --profiles-dir dbt_project
```

Add the orchestration control plane when you need scheduled deployments:

```bash
docker compose -f compose.core.yaml -f compose.prefect.yaml up -d --build
```

Prefect is then available at `localhost:4200`. Add the presentation plane independently:

```bash
docker compose -f compose.core.yaml -f compose.dashboard.yaml up -d --build
```

Streamlit is then available at `localhost:8501`. Business charts query only public analytics
marts; its separate operational metadata page reads Iceberg catalog metadata without redefining
domain metrics.

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
uv run dbt parse --project-dir dbt_project --profiles-dir dbt_project
docker compose -f compose.core.yaml config --quiet
docker compose -f compose.core.yaml -f compose.prefect.yaml config --quiet
docker compose -f compose.core.yaml -f compose.dashboard.yaml config --quiet
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
