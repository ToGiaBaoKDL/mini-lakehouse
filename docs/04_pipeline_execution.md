# Pipeline execution and operations

## Deployments

Prefect has six independently retryable deployments:

| Deployment | Boundary | Trigger |
|---|---|---|
| `el_github_archive` | GitHub Archive → landing | `15 * * * *` UTC |
| `tl_github_analytics` | landing → curated GitHub → phased dbt analytics | `30 * * * *` UTC |
| `etl_arxiv_metadata` | ArXiv OAI `T-1` → landing → curated ArXiv | `0 6 * * *` UTC |
| `etl_arxiv_ocr` | Reconcile remote OCR output → curated; submit next batch | `*/20 * * * *` UTC, paused by default |
| `gov_arxiv_ocr_resources` | Reconcile the selected provider's model/runtime resources | Manual only |
| `gov_iceberg_maintenance` | Policy-driven Iceberg maintenance | Weekly schedule |

The two hourly deployments are deliberately schedule-driven. There are no Prefect event sensors,
automation triggers, or cross-deployment payloads. Landing starts at minute 15; transformation
starts at minute 30 and executes curation, source freshness, curated source tests, serialized mart
table writes, and mart tests. The fifteen-minute offset is operational buffer, while landing
completeness validation makes a missing or delayed archive fail explicitly instead of silently
building stale analytics. Both flows derive the archive checkpoint from the Prefect run's scheduled
start time, so a queued run that starts after the next hour still processes the intended hour.
Separate work queues isolate ingestion, transformation, external processing, and maintenance,
while singleton `ENQUEUE` limits prevent overlapping writes.

Flow files live under `orchestration/flows/<domain>/`, follow `[job_type]_[description].py`, and
co-locate their Prefect tasks. Reusable dbt/retry mechanics live in `utils`; notification
integrations live in `plugins`. Source and product business logic remains under
`src/mini_lakehouse`.

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

The same single field is available in Prefect's custom-run form. Every TL run executes the same
phased dbt pipeline; there is no alternate historical-run branch.

Idempotency is enforced at the write that owns each boundary:

- GitHub raw objects use S3 conditional create and are immutable.
- Landing atomically overwrites one source-hour checkpoint using an equality predicate projected
  onto the hidden hourly partition; completed retries resolve from metadata without downloading or
  parsing again.
- Curated validates the selected landing hour, deduplicates by `event_id`, and `MERGE`s only that
  hour into canonical tables.
- Analytics tables use atomic full-table replacement. The current project deliberately does not
  claim incremental analytics semantics.
- ArXiv replaces one closed OAI datestamp partition and its deterministic raw archive key; the
  successful landing checkpoint is written last, then curated metadata converges by mutation hash.

Retrying a failed Prefect task preserves its resolved `archive_hour`. If the independent TL
schedule starts before landing is available, it fails clearly and can be retried after EL succeeds.

ArXiv OCR does not hold a Prefect task open while polling remote compute. Each scheduled run
performs one bounded state transition: reconcile the exact persisted provider execution, import
each successful document independently, and submit at most one next batch of two. Kaggle capacity
checks its available GPU quota before durable preparation; Modal authenticates and resolves the
deployed app. The
runner downloads PDFs into ephemeral storage and fingerprints the complete batch before resolving
model resources. A metadata mutation whose PDF content matches the latest compatible import creates
new request lineage that references the existing processing artifact; when every PDF is unchanged,
the runner never starts vLLM. Changed PDFs share one pinned model startup, and no source PDF is
published. `ocr_batches` is the durable outbox and `ocr_document_runs` preserves attempt history;
the outbox stores the immutable validated runner request used to reconcile an in-flight batch
across Prefect retries and code deployments. A prepared batch from an older processor configuration
is closed and reselected under the current configuration only when no matching remote run exists.
Prefect remains the owner of orchestration task/run history.

One deterministic runner bundle and output protocol are shared by all providers. Kaggle publishes
that bundle as a private, content-addressed Dataset and the pinned Hugging Face revisions as
private Models. Modal builds the locked dependencies into its image, keeps pinned model snapshots
in a Volume, and stores provider output in a separate Volume until Prefect validates and publishes
it. Modal reuses a compatible vLLM process while its single GPU container remains warm; Kaggle
starts and reaps one server per notebook. Run the manual resource deployment for the selected
provider before its first OCR run:

```dotenv
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...
```

```bash
prefect deployment run gov_arxiv_ocr_resources/gov_arxiv_ocr_resources \
  --param provider=kaggle
make modal-deploy
prefect deployment run gov_arxiv_ocr_resources/gov_arxiv_ocr_resources \
  --param provider=modal
```

Both adapters commit the archive before the final manifest marker. The Kaggle script then exits
normally and releases its session without notebook JavaScript; Modal retains only a bounded warm
container. A four-hour execution timeout is the remote guardrail. Failed documents cannot leak
partial files into the archive, and provider retries converge on the deterministic `batch_id`.

OCR identities have separate grains: `request_id` identifies one paper/source-mutation/processor
request and remains unchanged across retries; `batch_id` includes attempt ordinals, so a new retry
never overwrites prior attempt history; `processing_id` identifies one paper/PDF-content/processor
result. The content manifest excludes request identity, allowing a new metadata mutation with an
unchanged PDF to reuse validated artifacts without an ID collision.

The deployment and its 20-minute schedule are registered by `prefect-deploy`, but the schedule is
paused by default because GPU time is quota/cost-bound. After configuring one provider,
provisioning its resources, and validating one run, resume it from Prefect UI or:

```bash
PREFECT_API_URL=http://localhost:4200/api \
  uv run prefect deployment schedule resume etl_arxiv_ocr/etl_arxiv_ocr --all
```

Manual runs can restrict work without introducing a backfill DAG:

```bash
prefect deployment run etl_arxiv_metadata/etl_arxiv_metadata \
  --param datestamp_date=2026-07-22
prefect deployment run etl_arxiv_metadata/etl_arxiv_metadata \
  --param datestamp_date=2026-07-22 --param refresh=true
prefect deployment run etl_arxiv_ocr/etl_arxiv_ocr \
  --param arxiv_ids='["2607.00001"]'
prefect deployment run etl_arxiv_ocr/etl_arxiv_ocr \
  --param arxiv_ids='["2607.00001"]' --param verify_pdf=true
prefect deployment run etl_arxiv_ocr/etl_arxiv_ocr \
  --param arxiv_ids='["2607.00001"]' --param provider=modal
```

The normal metadata retry reuses a complete landing checkpoint and preserves its Iceberg snapshot
IDs. Use `refresh=true` only for an intentional re-harvest of a closed ArXiv datestamp; that path
replaces the exact daily raw archive and its identity-partitioned landing rows.
`verify_pdf=true` is intentionally valid only with explicit IDs: it checks for a source PDF change
even when OAI metadata has not changed, while avoiding an unbounded polling scan of ArXiv.

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
their full content against the YAML contract, and invokes bounded Trino maintenance procedures.
Polaris selects the applicable policy; YAML remains the only source of execution values. The flow
has no table allowlist or consumer-owned table registry.

## Local operations

```bash
make up
make prefect-deploy
make prefect-deployments

curl --fail http://localhost:8182/q/health/ready
curl --fail http://localhost:8080/v1/info
curl --fail http://localhost:4200/api/health
uv run dbt debug --project-dir dbt/analytics --profiles-dir dbt/analytics
```

The Compose overlay idempotently bootstraps the local process work pool and deployments. Removing
the local stack and named volumes is destructive:

```bash
make clean
```

Never use that cleanup command against shared or cloud storage.
