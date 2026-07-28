# Local infrastructure

This folder contains only tracked, non-secret configuration mounted by the Compose modules.

| Path | Owner | Responsibility |
|---|---|---|
| `config/platform.container.env` | Data Platform | Container network scope and internal Polaris routes |
| `config/platform-admin.guard` | Data Platform | Capability marker mounted only into the admin job |
| `config/orchestration.container.env` | Orchestration | Container-internal Trino and dbt routes |
| `config/ocr-review.container.env` | OCR Review | Read-only application identity and Trino route |
| `object-store/provision.sh` | Storage Platform | Provision buckets and workload-scoped AIStor users/policies, or verify them read-only |
| `polaris/bootstrap.sh` | Data Platform | Normalize the pinned admin tool's already-bootstrapped exit into an idempotent result |
| `postgres/init.sql` | Data Platform | Local Polaris and Prefect database creation |
| `trino/etc/` | Query Platform | Single-node local Trino and the `prod` Iceberg REST catalog |

Runtime values and local secret sources come from the ignored `.env`; Compose fails before startup
when a required credential is absent. Python application containers receive credentials as
files under `/run/secrets`, parsed directly by `pydantic-settings`. The tracked env files contain
stable container routes only
and must not contain credentials, storage endpoints, catalog names, or environment-specific
values. `object-store-provision` derives both its endpoint and physical bucket names from storage
settings, then uses the pinned official `mc` client to apply workload users and bucket-scoped
policies. AIStor root credentials are confined to the object store and this one-shot provisioner.
`platform-admin` is an operations-profile container for idempotent bootstrap and read-only live
validation of catalog, namespace, access, landing/curated Iceberg table, and policy state. Host
execution cannot mutate platform state because administration requires the container marker and
dedicated guard mount. Polaris receives the host-facing object-store endpoint separately from its
container-internal endpoint; neither is inferred from the other.
Only `platform-admin` receives the Polaris root credential. Prefect uses the contract-managed
`prefect_ingestion` principal for direct Iceberg access, while Trino uses the independent
`trino_engine` principal for its REST catalog. Applications querying through Trino receive no
Polaris credential. `contracts/access.yaml` owns only non-secret identities and RBAC; secret
delivery and explicit rotation remain deployment operations.
Storage credentials are independent from Polaris credentials: Polaris, platform administration,
Prefect ingestion, Trino, and OCR Review each receive a dedicated AIStor identity. OCR Review can
read only curated objects. Prefect can read/write landing and curated objects and read analytics
objects for governance discovery, but it cannot write analytics. Platform components that
maintain Iceberg state can read/write all lifecycle buckets.
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
