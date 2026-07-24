# Environment setup

Python 3.13 is pinned in `.python-version`. The project is an installable uv package using the
`src` layout and `uv_build`; no `PYTHONPATH` or `sys.path` mutation is required.

```bash
make setup
uv sync --frozen --all-extras --all-groups
```

Configuration uses nested Pydantic settings with the `LAKEHOUSE_` prefix and `__` delimiter. For
example, `LAKEHOUSE_STORAGE__LANDING_URI` maps to `settings.storage.landing_uri`. Secrets may be
mounted under `/run/secrets` in a managed deployment. The local `.env` is ignored by Git.
dbt's profile reads the same `LAKEHOUSE_TRINO__*` keys as Python, so catalog and Trino endpoints
do not have a second `DBT_CATALOG`/`TRINO_HOST` source of truth.

Stable desired state is separate from runtime settings and lives under `contracts/`. Validate both
layers before starting services:

```bash
make validate
```

Start the core data plane:

```bash
make up-core
make ps
make smoke
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
Python 3.12. The application image pins Python 3.13.14 and uv 0.11.30. Dependabot or a deliberate
maintenance change should update these versions together and rerun the end-to-end compatibility
gate; deployments must not pull a new major implicitly.

AIStor mounts the ignored local `minio.license` read-only at `/minio.license`. `make preflight`
rejects a missing or empty environment/license file before deployment, and the object-store
bootstrap verifies the active license before creating the `landing`, `curated`, and `analytics`
buckets. The GitHub Actions end-to-end job reads the same license content from the protected
repository secret `AISTOR_LICENSE`; fork pull requests do not receive or execute with that secret.

`make down` and `make restart` preserve the named `object-store-data`, `postgres-data`,
`trino-data`, and `redis-data` volumes. `make clean` destroys that state, while `make reset`
performs the destructive cleanup and then deploys a fresh stack. PostgreSQL major upgrades
additionally require a planned database migration when a volume is retained.

Only S3 dependencies are installed. There is no GCS compatibility package or inactive runtime
branch in this project.

The local Polaris metastore is PostgreSQL-backed. `polaris-admin` bootstraps its realm idempotently,
then the application bootstrap creates catalog `prod` and the governed namespace locations. No
package is installed dynamically when a container starts.
