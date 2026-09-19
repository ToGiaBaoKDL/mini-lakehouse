# Delivery Lifecycle

The lifecycle is a protocol for producing evidence, not a required choice of ticket system,
orchestrator, or deployment tool.

```text
proposed -> specified -> implemented -> validated -> published -> operated -> deprecated
```

## 1. Proposed

State the consumer decision, required freshness, source authority, expected scale, classification,
and why an existing product cannot satisfy the need. Reject projects whose only requirement is a
preferred tool.

## 2. Specified

Approve the product contract, owner, writer, consumers, lineage, SLO, compatibility, retention,
recovery, and cost boundary. Resolve domain language and grain before choosing physical layout.

## 3. Implemented

Build the smallest end-to-end path using supported SDKs and platform primitives. Scheduled,
retry, and backfill execution use one deterministic entrypoint. Infrastructure, catalog,
transformation, schedule, and semantic objects each have one reconciler.

## 4. Validated

CI and an isolated environment prove:

- contract and schema compatibility;
- primary-key and grain behavior;
- idempotent reruns;
- source/output reconciliation and declared quality gates;
- least-privilege allows and forbidden cross-boundary writes;
- bounded backfill, rollback, and recovery;
- performance and cost at representative volume.

Validation produces durable evidence tied to the proposed artifact revision.

## 5. Published

Promote the same immutable artifact. Validate inputs before mutation, write atomically or through a
staged commit, run blocking gates, and emit a publication record. Only `certified` or `published`
outputs are consumer-visible. A successful compute job with failed or missing evidence is not a
successful publication.

## 6. Operated

Measure product SLOs from logical data time, not job start time. Route service telemetry to the
runtime owner and data freshness/quality alerts to the product owner. Review cost, access,
consumers, recovery evidence, and noisy or ineffective alerts on a fixed cadence.

## 7. Deprecated

Inventory consumers, announce the compatibility window, provide a replacement, observe remaining
usage, disable writes, then remove compatibility objects on the recorded date. Retain publication
and audit evidence according to policy.

## Change classes

| Change | Minimum path |
| --- | --- |
| Documentation or non-behavioral metadata | specified -> validated -> published |
| Backward-compatible schema or logic | specified -> implemented -> validated -> published |
| Breaking schema, grain, key, or semantic definition | new contract/version plus cutover runbook |
| Historical correction | validated backfill plan plus consumer impact approval |
| Runtime/platform replacement | parallel reconciliation plus cutover and rollback evidence |

The machine-readable requirements for these stages are in `readiness-gates.yaml`.

