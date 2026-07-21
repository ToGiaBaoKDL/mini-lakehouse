from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict
from pyiceberg.catalog import Catalog

from mini_lakehouse.platform.polaris import PolarisPolicyClient
from mini_lakehouse.platform.policies import maintenance_statements
from mini_lakehouse.storage.iceberg import discover_tables


class MaintenancePlanItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str
    statements: tuple[str, ...]


def collect_maintenance_results(
    pending: Sequence[tuple[MaintenancePlanItem, Callable[..., int]]],
) -> tuple[int, int, list[str]]:
    """Resolve a submitted batch without abandoning successful sibling tables."""
    completed = 0
    statements = 0
    failures: list[str] = []
    for plan, result in pending:
        try:
            statements += result()
            completed += 1
        except Exception as error:
            failures.append(f"{plan.table}: {error}")
    return completed, statements, failures


def build_maintenance_plan(
    catalog: Catalog,
    policy_client: PolarisPolicyClient,
    trino_catalog: str,
) -> list[MaintenancePlanItem]:
    plan: list[MaintenancePlanItem] = []
    for table in sorted(discover_tables(catalog), key=lambda item: item.iceberg):
        statements = maintenance_statements(
            table,
            trino_catalog,
            policy_client.applicable_policies(table),
        )
        if statements:
            plan.append(
                MaintenancePlanItem(
                    table=table.trino(trino_catalog),
                    statements=statements,
                )
            )
    return plan
