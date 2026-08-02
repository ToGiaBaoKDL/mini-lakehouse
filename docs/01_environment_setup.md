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

Operator commands default to the `lakehouse-dev-*` assume-role profiles. Terraform itself follows
the standard AWS/OCI provider credential chains instead of accepting profile names as variables.
Runtime containers do
not inherit these profiles or mount `$HOME/.aws`; each receives a certificate-backed bundle from
`AWS_IDENTITY_DIR`:

```bash
make workload-pki-init
make aws-apply
make workload-identities-render
CATALOG_ADMIN_AWS_PROFILE=custom-catalog make catalog-apply
EMR_DEPLOYER_AWS_PROFILE=custom-deployer make emr-jobs-publish
IMAGE_PUBLISHER_AWS_PROFILE=custom-publisher make images-publish
make dbt-build
make ocr-kaggle-runner-publish
```

These selectors are not credentials. Airflow, dbt tasks, OCR tasks, Inspector, and the
services deployer each exchange their own X.509 certificate for short-lived credentials.
The CA private key stays on the administrator machine. After provisioning the OCI host, transfer
only the five leaf bundles over the private network:

```bash
make workload-identities-install
```

Application entitlements and non-secret notification destinations are checked-in environment
policy in Terraform. `tfvars` contains only account/deployment identities and resource OCIDs; SSM
is the runtime distribution layer, not the authoring source for IAM policy.

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
Task logs are written to the SSM-configured, KMS-encrypted logs bucket and expire after 30 days in
dev. Container recreation therefore does not erase completed task logs.

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
the OCR worker. The image publisher operator role is the only identity that can push to them.
Publish a clean commit, then pull and run that exact release on the Tailscale host:

```bash
make images-publish
make release-deploy
```

Neither command uses `latest`. Published images include AMD64 and ARM64 manifests. Repository
lifecycle policies retain the newest 20 releases. Publishing updates the SSM release manifest only
after all images exist. `release-deploy` pre-pulls every image, waits for healthy services, advances
task aliases last, and rolls services back on failure. It does not read Terraform state, and
`DockerOperator` does not need registry credentials at task time. For unpublished local iteration:

```bash
make images-build
make services-up
```

Only the Airflow scheduler receives the Docker socket because `LocalExecutor` launches task
containers there. The OCR DAG uses the stable local image name
`ocr-worker:runtime`; `release-deploy` moves that alias only after the corresponding Airflow and
Inspector services are healthy, so DAG files never contain a release SHA.
