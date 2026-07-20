from collections.abc import Iterator

import trino
from prefect import flow, task

from mini_lakehouse.config import get_settings

MAINTAINED_TABLES = (
    'prod."curated.github".fct_github_events',
    'prod."curated.github".dim_github_actors',
    'prod."curated.github".dim_github_repositories',
    'prod."analytics.engineering".fct_repository_activity_daily',
    'prod."analytics.engineering".fct_contributor_activity_daily',
)


def _maintenance_statements(table: str) -> Iterator[str]:
    yield f"ALTER TABLE {table} EXECUTE optimize(file_size_threshold => '128MB')"
    yield f"ALTER TABLE {table} EXECUTE expire_snapshots(retention_threshold => '7d')"
    yield f"ALTER TABLE {table} EXECUTE remove_orphan_files(retention_threshold => '7d')"


@task(name="maintain-iceberg-table", retries=1, retry_delay_seconds=60)
def maintain_table(table: str) -> None:
    if table not in MAINTAINED_TABLES:
        raise ValueError(f"Table is not in the maintenance allowlist: {table}")
    settings = get_settings()
    connection = trino.dbapi.connect(
        host=settings.trino.host,
        port=settings.trino.port,
        user=settings.trino.user,
        http_scheme=settings.trino.http_scheme,
    )
    try:
        cursor = connection.cursor()
        for statement in _maintenance_statements(table):
            cursor.execute(statement)
            cursor.fetchall()
    finally:
        connection.close()


@flow(name="iceberg-maintenance")
def maintain_iceberg_tables() -> None:
    for table in MAINTAINED_TABLES:
        maintain_table(table)
