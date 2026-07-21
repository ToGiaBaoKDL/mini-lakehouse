# dbt transformation

The dbt project follows a standard directional graph:

```text
source()
  └── staging/github_archive        one-to-one typed source views
        └── intermediate/github     ephemeral dedupe/latest-state logic plus one enriched table
              └── marts/github      conformed GitHub fact and dimensions in curated
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
- GitHub and Engineering marts materialize as full Iceberg tables. Only the bounded
  `is_incremental()` predicates remain dormant, so a future incremental decision does not require
  rewriting business SQL; no inactive `MERGE` or schema-change config is carried today.
- Public marts enforce dbt model contracts in addition to data tests, groups, and access rules.
- Source freshness measures `ingested_at`, because freshness is an ingestion SLA rather than an
  event-time SLA. A one-day `source_hour` filter bounds the query, while warning/error thresholds
  remain two/six hours.
- Prefect invokes named selectors from `selectors.yml`; deployment code does not repeat graph
  expressions.

Run and document the graph:

```bash
uv run dbt source freshness --selector github_archive_freshness --project-dir dbt_project --profiles-dir dbt_project
uv run dbt build --selector engineering_pipeline --project-dir dbt_project --profiles-dir dbt_project
uv run dbt docs generate --project-dir dbt_project --profiles-dir dbt_project
```

The Trino adapter writes Iceberg v2 Parquet tables through Polaris. Polaris governs catalog and
namespace locations; dbt governs SQL lineage, materialization, tests, ownership, and exposures.
