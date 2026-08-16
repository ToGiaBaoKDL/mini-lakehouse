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

## Backups

The services host runs a twice-daily systemd timer (02:30 and 14:30 UTC, jittered) that dumps each
metadata database with `pg_dump --format=custom` and uploads the dump plus a SHA-256 completion
marker under `metadata-postgres/<database>/<utc-date>/<am|pm>/` in the environment's backup bucket,
KMS-encrypted with the lakehouse key. The completion marker is uploaded last, so a run is only
treated as complete when both objects exist; re-running within the same 12-hour slot skips completed
databases. The bucket retains dumps for 35 days through its S3 lifecycle rule, giving roughly two
weeks of hourly-granularity recovery choice. The `metadata-postgres` workload identity is the only
principal with write access, limited to the `metadata-postgres/` prefix, and it cannot delete
existing backups.

Restore replaces one application database from an exact slot backup:

```bash
make metadata-postgres-restore ARGS='lightdash 2026-08-15 pm'
```

The script downloads the dump, verifies its checksum, drops and recreates the database owned by its
application role, and restores with `pg_restore --no-owner`. Run it against a bootstrapped metadata
cluster; the owning service's connection pool must tolerate the drop (stop the service first when in
doubt).
