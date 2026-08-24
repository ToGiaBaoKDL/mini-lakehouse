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

## Inputs before the first plan

Provider credentials stay in their standard process-level credential chains. Only stable,
reviewable resource choices belong in tfvars.

| Root | Provider authentication | Reviewed input |
|---|---|---|
| AWS state and platform | Default AWS chain; optionally select `AWS_PROFILE` | `roles_anywhere_ca_certificate_path` and one existing IAM role/user ARN in `catalog_admin_principal_arns` |
| Tailscale | AWS chain for the S3 backend, plus `TAILSCALE_OAUTH_CLIENT_ID`, `TAILSCALE_OAUTH_CLIENT_SECRET`, `TAILSCALE_TAILNET` | Owner email in the Tailscale tfvars file |
| GitHub | AWS chain for backend/remote state, plus `GITHUB_TOKEN` with access to this repository's Actions environment and variables | None; upstream values come from Terraform state |
| OCI | AWS chain for backend/remote state, plus the default OCI CLI config; optionally select `OCI_CONFIG_FILE_PROFILE` | Tenancy, compartment, region, and pinned Ubuntu image OCIDs |

Create the workload CA before copying the AWS example, because the example points to its public
certificate. `catalog_admin_principal_arns` must contain a durable IAM role or user ARN, not a
temporary `arn:aws:sts::...:assumed-role/...` session ARN. Terraform state contains infrastructure
metadata and the one-time Tailscale enrollment key, so access to the state bucket remains an
administrator boundary.

Set provider credentials only for the command that needs them. A typical operator shell uses these
standard names; this repository defines no aliases for them:

```bash
export AWS_PROFILE='<terraform-admin-profile>'
export TAILSCALE_OAUTH_CLIENT_ID='<scoped-client-id>'
export TAILSCALE_OAUTH_CLIENT_SECRET='<scoped-client-secret>'
export TAILSCALE_TAILNET='<tailnet-name>'
export OCI_CONFIG_FILE_PROFILE='<oci-cli-profile>'
```

The Tailscale OAuth client must be scoped to manage the checked-in ACL policy, auth keys, and the
GitHub federated identity. Prompt for the GitHub token only in the GitHub section below so it does
not enter shell history. Unset credentials for providers that are not part of the current root.

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

Terraform creates secret containers but never secret values. PostgreSQL bootstrap plus the Airflow
and Lightdash databases each use an independent object:

```json
{"version": 1, "password": "<random password>"}
```

Application runtime secrets are separate from database credentials. Airflow uses:

```json
{
  "version": 1,
  "fernet_key": "<Airflow Fernet key>",
  "jwt_secret": "<random JWT secret>",
  "admin_password": "<random UI admin password>"
}
```

Lightdash uses `{"version": 1, "secret": "<stable encryption secret>"}`.

Initialize them once without printing values or putting them in Terraform state:

```bash
AWS_PROFILE=your-terraform-admin make metadata-postgres-secrets-init
AWS_PROFILE=your-terraform-admin make airflow-secrets-init
AWS_PROFILE=your-terraform-admin make lightdash-secrets-init
```

All targets preserve an existing valid `AWSCURRENT` version. Each application deployment reconciles
only its own PostgreSQL owner/database and reapplies only that role's stored password. Airflow and Lightdash
then run their own schema migrations. The Airflow init container writes the SimpleAuthManager
password file before startup, so the UI password is never generated or logged by Airflow.

Populate notification connections independently:

```bash
aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/slack_api_default \
  --secret-string 'file://<0600-temporary-slack-connection.json>'

aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/smtp_default \
  --secret-string 'file://<0600-temporary-smtp-connection.json>'
```

OCR provider credentials remain OCR-owned:

```bash
aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/ocr/providers/modal \
  --secret-string 'file://<0600-temporary-modal.json>'
```

The five PostgreSQL, Airflow, and Lightdash credentials are generated locally by the idempotent
Make targets. Slack, SMTP, and Modal are the only operator-supplied secret values. No secret or
provider credential belongs in tfvars, GitHub variables, Compose, a command-line literal, or a
repository `.env` file. Delete each temporary file immediately after Secrets Manager accepts it.
The Modal OCR schema is `{"token_id":"...","token_secret":"..."}`.

## Component releases

ECR repositories are immutable and retain the newest 20 releases. After a change reaches `main`,
GitHub publishes the changed component immediately under the exact main-branch OIDC subject. The
subsequent OCI deployment waits for approval from the protected `dev` environment. AWS and
Tailscale access use OIDC; there are no long-lived CI credentials.

After applying AWS and Tailscale, apply the isolated GitHub root with a fine-grained token restricted
to this repository and granted `Administration: write`, `Environments: write`, and
`Variables: write` (`Metadata: read` is automatic):

```bash
read -rsp 'GitHub token: ' GITHUB_TOKEN
printf '\n'
export GITHUB_TOKEN
make github-plan
make github-apply
unset GITHUB_TOKEN
```

The repository-level variables are `AWS_IMAGE_PUBLISHER_ROLE_ARN`,
`AWS_EMR_PUBLISHER_ROLE_ARN`, `EMR_ARTIFACTS_URI`, and `EMR_CODE_PARAMETER_NAME`; they let the exact
main-branch OIDC subject publish dev artifacts without environment approval. Only
`TAILSCALE_CLIENT_ID` and `TAILSCALE_AUDIENCE` belong to the protected `dev` environment because
they are used to deploy OCI services. Terraform reads every value directly from the AWS and
Tailscale remote states and owns the `dev` environment, owner reviewer, and exact `main` deployment
policy. The token is provider authentication only: keep it in the process environment, never in
tfvars or Terraform state. A GitHub App can replace the local token if repository administration is
automated later.

Main-branch GitHub workflows are the only release publishers. Image rollback accepts an immutable
digest and verifies that it is the image tagged by the reviewed Git revision. EMR rollback accepts a
reviewed revision only after its completed checksum manifest is found. There are no duplicate human
image or EMR publishing targets to drift from CI. CI streams only the selected deployment files
over Tailscale SSH; the services host never clones the monorepo or invokes its root Makefile.

No release uses `latest`, and there is no global service manifest. Component path filters cover
only files that affect that component; changing a shared CI helper does not rebuild unrelated
images. Protected component rollback restores a verified image/deployment pair on OCI; EMR rollback
only restores a completed release pointer and therefore does not enter the OCI environment. Airflow,
Inspector, and Lightdash reconcile their own Compose services; each database-backed app reconciles
only its PostgreSQL database. The dbt and OCR deployments advance their stable
host-local aliases only after pulling an exact digest, so DAG files never contain image SHAs.

Airflow uses the official versioned `GitDagBundle` for `automation/airflow/bundle`. A DAG-only merge is
validated by the focused bundle CI, then fetched without rebuilding or restarting Airflow. Active
and retried runs keep their original Git commit by default. A new Airflow/provider dependency still
requires publishing the compatible runtime image first.

For unpublished local iteration:

```bash
make images-build
make airflow-up
make arxiv-inspector-up
make lightdash-up
```

Only the Airflow scheduler receives the Docker socket because `LocalExecutor` launches task
containers there. Task logs are written to the SSM-configured KMS-encrypted S3 prefix and expire
after 30 days in dev; container recreation does not erase completed task logs.
