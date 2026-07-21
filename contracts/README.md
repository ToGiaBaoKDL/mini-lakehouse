# Declarative contracts

This directory is the reviewed, non-secret desired state for the lakehouse. Python loads every
file with strict Pydantic models before making a network or storage call.

## Ownership

- `catalog.yaml`: catalog owner, lifecycle roots, namespaces, technical owners, catalog feature
  flags, and required catalog-role grants.
- `sources/*.yaml`: source boundary, shared landing namespace, immutable transport/object prefix,
  checkpoint key, table location, partition transform, and schema-contract reference.
- `products/*.yaml`: canonical curated product owner, upstream sources, keys, partitions, and
  physical locations.
- `domains/*.yaml`: analytics-domain owner, upstream curated products, and public relation registry.
- `policies/*.yaml`: one Polaris policy per file, including typed content and attachments.

Runtime endpoints and credentials do not belong here. They remain in environment variables or a
secret manager. dbt SQL, model tests, groups, access, and exposures remain in native dbt files.

## Validation

```bash
uv run lakehouse validate
```

Validation rejects unknown fields, duplicate YAML keys, missing namespace references, duplicate
policy attachments, unsafe object prefixes, and checkpoint-overwrite tables that do not partition
by their checkpoint field.

Polaris allows engine-specific keys in policy `config`, but this project intentionally accepts
only fields that its Trino maintenance runner can enforce. Unsupported fields fail validation
instead of being silently ignored.

Bootstrap safely creates/updates policy bodies and desired mappings. Removing a mapping is treated
as a destructive migration: the Polaris API does not expose a direct mapping-list endpoint, so a
reviewed detach operation must accompany the contract change instead of guessing and deleting
attachments during normal bootstrap.

## Adding a source and product

1. Add `sources/<source>.yaml`; publish source-prefixed table names in the shared `landing`
   namespace and retain transport/source hierarchy only in object prefixes.
2. Add a source-owned package with client, parser, schema, repository, and service as needed.
3. Give every Iceberg field a permanent ID and lock it with a schema test.
4. Choose a natural checkpoint/idempotency key and an atomic table commit strategy.
5. Add `products/<product>.yaml` and a source-conformance service under `products/<product>/`.
6. Add convention-named EL and TL DAGs that co-locate their Prefect tasks; prefer explicit
   schedules over cross-deployment event sensors when a fixed source SLA exists.
7. Declare the curated product as a dbt source; keep dbt staging/intermediate models ephemeral.
8. Reference the product from each consuming analytics domain contract.
9. Attach existing tier policies; do not copy policy bodies.
10. Add cross-layer, idempotent rerun, and failure-recovery tests before enabling a schedule.
