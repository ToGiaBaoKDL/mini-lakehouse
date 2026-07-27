from collections.abc import Iterator, Mapping

from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.table import Table

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import TableIdentifier
from mini_lakehouse.contracts.maintenance import MetadataRetentionContract

MANAGED_ICEBERG_FORMAT_VERSION = 2
MANAGED_ICEBERG_FILE_FORMAT = "parquet"
MANAGED_PARQUET_COMPRESSION = "zstd"
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


def managed_table_properties(retention: MetadataRetentionContract) -> dict[str, str]:
    return {
        "write.format.default": MANAGED_ICEBERG_FILE_FORMAT,
        "write.parquet.compression-codec": MANAGED_PARQUET_COMPRESSION,
        **iceberg_metadata_retention_properties(retention),
    }


def metadata_retention_is_current(
    properties: Mapping[str, str],
    retention: MetadataRetentionContract,
) -> bool:
    expected = iceberg_metadata_retention_properties(retention)
    return all(str(properties.get(key, "")).lower() == value for key, value in expected.items())


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
