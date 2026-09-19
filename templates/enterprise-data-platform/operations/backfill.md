# Backfill

Use this runbook to create or correct a bounded historical interval. A backfill is a controlled
publication change, not a manual retry.

## Prepare

1. Record product, exact half-open interval, partition unit, reason, artifact and contract versions,
   owner, expected source volume, consumers, and approval.
2. Prove the transformation is idempotent and uses logical time.
3. Select an immutable input snapshot or manifests; do not let the source set change during the
   run without creating a new plan.
4. Decide conflict behavior with scheduled writes and isolate compute, quotas, and query results.
5. Capture the current output snapshot/version and rollback procedure.

## Execute

1. Run one representative partition into an isolated target.
2. Validate keys, row counts, control totals, nulls, distributions, and consumer-critical metrics.
3. Publish partitions in bounded batches with pause/resume checkpoints.
4. Monitor failures, resource saturation, production SLOs, and cost after every batch.
5. Stop on a blocking quality failure, unplanned schema change, source drift, production impact, or
   loss of deterministic input identity.

## Complete

Reconcile the full interval, emit publication records, verify downstream consumers, resume normal
scheduling, and retain the plan, approvals, input/output versions, quality results, cost, and
rollback expiry. Do not report success from job status alone.

