# Metadata PostgreSQL

Shared metadata database for self-hosted platform services. Docker Compose owns the process and
persistent volume; Terraform owns only its workload identity and secret containers in AWS. Airflow
and Lightdash own their database migrations, not this service.

The bootstrap job idempotently reconciles only the database requested by its owning application;
deploying Airflow never requires the Lightdash credential, and vice versa. It never drops metadata.
The Lightdash bootstrap also enables its required `uuid-ossp` PostgreSQL extension before Lightdash
runs application migrations.
Initial secret generation and deployment order belong to the canonical
[infrastructure runbook](../../README.md).
