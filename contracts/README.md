# Data contracts

YAML is the source of truth for catalog identifiers and Iceberg metadata.

```text
sources/                  source ownership, raw prefix, checkpoints, landing tables
curated/                  reusable product tables and upstream sources
domains/                  analytics ownership and upstream products
```

Each source, product, and domain declares its Glue `database` explicitly. Table contracts declare
stable field IDs, types, optional primary keys, and partition transforms. Credentials, IAM policy,
maintenance schedules, processor tuning, and execution SQL do not belong here.

`python -m lakehouse_platform.platform.catalog.admin apply` creates missing objects and updates safe
table properties. Structural drift in location, schema, identifier fields, partition spec, or
format version fails and requires an explicit migration. The command never silently rewrites
existing tables.
