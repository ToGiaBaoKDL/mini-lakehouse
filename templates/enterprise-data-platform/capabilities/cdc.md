# Change Data Capture

Use CDC when consumers need source changes or deletes faster or more efficiently than bounded
snapshots provide. CDC records infrastructure changes; they are not automatically business domain
events.

## Contract

- Preserve source key, operation, transaction/order position, source event time, capture time, and
  schema version in immutable evidence.
- Define snapshot bootstrap, checkpoint/watermark, ordering scope, update image, delete, and
  truncation semantics before ingestion.
- Apply changes idempotently using a stable source position; delivery may be repeated.
- Keep a deterministic path from snapshot plus changes to current state.
- Detect gaps, position regression, and schema incompatibility before advancing the certified
  checkpoint.
- Protect the OLTP source with bounded reads, supported log/replication APIs, and measured load.

## Required proof

- insert, update, delete, duplicate, out-of-order, restart, and schema-change fixtures;
- snapshot-to-log handoff without gaps or double application;
- source/current-state reconciliation at a recorded watermark;
- checkpoint recovery and bounded backlog catch-up;
- documented resnapshot procedure when continuity cannot be certified.

Do not invent database replication protocols when an official connector or managed CDC service
satisfies the contract.

