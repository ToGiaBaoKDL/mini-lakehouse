# dbt transformation

The dbt project follows a standard directional graph:

```text
source()
  └── staging/github_archive        one-to-one typed source views
        └── intermediate/github     ephemeral dedupe/latest-state logic plus one enriched table
              └── marts/core/github conformed fact and dimensions in curated
                    └── marts/engineering public domain aggregates in analytics
```

Important rules:

- Staging always uses `source()`; no physical table name is hardcoded.
- Staging views and the shared enriched intermediate table are private in
  `curated.github.internal`; lightweight intermediate logic remains ephemeral. Materializing the
  enriched table ensures the landing source is parsed and deduplicated once per build instead of
  once for every downstream fact and dimension.
- Facts aggregate by stable `event_id`, `repository_id`, and `actor_id`, never mutable names.
- Repository and actor names come from latest-state dimensions after aggregation.
- `push_event_count` and `pushed_commit_count` are distinct metrics.
- Dates are explicitly UTC.
- JSON numeric fields use `try_cast` so an upstream payload change does not silently corrupt the
  entire transformation run.
- Core and analytics outputs currently materialize as full Iceberg tables. Their stable keys,
  `MERGE` settings, schema-change settings, and bounded lookback predicates remain dormant inside
  the models, so switching selected models back to `incremental` later does not require rewriting
  business SQL.
- Source freshness uses `source_hour`, not ingestion time. This catches a stalled hourly feed even
  when an old archive is backfilled today; warning/error thresholds are two/six hours. A one-day
  partition filter bounds the freshness query without weakening either threshold.

Run and document the graph:

```bash
uv run dbt source freshness --project-dir dbt_project --profiles-dir dbt_project
uv run dbt build --project-dir dbt_project --profiles-dir dbt_project
uv run dbt docs generate --project-dir dbt_project --profiles-dir dbt_project
```

The Trino adapter writes Iceberg v2 Parquet tables through Polaris. Polaris governs catalog and
namespace locations; dbt governs SQL lineage, materialization, tests, ownership, and exposures.
