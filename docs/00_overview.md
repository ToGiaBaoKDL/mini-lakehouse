# Architecture and ownership

## Data lifecycle

The lifecycle buckets are physical security and retention boundaries. Modeling layers are logical
and do not create extra namespaces:

```text
GitHub Archive
  └─ EL ─▶ prod.landing.github_archive_events_raw
             immutable raw object + replayable source-hour partition
              └─ TL/Trino ─▶ prod."curated.github".{events,actors_current,repositories_current}
                                canonical source-conformed GitHub product
                                  └─ TL/dbt ─▶ prod."analytics.engineering".fct_*_daily
                                                consumer-facing domain metrics
```

- `landing` is one logical namespace. Transport and source identity remain in physical object
  prefixes such as `api/github_archive/`; source-prefixed table names prevent collisions.
- `curated` drops transport identity and publishes reusable, validated source-conformed products.
- `analytics` is organized by business domain and owns consumer semantics.

For a future RDBMS source, use a unique landing table such as
`prod.landing.warehouse_raw_orders`; retain a deterministic transport path such as
`s3://landing/rdbms/warehouse_raw/raw/...` only for immutable source objects. Polaris owns the
namespace location, so the engine derives the managed table path as
`s3://landing/warehouse_raw_orders`. Its curation package publishes to a transport-free curated
product. A future domain can consume one or more curated products without depending on their
ingestion implementation.

## Code boundaries

```text
contracts/
├── catalog.yaml            catalog, namespaces, locations, grants
├── sources/                landing ownership and checkpoints
├── curated_products/       canonical curated products
├── domains/                analytics ownership and published marts
└── policies/               typed Polaris maintenance policy desired state

src/mini_lakehouse/
├── config/                 secret/runtime settings
├── contracts/              strict Pydantic contract models and cross-file validation
├── sources/                source-owned acquisition, parsing, and landing writes
├── curated_products/       curated product schemas and curation services
├── platform/               Polaris, Trino, namespace, RBAC, and maintenance adapters
└── storage/                S3-compatible object store and Iceberg catalog adapters

dbt/analytics/
├── models/staging/         ephemeral curated-source projections
├── models/intermediate/    ephemeral reusable business logic
└── models/marts/           physical analytics-domain tables only

orchestration/
├── flows/<domain>/         deployable DAGs with co-located tasks
├── utils/                  shared dbt and retry mechanics
└── plugins/                Slack/Gmail Prefect lifecycle integrations
```

Deployable flow files live below a domain or platform folder and follow
`[job_type]_[description].py`. Orchestration composes application services but does not contain
source or product business behavior. It is intentionally not a Python package and has no
`__init__.py` or catch-all task module.

## Ownership

| Boundary | Technical owner | Responsibility |
|---|---|---|
| `landing` / `github_archive_*` | Data Platform | Immutable raw archive, parsing fidelity, source-hour idempotency |
| `curated.github` | Data Platform | Canonical event/entity schema, deduplication, normalization, reusable source semantics |
| `analytics.engineering` | Engineering Analytics | Metric definitions, grains, dbt tests/contracts, BI-facing tables |
| Catalog and maintenance | Data Platform | Polaris desired state, access grants, policy reconciliation, Iceberg maintenance |

The registry validates source → curated product → analytics domain references. dbt source metadata,
contracts, tests, and future BI metadata repeat only the pieces dbt-native tooling needs; runtime
secrets remain environment-only.
