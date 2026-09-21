# Enterprise Data Platform Template

This directory is a vendor-neutral implementation standard for building a modern enterprise data
platform. It defines the stable contracts, delivery gates, capability boundaries, and operational
procedures that should survive changes in cloud, catalog, table format, compute engine, or
orchestrator.

It is deliberately not a universal ETL framework. Adopt existing platform SDKs and deployment
tools, map them to these contracts, and keep business logic with the owning data product.

## What is authoritative

| Path | Purpose |
| --- | --- |
| `architecture.md` | Platform invariants, planes, layers, ownership, and deployment boundary |
| `contracts/` | Machine-validatable product, publication, and publication-set interfaces |
| `delivery/` | Product lifecycle, curation, publication, SLOs, and readiness evidence |
| `capabilities/` | Optional workload contracts selected by a measured requirement |
| `operations/` | Reusable procedures for high-risk production operations |

An adopting repository owns the concrete infrastructure, jobs, SQL, orchestration, and
observability configuration. Those implementations must satisfy this template; they do not become
part of its portable contracts.

## Adoption sequence

1. Inventory sources, datasets, consumers, owners, sensitivity, freshness, recovery needs, and
   current failure modes.
2. Record platform decisions and map logical storage, catalog, identity, compute, orchestration,
   and observability boundaries to one concrete implementation.
3. Create one data-product contract per consumer-visible dataset from
   `contracts/examples/customer-transactions.yaml`.
4. Implement one thin source-to-product path following `delivery/curation.md` and
   `delivery/publication.md`. If several datasets need one consistent cut, publish their certified
   versions through a publication set rather than assuming a cross-table transaction.
5. Prove idempotency, reconciliation, least privilege, rollback, and recovery before adding more
   sources or infrastructure.
6. Enable only the capability contracts required by actual latency, scale, compliance, sharing,
   or machine-learning needs.

## Extension rule

Do not introduce platform profiles or adapters until a second implementation exists. When that
happens, keep this directory stable and place vendor mappings beside the adopting implementation.
The portable contract describes required behavior; the mapping describes how a platform provides
it.

## Validation

The contracts use JSON Schema Draft 2020-12. The reference validator adds semantic checks that JSON
Schema cannot express cleanly, including key/field references, classification strength, threshold
ordering, SLO gate references, interval chronology, and whether every blocking gate passed. A
publication is validated together with its exact product contract. `measure.py` calculates the
declared SLOs from versioned eligible intervals and consumer-visible publication evidence.

```bash
uv run python templates/enterprise-data-platform/validate.py data-product \
  templates/enterprise-data-platform/contracts/examples/customer-transactions.yaml
uv run python templates/enterprise-data-platform/validate.py publication \
  --product-contract templates/enterprise-data-platform/contracts/examples/customer-transactions.yaml \
  templates/enterprise-data-platform/contracts/examples/customer-transactions-publication.yaml
uv run python templates/enterprise-data-platform/validate.py publication-set \
  --product-contract templates/enterprise-data-platform/contracts/examples/customer-transactions.yaml \
  --product-contract templates/enterprise-data-platform/contracts/examples/customer-transaction-daily.yaml \
  --member-publication templates/enterprise-data-platform/contracts/examples/customer-transactions-certified.yaml \
  --member-publication templates/enterprise-data-platform/contracts/examples/customer-transaction-daily-certified.yaml \
  templates/enterprise-data-platform/contracts/examples/customer-transactions-set.yaml
uv run python templates/enterprise-data-platform/measure.py \
  --product customer_transactions \
  --product-contract templates/enterprise-data-platform/contracts/examples/customer-transactions.yaml \
  --eligible-intervals templates/enterprise-data-platform/contracts/examples/posted-transactions-intervals.yaml \
  --publication templates/enterprise-data-platform/contracts/examples/customer-transactions-publication.yaml
```

An adopting platform may replace this CLI with another Draft 2020-12 implementation, but it must
retain the semantic checks and negative tests.

## Definition of adopted

The template is adopted only when:

- at least one production data product passes every required delivery gate;
- contracts and examples are validated in CI;
- production uses short-lived workload identities and one writer per published dataset;
- reruns and bounded backfills are deterministic;
- publication evidence identifies exact inputs, output version, code artifact, and quality result;
- publication gates are reconciled with the product contract and SLOs are measured from consumer
  visibility over a versioned eligible-interval schedule;
- an operator has successfully exercised rollback and recovery procedures;
- ownership, SLO, lineage, classification, cost, and consumer impact can be answered without
  reading pipeline implementation code.
