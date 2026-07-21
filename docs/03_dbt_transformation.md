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
- Only domain marts are physical tables and each has an explicit canonical
  `s3://analytics/<domain>/<table>` location.
- Marts currently use full `table` materialization. Dormant bounded `is_incremental()` predicates
  remain next to business SQL so a later reviewed materialization change does not rewrite metrics.
- `on_table_exists='replace'` is model-level dbt-trino config, producing atomic Iceberg replacement
  without a permanent `__dbt_tmp` location. It is intentionally not a project YAML `+` config,
  which has incompatible schema validation in current dbt tooling.
- Aggregation uses stable IDs, and current names are joined after aggregation.
- dbt contracts fix public column types; uniqueness, nullability, relationships, groups, access,
  and exposure metadata remain dbt-native.
- Curated source freshness measures `max(source_hour)`, which represents the newest fully curated
  source checkpoint. Only the event stream has a freshness SLA; current entity snapshots do not.

Commands:

```bash
uv run dbt source freshness \
  --project-dir dbt/analytics --profiles-dir dbt/analytics
uv run dbt build \
  --project-dir dbt/analytics --profiles-dir dbt/analytics
uv run dbt docs generate --project-dir dbt/analytics --profiles-dir dbt/analytics
```

Polaris governs catalog metadata and namespace locations; the curation application owns canonical
curated schemas; dbt owns analytics SQL lineage, tests, contracts, materialization, and exposures.
