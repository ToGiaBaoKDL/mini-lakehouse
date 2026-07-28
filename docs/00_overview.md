# Architecture and ownership

## Data lifecycle

The lifecycle buckets are physical security and retention boundaries. Modeling layers are logical
and do not create extra namespaces:

```text
External API / RDBMS / stream
  └─ EL ─▶ <catalog>.landing.<source>_*_raw
             immutable source objects + replayable checkpointed tables
              └─ TL/Trino ─▶ <catalog>."curated.<product>".*
                                canonical transport-free products
                                  └─ TL/dbt ─▶ <catalog>."analytics.<domain>".*
                                                consumer-facing domain models
```

- `landing` is one logical namespace. Transport and source identity remain in physical object
  prefixes such as `api/github_archive/`; source-prefixed logical table names prevent collisions.
  Each landing Iceberg table is physically isolated below
  `<transport>/<source>/tables/<table-key>`.
- `curated` drops transport identity and publishes reusable, validated source-conformed products.
- `analytics` is organized by business domain and owns consumer semantics.

For example, an RDBMS source can use a unique landing table such as
`<catalog>.landing.warehouse_raw_orders`; retain a deterministic transport path such as
`s3://landing/rdbms/warehouse_raw/raw/...` for immutable source objects and publish the table at
`s3://landing/rdbms/warehouse_raw/tables/orders`. The contract derives that path from source type,
source name, and table key; it is not copied into table YAML. Its curation package publishes to a
transport-free curated product. A future domain can consume one or more curated products without
depending on their ingestion implementation.

## Code boundaries

```text
contracts/
├── platform.yaml           catalog and lifecycle roots
├── access.yaml             privileges for existing catalog roles
├── maintenance.yaml        tier defaults and bounded table optimizations
├── sources/                landing ownership and checkpoints
├── curated/                canonical curated products
├── domains/                analytics ownership and published marts
└── processors/             provider-neutral processing contracts

src/mini_lakehouse/
├── config/                 secret/runtime settings
├── contracts/              strict Pydantic contract models and cross-file validation
├── sources/                source-owned acquisition, parsing, and landing writes
├── curated/                curated product schemas and curation services
├── platform/
│   ├── catalog/            Polaris SDK, bootstrap, physical layout, and policy administration
│   ├── maintenance.py      Iceberg maintenance planning
│   ├── trino.py            shared Trino execution boundary
│   └── validate.py         runtime readiness checks
├── processing/ocr/         provider-neutral protocol plus remote compute adapters
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
| `landing` / source-prefixed tables | Declared source owner | Immutable capture, parsing fidelity, checkpoint idempotency |
| `curated.<product>` | Declared product owner | Canonical schema, deduplication, normalization, reusable source semantics |
| `analytics.<domain>` | Declared domain owner | Metric definitions, grains, dbt tests/contracts, BI-facing tables |
| Catalog and maintenance | Data Platform | Idempotent bootstrap, drift validation, access grants, policies, Iceberg maintenance |

The registry derives each curated-product and analytics-domain namespace from its owning contract,
then validates source → curated product → analytics domain references. dbt source metadata,
contracts, tests, and future BI metadata repeat only the pieces dbt-native tooling needs; runtime
secrets remain environment-only.
