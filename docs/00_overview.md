# Architecture

The repository uses a control-plane/data-plane split:

- Terraform provisions AWS resources and workload identities.
- YAML plus PyIceberg controls Glue databases and Iceberg table metadata.
- EMR Serverless Spark executes source-to-landing-to-curated jobs.
- Athena executes interactive reads and dbt analytics transformations.
- Self-hosted Airflow schedules, submits, defers, retries, and reports failures.

Landing preserves replayable source truth and parsed raw tables. Curated publishes current,
validated products without transport concerns. Analytics contains consumer-owned facts and
dimensions. Cross-layer writes are not shared between engines.
