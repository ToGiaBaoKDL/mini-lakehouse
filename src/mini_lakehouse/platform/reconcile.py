import logging
from collections.abc import Mapping
from time import sleep
from typing import Any, cast

from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError
from pyiceberg.table import Table

from mini_lakehouse.config import Settings, get_settings
from mini_lakehouse.contracts import (
    PlatformContracts,
    TableIdentifier,
    iceberg_schema,
    load_contracts,
    partition_spec,
)
from mini_lakehouse.logging import configure_logging
from mini_lakehouse.platform.desired_state import (
    DesiredCatalog,
    DesiredManagedTable,
    DesiredPlatformState,
    compile_desired_state,
)
from mini_lakehouse.platform.polaris import (
    PolarisManagementClient,
    PolarisPolicyClient,
    PolicyReconcileResult,
    create_retry_session,
    request_oauth_token,
)
from mini_lakehouse.storage.iceberg import (
    MANAGED_ICEBERG_FORMAT_VERSION,
    load_iceberg_catalog,
    managed_table_properties,
    validate_table_location,
)

logger = logging.getLogger(__name__)


def _catalog_drift(current_payload: object, desired: Mapping[str, Any]) -> list[str]:
    if not isinstance(current_payload, dict):
        return ["response is not a JSON object"]
    current = current_payload.get("catalog", current_payload)
    if not isinstance(current, dict):
        return ["response does not contain a catalog object"]
    drift: list[str] = []
    for key in ("name", "type"):
        if current.get(key) != desired[key]:
            drift.append(key)

    current_properties = current.get("properties")
    desired_properties = cast(dict[str, object], desired["properties"])
    if not isinstance(current_properties, dict):
        drift.append("properties")
    else:
        for key in sorted(current_properties.keys() | desired_properties.keys()):
            if current_properties.get(key) != desired_properties.get(key):
                drift.append(f"properties.{key}")

    current_storage = current.get("storageConfigInfo")
    desired_storage = cast(dict[str, object], desired["storageConfigInfo"])
    if not isinstance(current_storage, dict):
        drift.append("storageConfigInfo")
    else:
        for key in (
            "storageType",
            "endpoint",
            "endpointInternal",
            "pathStyleAccess",
            "region",
            "stsUnavailable",
            "kmsUnavailable",
        ):
            if current_storage.get(key) != desired_storage.get(key):
                drift.append(f"storageConfigInfo.{key}")
        current_locations = current_storage.get("allowedLocations")
        if not isinstance(current_locations, list) or set(current_locations) != set(
            cast(list[str], desired_storage["allowedLocations"])
        ):
            drift.append("storageConfigInfo.allowedLocations")
    return drift


def _catalog_object(current_payload: object) -> dict[str, Any]:
    if not isinstance(current_payload, dict):
        raise RuntimeError("Polaris catalog response is not a JSON object")
    current = current_payload.get("catalog", current_payload)
    if not isinstance(current, dict):
        raise RuntimeError("Polaris response does not contain a catalog object")
    return current


def _reconcile_existing_catalog(
    client: PolarisManagementClient,
    desired: dict[str, Any],
    current_payload: object,
) -> None:
    catalog_name = cast(str, desired["name"])
    for attempt in range(2):
        current = _catalog_object(current_payload)
        immutable_drift = [key for key in ("name", "type") if current.get(key) != desired[key]]
        if immutable_drift:
            raise RuntimeError(
                f"Existing Polaris catalog {catalog_name!r} has immutable contract drift "
                f"at: {', '.join(immutable_drift)}"
            )

        drift = _catalog_drift(current, desired)
        if not drift:
            logger.info("Polaris catalog %s already matches its contract", catalog_name)
            return

        entity_version = current.get("entityVersion")
        if not isinstance(entity_version, int) or isinstance(entity_version, bool):
            raise RuntimeError(
                f"Existing Polaris catalog {catalog_name!r} drifts at "
                f"{', '.join(drift)}, but its entityVersion is missing or invalid"
            )
        response = client.update_catalog(
            catalog_name,
            {
                "currentEntityVersion": entity_version,
                "properties": desired["properties"],
                "storageConfigInfo": desired["storageConfigInfo"],
            },
        )
        if response.status_code in (200, 204):
            logger.info(
                "Updated mutable Polaris catalog contract for %s at: %s",
                catalog_name,
                ", ".join(drift),
            )
            return
        if response.status_code != 409 or attempt == 1:
            response.raise_for_status()

        response = client.get_catalog(catalog_name)
        response.raise_for_status()
        current_payload = response.json()


def ensure_catalog(client: PolarisManagementClient, desired: DesiredCatalog) -> None:
    payload = desired.management_payload()
    response = client.get_catalog(desired.name)
    if response.status_code == 200:
        _reconcile_existing_catalog(client, payload, response.json())
        return
    if response.status_code != 404:
        response.raise_for_status()

    response = client.create_catalog(payload)
    if response.status_code in (200, 201):
        logger.info("Created Polaris catalog %s", desired.name)
        return
    if response.status_code != 409:
        response.raise_for_status()
        return

    response = client.get_catalog(desired.name)
    response.raise_for_status()
    _reconcile_existing_catalog(client, payload, response.json())


def ensure_catalog_role_grants(
    client: PolarisManagementClient,
    state: DesiredPlatformState,
) -> int:
    granted = 0
    for role in state.access_grants:
        response = client.get_catalog_role_grants(state.catalog.name, role.catalog_role)
        response.raise_for_status()
        payload = response.json()
        current_grants = payload.get("grants")
        if not isinstance(current_grants, list):
            raise RuntimeError(f"Polaris did not return grants for role {role.catalog_role!r}")
        current_privileges = {
            grant.get("privilege")
            for grant in current_grants
            if isinstance(grant, dict) and grant.get("type") == "catalog"
        }
        for privilege in role.privileges:
            if privilege in current_privileges:
                continue
            response = client.grant_catalog_privilege(
                state.catalog.name,
                role.catalog_role,
                privilege,
            )
            response.raise_for_status()
            granted += 1
        logger.info("Catalog role %s matches its privilege contract", role.catalog_role)
    return granted


def ensure_namespaces(catalog: Catalog, state: DesiredPlatformState) -> None:
    for desired in state.namespaces:
        properties = desired.iceberg_properties()
        try:
            catalog.create_namespace(desired.path, properties)
        except NamespaceAlreadyExistsError:
            current = catalog.load_namespace_properties(desired.path)
            updates = {key: value for key, value in properties.items() if current.get(key) != value}
            removals = set(current) - set(properties)
            if updates or removals:
                catalog.update_namespace_properties(
                    desired.path,
                    removals=removals,
                    updates=updates,
                )
        logger.info("Namespace %s matches its contract", ".".join(desired.path))


def load_catalog_with_retry(settings: Settings) -> Catalog:
    last_error: Exception | None = None
    for _ in range(12):
        try:
            catalog = load_iceberg_catalog(settings)
            catalog.list_namespaces()
            return catalog
        except Exception as error:
            last_error = error
            sleep(2)
    raise RuntimeError("Polaris catalog did not become readable") from last_error


def _schema_fingerprint(table: Table) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.field_id, field.name, field.field_type, field.required)
        for field in table.schema().fields
    )


def _expected_schema_fingerprint(desired: DesiredManagedTable) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.field_id, field.name, field.field_type, field.required)
        for field in iceberg_schema(desired.columns).fields
    )


def _partition_fingerprint(table: Table) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.source_id, field.field_id, field.transform, field.name)
        for field in table.spec().fields
    )


def _expected_partition_fingerprint(
    desired: DesiredManagedTable,
) -> tuple[tuple[object, ...], ...]:
    expected = partition_spec(desired.columns, desired.partitioning)
    return tuple(
        (field.source_id, field.field_id, field.transform, field.name) for field in expected.fields
    )


def _table_properties(
    contracts: PlatformContracts,
    desired: DesiredManagedTable,
) -> dict[str, str]:
    retention = contracts.maintenance.metadata_retention(
        TableIdentifier.from_iceberg(desired.identifier),
    )
    return managed_table_properties(retention)


def _validate_managed_table(table: Table, desired: DesiredManagedTable) -> None:
    identifier = ".".join(desired.identifier)
    validate_table_location(table, desired.location, owner=identifier)
    if table.format_version != MANAGED_ICEBERG_FORMAT_VERSION:
        raise RuntimeError(
            f"Iceberg table {identifier} format version drifted; "
            f"expected {MANAGED_ICEBERG_FORMAT_VERSION}, found {table.format_version}"
        )
    expected_schema = _expected_schema_fingerprint(desired)
    current_schema = _schema_fingerprint(table)
    if current_schema != expected_schema:
        raise RuntimeError(
            f"Iceberg table {identifier} schema drifted; "
            f"expected {expected_schema!r}, found {current_schema!r}"
        )
    expected_partitioning = _expected_partition_fingerprint(desired)
    current_partitioning = _partition_fingerprint(table)
    if current_partitioning != expected_partitioning:
        raise RuntimeError(
            f"Iceberg table {identifier} partition spec drifted; "
            f"expected {expected_partitioning!r}, found {current_partitioning!r}"
        )


def reconcile_managed_table(
    catalog: Catalog,
    contracts: PlatformContracts,
    desired: DesiredManagedTable,
) -> Table:
    properties = _table_properties(contracts, desired)
    try:
        if catalog.table_exists(desired.identifier):
            table = catalog.load_table(desired.identifier)
        else:
            table = catalog.create_table(
                identifier=desired.identifier,
                schema=iceberg_schema(desired.columns),
                location=desired.location,
                partition_spec=partition_spec(desired.columns, desired.partitioning),
                properties={
                    "format-version": str(MANAGED_ICEBERG_FORMAT_VERSION),
                    **properties,
                },
            )
    except TableAlreadyExistsError:
        table = catalog.load_table(desired.identifier)

    _validate_managed_table(table, desired)
    updates = {
        name: value
        for name, value in properties.items()
        if str(table.properties.get(name, "")).lower() != value
    }
    if updates:
        table.transaction().set_properties(updates).commit_transaction()
        table.refresh()
    logger.info("Iceberg table %s matches its contract", ".".join(desired.identifier))
    return table


def reconcile_managed_tables(
    catalog: Catalog,
    contracts: PlatformContracts,
    state: DesiredPlatformState,
) -> None:
    for desired in state.managed_tables:
        reconcile_managed_table(catalog, contracts, desired)


def reconcile_policies(
    client: PolarisPolicyClient,
    contracts: PlatformContracts,
) -> tuple[PolicyReconcileResult, ...]:
    return tuple(client.reconcile_policy(policy) for policy in contracts.policies)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.platform_admin.require_reconciliation_capability()
    contracts = load_contracts(settings.contracts_dir)
    state = compile_desired_state(settings, contracts)
    logger.info("Reconciling contract digest %s", state.contract_digest)
    with create_retry_session() as session:
        token = request_oauth_token(session, settings)
        management = PolarisManagementClient(session, settings, token)
        ensure_catalog(management, state.catalog)
        ensure_catalog_role_grants(management, state)
        with load_catalog_with_retry(settings) as catalog:
            ensure_namespaces(catalog, state)
            reconcile_managed_tables(catalog, contracts, state)
        policy_client = PolarisPolicyClient(session, settings, token)
        for result in reconcile_policies(policy_client, contracts):
            if result.pending_mappings:
                logger.info(
                    "Polaris policy %s: %s; ensured %d mappings, "
                    "%d pending (table not yet created)",
                    result.policy,
                    result.action,
                    result.ensured_mappings,
                    result.pending_mappings,
                )
            else:
                logger.info(
                    "Polaris policy %s: %s; ensured %d mappings",
                    result.policy,
                    result.action,
                    result.ensured_mappings,
                )
    logger.info("Lakehouse catalog contract is ready")


if __name__ == "__main__":
    main()
