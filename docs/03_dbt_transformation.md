# dbt analytics transformation

dbt starts at the canonical curated boundary. Raw acquisition and source-conformance are not dbt
models in this project:

```text
source('github', ...): prod."curated.github"
  └── staging/github          ephemeral one-to-one analytics projections
        └── intermediate/engineering  ephemeral reusable classifications
              └── marts/engineering   physical prod."analytics.engineering" tables
```

Rules:

- `source()` is the only way models address curated relations.
- Staging and intermediate models are ephemeral; curated never stores dbt implementation details.
- Only domain marts are physical tables. Polaris namespace properties own analytics storage
  locations; dbt does not duplicate table-level locations.
- Marts currently use full `table` materialization with a single Trino Iceberg
  `CREATE OR REPLACE TABLE AS SELECT` statement. This keeps refreshes atomic and avoids
  rename-based `__dbt_tmp` locations. Column types remain documented in model YAML; grain,
  nullability, and relationships are executable dbt tests. Dormant bounded `is_incremental()`
  predicates remain next to business SQL so a later reviewed materialization change does not
  rewrite metrics.
- Aggregation uses stable IDs, and current names are joined after aggregation.
- Public column types, uniqueness, nullability, relationships, and future BI metadata remain
  dbt-native. Trino infers the declared mart types from explicit model projections.
- Curated source freshness measures `max(source_hour)`, which represents the newest fully curated
  source checkpoint. Only the event stream has a freshness SLA; current entity snapshots do not.

Commands:

```bash
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
uv run dbt docs generate --project-dir dbt/analytics --profiles-dir dbt/analytics
```

Polaris governs catalog metadata and namespace locations; the curation application owns canonical
curated schemas; dbt owns analytics SQL lineage, tests, contracts, and materialization.
