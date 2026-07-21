# Contracts, ownership, and policy operations

## Ownership matrix

| Boundary | Source of truth | Owner | Runtime responsibility |
|---|---|---|---|
| Catalog, lifecycle namespaces, and RBAC | `contracts/catalog.yaml` | Data Platform | Isolated Polaris reconcilers |
| Source landing data | `contracts/sources/*.yaml` + source package | Declared source owner | Source service and repository |
| Curated GitHub models | dbt `marts/github` | Data Platform | dbt/Trino |
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
- Refuse catalog drift that cannot be updated safely in place.

It never drops a catalog, namespace, table, policy, object, or mapping. Catalog creation handles a
concurrent creator by reading and validating the winning state. Catalog and policy updates use
Polaris version checks and retry one concurrent update after re-reading server state.

Run the bootstrap through the core Compose module:

```bash
docker compose -f compose.core.yaml run --rm lakehouse-bootstrap
```

The local `catalog_admin` role is explicitly granted `CATALOG_MANAGE_CONTENT`, and the catalog
feature flag `polaris.config.drop-with-purge.enabled` is explicit because dbt full-table rebuilds
drop and recreate Iceberg relations. These settings belong to the catalog contract rather than a
container startup script.

Running bootstrap again with unchanged contracts must produce no catalog, role-grant, namespace,
or policy-content mutation. Mapping `PUT` requests remain safe and idempotent by Polaris API
contract.

## Removing a policy attachment

Polaris currently exposes attach/detach operations but no endpoint that lists all direct mappings
for one policy. Therefore removing `targets` from YAML is not treated as proof that an existing
mapping should be deleted.

Use a reviewed migration in this order:

1. Record the policy namespace/name and exact target from the current contract.
2. Send the Polaris detach request (`POST` to the policy `/mappings` endpoint) with that exact
   target and verify a `204` response.
3. Query `applicable-policies` on the affected resource and confirm the effective policy is the
   intended inherited/overridden policy.
4. Remove the target from YAML and run `lakehouse validate`.
5. Apply bootstrap twice and verify the second run is unchanged.

Do not make normal bootstrap infer destructive detach operations by scanning inherited policy
results; inheritance does not provide a complete direct-mapping inventory.

## Rollback

- Contract-only failure: revert the reviewed YAML/code commit and validate before applying again.
- Policy content failure: restore the previous YAML content; version-aware reconciliation updates
  the existing policy.
- New namespace created accidentally: stop and inventory tables/objects before any manual delete.
- Ingestion retry: rerun the same source hour. Conditional object create and partition overwrite
  make this safe without a cleanup script.

Never use catalog/table deletion as an automated rollback mechanism.
