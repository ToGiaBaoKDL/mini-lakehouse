# Streaming

Adopt continuous stream processing only when the value of data decays faster than a bounded batch
or micro-batch can meet. A dashboard refreshing hourly is not a streaming requirement.

## Contract

- Define delivery, ordering, partition-key, event-time, watermark, late-data, replay, and retention
  semantics.
- Use a durable event log when multiple live consumers or restart recovery require it.
- Checkpoint processing state and make every sink idempotent or transactional.
- Quarantine malformed or unprocessable events with original payload, reason, schema, position,
  and artifact version; never silently drop them.
- Bound state, joins, retries, backlog growth, and hot partitions.
- Live and replay paths use the same versioned business formulas.
- Preserve immutable capture evidence when regulatory audit or deterministic reconstruction needs
  more history than the event log retains.

## Required proof

- restart from checkpoint without loss or duplicate business effects;
- duplicate and out-of-order delivery;
- watermark and late-event behavior at window boundaries;
- poison event and dead-letter recovery;
- broker/source outage, sink outage, resubscription, and backlog catch-up;
- replay/live parity for selected snapshots and decisions.

Operational alerts cover consumer lag, checkpoint age, dead-letter growth, watermark delay, state
size, restart loops, sink errors, and data freshness.

