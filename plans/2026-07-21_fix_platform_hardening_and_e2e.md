# Platform hardening and end-to-end verification

## Metadata

- Status: `in_progress`
- Scope: Compose, S3/Iceberg contracts, GitHub curation, Streamlit, dbt, Prefect,
  maintenance, Docker, and CI
- Catalog: `prod`
- Migration type: destructive local table recreation; immutable raw archives are retained

## Goals

- Make a clean clone boot without untracked configuration.
- Make schema, partitioning, ownership, and maintenance behavior explicit and validated.
- Preserve event identity across partition changes and prove business behavior against live Trino.
- Make connection, retry, task naming, and dbt failure lifecycles deterministic.
- Keep maintenance bounded and reconcile only project-managed Polaris drift.
- Verify the complete landing → curated → analytics path in CI.

## Non-goals

- No Nimtable or additional metadata platform.
- No GCS dependency, Terraform, Spark, ClickHouse compatibility, or PII feature.
- No incremental dbt materialization yet; dormant bounded incremental SQL remains.
- No production credential migration; local Polaris root/static MinIO credentials remain local-only.

## Invariants

- `event_id` is the sole curated event key, independent of `event_date_utc` partitions.
- Landing commits are atomic per `source_hour`; the physical transform name is `archive_hour`.
- Curated `events` uses identity `event_date_utc`; analytics owns only domain marts.
- YAML contracts are the runtime schema and ownership source of truth; secrets remain environment-only.
- Only policies using the reserved `mlh-` prefix and the exact former curated policy identifiers
  may be automatically pruned.
- Every maintenance data rewrite has a typed partition predicate and finite lookback.

## Implementation checklist

### Compose and configuration

- [ ] Move the container environment to a tracked, non-ignored path.
- [ ] Validate all Compose overlays and env-file references from a clean clone.
- [ ] Make path style, delegation, STS, KMS, endpoint, and region explicit S3 settings.

### Schemas, partitions, and curation

- [ ] Generate Arrow, Iceberg, and Trino schemas from typed source/product contracts.
- [ ] Rename the landing partition field to `archive_hour`.
- [ ] Change curated event partitioning to identity `event_date_utc`.
- [ ] Split table lifecycle and SQL repository concerns out of `GithubCurationService`.
- [ ] Merge events only by the contract primary key and use a deterministic source version tuple.
- [ ] Bound late-event entity updates and metrics to actual affected event dates.
- [ ] Add live integration coverage for an event moving between date partitions.

### Dashboard, dbt, and Prefect

- [ ] Scope every Trino cursor/connection and Iceberg catalog session to one operation/rerun.
- [ ] Align dbt groups, owners, contacts, source metadata, and analytics domain contracts.
- [ ] Remove `select *`, invalid `on_table_exists`, and namespace inference from dbt macros.
- [ ] Retry a GitHub Archive 404 only at the bounded Prefect task boundary.
- [ ] Give reusable tasks meaningful run names and fail the Prefect task on any failed dbt result.

### Maintenance and drift

- [ ] Split Polaris policies by landing, curated, and analytics lifecycle tiers.
- [ ] Compile typed, partition-bounded Trino optimize statements.
- [ ] Replace changed target sets by policy name and prune stale `mlh-` policies with detach-all.
- [ ] Never probe unknown targets with speculative detach calls.

### Build, CI, migration, and evidence

- [ ] Add BuildKit/uv cache mounts and link-copy layers to all image targets.
- [ ] Run unit, format, lint, type, dbt parse, lock, and modular Compose checks.
- [ ] Build optional service images and run landing → curation → dbt integration in CI.
- [ ] Drop and recreate local landing/curated/analytics tables with the new partition specs.
- [ ] Re-run curation, dbt, policy bootstrap, and idempotency checks on recreated data.
- [ ] Update architecture, operations, migration, and verification documentation.
- [ ] Commit the complete hardening change as one reviewed checkpoint.

## Test matrix

| Boundary | Required evidence |
|---|---|
| Contracts | Duplicate/drift validation; stable field IDs; ownership/contact parity |
| Landing | `archive_hour`; one-hour overwrite; mixed-hour rejection; repeat no-op |
| Curation | Single-key MERGE; deterministic version ordering; cross-date partition move |
| dbt | Parse, freshness, build, tests, explicit projections, public contract types |
| Prefect | 404 classification, task names, strict dbt success semantics, failure hooks |
| Maintenance | Tier isolation, bounded SQL, mapping drift, managed policy prune plan |
| Presentation | Cursor and connection closure on success and exception |
| Deployment | Three Compose modules resolve without `.env`; all image targets build |
| End to end | Synthetic landing rows → curated key invariant → both analytics marts |

## Migration and rollback

1. Inventory current raw object hours and table locations.
2. Stop scheduled Prefect work while physical tables are recreated.
3. Drop analytics, curated, then landing Iceberg tables; retain immutable raw gzip objects.
4. Bootstrap catalog/policies, recreate landing from retained archives, curate each hour, and run dbt.
5. Verify locations, partition specs, counts, freshness, and two idempotent reruns before resuming jobs.

Rollback is code/contract revert plus another local rebuild. There is intentionally no compatibility
alias for the old partition specs or former ClickHouse/dbt-curated layout.

## Definition of Done

- Every checklist item is verified and checked.
- The live catalog contains exactly the declared tables at canonical bucket locations.
- Static checks, unit tests, dbt parse/build, Compose validation, image builds, and integration pass.
- The local runtime and CI exercise the same contracts and no stale managed policy survives bootstrap.
