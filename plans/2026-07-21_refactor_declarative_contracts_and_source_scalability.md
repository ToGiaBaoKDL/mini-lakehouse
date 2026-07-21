# Declarative contracts and source-scalable lakehouse refactor

## Metadata

- Status: `superseded`
- Completed checkpoint: `e3d0f63`
- Superseded by: `2026-07-21_refactor_landing_curated_analytics_boundaries.md`

This historical phase introduced strict YAML/Pydantic contracts, source-owned ingestion,
policy-driven maintenance, modular Compose files, Prefect conventions, uv packaging, and
Slack/Gmail lifecycle notifications. Its original dbt-from-landing design was intentionally
replaced after the curated boundary was clarified.

The active plan is the authoritative checklist for the current architecture. In particular:

- landing is immutable and replayable;
- application curation publishes canonical products into curated;
- dbt reads curated and physically publishes only analytics-domain marts;
- source → product → domain references make ownership explicit;
- no compatibility path for the former curated/dbt layout is retained.
