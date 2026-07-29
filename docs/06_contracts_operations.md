# Contract operations

`make catalog-apply` is an explicit administrative action. It uses PyIceberg's Glue catalog and the
standard AWS credential chain to create missing databases/tables and align safe table properties.

`make catalog-validate` is read-only and suitable for deployment gates. Unsafe drift includes
location, format version, schema, partition spec, and identifier fields. Those changes require a
reviewed migration because they can affect readers or existing data.

Spark jobs consume the same YAML files from the immutable EMR release prefix. Deploy code and
contracts together to prevent runtime/catalog drift.
