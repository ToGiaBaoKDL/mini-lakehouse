# Local infrastructure

This folder contains only tracked, non-secret configuration mounted by the Compose modules.

| Path | Owner | Responsibility |
|---|---|---|
| `config/platform.container.env` | Data Platform | Container network scope and internal Polaris routes |
| `config/platform-reconcile.guard` | Data Platform | Capability marker mounted only into the reconcile job |
| `config/orchestration.container.env` | Orchestration | Container-internal Trino and dbt routes |
| `config/ocr-review.container.env` | OCR Review | Read-only application identity and Trino route |
| `object-store/lifecycle-buckets.sh` | Storage Platform | Provision or read-only verify configured lifecycle buckets |
| `polaris/bootstrap.sh` | Data Platform | Normalize the pinned admin tool's already-bootstrapped exit into an idempotent result |
| `postgres/init.sql` | Data Platform | Local Polaris and Prefect database creation |
| `trino/etc/` | Query Platform | Single-node local Trino and the `prod` Iceberg REST catalog |

Runtime values and secrets come from the ignored `.env`; Compose fails before startup when a
required local credential is absent. The tracked env files contain stable container routes only
and must not contain credentials, storage endpoints, catalog names, or environment-specific
values. `object-store-provision` derives both its endpoint and physical bucket names from storage
settings.
`platform-reconcile` owns catalog, namespace, access, landing/curated Iceberg table, and desired
policy convergence. Policy deletion is never part of normal startup and requires an exact reviewed
plan hash. Host execution cannot mutate platform state because reconciliation requires both a
container marker and the dedicated guard mount. Polaris receives the host-facing object-store
endpoint separately from its container-internal endpoint; neither is inferred from the other.
The bootstrap wrapper delegates all work to the pinned official admin tool. It handles only the
verified exit-code-3 result for an existing realm, which keeps restart-with-preserved-volumes
idempotent; every other result is returned unchanged.

Long-running Compose services use `restart: unless-stopped`, so Docker restores the data,
control, and review planes together after a host restart. Bootstrap, provision, reconcile, and
deployment containers remain one-shot jobs with `restart: "no"`; `make start` runs them in
dependency order when declarative state needs to be reconciled.

This is a local deployment topology. Production should supply unique Trino node identities,
managed PostgreSQL/object storage, workload credentials, TLS, backups, and immutable image
digests through its deployment platform.
