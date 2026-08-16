# Production promotion

Create a separate environment root that reuses the modules and changes only reviewed inputs:

- `force_destroy = false`;
- explicit trusted workload/SSO principals;
- production capacity and concurrency;
- production database grants;
- a separate state key, buckets, KMS key, Athena query-result location, and EMR application.

Promote an immutable artifact prefix containing EMR entry points, locked dependencies, and
contracts. Run Terraform plan, catalog validation, bounded source smoke tests, and analytics tests
before enabling schedules. S3 data and Glue metadata must be backed up and migrated as one logical
Iceberg system; never restore one without validating the other. The dev backup timer, bucket
retention, and metadata PostgreSQL dump schedule must be re-established in the production
environment with a longer retention window and restore rehearsals before promoting data.
