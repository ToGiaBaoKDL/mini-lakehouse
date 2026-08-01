# Data contracts

YAML is the source of truth for platform-managed landing and curated tables, their Glue
identifiers, and Iceberg metadata. dbt owns analytics model tables.

```text
sources/                  source ownership, raw prefix, and landing tables
curated/                  reusable product tables and upstream sources
domains/                  analytics ownership and upstream products
```

Each source, product, and domain declares its Glue `database` explicitly. Table contracts declare
stable field IDs, types, optional primary keys, and partition transforms. Credentials, IAM policy,
maintenance schedules, processor tuning, and execution SQL do not belong here.

Primary keys compile to Iceberg identifier fields for schema semantics. Writers remain responsible
for enforcing uniqueness because Iceberg identifier fields are not uniqueness constraints.

`python -m lakehouse.catalog.admin apply` creates missing objects and updates safe
table properties. Structural drift in location, schema, identifier fields, partition spec, or
format version fails and requires an explicit migration. The command never silently rewrites
existing tables.
