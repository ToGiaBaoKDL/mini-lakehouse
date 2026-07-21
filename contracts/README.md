# Declarative contracts

This directory is the reviewed, non-secret desired state for the lakehouse. Python loads every
file with strict Pydantic models before making a network or storage call.

## Ownership

- `catalog.yaml`: catalog owner, lifecycle roots, namespaces, technical owners, catalog feature
  flags, and required catalog-role grants.
- `sources/*.yaml`: source boundary, landing namespace, immutable object prefix, checkpoint key,
  table location, and schema-contract reference.
- `domains/*.yaml`: analytics-domain owner and public relation registry used by consumers.
- `policies/*.yaml`: one Polaris policy per file, including typed content and attachments.

Runtime endpoints and credentials do not belong here. They remain in environment variables or a
secret manager. dbt SQL, model tests, groups, access, and exposures remain in native dbt files.

## Validation

```bash
uv run lakehouse validate
```

Validation rejects unknown fields, duplicate YAML keys, missing namespace references, duplicate
policy attachments, unsafe object prefixes, and partition-overwrite tables that do not partition
by their checkpoint field.

Polaris allows engine-specific keys in policy `config`, but this project intentionally accepts
only fields that its Trino maintenance runner can enforce. Unsupported fields fail validation
instead of being silently ignored.

Bootstrap safely creates/updates policy bodies and desired mappings. Removing a mapping is treated
as a destructive migration: the Polaris API does not expose a direct mapping-list endpoint, so a
reviewed detach operation must accompany the contract change instead of guessing and deleting
attachments during normal bootstrap.

## Adding a source

1. Add `sources/<source>.yaml` and its landing namespace to `catalog.yaml`.
2. Add a source-owned package with client, parser, schema, repository, and service as needed.
3. Give every Iceberg field a permanent ID and lock it with a schema test.
4. Choose a natural checkpoint/idempotency key and an atomic table commit strategy.
5. Add a convention-named DAG that co-locates its tasks and flow.
6. Add a dbt source and one-to-one staging models.
7. Attach existing tier policies; do not copy policy bodies.
8. Add cross-layer and rerun/backfill tests before enabling a schedule.
