# Architecture and ownership

## Bounded contexts

The Python package is split by responsibility rather than by technical script type. Deployable
Prefect DAGs stay outside the package because they are runtime entrypoints, not reusable domain
modules:

```text
orchestration/
├── etl_github_archive_hourly.py
├── etl_github_archive_backfill.py
└── gov_iceberg_maintenance.py

src/mini_lakehouse/
├── config/                 validated runtime configuration
├── contracts/              canonical catalog/table identifiers shared by all consumers
├── github_archive/         source client, boundary models, parser, ingestion service
├── storage/                object-store and Iceberg adapters
├── platform/               Polaris bootstrap, policies, and Iceberg maintenance planning
└── presentation/           read-only Streamlit application
```

Each DAG file follows `[job_type]_[description].py` and owns its Prefect tasks and flow. Shared
business logic remains in `mini_lakehouse`; DAG files only adapt that logic to Prefect.

`dbt_project/` remains a top-level analytics project because its graph, tests, docs, artifacts,
and release lifecycle differ from the Python application.

## Catalog and storage contract

There is one Polaris catalog named `prod`. Environments should use separate catalogs or separate
Polaris deployments; dbt must not invent environment prefixes inside production namespaces.

- `landing` bucket: source-owned data. Prefixes express transport (`api`, `rdbms`, `stream`).
- `curated` bucket: conformed data without transport details in its namespace.
- `analytics` bucket: consumer-facing data organized by business domain and owner.

GitHub Archive lands at `prod."landing.api.github_archive".events_raw`. Private dbt staging and
shared enriched data live in `prod."curated.github.internal"`; conformed public facts and
dimensions live in `prod."curated.github"`; Engineering-owned marts live in
`prod."analytics.engineering"`. Nested Polaris namespaces intentionally become one quoted Trino
schema, so all application code renders identifiers through the shared table contract.

For future RDBMS ingestion, use `prod."landing.rdbms.<database>_raw".<table>`. For streams, use
`prod."landing.stream.<platform>".<topic>`. These are naming contracts, not code paths coupled to
GitHub Archive.

## Ownership

- Data Platform owns ingestion, landing, Polaris contracts, `staging`, `intermediate`, and core
  GitHub facts/dimensions.
- Engineering Analytics owns the public engineering marts and their metric semantics.
- Platform operators own Iceberg maintenance and infrastructure lifecycle.
- Streamlit is a consumer. It cannot redefine metrics or query private intermediate models.

dbt `group`, `access`, source metadata, tests, and exposure declarations make those boundaries
machine-readable.
