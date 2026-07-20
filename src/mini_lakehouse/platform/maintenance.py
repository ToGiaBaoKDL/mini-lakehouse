from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict
from pyiceberg.catalog import Catalog

from mini_lakehouse.contracts import TableIdentifier
from mini_lakehouse.platform.polaris import PolarisPolicyClient
from mini_lakehouse.platform.policies import maintenance_statements


class MaintenancePlanItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str
    statements: tuple[str, ...]


def walk_namespaces(catalog: Catalog) -> Iterator[tuple[str, ...]]:
    pending = list(catalog.list_namespaces())
    seen: set[tuple[str, ...]] = set()
    while pending:
        namespace = pending.pop()
        if namespace in seen:
            continue
        seen.add(namespace)
        yield namespace
        pending.extend(catalog.list_namespaces(namespace))


def discover_tables(catalog: Catalog) -> Iterator[TableIdentifier]:
    for namespace in walk_namespaces(catalog):
        for identifier in catalog.list_tables(namespace):
            yield TableIdentifier.from_iceberg(identifier)


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
