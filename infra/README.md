# Local infrastructure

This folder contains only tracked, non-secret configuration mounted by the Compose modules.

| Path | Owner | Responsibility |
|---|---|---|
| `config/platform.container.env` | Data Platform | Container network scope and internal Polaris routes |
| `config/platform-admin.guard` | Data Platform | Capability marker mounted only into the admin job |
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
`platform-admin` is an operations-profile container for idempotent bootstrap and read-only live
validation of catalog, namespace, access, landing/curated Iceberg table, and policy state. Host
execution cannot mutate platform state because administration requires the container marker and
dedicated guard mount. Polaris receives the host-facing object-store endpoint separately from its
container-internal endpoint; neither is inferred from the other.
The bootstrap wrapper delegates all work to the pinned official admin tool. It handles only the
verified exit-code-3 result for an existing realm, which keeps restart-with-preserved-volumes
idempotent; every other result is returned unchanged.

Long-running Compose services use `restart: unless-stopped`, so Docker restores the data, control,
and review planes together after a host restart. Realm bootstrap, bucket provision, platform
administration, and deployment containers remain one-shot jobs with `restart: "no"`. The Make
startup commands run platform bootstrap once core services are healthy.

`make reset` builds all local images, starts a clean core plane, and bootstraps its contract-managed
resources. Re-running bootstrap is a no-op when the live platform is current. Incompatible changes
fail and are handled as explicit migrations; destructive policy cleanup remains separately
reviewed through `policy-prune-*`.

This is a local deployment topology. Production should supply unique Trino node identities,
managed PostgreSQL/object storage, workload credentials, TLS, backups, and immutable image
digests through its deployment platform.
