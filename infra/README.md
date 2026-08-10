# Infrastructure

Terraform has one local bootstrap root and five remote-state roots:

```text
terraform/
  aws/bootstrap/state/          one-time S3 backend bootstrap
  aws/environments/dev/         AWS data plane and workload IAM
  tailscale/environments/dev/   private access and host enrollment
  github/environments/dev/      protected release configuration
  oci/environments/dev/         private ARM services host
  cloudflare/environments/dev/  public edge, DNS, and identity access
```

AWS owns S3, KMS, ECR, the EMR Serverless application and egress network, IAM, SSM references, and
empty Secrets Manager containers.
Tailscale owns private administrative access. OCI owns the rebuildable services host. Cloudflare
owns the public tunnel, DNS, and Access boundary. GitHub owns release policy and non-secret CI
variables. Terraform does not own secret values, Glue databases, Iceberg tables, schedules, or dbt
models.

The backend bootstrap keeps only its small state at
`~/.cache/lakehouse/terraform/state/aws-bootstrap.tfstate`. It creates the versioned S3 bucket used
by the isolated AWS, Tailscale, GitHub, OCI, and Cloudflare state keys. Make keeps all Terraform
working data outside the repository and resolves that bucket automatically.

## Prerequisites

- AWS CLI profile `tgbao-dev`; the account must allow EMR Serverless (a Paid account plan is
  required for accounts whose Free plan excludes it).
- OCI CLI profile `tgbao-dev` and populated AWS, Tailscale, OCI, and Cloudflare dev tfvars.
- Local Tailscale client authenticated to the target tailnet.
- Terraform, AWS CLI, OCI CLI, Tailscale CLI, GitHub CLI, Docker, jq, and uv.
- Tailscale and Cloudflare provider API tokens plus a fine-grained GitHub token. Provider
  credentials stay in the process environment, never in tfvars or state.

Authenticate the operator shell once:

```bash
export AWS_PROFILE=tgbao-dev
aws login --profile "$AWS_PROFILE"
aws sts get-caller-identity
```

## First deployment

Run each plan, review it, then apply it. An interrupted apply is resumable; do not delete state or
destroy resources merely because a later resource failed.

### 1. Backend and AWS

```bash
make aws-state-plan
make aws-state-apply

make workload-pki-init
make aws-plan
make aws-apply
make workload-identities-render
```

The state bootstrap is the only local-state root. `aws-apply` creates the data buckets, KMS key,
ECR repositories, EMR Serverless application, its two-AZ public egress network, workload roles,
runtime parameters, and empty secret containers. The EMR network has no inbound access or NAT
Gateway; outbound HTTPS reaches external sources through its Internet Gateway while S3 uses a
Gateway Endpoint.

### 2. Tailscale

```bash
export TAILSCALE_OAUTH_CLIENT_ID='<client-id>'
export TAILSCALE_OAUTH_CLIENT_SECRET='<client-secret>'
export TAILSCALE_TAILNET='-'

make tailscale-init
make tailscale-policy-import  # once: adopt the existing tailnet policy
make tailscale-plan
make tailscale-apply
```

Never rerun the import after it succeeds. A temporary all-scope bootstrap client should be revoked
after replacing it with a scoped operator credential. Terraform applies the policy before creating
tagged auth keys or federated identities. If a tagged resource fails after the policy succeeds,
rerun plan/apply with the same credential and do not import the policy again.

### 3. GitHub release configuration

The GitHub root depends on both AWS and Tailscale state. Use a fine-grained PAT owned by
`ToGiaBaoKDL`, restricted to `mini-lakehouse`, with repository permissions `Administration: write`
and `Environments: write` (`Metadata: read` is automatic). The first permission owns the deployment
environment and branch policy; the second owns its non-secret Actions variables.

```bash
export GITHUB_TOKEN='<fine-grained-token>'
make github-plan
make github-apply
unset GITHUB_TOKEN
```

### 4. OCI services host

OCI reads the one-time host enrollment key from Tailscale state; its Terraform backend remains in
AWS. The host has outbound internet access but no public ingress.

```bash
export OCI_CONFIG_FILE_PROFILE=tgbao-dev
make oci-plan
make oci-apply

tailscale ping tgbao-dev-services
make workload-identities-install
```

`workload-identities-install` transfers only service leaf identities over Tailscale SSH. The local
CA private key never leaves the operator machine.

### 5. Cloudflare edge

Cloudflare manages one remotely configured tunnel for both applications. DNS and Access policy are
generated from the same reviewed application map, and the final catch-all ingress returns 404.

```bash
export CLOUDFLARE_API_TOKEN='<scoped-api-token>'
make cloudflare-plan
make cloudflare-apply
AWS_PROFILE=tgbao-dev make cloudflare-secret-sync
unset CLOUDFLARE_API_TOKEN
```

The token needs account-scoped Tunnel and Access application write permissions plus DNS write for
`tgblab.io.vn`. The explicit sync writes a versioned connector payload directly to the empty AWS
secret container without placing it in Terraform state, GitHub, command arguments, or a local file.

### 6. Catalog operator and contracts

Configure the human catalog role once, then apply the YAML source of truth before scheduled jobs
can write tables:

```bash
account_id="$(aws sts get-caller-identity --query Account --output text)"
aws configure set source_profile tgbao-dev --profile tgbao-dev-catalog
aws configure set role_arn "arn:aws:iam::$account_id:role/tgbao-dev-catalog-admin" \
  --profile tgbao-dev-catalog
aws configure set region ap-southeast-1 --profile tgbao-dev-catalog

AWS_PROFILE=tgbao-dev-catalog make catalog-apply
AWS_PROFILE=tgbao-dev-catalog make catalog-validate
```

## Runtime secrets

Terraform creates Secrets Manager containers but deliberately does not own secret values. After
the AWS root has been applied, generate the PostgreSQL and Airflow runtime values idempotently:

```bash
AWS_PROFILE=tgbao-dev make metadata-postgres-secrets-init
AWS_PROFILE=tgbao-dev make airflow-secrets-init
```

Populate optional integration credentials only when their workloads are enabled. Local payloads
under `.secrets/dev` are ignored by Git and must not be uploaded while they still contain
`REPLACE_...` placeholders:

- Slack `password` is the installed app's `xoxb-...` Bot User OAuth Token, not its client secret.
- SMTP `password` is the Gmail app password; `from_email` must be the authenticated account or an
  approved alias.
- Kaggle and Modal payloads contain their provider API credentials.

```bash
AWS_PROFILE=tgbao-dev aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/slack_api_default \
  --secret-string file://.secrets/dev/airflow/slack_api_default.json

AWS_PROFILE=tgbao-dev aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/smtp_default \
  --secret-string file://.secrets/dev/airflow/smtp_default.json

AWS_PROFILE=tgbao-dev aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/ocr/providers/kaggle \
  --secret-string file://.secrets/dev/ocr/kaggle.json

AWS_PROFILE=tgbao-dev aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/ocr/providers/modal \
  --secret-string file://.secrets/dev/ocr/modal.json
```

## Initial releases

Push the reviewed revision to `main`, then dispatch and approve the protected `dev` workflows in
this order. Wait for each workflow before starting the next one so Airflow cannot schedule work
against missing artifacts or task images.

```bash
gh workflow run release-emr-jobs.yml --ref main
gh workflow run release-dbt-engineering.yml --ref main
gh workflow run release-dbt-research.yml --ref main
gh workflow run release-ocr-worker.yml --ref main
gh workflow run release-airflow.yml --ref main
gh workflow run release-arxiv-inspector.yml --ref main
gh workflow run deploy-cloudflare.yml --ref main
```

Deploy the remote GPU runner after its provider secret is available. This control-plane operation
uses the selected operator AWS profile; it does not impersonate the runtime OCR role. Re-running it
updates the same persistent Modal app in place:

```bash
AWS_PROFILE=tgbao-dev make ocr-modal-runner-deploy
```

The EMR workflow publishes an immutable contract/job bundle and updates its SSM pointer only after
the checksum manifest is complete. Component workflows build multi-architecture images, publish
immutable Git-SHA tags to ECR, resolve their digests, and deploy only the selected component over
Tailscale SSH. The host receives deployment bundles, not a repository checkout or Terraform state.
The Cloudflare workflow does not build an image: it deploys the reviewed upstream image digest,
materializes the connector token through the narrowly scoped services-deployer identity, and waits
for the local readiness endpoint.

## Verification

```bash
tailscale status
tailscale ping tgbao-dev-services
tailscale ssh ubuntu@tgbao-dev-services 'docker ps'
```

Open Airflow at `https://airflow.tgblab.io.vn` and ArXiv Inspector at
`https://arxiv.tgblab.io.vn`; Cloudflare Access restricts both applications to the reviewed email
set. Tailnet endpoints remain available for private diagnosis. Airflow task logs are stored in the
KMS-encrypted logs bucket with 30-day dev retention.

## Day-two changes

- Review and apply only the Terraform root whose ownership changed.
- DAG-only changes are fetched from the versioned Git DAG bundle; they do not rebuild Airflow.
- Runtime, task, and application changes publish only their component image.
- EMR job changes publish a new immutable S3 release; they do not rebuild service images.
- Use the component or EMR rollback workflow with an exact reviewed revision/digest.
- Re-run contract validation after contract or catalog changes.

The services deployer can pull reviewed ECR digests and read only the Cloudflare connector token;
it cannot read application secrets or data. Airflow, metadata PostgreSQL, dbt, OCR, and Inspector
use separate certificate-backed AWS roles. Only EMR and the catalog administrator have tier-wide
landing/curated access. Dev is rebuildable; production should disable destructive bucket/ECR flags
and replace the local CA with managed certificate issuance while preserving these boundaries.
