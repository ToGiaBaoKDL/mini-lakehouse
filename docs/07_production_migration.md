# Production migration runbook

The authoritative state is split across two systems and must move as one logical checkpoint:

- AIStor/S3 contains every landing object and every Iceberg data, manifest, and metadata file.
- PostgreSQL contains the Polaris catalog pointers and Prefect orchestration state.

Redis data and Trino local data are disposable messaging, cache, and spill state. Do not restore
them during a platform migration. Secrets, AIStor licenses, IAM policies, DNS, and runtime
configuration belong in the deployment secret/configuration system, not in a data-volume backup.

## Preferred strategy: blue/green

1. Read the target AIStor, Polaris, Trino, and Prefect migration notes. Prove the exact version set
   and the restore procedure in a staging clone before touching production.
2. Provision the green stack with empty databases and storage. Start continuous object replication
   and PostgreSQL physical/WAL replication, or take an initial full copy while blue still serves
   traffic.
3. Pause Prefect schedules, stop ingestion/transformation/maintenance workers, and wait for active
   flow runs to reach terminal states. Block every other Iceberg writer.
4. With writers quiesced, finish the object-store delta and take the final consistent PostgreSQL
   checkpoint. Retain the original object store and database together as the rollback checkpoint.
5. Restore objects first, preserving bucket names and object keys. Restore Polaris and Prefect
   PostgreSQL databases second, then configure the green endpoint, credentials, and license.
6. Validate green read-only: bucket/object counts and checksums, Polaris catalog/namespaces,
   current Iceberg snapshot IDs, metadata-file availability, representative row counts, maximum
   business timestamps, and critical aggregate/hash comparisons.
7. Cut over the object-store/API endpoints atomically, run `make smoke`, enable workers, and resume
   schedules. Monitor failed commits, background-service lag, catalog errors, and data freshness.
8. Keep blue read-only for the agreed rollback window. Delete it only after backup-restore evidence
   and business validation have been signed off.

Never copy a live Docker volume as the production backup. Never restore Polaris independently from
its matching objects: the catalog may reference metadata files that do not exist. If an upgrade can
change the AIStor on-disk format or Polaris schema, do not start the new binary against the only
copy; rollback means restoring both the old object checkpoint and old database checkpoint.

For routine recoverability, enable object versioning/replication and PostgreSQL point-in-time
recovery, store backups in a separate failure domain, encrypt them, and run scheduled restore
drills. A backup that has not passed a restore drill is not a verified backup.
