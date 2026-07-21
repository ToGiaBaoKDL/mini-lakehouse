# Landing → curated → analytics boundary refactor

## Metadata

- Status: `completed`
- Catalog: `prod`
- Runtime: local Docker Compose, MinIO, Polaris, Trino, Prefect, dbt-trino
- Supersedes: `2026-07-21_refactor_declarative_contracts_and_source_scalability.md`

## Scope

- [x] Landing remains immutable, source-owned, and replayable.
- [x] Curated contains only canonical, validated GitHub data product tables.
- [x] Analytics contains only domain-owned dbt marts.
- [x] dbt reads curated sources and never materializes staging/intermediate relations in curated.
- [x] Polaris namespaces, storage locations, contracts, flows, documentation, and runtime state agree.
- [x] Stale relations and active `__dbt_tmp` locations are removed through a reviewed rebuild.
- [x] Each write is retryable, observable, and idempotent without requiring one deployment per write.

## Explicit exclusions

- Terraform is not introduced.
- PII classification, masking, tokenization, and PII-specific access policy are not introduced.
- Spark is not introduced; Trino is the current curation execution engine.
- dbt models remain full-table materializations; incremental materialization is deferred.

## Target data plane

```text
prod
├── landing
│   └── github_archive_events_raw
├── curated.github
│   ├── events
│   ├── actors_current
│   └── repositories_current
└── analytics.engineering
    ├── fct_repository_activity_daily
    └── fct_contributor_activity_daily
```

## Implementation checklist

### Contracts and catalog

- [x] Add a typed curated product contract and cross-layer source/domain references.
- [x] Remove the former private curated namespace from desired and live catalog state.
- [x] Remove landing/dbt ownership coupling from source contracts.
- [x] Validate one curated product per curated leaf namespace.
- [x] Validate analytics domains reference existing curated products.
- [x] Keep transport classification in landing object prefixes, not logical namespaces.
- [x] Use globally unique source-prefixed table names in the shared landing namespace.
- [x] Use `hour(source_hour)` plus atomic checkpoint-predicate overwrite for readable partitions
  and retry-safe landing writes.

### Curation

- [x] Create canonical curated Iceberg tables at contract-derived locations.
- [x] Curate one resolved source hour with partition predicates.
- [x] Deduplicate events by stable event ID.
- [x] Extract reusable event attributes and exclude raw payload columns from curated output.
- [x] Merge event/entity state idempotently without rewriting the full landing table.
- [x] Fail explicitly when the resolved landing hour is missing.
- [x] Expose the curation boundary through both application CLI and Prefect.

### Orchestration

- [x] Schedule `el_github_archive` at minute 15 UTC: source → landing only.
- [x] Schedule one `tl_github_analytics` at minute 30 UTC: curate → freshness → full dbt build.
- [x] Remove Prefect event emission, event triggers, sensor-style chaining, and window propagation.
- [x] Preserve task/flow retry and Slack/Gmail lifecycle notifications.
- [x] Isolate ingestion, transformation, and maintenance work queues.

### dbt analytics

- [x] Move the project to `dbt/analytics`.
- [x] Declare curated GitHub tables as dbt sources with curated checkpoint freshness.
- [x] Keep staging and intermediate models ephemeral.
- [x] Keep only Engineering marts as physical dbt tables.
- [x] Use canonical analytics locations and atomic Iceberg table replacement.
- [x] Remove GitHub curated marts and landing source definitions from dbt.
- [x] Keep dormant bounded incremental predicates without enabling incremental materialization.

### Migration and verification

- [x] Purge legacy curated/internal and legacy curated mart relations.
- [x] Recreate curated and analytics tables at canonical locations.
- [x] Reconcile the two cron deployments and remove the two event-triggered TL deployments live.
- [x] Recreate landing under `prod.landing.github_archive_events_raw` and remove the old nested
  namespace/table without a compatibility alias.
- [x] Verify row counts, relation types, locations, freshness, and repeat-run idempotency live.
- [x] Run image and live runtime checks; lock, format, lint, type, unit, dbt parse, and Compose
  validation pass.
- [x] Commit the completed boundary refactor separately from the checkpoint commit.

## Verification evidence

- Contract and live catalog both have 5 desired namespaces and one logical landing namespace; the
  former `landing.api*` and empty `curated.github.internal` namespaces were removed.
- Live relations are exactly 1 landing table, 3 curated product tables, and 2 analytics marts.
- Canonical locations are `s3://curated/github/<table>` and
  `s3://analytics/engineering/<table>`; no active table points to `__dbt_tmp`.
- Two consecutive dbt builds completed with `PASS=20`, `ERROR=0`, `NO-OP=1`; curated source
  freshness passed.
- Prefect live uses independent minute-15 and minute-30 cron deployments. The TL deployment
  curates one archive hour and then runs source freshness plus the complete dbt project in the same
  flow. Both former event-triggered deployments and their automations were removed.
- Governance discovered and maintained all 6 Iceberg tables successfully.
- Landing and curated both contain `995,516` rows for source hours 04–09 UTC. Landing data files
  use paths such as `source_hour_hour=2026-07-21-04`, and Trino reports
  `partitioning = ARRAY['hour(source_hour)']`.
- Live TL run `109cbdf3-43ee-417c-8e7c-470fd40e6ad8` completed curation, source freshness,
  full-project dbt models, and tests.
- Landing retry for hour 09 returned `was_written=false`; TL retry
  `a4ff6562-680e-435a-ae39-0418fa7825b0` completed with row parity unchanged at `995,516`.
- Current unit/contract suite: 81 passed; live integration: 1 passed. Ruff, Pyright, dbt parse,
  contract validation, modular Compose validation, and the two targeted image builds are clean.
