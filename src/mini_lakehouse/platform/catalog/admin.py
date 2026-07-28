import argparse
import json
import logging
from collections.abc import Sequence

from apache_polaris.sdk.management.exceptions import ConflictException
from apache_polaris.sdk.management.models import AwsStorageConfigInfo
from pyiceberg.catalog import Catalog
from pyiceberg.table import Table

from mini_lakehouse.config import Settings, get_settings
from mini_lakehouse.contracts import (
    ManagedIcebergTableContract,
    PlatformContracts,
    TableIdentifier,
    iceberg_schema,
    load_contracts,
    partition_spec,
)
from mini_lakehouse.contracts.maintenance import policy_content_json
from mini_lakehouse.logging import configure_logging
from mini_lakehouse.platform.catalog.layout import (
    catalog_allowed_locations,
    catalog_properties,
    managed_tables,
    namespace_storage_uri,
    validate_runtime_contract,
)
from mini_lakehouse.platform.catalog.polaris import (
    PolarisCatalog,
    PolarisClients,
    PolarisManagementClient,
    PolarisPolicyClient,
    canonical_policy_content,
)
from mini_lakehouse.platform.catalog.tables import (
    MANAGED_ICEBERG_FORMAT_VERSION,
    managed_table_properties,
)
from mini_lakehouse.storage.iceberg import load_iceberg_catalog

logger = logging.getLogger(__name__)


def require_valid(errors: tuple[str, ...]) -> None:
    if errors:
        raise RuntimeError("Platform validation failed:\n- " + "\n- ".join(errors))


def validation_payload(errors: tuple[str, ...]) -> dict[str, object]:
    return {"valid": not errors, "errors": list(errors)}


def _catalog_drift(
    current: PolarisCatalog,
    settings: Settings,
    contracts: PlatformContracts,
) -> tuple[str, ...]:
    desired_properties = catalog_properties(settings, contracts)
    desired_locations = catalog_allowed_locations(settings, contracts)
    storage = settings.storage
    current_properties = current.properties.to_dict()
    current_storage = current.storage_config_info
    if not isinstance(current_storage, AwsStorageConfigInfo):
        return ("storage.type",)
    drift: list[str] = []
    if current.type != contracts.platform.catalog.type:
        drift.append("type")
    for name in sorted(current_properties.keys() | desired_properties.keys()):
        if current_properties.get(name) != desired_properties.get(name):
            drift.append(f"properties.{name}")
    expected_storage = {
        "allowed_locations": set(desired_locations),
        "endpoint": storage.endpoints.external_url,
        "internal_endpoint": storage.endpoints.internal_url,
        "path_style_access": storage.path_style_access,
        "region": storage.region,
        "sts_unavailable": storage.sts_unavailable,
        "kms_unavailable": storage.kms_unavailable,
    }
    current_values = {
        "allowed_locations": set(current_storage.allowed_locations or ()),
        "endpoint": current_storage.endpoint,
        "internal_endpoint": current_storage.endpoint_internal,
        "path_style_access": bool(current_storage.path_style_access),
        "region": current_storage.region,
        "sts_unavailable": current_storage.sts_unavailable,
        "kms_unavailable": current_storage.kms_unavailable,
    }
    drift.extend(
        f"storage.{name}"
        for name, value in expected_storage.items()
        if current_values[name] != value
    )
    return tuple(drift)


def _schema_fingerprint(table: Table) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.field_id, field.name, field.field_type, field.required)
        for field in table.schema().fields
    )


def _expected_schema_fingerprint(
    contract: ManagedIcebergTableContract,
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.field_id, field.name, field.field_type, field.required)
        for field in iceberg_schema(contract.columns, contract.primary_key).fields
    )


def _partition_fingerprint(table: Table) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.source_id, field.field_id, field.transform, field.name)
        for field in table.spec().fields
    )


def _expected_partition_fingerprint(
    contract: ManagedIcebergTableContract,
) -> tuple[tuple[object, ...], ...]:
    spec = partition_spec(contract.columns, contract.partitioning)
    return tuple(
        (field.source_id, field.field_id, field.transform, field.name) for field in spec.fields
    )


def _expected_identifier_fields(
    contract: ManagedIcebergTableContract,
) -> frozenset[int]:
    return frozenset(
        column.field_id for column in contract.columns if column.name in contract.primary_key
    )


def _table_drift(
    table: Table,
    location: str,
    contract: ManagedIcebergTableContract,
    properties: dict[str, str],
) -> tuple[str, ...]:
    drift: list[str] = []
    if table.location().rstrip("/") != location.rstrip("/"):
        drift.append("location")
    if table.format_version != MANAGED_ICEBERG_FORMAT_VERSION:
        drift.append("format_version")
    if _schema_fingerprint(table) != _expected_schema_fingerprint(contract):
        drift.append("schema")
    if _partition_fingerprint(table) != _expected_partition_fingerprint(contract):
        drift.append("partition_spec")
    if frozenset(table.schema().identifier_field_ids) != _expected_identifier_fields(contract):
        drift.append("identifier_fields")
    drift.extend(
        f"properties.{name}"
        for name, value in properties.items()
        if str(table.properties.get(name, "")).lower() != value
    )
    return tuple(drift)


def _table_properties(
    contracts: PlatformContracts,
    identifier: TableIdentifier,
) -> dict[str, str]:
    return managed_table_properties(contracts.maintenance.metadata_retention(identifier))


def bootstrap_catalog(
    client: PolarisManagementClient,
    settings: Settings,
    contracts: PlatformContracts,
) -> None:
    name = contracts.platform.catalog.name
    properties = catalog_properties(settings, contracts)
    locations = catalog_allowed_locations(settings, contracts)
    current = client.load_catalog(name)
    if current is None:
        try:
            client.create_catalog(
                name,
                contracts.platform.catalog.type,
                properties,
                locations,
            )
            return
        except ConflictException:
            current = client.load_catalog(name)
        if current is None:
            raise RuntimeError(f"Polaris catalog {name!r} disappeared after creation")

    for attempt in range(2):
        drift = _catalog_drift(current, settings, contracts)
        if not drift:
            return
        if "type" in drift:
            raise RuntimeError(f"Polaris catalog {name!r} requires an explicit migration: type")
        try:
            client.update_catalog(current, properties, locations)
            return
        except ConflictException:
            if attempt == 1:
                raise
        refreshed = client.load_catalog(name)
        if refreshed is None:
            raise RuntimeError(f"Polaris catalog {name!r} disappeared during update")
        current = refreshed


def bootstrap_table(
    catalog: Catalog,
    contracts: PlatformContracts,
    identifier: TableIdentifier,
    location: str,
    contract: ManagedIcebergTableContract,
) -> Table:
    properties = _table_properties(contracts, identifier)
    table = catalog.create_table_if_not_exists(
        identifier=identifier.iceberg,
        schema=iceberg_schema(contract.columns, contract.primary_key),
        location=location,
        partition_spec=partition_spec(contract.columns, contract.partitioning),
        properties={
            "format-version": str(MANAGED_ICEBERG_FORMAT_VERSION),
            **properties,
        },
    )
    drift = set(_table_drift(table, location, contract, properties))
    unsafe = {item for item in drift if not item.startswith("properties.")}
    safe_identifier_update = (
        unsafe == {"identifier_fields"}
        and not table.schema().identifier_field_ids
        and bool(contract.primary_key)
    )
    if unsafe and not safe_identifier_update:
        raise RuntimeError(
            f"Iceberg table {'.'.join(identifier.iceberg)} requires an explicit migration: "
            f"{', '.join(sorted(unsafe))}"
        )
    if safe_identifier_update:
        with table.update_schema() as update:
            update.set_identifier_fields(*contract.primary_key)
        table.refresh()
    updates = {
        name: value
        for name, value in properties.items()
        if str(table.properties.get(name, "")).lower() != value
    }
    if updates:
        table.transaction().set_properties(updates).commit_transaction()
        table.refresh()
    return table


def _bootstrap_iceberg(
    catalog: Catalog,
    settings: Settings,
    contracts: PlatformContracts,
) -> None:
    for namespace in contracts.managed_namespaces():
        properties = namespace.iceberg_properties(namespace_storage_uri(settings, namespace.path))
        catalog.create_namespace_if_not_exists(namespace.path, properties)
        current = catalog.load_namespace_properties(namespace.path)
        updates = {name: value for name, value in properties.items() if current.get(name) != value}
        removals = set(current) - set(properties)
        if updates or removals:
            catalog.update_namespace_properties(
                namespace.path,
                removals=removals,
                updates=updates,
            )
    for identifier, location, contract in managed_tables(settings, contracts):
        bootstrap_table(catalog, contracts, identifier, location, contract)


def _bootstrap_access(
    client: PolarisManagementClient,
    contracts: PlatformContracts,
) -> None:
    catalog_name = contracts.platform.catalog.name
    for role in contracts.access.catalog_role_grants:
        current = client.catalog_privileges(catalog_name, role.catalog_role)
        for privilege in role.privileges:
            if privilege not in current:
                client.grant_catalog_privilege(catalog_name, role.catalog_role, privilege)


def _bootstrap_policies(
    client: PolarisPolicyClient,
    contracts: PlatformContracts,
) -> None:
    for policy in contracts.policies:
        client.apply_policy(policy)
        for target in policy.targets:
            if not client.policy_applies(policy, target):
                client.attach_policy(policy, target)


def validate_platform(
    settings: Settings,
    contracts: PlatformContracts,
    management: PolarisManagementClient,
    policies: PolarisPolicyClient,
) -> tuple[str, ...]:
    errors: list[str] = []
    catalog_name = contracts.platform.catalog.name
    current_catalog = management.load_catalog(catalog_name)
    if current_catalog is None:
        return (f"catalog:{catalog_name}:missing",)
    errors.extend(
        f"catalog:{catalog_name}:{item}"
        for item in _catalog_drift(current_catalog, settings, contracts)
    )
    for role in contracts.access.catalog_role_grants:
        current = management.catalog_privileges(catalog_name, role.catalog_role)
        errors.extend(
            f"grant:{role.catalog_role}:{privilege}:missing"
            for privilege in role.privileges
            if privilege not in current
        )

    existing_namespaces: set[tuple[str, ...]] = set()
    existing_tables: set[tuple[str, ...]] = set()
    with load_iceberg_catalog(settings) as catalog:
        for namespace in contracts.managed_namespaces():
            identifier = ".".join(namespace.path)
            if not catalog.namespace_exists(namespace.path):
                errors.append(f"namespace:{identifier}:missing")
                continue
            existing_namespaces.add(namespace.path)
            expected = namespace.iceberg_properties(namespace_storage_uri(settings, namespace.path))
            current = catalog.load_namespace_properties(namespace.path)
            for name in sorted(current.keys() | expected.keys()):
                if current.get(name) != expected.get(name):
                    errors.append(f"namespace:{identifier}:properties.{name}")

        for identifier, location, contract in managed_tables(settings, contracts):
            rendered = ".".join(identifier.iceberg)
            if identifier.namespace not in existing_namespaces:
                continue
            if not catalog.table_exists(identifier.iceberg):
                errors.append(f"table:{rendered}:missing")
                continue
            existing_tables.add(identifier.iceberg)
            table = catalog.load_table(identifier.iceberg)
            properties = _table_properties(contracts, identifier)
            errors.extend(
                f"table:{rendered}:{item}"
                for item in _table_drift(table, location, contract, properties)
            )

    for policy in contracts.policies:
        identifier = ".".join((*policy.namespace, policy.name))
        current = (
            policies.load_policy(policy.namespace, policy.name)
            if policy.namespace in existing_namespaces
            else None
        )
        if current is None:
            errors.append(f"policy:{identifier}:missing")
            continue
        if current.policy_type != policy.policy_type:
            errors.append(f"policy:{identifier}:type")
        if current.description != policy.description:
            errors.append(f"policy:{identifier}:description")
        if canonical_policy_content(current) != policy_content_json(policy):
            errors.append(f"policy:{identifier}:content")
        for target in policy.targets:
            target_exists = (
                target.path in existing_namespaces
                if target.type == "namespace"
                else target.path in existing_tables
            )
            if target_exists and not policies.policy_applies(policy, target):
                errors.append(
                    f"policy_mapping:{identifier}->{target.type}:{'.'.join(target.path)}:missing"
                )
    return tuple(sorted(errors))


def bootstrap_platform(
    settings: Settings,
    contracts: PlatformContracts,
    clients: PolarisClients,
) -> tuple[str, ...]:
    validate_runtime_contract(settings, contracts)
    bootstrap_catalog(clients.management, settings, contracts)
    _bootstrap_access(clients.management, contracts)
    with load_iceberg_catalog(settings) as catalog:
        _bootstrap_iceberg(catalog, settings, contracts)
    _bootstrap_policies(clients.policies, contracts)
    errors = validate_platform(
        settings,
        contracts,
        clients.management,
        clients.policies,
    )
    require_valid(errors)
    return errors


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap or validate the contract-managed lakehouse platform."
    )
    parser.add_argument("operation", choices=("bootstrap", "validate"))
    parsed = parser.parse_args(arguments)

    settings = get_settings()
    configure_logging(settings.log_level)
    settings.platform_admin.require_capability()
    contracts = load_contracts(settings.contracts_dir)
    validate_runtime_contract(settings, contracts)
    with PolarisClients(settings) as clients:
        if parsed.operation == "bootstrap":
            errors = bootstrap_platform(settings, contracts, clients)
            logger.info("Platform bootstrap completed without drift")
        else:
            errors = validate_platform(
                settings,
                contracts,
                clients.management,
                clients.policies,
            )
            require_valid(errors)
    print(json.dumps(validation_payload(errors), indent=2))


if __name__ == "__main__":
    main()
