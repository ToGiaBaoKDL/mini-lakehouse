# Local infrastructure

This folder contains only tracked, non-secret configuration mounted by the Compose modules.

| Path | Owner | Responsibility |
|---|---|---|
| `config/platform.container.env` | Data Platform | Container-internal storage and Polaris routes |
| `config/orchestration.container.env` | Orchestration | Container-internal Trino and dbt routes |
| `config/ocr-review.container.env` | OCR Review | Read-only application identity and Trino route |
| `object-store/lifecycle-buckets.sh` | Storage Platform | Provision or read-only verify configured lifecycle buckets |
| `polaris/bootstrap.sh` | Data Platform | Idempotent realm/root-principal initialization only |
| `postgres/init.sql` | Data Platform | Local Polaris and Prefect database creation |
| `trino/etc/` | Query Platform | Single-node local Trino and the `prod` Iceberg REST catalog |

Runtime values and secrets come from the ignored `.env`; the tracked env files must not contain
credentials. `object-store-provision` derives physical bucket names from lifecycle URIs.
`platform-reconcile` owns catalog, namespace, access, and desired policy convergence. Policy
deletion is never part of normal startup and requires an exact reviewed plan hash.

Long-running Compose services use `restart: unless-stopped`, so Docker restores the data,
control, and review planes together after a host restart. Bootstrap, provision, reconcile, and
deployment containers remain one-shot jobs with `restart: "no"`; `make start` runs them in
dependency order when declarative state needs to be reconciled.

This is a local deployment topology. Production should supply unique Trino node identities,
managed PostgreSQL/object storage, workload credentials, TLS, backups, and immutable image
digests through its deployment platform.
