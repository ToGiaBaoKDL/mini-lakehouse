# Infrastructure

Terraform has three independent roots and state keys:

```text
terraform/
  aws/
    bootstrap/state/       one-time versioned S3 backend
    environments/dev/      AWS environment composition
    modules/               storage, EMR, ECR, and IAM boundaries
  tailscale/environments/dev/  private access policy and one-time enrollment key
  oci/environments/dev/        ARM services host with no public ingress
```

AWS owns S3, KMS, EMR Serverless, ECR, workload IAM, SSM references, and empty Secrets Manager
containers. Terraform does not create Glue databases, Iceberg tables, schedules, dbt models, or
secret values. OCI owns only the self-hosted compute/network boundary. Tailscale owns only private
network access. Each root has an isolated state key and can be planned independently.

## Bootstrap order

```bash
# 1. Create the remote-state bucket once.
cp infra/terraform/aws/bootstrap/state/terraform.tfvars.example \
  infra/terraform/aws/bootstrap/state/terraform.tfvars
make aws-state-apply
export TF_STATE_BUCKET="$(terraform -chdir=infra/terraform/aws/bootstrap/state output -raw bucket_name)"

# 2. Create the offline CA and apply AWS, including IAM Roles Anywhere.
make workload-pki-init
cp infra/terraform/aws/environments/dev/terraform.tfvars.example \
  infra/terraform/aws/environments/dev/terraform.tfvars
make aws-plan
make aws-apply
make workload-identities-render

# 3. Import the existing tailnet policy once, review it, then apply private access.
cp infra/terraform/tailscale/environments/dev/terraform.tfvars.example \
  infra/terraform/tailscale/environments/dev/terraform.tfvars
make tailscale-init
terraform -chdir=infra/terraform/tailscale/environments/dev import tailscale_acl.policy acl
make tailscale-plan
make tailscale-apply

# 4. Configure the OCI CLI profile, copy the OCI example, and apply the host.
export PATH="$HOME/bin:$PATH"
oci setup config
cp infra/terraform/oci/environments/dev/terraform.tfvars.example \
  infra/terraform/oci/environments/dev/terraform.tfvars
export TF_VAR_tailscale_auth_key="$(terraform -chdir=infra/terraform/tailscale/environments/dev output -raw services_auth_key)"
make oci-plan
make oci-apply
unset TF_VAR_tailscale_auth_key
```

Authenticate the Tailscale provider with scoped OAuth environment variables. The OCI provider
uses the named profile in `~/.oci/config`; private keys and OCIDs are not copied into Terraform
source. The Tailscale enrollment key is single-use and expires after one hour. OCI cloud-init
deletes it after enrollment.

IAM Roles Anywhere trusts the public workload CA only. The CA private key stays outside the
repository; containers receive only their own leaf certificate, private key, and generated AWS
config. The services deployer can pull ECR images and read the Airflow bootstrap secret but
cannot access lakehouse data. Airflow, dbt, OCR, and Inspector retain separate least-privilege
roles.

Review every plan. Dev remains rebuildable; production should disable destructive bucket/ECR
flags, use managed certificate issuance, and keep the same ownership boundaries.
