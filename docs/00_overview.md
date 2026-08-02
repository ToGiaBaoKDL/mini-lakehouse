# Architecture

The repository separates management, execution, and service-runtime responsibilities:

- Terraform independently provisions the AWS data plane, OCI services host, and Tailscale access.
- YAML plus PyIceberg controls Glue databases and Iceberg table metadata.
- EMR Serverless Spark executes source-to-landing-to-curated jobs.
- Athena executes interactive reads and dbt analytics transformations.
- OCI-hosted Airflow schedules, submits, defers, reports failures, and records logical assets only
  after successful producer tasks.

OCI has outbound internet access but no public ingress. Tailscale grants are the only operator
network path. IAM Roles Anywhere exchanges per-workload X.509 identities for short-lived AWS
credentials; no shared AWS credentials directory is mounted into services.

Landing preserves replayable source truth and parsed raw tables. Curated publishes current,
validated products without transport concerns. Analytics contains consumer-owned facts and
dimensions. Cross-layer writes are not shared between engines.
