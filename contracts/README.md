# Declarative contracts

This directory is the reviewed, non-secret desired state for the lakehouse. Python loads every
file with strict Pydantic models before making a network or storage call.

## Ownership

- `catalog.yaml`: catalog owner, three lifecycle roots, technical owners, catalog properties, and
  required catalog-role grants.
- `sources/*.yaml`: source boundary, optional raw-object subpath, checkpoint key, stable field IDs,
  partition transform, and schema version.
- `curated_products/*.yaml`: canonical curated product owner, upstream sources, keys, stable field
  IDs, partitions, and schemas.
- `processors/*.yaml`: external processing owner, pinned model revisions, output protocol,
  resource limits, artifact prefix, runner, and retry policy.
- `domains/*.yaml`: analytics-domain owner, upstream curated products, mart grain, partitioning,
  and public relation registry.
- `policies/*.yaml`: one Polaris policy per file, including typed content and attachments.

Runtime endpoints and credentials do not belong here. They remain in environment variables or a
secret manager. dbt SQL, model tests, and future BI metadata remain in native dbt files.
Physical locations of managed Iceberg tables are also absent from individual table declarations.
Namespace roots come from `catalog.yaml`; curated product and analytics domain contracts own their
child namespaces. Curated/analytics locations follow those dedicated namespaces, while landing
locations are derived centrally as
`<landing>/<source-type>/<source-name>/tables/<table-key>` because all sources share one logical
namespace. Raw roots are derived as `<source-type>/<source-name>/raw[/<raw-subpath>]`. The
repository implements bounded idempotent writes from the declared checkpoint and partition.

## Validation

```bash
make validate
```

Validation rejects unknown fields, duplicate YAML keys, missing namespace references, duplicate
policy attachments, unsafe object prefixes, and source tables that do not partition by their
checkpoint field.

Polaris allows engine-specific keys in policy `config`, but this project intentionally accepts
only fields that its Trino maintenance runner can enforce. Unsupported fields fail validation
instead of being silently ignored.

Platform reconciliation safely creates or updates desired policy bodies and mappings. The `mlh-`
prefix is reserved for this repository. Removing stale managed policies is a separate, explicit
plan/apply operation; normal deployment never deletes them.

## Adding a source and product

1. Add `sources/<source>.yaml`; publish source-prefixed table names in the shared `landing`
   namespace and retain transport/source hierarchy only in object prefixes.
2. Add a source-owned package with client, parser, repository, and service as needed; consume the
   YAML column contract instead of defining another in-code schema.
3. Give every Iceberg field a permanent ID and lock it with a schema test.
4. Choose a natural checkpoint/idempotency key and an atomic table commit strategy.
5. Add `curated_products/<product>.yaml` and a source-conformance service under
   `curated_products/<product>/`.
6. Add convention-named EL and TL DAGs that co-locate their Prefect tasks; prefer explicit
   schedules over cross-deployment event sensors when a fixed source SLA exists.
7. Add `processors/<processor>.yaml` only when compute leaves the lakehouse runtime; pin every
   output-affecting model/config and keep provider credentials in the environment.
8. Declare the curated product as a dbt source; keep dbt staging/intermediate models ephemeral.
9. Reference the product from each consuming analytics domain contract.
10. Attach the tier policy; change its name when changing targets so stale mappings are prunable.
11. Add cross-layer, idempotent rerun, and failure-recovery tests before enabling a schedule.
