# AWS infrastructure

Terraform owns AWS infrastructure only:

- five encrypted S3 buckets with stable random suffixes;
- a KMS key;
- an EMR Serverless Spark application;
- immutable ECR repositories for local services and task workers;
- one encrypted query-result bucket with workload-isolated prefixes for the built-in `primary`
  Athena workgroup;
- environment-prefixed workload IAM roles;
- empty Secrets Manager containers;
- non-secret AWS resource references in Systems Manager Parameter Store.

Glue databases, Iceberg tables, source schedules, dbt models, and secret values are intentionally
outside Terraform.

## Layout

```text
terraform/
  bootstrap/state/       one-time versioned S3 backend
  environments/dev/      environment composition and capacity choices
  modules/
    storage/
    emr_serverless/
    identity/
```

Modules contain no source-specific identifiers. The environment root owns concrete SSM parameters
and Secrets Manager containers; reusable modules retain only cohesive storage, compute, and
identity boundaries.

```bash
cp infra/terraform/bootstrap/state/terraform.tfvars.example \
  infra/terraform/bootstrap/state/terraform.tfvars
make terraform-state-apply

export TF_STATE_BUCKET="$(terraform -chdir=infra/terraform/bootstrap/state output -raw bucket_name)"
cp infra/terraform/environments/dev/terraform.tfvars.example \
  infra/terraform/environments/dev/terraform.tfvars
make terraform-plan
make terraform-apply
```

Review every plan. Populate Secrets Manager values separately; Terraform deliberately creates no
secret versions. `role_trust` requires explicit principals per workload; dev may reuse one
developer role, while independently deployed workloads should use distinct identities. Dev roles
use the `tgbao-dev-<workload>` convention; the `emr-deployer` role alone publishes immutable job
artifacts and advances the SSM release pointer.
