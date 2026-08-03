# Infrastructure

Terraform has one backend bootstrap and four independently planned operational roots:

```text
terraform/
  aws/
    bootstrap/state/       one-time versioned S3 backend
    environments/dev/      AWS environment composition
    modules/               storage, EMR, ECR, and IAM boundaries
  tailscale/environments/dev/  private access policy and one-time enrollment key
  github/environments/dev/     protected delivery environment and release variables
  oci/environments/dev/        ARM services host with no public ingress
```

AWS owns S3, KMS, EMR Serverless, ECR, workload IAM, SSM references, and empty Secrets Manager
containers. Terraform does not create Glue databases, Iceberg tables, schedules, dbt models, or
secret values. OCI owns only the self-hosted compute/network boundary. Tailscale owns only private
network access. GitHub owns the protected release boundary and derives its non-secret variables
from the AWS and Tailscale remote states. Each root has an isolated state key and can be planned
independently.
AWS also owns repository/environment-scoped GitHub OIDC release roles. Tailscale owns the matching
federated CI identity and grants it SSH only; human operator roles and runtime Roles Anywhere
identities remain separate trust boundaries.
Make stores Terraform working data under `~/.cache/lakehouse/terraform`, shares one provider cache,
and resolves the remote-state bucket from the bootstrap output. The bootstrap state itself lives at
`~/.cache/lakehouse/terraform/state/aws-bootstrap.tfstate`; the AWS, Tailscale, GitHub, and OCI
roots use isolated, natively locked keys in the versioned S3 bucket it creates. Terraform never
writes `.terraform` directories or state into the worktree when invoked through Make.

## Bootstrap order

```bash
# 1. Create the remote-state bucket once.
make aws-state-apply

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
make tailscale-policy-import
make tailscale-plan
make tailscale-apply

# 4. Apply repository delivery configuration using provider authentication only.
GITHUB_TOKEN='<fine-grained token>' make github-plan
GITHUB_TOKEN='<fine-grained token>' make github-apply

# 5. Configure the OCI CLI profile, copy the OCI example, and apply the host.
export PATH="$HOME/bin:$PATH"
oci setup config
cp infra/terraform/oci/environments/dev/terraform.tfvars.example \
  infra/terraform/oci/environments/dev/terraform.tfvars
make oci-plan
make oci-apply
make workload-identities-install
```

Authenticate the Tailscale provider with scoped OAuth environment variables. AWS and OCI providers
use their standard SDK credential chains; select non-default operator profiles with `AWS_PROFILE`
and `OCI_CONFIG_FILE_PROFILE` at the command boundary. The GitHub provider uses `GITHUB_TOKEN` (or
its standard GitHub App authentication variables); provider credentials never belong in tfvars.
Private keys and OCIDs are not copied into Terraform source. OCI reads the single-use enrollment
key from the isolated Tailscale remote state; cloud-init deletes it after enrollment.

The GitHub root manages the existing repository's `dev` environment, owner approval, exact `main`
deployment policy, and six non-secret release variables. It deliberately does not adopt ownership
of the repository itself. CI uses immutable GitHub repository IDs in the OIDC subject and receives
only image/EMR publication plus port-22 deployment access.

Pin `image_ocid` in OCI tfvars to one reviewed Ubuntu 24.04 AArch64 image; Terraform never moves
the host to a newly published image implicitly. IAM Roles Anywhere trusts the public workload CA only. The CA private key stays outside the
repository; containers receive only their own leaf certificate, private key, and generated AWS
config. `make workload-identities-install` transfers only leaf bundles over Tailscale SSH and never
copies the CA. The services deployer can pull reviewed ECR digests but cannot read application
secrets or lakehouse data. Airflow and metadata PostgreSQL retrieve only their own runtime and
database secrets with separate workload identities. dbt, OCR, and Inspector retain separate
least-privilege roles. Checked-in workload entitlements bind dbt to
`curated_github`/`analytics_engineering`, OCR and Inspector to `curated_arxiv`, and each workload to
its matching S3 prefix. Only the shared EMR processor and catalog administrator retain tier-wide
landing/curated access by design.

Airflow task logs use a dedicated KMS-encrypted S3 bucket with 30-day dev retention. Its generated
dev-auth password file, DAG bundle cache, and shared PostgreSQL data remain persistent Docker
volumes on the single dev host.

Review every plan. Dev remains rebuildable; production should disable destructive bucket/ECR
flags, use managed certificate issuance, and keep the same ownership boundaries.
