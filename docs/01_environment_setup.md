# Environment setup

Use the AWS SDK credential chain: an SSO/named profile locally and workload roles in deployed
environments. `.env` contains local process settings and named AWS profiles, never access keys or
AWS resource identifiers. Runtime resource references are read from SSM Parameter Store.

Terraform state uses a separate, versioned S3 bootstrap bucket with native lock files. The dev
environment uses random-suffixed data buckets and can be destroyed; a future production environment
should set `force_destroy = false`, use production trusted principals, and retain the same modules.

Only Airflow and Document Inspector are composed locally. S3, Glue, EMR Serverless, Athena, KMS,
IAM, and Secrets Manager are AWS services.

Terraform creates the Airflow connection secret containers but intentionally does not write their
values into Terraform state. After `make terraform-apply`, populate each connection with an Airflow
connection URI or JSON using an administrator profile:

```bash
aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/slack_api_default \
  --secret-string '<Airflow Slack connection JSON>'

aws secretsmanager put-secret-value \
  --secret-id lakehouse/dev/airflow/connections/smtp_default \
  --secret-string '<Airflow SMTP connection JSON>'
```

Airflow reads these values through its AWS Secrets Manager backend. Rotate them with another
`put-secret-value`; do not add secret values to `.env`, Terraform variables, or Git.
