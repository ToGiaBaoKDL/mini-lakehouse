from collections.abc import Iterator, Mapping

from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.table import Table

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts, TableIdentifier
from mini_lakehouse.contracts.policies import (
    MetadataCompactionPolicyContract,
    MetadataRetentionContract,
)

ICEBERG_DELETE_AFTER_COMMIT = "write.metadata.delete-after-commit.enabled"
ICEBERG_PREVIOUS_VERSIONS_MAX = "write.metadata.previous-versions-max"
TRINO_DELETE_AFTER_COMMIT = "delete_after_commit_enabled"
TRINO_PREVIOUS_VERSIONS_MAX = "max_previous_versions"


def iceberg_catalog_properties(settings: Settings) -> dict[str, str]:
    storage = settings.storage
    properties = {
        "type": "rest",
        "uri": settings.polaris.uri,
        "warehouse": settings.polaris.catalog_name,
        "credential": settings.polaris.credential.get_secret_value(),
        "scope": settings.polaris.scope,
        "oauth2-server-uri": settings.polaris.oauth2_server_uri,
        "s3.region": storage.region,
        "s3.path-style-access": str(storage.path_style_access).lower(),
    }
    if storage.endpoint_url:
        properties["s3.endpoint"] = storage.endpoint_url
    access_key = storage.secret_value(storage.access_key)
    secret_key = storage.secret_value(storage.secret_key)
    if storage.iceberg_access_delegation == "vended-credentials":
        properties["header.X-Iceberg-Access-Delegation"] = "vended-credentials"
    elif access_key and secret_key:
        properties["s3.access-key-id"] = access_key
        properties["s3.secret-access-key"] = secret_key
        # PyIceberg otherwise requests vended credentials by default. Static
        # storage credentials and delegation are mutually exclusive modes; an
        # empty explicit header prevents that default for local and other
        # static-credential deployments.
        properties["header.X-Iceberg-Access-Delegation"] = ""
    return properties


def load_iceberg_catalog(settings: Settings) -> Catalog:
    return load_catalog(
        settings.polaris.catalog_name,
        **iceberg_catalog_properties(settings),
    )


def validate_table_location(table: Table, expected: str, *, owner: str) -> None:
    """Reject catalog drift instead of writing outside an ownership boundary."""
    actual = table.location().rstrip("/")
    canonical = expected.rstrip("/")
    if actual != canonical:
        raise RuntimeError(
            f"{owner} table location drifted; expected {canonical!r}, found {actual!r}"
        )


def metadata_retention_for_table(
    contracts: PlatformContracts,
    table: TableIdentifier,
) -> MetadataRetentionContract:
    policies = [
        policy
        for policy in contracts.policies
        if isinstance(policy, MetadataCompactionPolicyContract)
        and any(
            target.type == "catalog"
            or (target.type == "namespace" and table.namespace[: len(target.path)] == target.path)
            or (target.type == "table-like" and table.iceberg == target.path)
            for target in policy.targets
        )
    ]
    if len(policies) != 1:
        raise ValueError(
            f"Table {table.iceberg!r} must have exactly one metadata retention policy; "
            f"found {len(policies)}"
        )
    return policies[0].retention


def iceberg_metadata_retention_properties(
    retention: MetadataRetentionContract,
) -> dict[str, str]:
    return {
        ICEBERG_DELETE_AFTER_COMMIT: str(retention.delete_after_commit).lower(),
        ICEBERG_PREVIOUS_VERSIONS_MAX: str(retention.previous_versions_max),
    }


def trino_metadata_retention_properties(
    retention: MetadataRetentionContract,
) -> dict[str, str]:
    return {
        TRINO_DELETE_AFTER_COMMIT: str(retention.delete_after_commit).lower(),
        TRINO_PREVIOUS_VERSIONS_MAX: str(retention.previous_versions_max),
    }


def metadata_retention_is_current(
    properties: Mapping[str, str],
    retention: MetadataRetentionContract,
) -> bool:
    expected = iceberg_metadata_retention_properties(retention)
    return all(str(properties.get(key, "")).lower() == value for key, value in expected.items())


def reconcile_metadata_retention(
    table: Table,
    retention: MetadataRetentionContract,
) -> Table:
    if metadata_retention_is_current(table.properties, retention):
        return table
    table.transaction().set_properties(
        iceberg_metadata_retention_properties(retention)
    ).commit_transaction()
    table.refresh()
    return table


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
