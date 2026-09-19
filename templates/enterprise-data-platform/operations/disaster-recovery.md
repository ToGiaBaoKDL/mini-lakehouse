# Disaster Recovery

Disaster recovery proves that the platform can meet each product's declared RPO and RTO after loss
of a region, account, catalog, storage boundary, runtime, or metadata service.

## Classify state

| State | Default recovery source |
| --- | --- |
| Raw evidence | Replicated/versioned immutable objects or authoritative upstream |
| Managed analytical tables | Table snapshots plus protected object storage/catalog metadata |
| Derived products | Deterministic replay or recomputation from certified inputs |
| Platform configuration | Source control and reviewed infrastructure/deployment state |
| Stateful control services | Independent encrypted backups with restore verification |
| Cache, query results, local workspace | Rebuild or discard |

## Drill

1. Select a declared failure scenario and record target RPO/RTO.
2. Assume the failed boundary is unavailable; do not use undocumented access to make the drill pass.
3. Recreate identity, network, storage/catalog access, compute, orchestration, and observability in
   dependency order.
4. Restore or replay one representative critical product and verify publication evidence,
   reconciliation, permissions, and consumers.
5. Measure actual data loss window, recovery duration, manual steps, cost, and failed assumptions.
6. Restore normal ownership, remove temporary privilege, and record remediation owners/dates.

Backups without successful restore evidence do not satisfy recovery readiness. Run drills at the
cadence required by the highest criticality products on the shared boundary.

