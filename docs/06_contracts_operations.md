# Contracts and platform operations

## Ownership

| Boundary | Source of truth | Owner | Runtime responsibility |
|---|---|---|---|
| Catalog roots | `contracts/platform.yaml` | Data Platform | Polaris SDK bootstrap |
| Catalog access | `contracts/access.yaml` | Data Platform | Polaris SDK bootstrap |
| Curated/analytics namespaces | Product and domain contracts | Declared owner | PyIceberg bootstrap |
| Landing tables | `contracts/sources/*.yaml` | Declared source owner | PyIceberg bootstrap; source repository writes |
| Curated tables | `contracts/curated/*.yaml` | Declared product owner | PyIceberg bootstrap; curation repository merges |
| Analytics models | Domain contracts + dbt metadata/tests | Declared domain owner | dbt and Trino |
| Maintenance policy | `contracts/maintenance.yaml` | Data Platform | Polaris policy API + governance flow |

YAML is the source of truth for stable, non-secret definitions. Endpoints, credentials, and
environment identity remain runtime configuration.

```bash
make validate
```

This local validation loads the complete Pydantic registry, validates cross-contract references,
and checks runtime catalog consistency without contacting the data plane.

## Platform lifecycle

The lifecycle intentionally has no generic desired-state planner:

1. `polaris-bootstrap` initializes the Polaris realm and root administration principal.
2. `object-store-provision` creates missing lifecycle buckets and applies workload-scoped AIStor
   users and policies through the official `mc` client.
3. `platform-bootstrap` creates service principals and their RBAC graph, then compiles the
   remaining contracts directly into official Polaris SDK and PyIceberg calls.
4. `platform-validate` reads live state and fails when managed resources drift.
5. Explicit migrations handle incompatible schema, partition, location, format, or policy-type
   changes.

```bash
make platform-bootstrap
make platform-validate
```

Bootstrap is safe to rerun. It may:

- create missing service principals, roles, grants, namespaces, landing/curated tables, policies,
  and mappings;
- initialize the configured credential only for a newly created service principal;
- update mutable catalog, namespace, policy, and managed Iceberg properties;
- add identifier fields to an existing curated table when its declared primary key is already
  represented by required Iceberg columns;

Bootstrap never drops a catalog, namespace, table, object, policy, or team-owned resource. It
fails on incompatible table structure or immutable policy type instead of hiding a migration.
Polaris version conflicts fail safely; rerun the serialized bootstrap after reviewing concurrent
platform activity.
Repositories and dbt do not create platform-owned landing/curated tables.

Live validation is read-only. It checks:

- catalog properties and external/internal S3 endpoints;
- service principal client IDs, role assignments, and scoped grants;
- namespace locations and ownership properties;
- Iceberg locations, format version, field IDs, types, requiredness, identifier fields,
  partition specs, and managed retention properties;
- Polaris policy content and applicable mappings.

Changing a Polaris service secret does not rotate the live principal implicitly. After updating
the secret manager or local `.env`, rotate the selected identity explicitly before restarting its
consumer:

```bash
make platform-rotate-credentials IDENTITIES="prefect_ingestion"
```

Omit `IDENTITIES` only for a deliberate rotation of every contract identity.

## Explicit migrations and pruning

Schema evolution and destructive changes must be implemented as named, reviewed migrations. A
migration should validate its exact precondition, perform one bounded change through the relevant
SDK, and validate the resulting contract before workloads resume.

Polaris policy removal remains a separate reviewed operation:

```bash
make policy-prune-plan
make policy-prune-apply PLAN_SHA256=<reviewed-plan-sha256>
```

Only absent policies using the reserved `mlh-` prefix are eligible. Never use that prefix for
manually managed policies.

Changing the target of an existing policy is an explicit mapping migration: bootstrap the new
direct attachment, validate it, then detach the old target through a reviewed SDK migration.
`policy-prune-*` removes only policies absent from the contract; it must not guess which attachment
was intentionally retained.

## Rollback

- Contract-only failure: revert the YAML/code change and validate it.
- Mutable catalog or policy failure: restore the previous contract and rerun bootstrap.
- Structural Iceberg failure: run the explicit reverse migration; do not delete the table.
- Accidental namespace creation: inventory tables and objects before any manual removal.
- Ingestion retry: rerun the same source checkpoint; object publication and table writes remain
  idempotent.
