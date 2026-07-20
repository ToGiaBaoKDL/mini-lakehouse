import trino
from prefect import flow, task

from mini_lakehouse.config import get_settings
from mini_lakehouse.platform.maintenance import MaintenancePlanItem, build_maintenance_plan
from mini_lakehouse.platform.polaris import (
    PolarisPolicyClient,
    create_retry_session,
    request_oauth_token,
)
from mini_lakehouse.storage.iceberg import load_prod_catalog


@task(name="gov_discover_iceberg_maintenance")
def discover_maintenance_plan() -> list[MaintenancePlanItem]:
    settings = get_settings()
    session = create_retry_session()
    token = request_oauth_token(session, settings)
    return build_maintenance_plan(
        load_prod_catalog(settings),
        PolarisPolicyClient(session, settings, token),
        settings.trino.catalog,
    )


@task(name="gov_maintain_iceberg_table", retries=1, retry_delay_seconds=60)
def maintain_table(plan: MaintenancePlanItem) -> None:
    settings = get_settings()
    connection = trino.dbapi.connect(
        host=settings.trino.host,
        port=settings.trino.port,
        user=settings.trino.user,
        http_scheme=settings.trino.http_scheme,
    )
    try:
        cursor = connection.cursor()
        for statement in plan.statements:
            cursor.execute(statement)
            cursor.fetchall()
    finally:
        connection.close()


@flow(name="gov_iceberg_maintenance")
def gov_iceberg_maintenance() -> None:
    for plan in discover_maintenance_plan():
        maintain_table(plan)
