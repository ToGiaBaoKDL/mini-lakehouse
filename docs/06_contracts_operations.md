# Contracts, ownership, and policy operations

## Ownership matrix

| Boundary | Source of truth | Owner | Runtime responsibility |
|---|---|---|---|
| Catalog, lifecycle namespaces, and RBAC | `contracts/catalog.yaml` | Data Platform | Isolated Polaris reconcilers |
| Source landing data | `contracts/sources/*.yaml` + source package | Declared source owner | Source service and repository |
| Curated GitHub product | `contracts/products/github.yaml` + product package | Data Platform | Trino curation service |
| Analytics domain models | `contracts/domains/*.yaml` + dbt group | Declared domain owner | dbt/Trino |
| Maintenance policy | `contracts/policies/*.yaml` | Declared policy owner | Polaris metadata + governance flow |
| Dashboard business queries | Domain relation registry | Engineering Analytics | Read-only Streamlit service |
| Dashboard operational metadata | Iceberg catalog discovery | Data Platform | Read-only Streamlit service |
| Deployment lifecycle | Compose and `prefect.yaml` | Platform operators | Docker Compose and Prefect |

Validate the complete non-secret registry and its runtime catalog settings before every apply:

```bash
uv run lakehouse validate
```

## Safe bootstrap semantics

The platform bootstrap composes separate catalog, catalog-access, namespace, and policy
reconcilers. It performs only these normal operations:

- Create a missing catalog, namespace, or policy and grant a declared missing catalog privilege.
- Update mutable catalog properties/storage configuration with Polaris `entityVersion` optimistic
  concurrency; reject immutable catalog name/type drift.
- Update drifted namespace properties or policy content.
- Idempotently attach every desired policy mapping.
- Delete stale repository-managed `mlh-` policies with `detach-all` after producing a prune plan.
- Refuse catalog drift that cannot be updated safely in place.

It never drops a catalog, namespace, table, object, or team-owned policy. Catalog creation handles
a concurrent creator by reading and validating the winning state. Catalog and policy updates use
Polaris version checks and retry one concurrent update after re-reading server state.

Run the bootstrap through the core Compose module:

```bash
docker compose -f compose.core.yaml run --rm lakehouse-bootstrap
```

The local `catalog_admin` role is explicitly granted `CATALOG_MANAGE_CONTENT`, and the catalog
feature flag `polaris.config.drop-with-purge.enabled` is explicit for reviewed table retirement
and local rebuild migrations. Normal dbt models use atomic table replacement. These settings
belong to the catalog contract rather than a container startup script.

Running bootstrap again with unchanged contracts must produce no catalog, role-grant, namespace,
or policy-content mutation. Mapping `PUT` requests remain safe and idempotent by Polaris API
contract.

## Changing policy targets

Polaris exposes attach/detach operations but not a complete direct-mapping inventory for one
policy. Its current server may also report a missing mapping as HTTP 500. Bootstrap therefore does
not issue speculative detach calls across every table.

Use replacement semantics instead:

1. Give the changed target set a new descriptive `mlh-<tier>-...` policy name.
2. Keep only the new policy contract and run `lakehouse validate`.
3. Review the prune plan: only absent policies with the reserved `mlh-` prefix are eligible.
4. Apply bootstrap. It deletes stale managed policies with `detach-all`, then creates/attaches the
   replacement; plan a brief policy transition window for this reviewed operation.
5. Query `applicable-policies` on affected tables and apply bootstrap again to verify a no-op.

Never use the `mlh-` prefix for manually/team-managed policies.

## Rollback

- Contract-only failure: revert the reviewed YAML/code commit and validate before applying again.
- Policy content failure: restore the previous YAML content; version-aware reconciliation updates
  the existing policy.
- New namespace created accidentally: stop and inventory tables/objects before any manual delete.
- Ingestion retry: rerun the same source hour. Conditional object create and partition overwrite
  make this safe without a cleanup script.

Never use catalog/table deletion as an automated rollback mechanism.
