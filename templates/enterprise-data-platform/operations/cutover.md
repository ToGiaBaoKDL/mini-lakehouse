# Platform or Pipeline Cutover

Use this runbook when replacing a writer, engine, catalog path, or serving implementation without
changing the product contract.

## Prepare

1. Freeze the contract and define old/new writer authority, cutover interval, rollback deadline,
   and maximum allowed divergence.
2. Build the new path from identical immutable inputs into an isolated target.
3. Compare schema, keys, counts, control totals, nulls, distributions, and consumer metrics across
   representative intervals, including empty and late-data cases.
4. Load-test concurrency, cost, permissions, observability, maintenance, and recovery.

## Cut over

1. Prevent overlapping writes through identity and catalog controls, not operator convention.
2. Record the final old output and source watermark.
3. Activate the new writer at the agreed logical boundary.
4. Reconcile the first publication before switching consumers or aliases.
5. Monitor product and service SLOs through the rollback window.

Rollback selects the last certified implementation and product version; it never repairs table
files manually. After the window, remove old write permissions and schedules, retain audit evidence,
and assign a dated removal action to every compatibility object.

