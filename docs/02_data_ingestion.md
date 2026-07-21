# GitHub Archive ingestion

The command below targets the previous complete UTC hour unless `--hour` is supplied:

```bash
uv run lakehouse ingest github-archive --hour 2025-01-01T00:00:00Z
```

The application performs four explicit operations:

1. Validate an hour-aligned, timezone-aware `ArchiveHour`.
2. Download or reuse the immutable gzip object in
   `s3://landing/api/github_archive/raw/year=.../month=.../day=.../hour=.../`.
3. Validate JSON records with Pydantic, preserve the original JSON, and materialize real UTC
   timestamp columns.
4. Replace the Iceberg `source_hour` partition dynamically, making a retry idempotent.

The raw table is `prod."landing.api.github_archive".events_raw`. It is partitioned by source hour,
not business event time, because source-hour replacement is the ingestion transaction boundary.

Malformed rows are counted and the batch fails if their ratio exceeds the configured contract.
HTTP retries apply only to transient statuses; a not-yet-published archive hour is a distinct
domain error.
