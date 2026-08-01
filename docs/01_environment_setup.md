# Environment setup

Use the AWS SDK credential chain: an SSO/named profile locally and workload roles in deployed
environments. The repository does not use `.env` files. Secrets Manager owns credentials and
cryptographic material, Parameter Store owns runtime resource references, and the local
Makefile/Compose boundary supplies stable development defaults.

Terraform state uses a separate, versioned S3 bootstrap bucket with native lock files. The dev
environment uses random-suffixed data buckets and can be destroyed; a future production environment
should set `force_destroy = false`, use production trusted principals, and retain the same modules.

Only Airflow and ArXiv Inspector are composed locally. S3, Glue, EMR Serverless, Athena, KMS,
IAM, and Secrets Manager are AWS services.

The local commands default to the `lakehouse-dev-*` assume-role profiles and the standard
`$HOME/.aws` configuration directory. Override a selector at the command boundary only when the
local AWS configuration uses a different name or location:

```bash
AWS_CONFIG_DIR="$HOME/.aws" make airflow-up
AIRFLOW_AWS_PROFILE=custom-airflow make airflow-up
CATALOG_ADMIN_AWS_PROFILE=custom-catalog make catalog-apply
DBT_AWS_PROFILE=custom-dbt make dbt-build
EMR_DEPLOYER_AWS_PROFILE=custom-deployer make emr-jobs-publish
IMAGE_PUBLISHER_AWS_PROFILE=custom-publisher make ecr-publish
ARXIV_INSPECTOR_AWS_PROFILE=custom-inspector make arxiv-inspector-up
OCR_AWS_PROFILE=custom-ocr make airflow-up
```

These selectors are not credentials. In a deployed runtime, attach the corresponding workload role
and omit named profiles entirely.

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

`make airflow-up` reads this object using the Airflow assume-role profile and passes it to Compose
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

Terraform creates separate immutable ECR repositories for Airflow, ArXiv Inspector, and the OCR
worker. The image publisher role is the only local workload role that can push to them. Publish a
clean commit, then pull and run that exact release locally:

```bash
make ecr-publish
make ecr-deploy
```

Neither command uses `latest`. Repository lifecycle policies retain the newest 20 releases.
`ecr-deploy` pre-pulls every image before restarting services, so `DockerOperator` does not
need registry credentials at task time. For unpublished local iteration:

```bash
make images-build
make services-up
```

Only the Airflow scheduler receives the Docker socket because `LocalExecutor` launches task
containers there. The OCR DAG uses the stable local image name
`ocr-worker:runtime`; `ecr-deploy` moves that alias to the exact immutable
ECR release before Airflow starts, so DAG files never contain a release SHA.
