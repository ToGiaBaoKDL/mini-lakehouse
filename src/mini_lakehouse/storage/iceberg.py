from collections.abc import Mapping
from typing import Any

import pyarrow as pa
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.transforms import HourTransform
from pyiceberg.types import (
    BooleanType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from mini_lakehouse.config.settings import Settings

EVENTS_IDENTIFIER = ("landing", "api", "github_archive", "events_raw")

LANDING_ICEBERG_SCHEMA = Schema(
    NestedField(field_id=1, name="event_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="event_type", field_type=StringType(), required=True),
    NestedField(field_id=3, name="actor_id", field_type=LongType(), required=False),
    NestedField(field_id=4, name="actor_login", field_type=StringType(), required=False),
    NestedField(field_id=5, name="repository_id", field_type=LongType(), required=False),
    NestedField(field_id=6, name="repository_name", field_type=StringType(), required=False),
    NestedField(field_id=7, name="payload_json", field_type=StringType(), required=True),
    NestedField(field_id=8, name="is_public", field_type=BooleanType(), required=True),
    NestedField(field_id=9, name="occurred_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=10, name="source_file", field_type=StringType(), required=True),
    NestedField(field_id=11, name="source_hour", field_type=TimestamptzType(), required=True),
    NestedField(field_id=12, name="ingested_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=13, name="raw_event_json", field_type=StringType(), required=True),
)

LANDING_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=11,
        field_id=1000,
        transform=HourTransform(),
        name="source_hour_hour",
    )
)


def _secret_properties(settings: Settings) -> dict[str, str]:
    storage = settings.storage
    properties = {
        "type": "rest",
        "uri": settings.polaris.uri,
        "warehouse": settings.polaris.catalog_name,
        "credential": settings.polaris.credential.get_secret_value(),
        "scope": settings.polaris.scope,
        "oauth2-server-uri": settings.polaris.oauth2_server_uri,
        "s3.region": storage.region,
        "s3.path-style-access": "true",
    }
    if storage.endpoint_url:
        properties["s3.endpoint"] = storage.endpoint_url
    access_key = storage.secret_value(storage.access_key)
    secret_key = storage.secret_value(storage.secret_key)
    if access_key:
        properties["s3.access-key-id"] = access_key
    if secret_key:
        properties["s3.secret-access-key"] = secret_key
    return properties


def load_prod_catalog(settings: Settings) -> Catalog:
    return load_catalog(settings.polaris.catalog_name, **_secret_properties(settings))


class LandingEventsRepository:
    def __init__(self, settings: Settings, catalog: Catalog | None = None) -> None:
        self._settings = settings
        self._catalog = catalog or load_prod_catalog(settings)

    def ensure_table(self) -> Table:
        if self._catalog.table_exists(EVENTS_IDENTIFIER):
            return self._catalog.load_table(EVENTS_IDENTIFIER)
        return self._catalog.create_table(
            identifier=EVENTS_IDENTIFIER,
            schema=LANDING_ICEBERG_SCHEMA,
            location=f"{self._settings.storage.landing_uri}/api/github_archive/events_raw",
            partition_spec=LANDING_PARTITION_SPEC,
            properties={
                "format-version": "2",
                "write.format.default": "parquet",
                "write.parquet.compression-codec": "zstd",
            },
        )

    def replace_hour(self, events: pa.Table) -> int | None:
        table = self.ensure_table()
        table.dynamic_partition_overwrite(events)
        snapshot = table.refresh().current_snapshot()
        return snapshot.snapshot_id if snapshot is not None else None

    def properties(self) -> Mapping[str, Any]:
        return self.ensure_table().properties
