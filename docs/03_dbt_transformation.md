# Analytics transformations

dbt reads curated Glue databases through Athena and writes only analytics Iceberg tables. Current
models use full `table` materialization; repeated runs replace the logical table instead of
appending duplicate rows.

Staging and intermediate models are ephemeral. Only marts are physical. Models use explicit
projections, dbt-utils tests, adapter-managed unique Iceberg locations, and a scoped dbt IAM role.
The dbt client supplies its isolated `dbt/` query-result prefix when using Athena's built-in
`primary` workgroup; final table data lives only in the analytics bucket.

Model YAML documents every projected column and its Athena type. Source declarations mirror the
curated product contract; repository tests prevent table, column, description, type, schema, and
ownership drift between the two boundaries.
