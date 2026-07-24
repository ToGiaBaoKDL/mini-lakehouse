# Mini Lakehouse

A local-first, production-shaped lakehouse for GitHub Archive and ArXiv data. The stack uses Apache
Polaris as the single `prod` catalog, Apache Iceberg for all persisted tables, Trino for SQL,
dbt for transformation, Prefect for orchestration, and uv for Python packaging.

## Architecture

```text
GitHub Archive API
        │
        ▼
s3://landing/
  └── api/github_archive/
      ├── raw/year=.../month=.../day=.../hour=.../*.json.gz
      └── tables/events_raw/              prod.landing.github_archive_events_raw
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

ArXiv OAI API
        │
        ├── compressed response pages     s3://landing/api/arxiv/raw/oai/datestamp=.../
        └── source-owned Iceberg tables   s3://landing/api/arxiv/tables/
                                           prod.landing.arxiv_oai_*
                    │
                    ▼ current-state curation
s3://curated/arxiv/                       prod."curated.arxiv"
  ├── papers, paper_authors, paper_categories
  └── OCR lineage and canonical elements
                    ▲
                    │ private Kaggle GLM-OCR batch; source PDFs remain ephemeral
```

The three physical buckets are lifecycle/security boundaries. They are intentionally not the
same thing as dbt's modeling layers. Managed table locations are derived from the namespace
and ownership contract. Landing tables receive an explicit source-owned path because all sources
share one logical namespace; curated and analytics tables derive their paths from dedicated
namespace ownership. Per-table locations are not repeated in YAML.

| Contract | Owner | Rules |
|---|---|---|
| Landing | Data Platform | Source response capture plus atomic, source-checkpoint Iceberg partitions |
| Curated GitHub | Data Platform | Stable IDs, deduplication, conformed facts and dimensions |
| Curated ArXiv | Data Platform | Current metadata, OCR lineage, immutable artifacts and elements |
| Engineering marts | Engineering Analytics | Public business metrics at documented grains |

## Quick start

Requirements: Docker with Compose v2, [uv](https://docs.astral.sh/uv/), and an active
AIStor license saved as the ignored local file `minio.license`.
Compose modules consistently follow `compose.<module>.yaml`: `core` is the required data plane,
while `prefect` is the optional orchestration overlay. The BI plane is intentionally absent until
Lightdash is added as a real service.

```bash
make setup
uv sync --frozen --all-extras --all-groups
make validate
make up-core
make smoke
```

The core data plane exposes only loopback ports:

- AIStor S3 API/console: `localhost:9000` / `localhost:9001`
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
make up
```

Set `LAKEHOUSE_KAGGLE__USERNAME` and `LAKEHOUSE_KAGGLE__API_TOKEN` in `.env` before
enabling ArXiv OCR. Run the manual `gov_arxiv_ocr_resources` deployment once after each runner
or pinned-model revision change. It reconciles a private runner Dataset and two private Kaggle
Models, creating a new immutable version only when that resource's desired identity changes.
The OCR deployment's 20-minute schedule is deployed paused by default; metadata ingestion does not
require Kaggle credentials.

Prefect is then available at `localhost:4200`. BI services are not deployed by the current Compose
stack. When Lightdash is added, it
should consume only public analytics marts and dbt metadata, not landing or curated tables directly.

GitHub ingestion runs hourly at minute 15 UTC. A single transformation deployment runs at
minute 30, curates the corresponding archive hour, validates source freshness, tests curated
sources, writes analytics marts serially for Iceberg transaction safety, then runs mart tests.
ArXiv metadata processes the closed OAI datestamp day daily. Once enabled, its OCR deployment
reconciles one private Kaggle run and submits the next bounded batch every 20 minutes.
Prefect task/flow failures and flow success can be sent to Slack and Gmail using the
`LAKEHOUSE_NOTIFICATIONS__*` settings documented in
[pipeline operations](docs/04_pipeline_execution.md). Channels are disabled unless configured.

## Development checks

```bash
make check
```

Integration tests mutate their disposable stack. They refuse to run unless the environment is
explicitly marked `ci`:

```bash
LAKEHOUSE_ENVIRONMENT=ci RUN_LAKEHOUSE_INTEGRATION=1 uv run pytest -m integration
```

See [docs/00_overview.md](docs/00_overview.md) for boundaries and
[docs/04_pipeline_execution.md](docs/04_pipeline_execution.md) for operations. Source and policy
onboarding rules live beside the desired state in [contracts/README.md](contracts/README.md).
The ownership matrix and safe policy migration procedure are documented in
[docs/06_contracts_operations.md](docs/06_contracts_operations.md). Production data moves use the
blue/green procedure in [docs/07_production_migration.md](docs/07_production_migration.md).
