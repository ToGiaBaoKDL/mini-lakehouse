# Environment setup

Use named/SSO profiles for operator commands and IAM Roles Anywhere for self-hosted workloads. The
repository does not use `.env` files. Secrets Manager owns credentials, Parameter Store owns
runtime resource discovery, and Git owns stable application configuration.

The backend bootstrap keeps its local state outside the worktree under
`~/.cache/lakehouse/terraform/state/`. It creates the versioned S3 bucket where AWS, Tailscale,
GitHub, and OCI use separate state keys with native lock files. Dev resources can be rebuilt;
production should disable destructive bucket behavior while retaining the same module boundaries.

Only shared metadata PostgreSQL, Airflow, and ArXiv Inspector are composed on the OCI services host.
AWS owns S3, Glue, EMR Serverless, Athena, KMS, IAM, ECR, SSM, and Secrets Manager. OCI exposes no
public ingress; Tailscale carries SSH and application traffic.

## Workload identities

Terraform follows the standard AWS/OCI credential chains. Runtime containers never inherit an
operator profile or mount `$HOME/.aws`; each receives a certificate-backed identity under
`AWS_IDENTITY_DIR`.

```bash
make workload-pki-init
make aws-apply
make workload-identities-render
make workload-identities-install
```

Airflow, metadata PostgreSQL, dbt, OCR, Inspector, and the services deployer exchange separate X.509
certificates for short-lived AWS credentials. The services deployer may pull reviewed ECR images but
cannot read application secrets. The CA private key stays on the administrator machine; only leaf
identities cross the private network.

Operator commands use the standard AWS credential chain. Select a named profile only at the command
boundary when the default chain is not the intended identity:

```bash
AWS_PROFILE=custom-catalog make catalog-apply
AWS_PROFILE=custom-terraform-admin make aws-plan
```

## Secrets

Terraform creates secret containers but never secret values. PostgreSQL bootstrap and the Airflow
database each use an independent object:

```json
{"version": 1, "password": "<random password>"}
```

The Airflow runtime secret is separate from its database credential:

```json
{
  "version": 1,
  "fernet_key": "<Airflow Fernet key>",
  "jwt_secret": "<random JWT secret>",
  "admin_password": "<random UI admin password>"
}
```

Initialize them once without printing values or putting them in Terraform state:

```bash
AWS_PROFILE=your-terraform-admin make metadata-postgres-secrets-init
AWS_PROFILE=your-terraform-admin make airflow-secrets-init
```

Both targets preserve an existing valid `AWSCURRENT` version. `make metadata-postgres-up`
idempotently reconciles the Airflow owner/database and rotates only that role password. Airflow owns
and runs its schema migrations. The Airflow init container writes the SimpleAuthManager password
file before startup, so the UI password is never generated or logged by Airflow.

Populate notification connections independently:

```bash
aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/slack_api_default \
  --secret-string '<Airflow Slack connection JSON>'

aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/smtp_default \
  --secret-string '<Airflow SMTP connection JSON>'
```

OCR provider credentials remain OCR-owned:

```bash
aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/ocr/providers/kaggle \
  --secret-string '{"username":"<kaggle-user>","api_token":"<token>"}'

aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/ocr/providers/modal \
  --secret-string '{"token_id":"<token-id>","token_secret":"<token-secret>"}'
```

## Component releases

ECR repositories are immutable and retain the newest 20 releases. GitHub publishes and deploys only
the changed component after the protected `dev` environment is approved. AWS and Tailscale access
uses OIDC; there are no long-lived CI credentials.

After applying AWS and Tailscale, apply the isolated GitHub root with a fine-grained token that can
manage Actions environments and variables for this repository:

```bash
GITHUB_TOKEN='<fine-grained token>' make github-plan
GITHUB_TOKEN='<fine-grained token>' make github-apply
```

The variable names are `AWS_IMAGE_PUBLISHER_ROLE_ARN`, `AWS_EMR_PUBLISHER_ROLE_ARN`,
`EMR_ARTIFACTS_URI`, `EMR_CODE_PARAMETER_NAME`, `TAILSCALE_CLIENT_ID`, and
`TAILSCALE_AUDIENCE`. Terraform reads them directly from the AWS and Tailscale remote states and
owns the `dev` environment, owner reviewer, and exact `main` deployment policy. The token is provider
authentication only: keep it in the process environment, never in tfvars or Terraform state. A
GitHub App can replace the local token if repository administration is automated later.

Protected GitHub workflows are the only release publishers. Rollback is a separate protected
workflow and accepts an immutable digest plus its owning Git revision; there are no duplicate human
image or EMR publishing targets to drift from CI.

On the services host, deploy or install each component independently by digest:

```bash
make metadata-postgres-up
make airflow-deploy AIRFLOW_IMAGE='<repository>@sha256:<digest>'
make arxiv-inspector-deploy ARXIV_INSPECTOR_IMAGE='<repository>@sha256:<digest>'
make dbt-task-install DBT_TASK_IMAGE='<repository>@sha256:<digest>'
make ocr-worker-install OCR_WORKER_IMAGE='<repository>@sha256:<digest>'
```

No release uses `latest`, and there is no global service manifest. The protected `Roll back
component` workflow deploys a previous reviewed digest with the exact Git revision that owns its
Compose/Make boundary. The dbt and OCR install targets advance their stable host-local aliases only
after pulling an exact digest, so DAG files never contain image SHAs.

Airflow uses the official versioned `GitDagBundle` for `orchestration/bundle`. A DAG-only merge is
validated by the focused bundle CI, then fetched without rebuilding or restarting Airflow. Active
and retried runs keep their original Git commit by default. A new Airflow/provider dependency still
requires publishing the compatible runtime image first.

For unpublished local iteration:

```bash
make images-build
make metadata-postgres-up
make airflow-up
make arxiv-inspector-up
```

Only the Airflow scheduler receives the Docker socket because `LocalExecutor` launches task
containers there. Task logs are written to the SSM-configured KMS-encrypted S3 prefix and expire
after 30 days in dev; container recreation does not erase completed task logs.
