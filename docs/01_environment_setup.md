# Environment setup

Python 3.13 is pinned in `.python-version`. The project is an installable uv package using the
`src` layout and `uv_build`; no `PYTHONPATH` or `sys.path` mutation is required.

```bash
make setup
uv sync --frozen --all-extras --all-groups
```

Configuration uses nested Pydantic settings with the `LAKEHOUSE_` prefix and `__` delimiter. For
example, `LAKEHOUSE_STORAGE__LANDING_URI` maps to `settings.storage.landing_uri`. Compose mounts
Python workload credentials under `/run/secrets`, and `pydantic-settings` reads the same nested
names without a custom secret adapter. The local `.env` is only the ignored source for those
development secrets.
Storage declares independent
`LAKEHOUSE_STORAGE__ENDPOINTS__EXTERNAL_URL` and
`LAKEHOUSE_STORAGE__ENDPOINTS__INTERNAL_URL` values. Host processes select the external endpoint;
container workloads select the internal endpoint through
`LAKEHOUSE_STORAGE__NETWORK_SCOPE`. Native cloud S3 may leave both custom endpoints unset.
dbt reads Trino connection keys from `LAKEHOUSE_TRINO__*` and the shared catalog name from
`LAKEHOUSE_POLARIS__CATALOG_NAME`, so it does not introduce separate
`DBT_CATALOG`/`TRINO_HOST` settings.
Tracked container-only routing is split by ownership:
`infra/config/platform.container.env` contains the container network scope and internal Polaris
routes, while
`infra/config/orchestration.container.env` contains Trino/dbt/Prefect routes. Lifecycle URIs,
storage endpoints, and catalog names remain runtime configuration; secret values are granted only
to the Compose services that consume them.

Stable non-secret platform definitions live under `contracts/`. Validate contracts and settings
before starting services:

```bash
make validate
```

Start the core data plane:

```bash
make up-core
make platform-validate
make ps
make smoke-core
```

Compose files follow the `compose.<module>.yaml` convention. `compose.core.yaml` is the required
data plane, and `compose.prefect.yaml` adds orchestration. A BI overlay should only be added when
Lightdash is configured as a real service:

```bash
make up
```

Compose pins tested service versions rather than following mutable `latest` tags. The current
compatibility set is PostgreSQL 18.4, AIStor `RELEASE.2026-06-06T02-44-06Z`, AIStor Client
`RELEASE.2026-04-21T04-26-49Z`, Polaris 1.6.0, Trino 483, Redis 8.8.0, and Prefect 3.7.8 on
Python 3.12. The application image pins Python 3.13.14 and uv 0.11.30. A deliberate maintenance
change should update these versions together, run `make check`, then run the disposable integration
suite and deployment smoke checks; deployments must not pull a new major implicitly.

AIStor mounts the ignored local `minio.license` read-only at `/minio.license`. `make preflight`
rejects a missing or empty environment/license file before deployment, and the object-store
provisioner verifies the active license before creating any missing lifecycle buckets. The bucket
names are derived from the three configured lifecycle URIs. The same provisioner uses the pinned
official `mc` client to
apply dedicated workload users and least-privilege bucket policies. Root storage credentials are
not passed to Polaris, Trino, Prefect, platform administration, or OCR Review. The license and all
credentials remain local runtime configuration and are never committed to the repository.

`make down` and `make restart` preserve the named `object-store-data`, `postgres-data`,
`trino-data`, and `redis-data` volumes. `make clean` destroys that state. `make reset` performs the
destructive cleanup, builds all local images, starts the clean core plane, and runs the idempotent
platform bootstrap. PostgreSQL major upgrades additionally require an explicit database migration
when a volume is retained.

Only S3 dependencies are installed. There is no GCS compatibility package or inactive runtime
branch in this project.

The local Polaris metastore is PostgreSQL-backed. `polaris-bootstrap` only initializes the realm
and root principal. `object-store-provision` owns physical buckets and local AIStor workload
access; it does not create Iceberg or Polaris metadata.
The operations-profile `platform-admin` container owns catalog administration. Normal startup runs
the bootstrap after core services become healthy, then live validation verifies catalog
configuration, namespaces, grants, landing/curated Iceberg tables, policies, and mappings:

```bash
make platform-bootstrap
make platform-validate
```

Both commands require the tracked admin guard mounted read-only in the one-shot container. The
bootstrap creates missing resources and updates only safe mutable metadata; incompatible schema,
partition, location, format, or policy-type drift fails and requires an explicit migration. No
package is installed dynamically when a container starts.
