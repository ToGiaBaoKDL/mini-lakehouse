# Metadata PostgreSQL

Shared metadata database for self-hosted control-plane services. Docker Compose owns
the process and persistent volume; Terraform owns only the workload identity and
secret containers in AWS.

The bootstrap job is idempotent. It creates or reconciles the Airflow role and
database without dropping existing metadata.
