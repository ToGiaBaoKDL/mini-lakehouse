import pytest
from pyiceberg.transforms import HourTransform

from lakehouse_platform.contracts import (
    ManagedIcebergTableContract,
    TableIdentifier,
    load_contracts,
)
from lakehouse_platform.platform.catalog.schema import partition_spec


def test_github_archive_partition_spec_uses_named_hour_transform() -> None:
    table = load_contracts().source("github_archive").table("events_raw")
    field = partition_spec(table.columns, table.partitioning).fields[0]

    assert field.name == "archive_hour"
    assert isinstance(field.transform, HourTransform)


def test_glue_relation_uses_one_database_and_no_catalog_prefix() -> None:
    identifier = TableIdentifier(namespace=("curated_github",), name="events")

    assert identifier.athena() == '"curated_github"."events"'


@pytest.mark.parametrize("part", ["bad-name", "bad.name", 'bad"name', ""])
def test_table_contract_rejects_unsafe_identifiers(part: str) -> None:
    with pytest.raises(ValueError, match="Invalid catalog identifier"):
        TableIdentifier(namespace=("curated_github",), name=part)


@pytest.mark.parametrize(
    ("data_type", "transform"),
    [
        ("string", "day"),
        ("long", "month"),
        ("boolean", "year"),
        ("date", "hour"),
    ],
)
def test_table_contract_rejects_incompatible_partition_transforms(
    data_type: str,
    transform: str,
) -> None:
    with pytest.raises(ValueError, match=f"cannot apply {transform!r}"):
        ManagedIcebergTableContract.model_validate(
            {
                "key": "invalid_partition",
                "name": "invalid_partition",
                "description": "Invalid partition transform fixture.",
                "columns": [
                    {
                        "field_id": 1,
                        "name": "partition_value",
                        "data_type": data_type,
                        "required": True,
                        "description": "Fixture value.",
                    }
                ],
                "partitioning": [
                    {
                        "field_id": 1000,
                        "field": "partition_value",
                        "transform": transform,
                    }
                ],
            }
        )
