# Production migration notes

Local defaults deliberately use static MinIO credentials and disable Polaris credential vending
because MinIO has no AWS STS. Do not copy that security model to cloud deployments.

For AWS S3 or GCS:

1. Keep bucket roots separate for lifecycle and IAM boundaries.
2. Configure Polaris storage credentials with workload identity/IAM roles and enable credential
   vending in Trino/PyIceberg clients.
3. Create separate service principals and catalog roles for ingestion, dbt, and read-only BI.
4. Require a Polaris realm header and external identity provider where appropriate.
5. Put TLS and authentication in front of Trino, Polaris, Prefect, and the dashboard.
6. Move PostgreSQL and Redis to backed-up managed services.
7. Promote tested immutable container digests, configure resource limits, and emit metrics/logs to
   the platform observability stack.
8. Use a separate catalog or Polaris deployment per environment; never let CI write to `prod`.

Iceberg retention must be longer than the maximum job retry/backfill window. `remove_orphan_files`
must not run with aggressive retention while another engine may still be committing files.

