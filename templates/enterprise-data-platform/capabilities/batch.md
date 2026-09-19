# Batch Processing

Use batch processing when bounded intervals can meet the consumer freshness objective. Prefer it
over continuous infrastructure until measured latency requires otherwise.

## Contract

- Input is selected by an explicit logical interval, manifest, or immutable table snapshot.
- Retry and backfill call the same transformation with the same semantics.
- Writes replace or merge an owned interval/key set atomically; blind append is not idempotency.
- The job records input versions, output version, artifact revision, counts, quality, and duration.
- Scheduled and backfill capacity have separate concurrency limits.
- Empty input, partial input, and late input have explicit outcomes.

## Required proof

- rerunning the same interval produces the same keys and values;
- one-partition dry-run reconciles before a multi-partition backfill;
- duplicate source delivery does not duplicate the product;
- a mid-write failure leaves either the previous certified version or a recoverable staged write;
- representative volume completes within the SLO and cost budget.

Avoid monolithic jobs whose retry repeats hours of unrelated work. Split at independently
recoverable data boundaries, not at every SQL statement or function.

