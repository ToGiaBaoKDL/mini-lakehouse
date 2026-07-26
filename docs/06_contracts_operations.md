# Contracts, ownership, and policy operations

## Ownership matrix

| Boundary | Source of truth | Owner | Runtime responsibility |
|---|---|---|---|
| Catalog roots and RBAC | `contracts/catalog.yaml` | Data Platform | Polaris reconciler |
| Curated/analytics namespaces | Product and domain contracts | Declared owner | Polaris reconciler |
| Source landing data | `contracts/sources/*.yaml` + source package | Declared source owner | Source service and repository |
| Curated GitHub product | `contracts/curated_products/github.yaml` + curated product package | Data Platform | Trino curation service |
| Analytics domain models | `contracts/domains/*.yaml` + dbt metadata/tests | Declared domain owner | dbt/Trino |
| Maintenance policy | `contracts/policies/*.yaml` | Declared policy owner | Polaris metadata + governance flow |
| Deployment lifecycle | Compose and `prefect.yaml` | Platform operators | Docker Compose and Prefect |

Validate the complete non-secret registry and its runtime catalog settings before every apply:

```bash
make validate
```

## Platform lifecycle

Each lifecycle has one narrow responsibility:

- `polaris-bootstrap` initializes the realm and root principal.
- `object-store-provision` creates missing buckets derived from lifecycle URIs.
- `platform-reconcile` creates or updates catalog, namespace, access, policy content and desired
  mappings.
- `policy-prune-plan` is read-only; `policy-prune-apply` performs only deletion of the exact
  reviewed stale-policy plan identified by its SHA-256.

The normal reconciler may:

- Create a missing catalog, namespace, or policy and grant a declared missing catalog privilege.
- Update mutable catalog properties/storage configuration with Polaris `entityVersion` optimistic
  concurrency, including removal of properties deleted from the reviewed catalog contract; reject
  immutable catalog name/type drift.
- Update drifted namespace properties or policy content.
- Idempotently attach every desired policy mapping.
- Refuse catalog drift that cannot be updated safely in place.

It never drops a catalog, namespace, table, object, policy, or team-owned resource. Catalog creation handles
a concurrent creator by reading and validating the winning state. Catalog and policy updates use
Polaris version checks and retry one concurrent update after re-reading server state.

Run reconciliation through the core Compose module:

```bash
make platform-reconcile
```

The local `catalog_admin` role is explicitly granted `CATALOG_MANAGE_CONTENT`. Namespace
locations are the physical source of truth; table creators do not provide another path,
and Trino is configured to derive stable table directories without UUID suffixes. Normal dbt models
use table replacement. These settings belong to reviewed platform configuration rather than a
container startup script.

Running reconciliation again with unchanged contracts must produce no catalog, role-grant, namespace,
or policy-content mutation. Mapping `PUT` requests remain safe and idempotent by Polaris API
contract.

## Changing policy targets

Polaris exposes attach/detach operations but not a complete direct-mapping inventory for one
policy. Its current server may also report a missing mapping as HTTP 500. Reconciliation therefore
does not issue speculative detach calls across every table.

Use replacement semantics instead:

1. Give the changed target set a new descriptive `mlh-<tier>-...` policy name.
2. Keep only the new policy contract and run `make validate`.
3. Run `make platform-reconcile` so the replacement exists and is attached first.
4. Run `make policy-prune-plan`; only absent policies with the reserved `mlh-` prefix are eligible.
5. Review the output, then run
   `make policy-prune-apply PLAN_SHA256=<reviewed-plan-sha256>`. Apply aborts if current state
   produces a different plan.
6. Query `applicable-policies` on affected tables and reconcile again to verify a no-op.

Never use the `mlh-` prefix for manually/team-managed policies.

## Rollback

- Contract-only failure: revert the reviewed YAML/code commit and validate before applying again.
- Policy content failure: restore the previous YAML content; version-aware reconciliation updates
  the existing policy.
- New namespace created accidentally: stop and inventory tables/objects before any manual delete.
- Ingestion retry: rerun the same source hour. Conditional object create and partition overwrite
  make this safe without a cleanup script.

Never use catalog/table deletion as an automated rollback mechanism.
