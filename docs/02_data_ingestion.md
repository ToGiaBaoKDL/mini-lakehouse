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

The raw table is `prod.landing.github_archive_events_raw`. It uses the hidden Iceberg transform
`hour(source_hour)`, not business event time, because source-hour replacement is the ingestion
transaction boundary. Its physical location is derived from source ownership and fixed at
`s3://landing/api/github_archive/tables/events_raw`, beside but isolated from the deterministic
`api/github_archive/raw` object prefix.

Malformed rows are counted and the batch fails if their ratio exceeds the configured contract.
HTTP retries apply only to transient statuses; a not-yet-published archive hour is a distinct
domain error.

## ArXiv metadata

`etl_arxiv_metadata` requests the closed OAI datestamp day `T-1` with identical inclusive
`from`/`until` values and follows every `resumptionToken`. One run publishes:

- `s3://landing/api/arxiv/raw/oai/datestamp=YYYY-MM-DD/responses.tar.zst`: deterministic compressed
  source responses;
- `prod.landing.arxiv_oai_records_raw`: typed records replacing that exact day partition;
- `prod.landing.arxiv_oai_checkpoints`: one successful business checkpoint, written last and also
  present for a valid zero-record day.

The same response bytes build the archive and Arrow rows in one process; the flow does not upload
and download its own archive. Every row carries the archive SHA-256, and a complete retry verifies
the checkpoint, landing-row count/hash, and object hash before becoming a no-op. An explicit
`refresh=true` is required to re-harvest and replace an already committed day. The archive remains
sufficient to reproduce the landing records.
The following task merges current metadata into `prod."curated.arxiv"` and replaces author/category
children only for pending paper mutations. It publishes children before the paper hash used as
the durable completion marker, so a failed run remains retryable and a completed rerun creates no
new Iceberg snapshots. Metadata ingestion never downloads PDFs.
