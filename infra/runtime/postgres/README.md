# Metadata PostgreSQL

Shared metadata database for self-hosted platform services. Docker Compose owns the process and
persistent volume; Terraform owns only its workload identity and secret containers in AWS. Airflow
owns its database migrations, not this service.

The bootstrap job idempotently reconciles the Airflow role and database without dropping metadata.
Initial secret generation and deployment order belong to the canonical
[infrastructure runbook](../../README.md).
