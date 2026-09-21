# Architecture

## Outcome

Build a data platform whose durable products remain understandable, recoverable, and governable
when individual engines or vendors change.

```text
sources
  -> immutable capture evidence
  -> source-shaped records
  -> canonical domain facts
  -> governed data products
  -> replaceable query, BI, application, sharing, and ML consumers
```

The common Raw/Bronze/Silver/Gold names are useful shorthand, but the contracts below are the real
boundaries:

| Boundary | Required meaning |
| --- | --- |
| Raw | Immutable source evidence with manifest, checksum, source/capture time, scope, and replay position |
| Bronze | Queryable source semantics with complete Raw lineage and only physical normalization |
| Silver | Stable domain facts with deterministic keys, deduplication, delete semantics, classification, and event/availability time |
| Gold | Consumer-oriented products with governed business definitions and point-in-time correctness where required |
| Serving | Replaceable optimization over named Gold products; never the only owner of a business fact |

Small systems may combine physical storage or compute steps. They must not combine ownership,
quality, or publication semantics.

The required behavior at the Bronze-to-Silver boundary is defined in `delivery/curation.md`.
Security controls, deterministic CDC state, type and time normalization, nested-data modeling,
domain enrichment, and table-format publication are selected by source and product requirements;
none is an unconditional transformation applied to every dataset.

One contract describes one consumer-visible dataset, not an arbitrary bundle of physical tables.
The publication and SLO protocols are defined in `delivery/publication.md` and `delivery/slo.md`.
Several datasets may share one consistent visibility point through a publication set, but a
table-format commit alone does not provide that visibility guarantee.

## Platform planes

```text
management plane     contracts, source control, CI/CD, policy, catalog intent
control plane        orchestration, scheduling, dependency and backfill coordination
execution plane      batch, SQL, stream, ML, and maintenance compute
durable data plane   object storage or warehouse tables, catalog, snapshots, manifests
serving plane        interactive SQL, BI, APIs, search, reverse ETL, feature access
operations plane     telemetry, data SLOs, audit, lineage, cost, incident and recovery evidence
```

Each plane has a distinct owner and identity. The orchestrator may start work but does not own
business data or transformation logic. Observability reports health but does not certify data.
Serving can be removed and rebuilt from the durable data plane.

## Non-negotiable invariants

1. Every published dataset has one accountable owner, one writer identity, declared grain and key,
   upstream lineage, classification, SLO, compatibility, retention, and recovery policy.
2. Immutable evidence or an explicitly documented authoritative source can rebuild every durable
   derived product.
3. Scheduled, retry, and backfill execution call the same deterministic transformation entrypoint
   with a logical interval; wall-clock time never selects business input implicitly.
4. No consumer depends on a physical object path, temporary workspace, or another team's private
   schema.
5. Schema changes are classified before deployment. Breaking changes require a new contract or an
   owned compatibility window with a removal date.
6. Publication is fail-closed: compute success is not product certification.
7. Human, CI, orchestration, writer, BI, and break-glass identities are separate and least
   privileged.
8. Infrastructure, catalog objects, transformations, schedules, and semantic content each have one
   reconciler. Two tools never manage the same object.
9. Dev, staging, and production promote the same immutable artifact; environments differ through
   reviewed configuration and identities.
10. New infrastructure requires a measured latency, scale, reliability, compliance, governance,
    or cost objective.

## Domain and product topology

- Capture and Bronze follow source ownership.
- Silver follows stable business domains and shared language, not the organization chart.
- Gold follows governed products or consumer domains.
- Team workspaces are non-canonical, quota-bound, and expire unless promoted through review.
- Cross-domain use occurs through published products, not shared write access.

Use strategic domain boundaries only where language, ownership, security, or change cadence truly
differ. A small team should prefer clear modules over artificial services and duplicated datasets.

## Logical deployment contract

Every implementation must provide:

| Capability | Minimum production behavior |
| --- | --- |
| Identity | Short-lived workload credentials, least privilege, audited break-glass access |
| Storage | Encryption, lifecycle, version/recovery policy, immutable capture boundary |
| Catalog | One authoritative namespace and schema/compatibility control |
| Compute | Isolated scheduled and backfill capacity with bounded concurrency |
| Orchestration | Logical intervals, data dependencies, retries, catch-up controls, and ownership |
| Transformation | One writer, idempotent outputs, deterministic code/artifact version |
| Publication | Contract validation, reconciliation, snapshot/version evidence, fail-closed status |
| Observability | Service telemetry plus separate data freshness, quality, lineage, and cost signals |
| Delivery | Reviewed IaC/code, immutable promotion, verification, rollback, and audit trail |
| Recovery | Tested restore or replay procedure meeting declared RPO/RTO |

The adopting repository records the concrete mapping as architecture decisions. Do not put cloud
resource names, credentials, retry counts, schedules, SQL, or implementation symbols in portable
data contracts.

## Storage and namespace rules

- Separate storage only for different access, encryption, retention, replication, or blast-radius
  requirements; layers alone do not require separate buckets or accounts.
- Partition for dominant pruning and maintenance behavior, not directory aesthetics.
- Capture identifiers and manifests preserve retries without overwriting evidence.
- Managed table files belong to the table format/catalog implementation; applications never edit
  them directly.
- One namespace maps to one ownership and write boundary. Read grants may span products; write
  grants do not.
- Workspace and query-result storage have short independent lifecycle policies.

## Delivery and operating model

Platform engineering owns paved roads, shared controls, identity, catalog foundations, and
reliability standards. Domain teams own definitions, transformations, tests, SLOs, consumers, and
on-call response for their products. Governance defines classification and policy; it does not
become a ticket queue for ordinary product delivery.

The evidence required to move from idea to production is defined in `delivery/`. Optional runtime
behavior is defined in `capabilities/`. High-risk operations follow `operations/` rather than
ad-hoc commands.
