# Environment setup

Use an SSO/named profile for operator commands and IAM Roles Anywhere for self-hosted workloads.
The repository does not use `.env` files. Secrets Manager owns credentials and
cryptographic material, Parameter Store owns runtime resource references, and the local
Makefile/Compose boundary supplies stable development defaults.

Terraform state uses a separate, versioned S3 bootstrap bucket with native lock files. The dev
environment uses explicit unique data-bucket names and can be destroyed; a future production
environment should set `force_destroy = false`, use production trusted principals, and retain the
same modules.

Only Airflow, Postgres, and ArXiv Inspector are composed on the OCI services host. S3, Glue, EMR
Serverless, Athena, KMS, IAM, and Secrets Manager remain AWS services. OCI exposes no public
ingress; Tailscale carries SSH and application traffic.

Operator commands default to the `lakehouse-dev-*` assume-role profiles. Runtime containers do
not inherit these profiles or mount `$HOME/.aws`; each receives a certificate-backed bundle from
`AWS_IDENTITY_DIR`:

```bash
make workload-pki-init
make aws-apply
make workload-identities-render
CATALOG_ADMIN_AWS_PROFILE=custom-catalog make catalog-apply
DBT_AWS_PROFILE=custom-dbt make dbt-build
EMR_DEPLOYER_AWS_PROFILE=custom-deployer make emr-jobs-publish
IMAGE_PUBLISHER_AWS_PROFILE=custom-publisher make images-publish
OCR_AWS_PROFILE=custom-ocr make ocr-kaggle-runner-publish
```

These selectors are not credentials. Airflow, dbt tasks, OCR tasks, Inspector, and the
services deployer each exchange their own X.509 certificate for short-lived credentials.
The CA private key stays on the administrator machine; copy only the five workload directories to
the services host.

Terraform creates one Airflow bootstrap secret and the Airflow connection secret containers, but
intentionally does not write their values into Terraform state. Populate the bootstrap secret with
one JSON object:

```json
{
  "version": 1,
  "database_password": "<random password>",
  "fernet_key": "<Airflow Fernet key>",
  "jwt_secret": "<random JWT secret>"
}
```

`make airflow-up` reads this object using the services deployer identity and passes it to Compose
as three service-scoped files. Repeating the command is read-only and idempotent. Initialize the
value once without printing it or storing it in Terraform state:

```bash
AWS_PROFILE=your-terraform-admin make airflow-bootstrap-init
```

The initialization target is also idempotent: it exits without creating a new Secrets Manager
version when an `AWSCURRENT` version already exists. Populate each external connection with an
Airflow connection JSON using an administrator profile:

```bash
aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/slack_api_default \
  --secret-string '<Airflow Slack connection JSON>'

aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/smtp_default \
  --secret-string '<Airflow SMTP connection JSON>'
```

Airflow reads connection values through its AWS Secrets Manager backend. Rotate them with another
`put-secret-value`; do not add secret values to local configuration, Terraform variables, or Git.

OCR provider credentials use OCR-owned secrets instead of Airflow connections:

```bash
aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/ocr/providers/kaggle \
  --secret-string '{"username":"<kaggle-user>","api_token":"<token>"}'

aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/ocr/providers/modal \
  --secret-string '{"token_id":"<token-id>","token_secret":"<token-secret>"}'
```

## Local service releases

Terraform creates separate immutable ECR repositories for Airflow, dbt tasks, ArXiv Inspector, and
the OCR worker. The image publisher role is the only local workload role that can push to them.
Publish a clean commit, then pull and run that exact release on the Tailscale host:

```bash
make images-publish
make release-deploy
```

Neither command uses `latest`. Published images include AMD64 and ARM64 manifests. Repository
lifecycle policies retain the newest 20 releases.
`release-deploy` pre-pulls every image before restarting services, so `DockerOperator` does not
need registry credentials at task time. For unpublished local iteration:

```bash
make images-build
make services-up
```

Only the Airflow scheduler receives the Docker socket because `LocalExecutor` launches task
containers there. The OCR DAG uses the stable local image name
`ocr-worker:runtime`; `release-deploy` moves that alias to the exact immutable
ECR release before Airflow starts, so DAG files never contain a release SHA.
