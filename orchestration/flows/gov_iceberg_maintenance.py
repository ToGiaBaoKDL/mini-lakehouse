import trino
from prefect import flow, task

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.platform.maintenance import (
    MaintenancePlanItem,
    build_maintenance_plan,
    collect_maintenance_results,
)
from mini_lakehouse.platform.polaris import (
    PolarisPolicyClient,
    create_retry_session,
    request_oauth_token,
)
from mini_lakehouse.storage.iceberg import load_iceberg_catalog
from orchestration.plugins.notifications import (
    notify_flow_failure,
    notify_flow_running,
    notify_flow_success,
    notify_task_failure,
)
from orchestration.utils.retries import MAINTENANCE_RETRY_DELAY_SECONDS


@task(
    name="gov_discover_iceberg_maintenance",
    task_run_name="discover-iceberg-maintenance-plan",
    on_failure=[notify_task_failure],
)
def discover_maintenance_plan() -> list[MaintenancePlanItem]:
    settings = get_settings()
    with create_retry_session() as session, load_iceberg_catalog(settings) as catalog:
        token = request_oauth_token(session, settings)
        return build_maintenance_plan(
            catalog,
            PolarisPolicyClient(session, settings, token),
            settings.trino.catalog,
            load_contracts(settings.contracts_dir),
        )


@task(
    name="gov_maintain_iceberg_table",
    task_run_name="maintain-{plan.table}",
    retries=1,
    retry_delay_seconds=MAINTENANCE_RETRY_DELAY_SECONDS,
    on_failure=[notify_task_failure],
)
def maintain_table(plan: MaintenancePlanItem) -> int:
    settings = get_settings()
    connection = trino.dbapi.connect(
        host=settings.trino.host,
        port=settings.trino.port,
        user=settings.trino.user,
        http_scheme=settings.trino.http_scheme,
    )
    try:
        cursor = connection.cursor()
        try:
            for statement in plan.statements:
                cursor.execute(statement)
                cursor.fetchall()
            return len(plan.statements)
        finally:
            cursor.close()
    finally:
        connection.close()


@flow(
    name="gov_iceberg_maintenance",
    on_running=[notify_flow_running],
    on_completion=[notify_flow_success],
    on_failure=[notify_flow_failure],
    on_cancellation=[notify_flow_failure],
    on_crashed=[notify_flow_failure],
)
def gov_iceberg_maintenance(max_concurrency: int = 4) -> dict[str, int]:
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least one")
    plans = discover_maintenance_plan()
    completed = 0
    statements = 0
    failures: list[str] = []
    for offset in range(0, len(plans), max_concurrency):
        batch = plans[offset : offset + max_concurrency]
        pending = [(plan, maintain_table.submit(plan).result) for plan in batch]
        batch_completed, batch_statements, batch_failures = collect_maintenance_results(pending)
        completed += batch_completed
        statements += batch_statements
        failures.extend(batch_failures)
    if failures:
        raise RuntimeError("Iceberg maintenance failed for: " + "; ".join(failures))
    return {
        "tables_discovered": len(plans),
        "tables_completed": completed,
        "statements_executed": statements,
    }
