# Pipeline execution and operations

## Deployments

Prefect has three independently retryable deployments:

| Deployment | Boundary | Trigger |
|---|---|---|
| `el_github_archive` | GitHub Archive → landing | `15 * * * *` UTC |
| `tl_github_analytics` | landing → curated GitHub → full dbt analytics | `30 * * * *` UTC |
| `gov_iceberg_maintenance` | Policy-driven Iceberg maintenance | Weekly schedule |

The two hourly deployments are deliberately schedule-driven. There are no Prefect event sensors,
automation triggers, or cross-deployment payloads. Landing starts at minute 15; transformation
starts at minute 30 and executes curation, source freshness, and a full `dbt build` sequentially.
The fifteen-minute offset is operational buffer, while landing completeness validation makes a
missing or delayed archive fail explicitly instead of silently building stale analytics. Both
flows derive the archive checkpoint from the Prefect run's scheduled start time, so a queued run
that starts after the next hour still processes the intended hour. Separate work queues isolate
ingestion, transformation, and maintenance, while singleton `ENQUEUE` limits prevent overlapping
writes.

Flow files live under `orchestration/flows/`, follow `[job_type]_[description].py`, and co-locate
their Prefect tasks. Reusable dbt/retry mechanics live in `utils`; notification integrations live
in `plugins`. Source and product business logic remains under `src/mini_lakehouse`.

## Normal runs and retries

A scheduled run processes the previous complete UTC archive hour. A manual Prefect run may supply
one `archive_hour`; multi-hour windows and dedicated backfill DAGs are intentionally absent:

```bash
prefect deployment run el_github_archive/el_github_archive
prefect deployment run el_github_archive/el_github_archive \
  --param archive_hour=2026-07-21T04:00:00Z
prefect deployment run tl_github_analytics/tl_github_analytics \
  --param archive_hour=2026-07-21T04:00:00Z
```

The same single field is available in Prefect's custom-run form. Every TL run executes curated
source freshness and the complete dbt project; there is no alternate historical-run branch.

Idempotency is enforced at the write that owns each boundary:

- Raw objects use S3 conditional create and are immutable.
- Landing atomically overwrites one source-hour checkpoint using an equality predicate projected
  onto the hidden hourly partition; completed retries resolve from metadata without downloading or
  parsing again.
- Curated validates the selected landing hour, deduplicates by `event_id`, and `MERGE`s only that
  hour into canonical tables.
- Analytics tables use atomic full-table replacement. The current project deliberately does not
  claim incremental analytics semantics.

Retrying a failed Prefect task preserves its resolved `archive_hour`. If the independent TL
schedule starts before landing is available, it fails clearly and can be retried after EL succeeds.

## Notifications

Every task has a failure hook. Every flow reports running, success, failure, cancellation, and
crash states. Configure both Slack Bot API and Gmail App Password delivery in `.env`:

```dotenv
LAKEHOUSE_NOTIFICATIONS__SLACK_BOT_TOKEN=xoxb-...
LAKEHOUSE_NOTIFICATIONS__SLACK_CHANNEL_ID=C0123456789
LAKEHOUSE_NOTIFICATIONS__GMAIL_SENDER=lakehouse-alerts@gmail.com
LAKEHOUSE_NOTIFICATIONS__GMAIL_APP_PASSWORD=google-app-password
LAKEHOUSE_NOTIFICATIONS__GMAIL_RECIPIENTS=["data-platform@example.com"]
```

Slack creates one parent message per flow run. Task failures reply in its thread; the terminal
flow state updates the parent. Gmail sends styled flow success/failure and task-failure messages.
Delivery channels are independent and best-effort so notification outages cannot mask pipeline
state. With incomplete configuration, the channel is disabled.

## Maintenance

Typed YAML policies attach at lifecycle namespaces and are inherited by child namespaces. The
governance flow discovers tables from the catalog, resolves applicable Polaris policies, validates
their full content, and invokes bounded Trino maintenance procedures. It has no table allowlist or
dashboard-owned table registry.

## Local operations

```bash
docker compose -f compose.core.yaml -f compose.prefect.yaml up -d
PREFECT_API_URL=http://localhost:4200/api uv run prefect deploy --all

curl --fail http://localhost:8182/q/health/ready
curl --fail http://localhost:8080/v1/info
curl --fail http://localhost:4200/api/health
uv run dbt debug --project-dir dbt/analytics --profiles-dir dbt/analytics
```

The Compose overlay idempotently reconciles the local process work pool and deployments. Removing
the local stack and named volumes is destructive:

```bash
docker compose -f compose.core.yaml -f compose.prefect.yaml down --volumes
```

Never use that cleanup command against shared or cloud storage.
