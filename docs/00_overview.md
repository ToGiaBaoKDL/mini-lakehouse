# Architecture and ownership

## Bounded contexts

The Python package is split by responsibility rather than by technical script type. Deployable
Prefect DAGs stay outside the package because they are runtime entrypoints, not reusable domain
modules:

```text
orchestration/
├── flows/                   deployable Prefect DAGs with co-located tasks
│   ├── etl_github_archive.py
│   └── gov_iceberg_maintenance.py
├── utils/                   dbt invocation and retry policy
└── plugins/                 threaded Slack/Gmail Prefect lifecycle hooks

contracts/                  reviewed non-secret platform desired state

src/mini_lakehouse/
├── config/                 endpoints, credentials, and runtime environment settings
├── contracts/              strict, ownership-scoped contract modules
│   ├── catalog.py          catalog, namespaces, and RBAC desired state
│   ├── sources.py          source/checkpoint/table declarations
│   ├── domains.py          analytics ownership and published tables
│   ├── policies.py         typed Polaris policy contents and targets
│   └── registry.py         cross-file reference and boundary validation
├── sources/                one package per source boundary
│   └── github_archive/     client, schema, parser, repository, ingestion service
├── storage/                provider and Iceberg catalog adapters only
├── platform/               Polaris adapter plus isolated catalog/RBAC/namespace reconcilers
└── presentation/           read-only Streamlit application
```

Every deployable DAG lives under `flows/` and follows `[job_type]_[description].py`, analogous to
Airflow's `dags/` discovery boundary. Its flow and source-owned tasks stay in the same file.
Cross-DAG mechanics live under `utils/` and Prefect integrations under `plugins/`; there is no
private task sidecar or catch-all `tasks.py`. None of these directories needs `__init__.py`.
Source and platform behavior remains in `mini_lakehouse`; orchestration only composes it.

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

- Data Platform owns ingestion, landing, Polaris contracts, `staging`, `intermediate`, and GitHub
  facts/dimensions.
- Engineering Analytics owns the public engineering marts and their metric semantics.
- Platform operators own Iceberg maintenance and infrastructure lifecycle.
- Streamlit is a consumer. It cannot redefine metrics or query private intermediate models.

dbt `group`, `access`, source metadata, tests, and exposure declarations make those boundaries
machine-readable.

The root `contracts/` registry makes platform desired state machine-readable as well. It contains
no executable SQL and no secret. `uv run lakehouse validate` must pass before bootstrap or ingest.
