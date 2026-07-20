# Architecture and ownership

## Bounded contexts

The Python package is split by responsibility rather than by technical script type:

```text
src/mini_lakehouse/
├── config/                 validated runtime configuration
├── github_archive/         source client, boundary models, parser, ingestion service
├── storage/                object-store and Iceberg adapters
├── orchestration/          thin Prefect tasks and deployable flows
├── platform/               idempotent Polaris namespace bootstrap
└── presentation/           read-only Streamlit application
```

`dbt_project/` remains a top-level analytics project because its graph, tests, docs, artifacts,
and release lifecycle differ from the Python application.

## Catalog and storage contract

There is one Polaris catalog named `prod`. Environments should use separate catalogs or separate
Polaris deployments; dbt must not invent environment prefixes inside production namespaces.

- `landing` bucket: source-owned data. Prefixes express transport (`api`, `rdbms`, `stream`).
- `curated` bucket: conformed data without transport details in its namespace.
- `analytics` bucket: consumer-facing data organized by business domain and owner.

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
