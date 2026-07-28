from collections.abc import Mapping

from mini_lakehouse.contracts.maintenance import MetadataRetentionContract

MANAGED_ICEBERG_FORMAT_VERSION = 2

_MANAGED_TABLE_DEFAULTS = {
    "write.format.default": "parquet",
    "write.parquet.compression-codec": "zstd",
}
_DELETE_AFTER_COMMIT = "write.metadata.delete-after-commit.enabled"
_PREVIOUS_VERSIONS_MAX = "write.metadata.previous-versions-max"


def metadata_retention_properties(
    retention: MetadataRetentionContract,
) -> dict[str, str]:
    return {
        _DELETE_AFTER_COMMIT: str(retention.delete_after_commit).lower(),
        _PREVIOUS_VERSIONS_MAX: str(retention.previous_versions_max),
    }


def managed_table_properties(
    retention: MetadataRetentionContract,
) -> dict[str, str]:
    return {
        **_MANAGED_TABLE_DEFAULTS,
        **metadata_retention_properties(retention),
    }


def metadata_retention_is_current(
    properties: Mapping[str, str],
    retention: MetadataRetentionContract,
) -> bool:
    expected = metadata_retention_properties(retention)
    return all(str(properties.get(key, "")).lower() == value for key, value in expected.items())
