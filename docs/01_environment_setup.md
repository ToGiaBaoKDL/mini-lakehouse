# Environment setup

Use the AWS SDK credential chain: an SSO/named profile locally and workload roles in deployed
environments. `.env` contains resource identifiers and local Airflow metadata settings, never
access keys.

Terraform state uses a separate, versioned S3 bootstrap bucket with native lock files. The dev
environment uses random-suffixed data buckets and can be destroyed; a future production environment
should set `force_destroy = false`, use production trusted principals, and retain the same modules.

Only Airflow and Document Inspector are composed locally. S3, Glue, EMR Serverless, Athena, KMS,
IAM, and Secrets Manager are AWS services.
