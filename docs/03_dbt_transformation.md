# Analytics transformations

dbt reads curated Glue databases through Athena and writes only analytics Iceberg tables. Current
models use full `table` materialization; repeated runs replace the logical table instead of
appending duplicate rows.

Staging and intermediate models are ephemeral. Only marts are physical. Models use explicit
projections, dbt-utils tests, adapter-managed unique Iceberg locations, and a scoped dbt IAM role.
The dbt client supplies the shared query-result location when using Athena's built-in `primary`
workgroup; final table data lives only in the analytics bucket.
