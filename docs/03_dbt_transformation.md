# dbt transformation

The dbt project follows a standard directional graph:

```text
source()
  └── staging/github_archive        one-to-one typed source views
        └── intermediate/github     ephemeral dedupe/enrichment/latest-state logic
              └── marts/core/github conformed fact and dimensions in curated
                    └── marts/engineering public domain aggregates in analytics
```

Important rules:

- Staging always uses `source()`; no physical table name is hardcoded.
- Intermediate models are private and ephemeral until their cost warrants materialization.
- Facts aggregate by stable `event_id`, `repository_id`, and `actor_id`, never mutable names.
- Repository and actor names come from latest-state dimensions after aggregation.
- `push_event_count` and `pushed_commit_count` are distinct metrics.
- Dates are explicitly UTC.
- JSON numeric fields use `try_cast` so an upstream payload change does not silently corrupt the
  entire transformation run.
- Daily marts use Iceberg `MERGE`, a two-day lookback, stable composite keys, and schema-change
  synchronization.

Run and document the graph:

```bash
uv run dbt source freshness --project-dir dbt_project --profiles-dir dbt_project
uv run dbt build --project-dir dbt_project --profiles-dir dbt_project
uv run dbt docs generate --project-dir dbt_project --profiles-dir dbt_project
```

The Trino adapter writes Iceberg v2 Parquet tables through Polaris. Polaris governs catalog and
namespace locations; dbt governs SQL lineage, materialization, tests, ownership, and exposures.
