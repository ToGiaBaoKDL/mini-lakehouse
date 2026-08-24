# Infrastructure

Terraform has one local bootstrap root and five remote-state roots:

```text
terraform/
  aws/bootstrap/state/          one-time S3 backend bootstrap
  aws/environments/dev/         AWS data plane and workload IAM
  tailscale/environments/dev/   private access and host enrollment
  github/environments/dev/      release variables and protected OCI deployment
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

## AWS identities and permissions

No AWS access key is stored in GitHub, Airflow, a dbt profile, or the repository. CI exchanges a
GitHub OIDC token for short-lived STS credentials. Workloads on the OCI host use isolated leaf
certificates with IAM Roles Anywhere; `aws_signing_helper` supplies temporary credentials through
the standard AWS `credential_process` chain.

| Identity | Authentication | Allowed responsibility |
|---|---|---|
| `github-emr-publisher` | GitHub OIDC, restricted to this repository's `main` branch | Read/write immutable `artifacts/emr/jobs/*`, update only the `emr/code_uri` SSM pointer, and use the lakehouse KMS key. It cannot submit EMR jobs or access data buckets. |
| `github-image-publisher` | GitHub OIDC, restricted to `main` for publishing and protected `dev` jobs for rollback verification | Authenticate to ECR and push/pull only component repositories. It has no lakehouse data access. |
| `github-lightdash-deployer` | GitHub OIDC, restricted to the protected `dev` environment | Read only the Lightdash CI token secret and deliver reviewed semantic layers and content over Tailscale. It has no lakehouse data access. |
| `services-deployer` | Roles Anywhere certificate on the OCI host | Pull reviewed ECR images and read only infrastructure connector secrets. It cannot read application secrets or data. |
| `airflow` | Roles Anywhere certificate mounted read-only at `/run/aws` | Start, inspect, and cancel jobs in the owned EMR Serverless application; pass only `emr-runtime`; read its SSM parameters and Airflow secrets; read/write only the Airflow task-log prefix. It has no landing, curated, or analytics access. |
| `emr-runtime` | AWS service role assumed by EMR Serverless | Read immutable EMR artifacts; read/write landing and curated objects; read/update the corresponding Glue Iceberg tables; use the lakehouse KMS key. |
| `dbt-<domain>` | Separate Roles Anywhere certificate mounted into each ephemeral dbt container | Run Athena queries; read only the domain's curated databases/prefixes; manage only its analytics database/prefix; write only its Athena result prefix; read only its SSM parameters. |
| `arxiv-inspector` | Roles Anywhere certificate mounted into the application | Read ArXiv curated catalog/objects, run Athena, and manage only its query-result prefix. |
| `lightdash` | Roles Anywhere certificate mounted into the application | Read Engineering and Research analytics only, run Athena, manage its query-result prefix and its dedicated S3 application bucket, and read its two runtime secrets. |
| `ocr-worker` | Roles Anywhere certificate mounted into the task container | Read OCR provider secrets and update only ArXiv curated catalog/object prefixes. |
| `metadata-postgres` | Roles Anywhere certificate on the OCI host | Read only metadata PostgreSQL secrets and its backup destination parameter, and manage only daily metadata database backup objects under the backup bucket's `metadata-postgres/` prefix. |
| `catalog-admin` | Explicit operator `AssumeRole` | Apply and validate contract-owned Glue/Iceberg metadata. It does not run scheduled workloads. |

The EMR release workflow does not run Spark. It uploads
`emr/jobs/<commit-sha>/`, writes the checksum completion marker, and then updates
`/lakehouse/<env>/emr/code_uri`. Airflow reads that pointer and submits the job with its own role;
EMR Serverless subsequently assumes `emr-runtime` for the Spark process.

On the OCI host, workload bundles live outside the repository under
`~/.config/lakehouse/<env>/aws/<workload>/`. Airflow mounts `airflow/`; Docker tasks mount the exact
workload directory such as `dbt-engineering/` or `dbt-research/`. Both identities run the shared
`dbt:runtime` image, while the container sees only:

```text
/run/aws/
  certificate.pem
  private-key.pem
  config              # credential_process -> aws_signing_helper -> temporary STS session
```

AWS SDKs and the dbt Athena adapter use this default credential chain; no Airflow AWS connection or
role field is required. Secrets Manager contains secret values, while SSM Parameter Store contains
non-secret runtime references such as storage URIs, EMR identifiers, and query-result URIs. The
action-level source of truth is `terraform/aws/modules/identity/`.

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
`ToGiaBaoKDL`, restricted to `mini-lakehouse`, with repository permissions `Administration: write`,
`Environments: write`, and `Variables: write` (`Metadata: read` is automatic). These permissions
own the protected environment and policy, its deploy variables, and the repository publish
variables respectively; `Actions: write` is not required.

```bash
read -rsp 'GitHub token: ' GITHUB_TOKEN
printf '\n'
export GITHUB_TOKEN
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

Cloudflare manages one remotely configured tunnel for all applications. DNS and Access policy are
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
the AWS root has been applied, initialize the PostgreSQL bootstrap credential and each
application-owned database/runtime pair idempotently:

```bash
AWS_PROFILE=tgbao-dev make metadata-postgres-secrets-init
AWS_PROFILE=tgbao-dev make airflow-secrets-init
AWS_PROFILE=tgbao-dev make lightdash-secrets-init
```

Populate optional integration credentials only when their workloads are enabled. Local payloads
under `.secrets/dev` are ignored by Git and must not be uploaded while they still contain
`REPLACE_...` placeholders:

- Lightdash CI `api_key` is a personal access token created from the dedicated CI user's Lightdash
  settings. One token can deploy both projects when that user has Developer access to both. Its
  local payload is exactly `{"version":1,"api_key":"ldpat_..."}`; service accounts require
  Lightdash Enterprise and are not enabled by this community deployment.
- Slack `password` is the installed app's `xoxb-...` Bot User OAuth Token, not its client secret.
- SMTP `password` is the Gmail app password; `from_email` must be the authenticated account or an
  approved alias.
- The Modal payload contains its provider API credentials.

```bash
AWS_PROFILE=tgbao-dev make lightdash-ci-secret-sync

AWS_PROFILE=tgbao-dev aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/slack_api_default \
  --secret-string file://.secrets/dev/airflow/slack_api_default.json

AWS_PROFILE=tgbao-dev aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/smtp_default \
  --secret-string file://.secrets/dev/airflow/smtp_default.json

AWS_PROFILE=tgbao-dev aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/ocr/providers/modal \
  --secret-string file://.secrets/dev/ocr/modal.json
```

## Initial releases

Merges to `main` automatically publish only changed artifacts. On a fresh environment whose current
revision has no releases yet, dispatch the missing publishers explicitly:

```bash
gh workflow run release-emr-jobs.yml --ref main
gh workflow run release-dbt.yml --ref main
gh workflow run release-ocr-worker.yml --ref main
gh workflow run release-airflow.yml --ref main
gh workflow run release-arxiv-inspector.yml --ref main
gh workflow run release-lightdash.yml --ref main
```

Image and EMR publish jobs do not require approval. Wait for EMR, dbt, and OCR publishing to
complete; then approve the protected OCI deploy jobs for dbt and OCR before Airflow. Inspector and
Lightdash are independent and may be approved afterward. Deploy the Cloudflare connector once its
Terraform resources and secret are ready:

```bash
gh workflow run deploy-cloudflare.yml --ref main
```

Deploy the Modal GPU worker after its credential secret is available. This control-plane operation
uses the selected operator AWS profile; it does not impersonate the runtime OCR role. Re-running it
updates the same persistent Modal app in place:

```bash
AWS_PROFILE=tgbao-dev make ocr-modal-deploy
```

The Lightdash workflow builds the unmodified upstream `1.146.0` commit on a native ARM GitHub
runner, publishes only the OCI A1-compatible ARM64 image, and deploys it by digest. The official
release image is not used because it does not publish an ARM64 manifest. The pinned upstream commit
is the image build identity: deployment-only revisions add their immutable Git-SHA tag to the same
ECR manifest without pulling, rebuilding, or pushing its layers. The EMR workflow publishes
an immutable contract/job bundle and updates its SSM pointer only after
the checksum manifest is complete. Component workflows build their required architectures, publish
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

Open Airflow at `https://airflow.tgblab.io.vn`, ArXiv Inspector at
`https://arxiv.tgblab.io.vn`, and Lightdash at `https://analytics.tgblab.io.vn`; Cloudflare Access
restricts all applications to the reviewed email
set. Tailnet endpoints remain available for private diagnosis. Airflow task logs are stored in the
KMS-encrypted logs bucket with 30-day dev retention.

### Bootstrap Lightdash projects

After the first Lightdash release, open the application through Cloudflare Access and create the
initial organization and administrator. Create two projects, `Engineering` and `Research`, using
Athena IAM role authentication. Both use region `ap-southeast-1`, catalog `AwsDataCatalog`,
workgroup `primary`, and the Lightdash query-result URI from
`/lakehouse/dev/athena/lightdash_output_uri`; their schemas are `analytics_engineering` and
`analytics_research`, respectively. Do not enter static AWS access keys.

Create a personal access token named `github-actions-dev` for a dedicated CI user with access to
both projects. Replace `REPLACE_LIGHTDASH_API_KEY` in `.secrets/dev/lightdash/ci.json`, then run
`AWS_PROFILE=tgbao-dev make lightdash-ci-secret-sync`. The ignored local file is the operator
handoff only; protected GitHub jobs retrieve the value from `lakehouse/dev/lightdash/ci` through
their environment-scoped OIDC role.

Terraform publishes the private delivery endpoint, secret identifier, and OIDC identity references
as protected GitHub environment variables. The workflow matrix owns the non-secret mapping from a
domain to its dbt project, managed content, and immutable Lightdash project UUID. Validate managed
content without cloud access:

```bash
npm install --global @lightdash/cli@1.146.0
make lightdash-validate
```

After the one-time bootstrap, `.github/workflows/deploy-lightdash-projects.yml` is the normal
delivery path. It uses the protected `dev` environment, short-lived AWS and Tailscale identities,
and the PAT stored at `lakehouse/dev/lightdash/ci`; no CLI login state or custom deployment wrapper
is used. Managed content lives under `analytics/lightdash/projects/<domain>/content`. Treat Git as
the source
of truth for the managed `Shared` spaces and keep ordinary UI-authored content in other spaces.

## Day-two changes

- Review and apply only the Terraform root whose ownership changed.
- DAG-only changes are fetched from the versioned Git DAG bundle; they do not rebuild Airflow.
- Runtime, task, and application changes publish only their component image. A Lightdash version
  upgrade must update its release workflow commit, CLI/skill version, and validation together.
- EMR job changes publish a new immutable S3 release; they do not rebuild service images.
- Use the protected component rollback for OCI changes, or the EMR pointer rollback, with an exact
  reviewed revision/digest.
- Re-run contract validation after contract or catalog changes.

Metadata database durability is the services host's own responsibility, not a pipeline task. The
host reconciles a twice-daily systemd backup timer that dumps `airflow` and `lightdash` into the
backup bucket (35-day retention, KMS-encrypted, checksum-verified); backup scheduling changes ship
through the services-host reconcile workflow. Restore one application database from an exact slot
backup with `make metadata-postgres-restore ARGS='<database> <utc-date> <am|pm>'` against a
bootstrapped cluster.

The services deployer can pull reviewed ECR digests and read only the Cloudflare connector token;
it cannot read application secrets or data. Airflow, metadata PostgreSQL, dbt, OCR, and Inspector
use separate certificate-backed AWS roles. Only EMR and the catalog administrator have tier-wide
landing/curated access. Dev is rebuildable; production should disable destructive bucket/ECR flags
and replace the local CA with managed certificate issuance while preserving these boundaries.
