from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pyarrow as pa
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.expressions import EqualTo, Reference
from pyiceberg.expressions.literals import literal
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
from mini_lakehouse.contracts import GITHUB_EVENTS_RAW


@dataclass(frozen=True, slots=True)
class LandingHourWrite:
    row_count: int
    snapshot_id: int | None
    was_appended: bool


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
        if self._catalog.table_exists(GITHUB_EVENTS_RAW.iceberg):
            return self._catalog.load_table(GITHUB_EVENTS_RAW.iceberg)
        return self._catalog.create_table(
            identifier=GITHUB_EVENTS_RAW.iceberg,
            schema=LANDING_ICEBERG_SCHEMA,
            location=f"{self._settings.storage.landing_uri}/api/github_archive/events_raw",
            partition_spec=LANDING_PARTITION_SPEC,
            properties={
                "format-version": "2",
                "write.format.default": "parquet",
                "write.parquet.compression-codec": "zstd",
            },
        )

    def hour_state(self, source_hour: datetime) -> LandingHourWrite | None:
        if not self._catalog.table_exists(GITHUB_EVENTS_RAW.iceberg):
            return None
        table = self._catalog.load_table(GITHUB_EVENTS_RAW.iceberg)
        row_count = table.scan(
            row_filter=EqualTo(
                term=Reference(name="source_hour"),
                value=literal(source_hour),
            )
        ).count()
        if row_count == 0:
            return None
        source_hour_value = source_hour.isoformat()
        snapshot_id = next(
            (
                snapshot.snapshot_id
                for snapshot in reversed(table.snapshots())
                if snapshot.summary is not None
                and snapshot.summary.get("source-hour") == source_hour_value
            ),
            None,
        )
        return LandingHourWrite(
            row_count=row_count,
            snapshot_id=snapshot_id,
            was_appended=False,
        )

    def append_hour(self, events: pa.Table, source_hour: datetime) -> LandingHourWrite:
        if events.num_rows == 0:
            raise ValueError("Cannot append an empty GitHub Archive hour")
        source_hours = events.column("source_hour").unique().to_pylist()
        if source_hours != [source_hour]:
            raise ValueError(
                "Every landing row must match the requested source hour; "
                f"expected {source_hour.isoformat()}, found {source_hours!r}"
            )

        table = self.ensure_table()
        existing = self.hour_state(source_hour)
        if existing is not None:
            return existing
        table.append(
            events,
            snapshot_properties={
                "data-tier": "landing",
                "source-hour": source_hour.isoformat(),
                "source-system": "github_archive",
            },
        )
        snapshot = table.refresh().current_snapshot()
        return LandingHourWrite(
            row_count=events.num_rows,
            snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
            was_appended=True,
        )

    def properties(self) -> Mapping[str, Any]:
        return self.ensure_table().properties
