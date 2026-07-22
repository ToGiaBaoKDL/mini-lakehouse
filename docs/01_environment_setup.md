# Environment setup

Python 3.13 is pinned in `.python-version`. The project is an installable uv package using the
`src` layout and `uv_build`; no `PYTHONPATH` or `sys.path` mutation is required.

```bash
cp .env.example .env
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
uv run lakehouse validate
```

Start the core data plane:

```bash
docker compose -f compose.core.yaml up -d --build
docker compose -f compose.core.yaml ps
```

Compose files follow the `compose.<module>.yaml` convention. `compose.core.yaml` is the required
data plane, and `compose.prefect.yaml` adds orchestration. A BI overlay should only be added when
Lightdash is configured as a real service:

```bash
docker compose -f compose.core.yaml -f compose.prefect.yaml up -d --build
```

Compose uses floating latest service images by current project policy. Prefect is the one explicit
major-family exception: its official current image is `prefecthq/prefect:3-latest`, while the bare
`latest` tag resolves to the legacy major line. CI and the local integration stack are therefore
compatibility gates. A managed production rollout should still promote tested digests between
environments even while this repository deliberately tracks latest tags.

Only S3 dependencies are installed. There is no GCS compatibility package or inactive runtime
branch in this project.

The local Polaris metastore is PostgreSQL-backed. `polaris-admin` bootstraps its realm idempotently,
then the application bootstrap creates catalog `prod` and the governed namespace locations. No
package is installed dynamically when a container starts.
