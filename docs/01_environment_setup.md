# Environment setup

Python 3.13 is pinned in `.python-version`. The project is an installable uv package using the
`src` layout and `uv_build`; no `PYTHONPATH` or `sys.path` mutation is required.

```bash
cp .env.example .env
uv sync --locked --all-extras --all-groups
```

Configuration uses nested Pydantic settings with the `LAKEHOUSE_` prefix and `__` delimiter. For
example, `LAKEHOUSE_STORAGE__LANDING_URI` maps to `settings.storage.landing_uri`. Secrets may be
mounted under `/run/secrets` in a managed deployment. The local `.env` is ignored by Git.

Start the core data plane:

```bash
docker compose -f compose.core.yaml up -d --build
docker compose -f compose.core.yaml ps
```

Compose files follow the `compose.<module>.yaml` convention. `compose.core.yaml` is the required
data plane, `compose.prefect.yaml` adds orchestration, and `compose.dashboard.yaml` adds
presentation:

```bash
docker compose -f compose.core.yaml -f compose.prefect.yaml up -d --build
docker compose -f compose.core.yaml -f compose.dashboard.yaml up -d --build
```

Compose uses floating latest service images by project policy. Prefect is the one explicit
major-family exception: its official current image is `prefecthq/prefect:3-latest`, while the bare
`latest` tag still resolves to legacy Prefect 1. This is convenient for this learning environment
but means CI is also a compatibility signal. A production deployment should promote tested
digests through environments even if local development follows latest tags.

Only S3 dependencies are installed. The object-store port remains behind an adapter boundary so a
future GCS implementation can be added deliberately without carrying `gcsfs` today.

The local Polaris metastore is PostgreSQL-backed. `polaris-admin` bootstraps its realm idempotently,
then the application bootstrap creates catalog `prod` and the governed namespace locations. No
package is installed dynamically when a container starts.
