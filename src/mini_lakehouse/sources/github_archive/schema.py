"""Stable Arrow and Iceberg schemas owned by the GitHub Archive source."""

import pyarrow as pa
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import HourTransform
from pyiceberg.types import (
    BooleanType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

EVENTS_SCHEMA_CONTRACT = "github_archive.events_raw.v1"

EVENTS_ARROW_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("actor_id", pa.int64()),
        pa.field("actor_login", pa.string()),
        pa.field("repository_id", pa.int64()),
        pa.field("repository_name", pa.string()),
        pa.field("payload_json", pa.string(), nullable=False),
        pa.field("is_public", pa.bool_(), nullable=False),
        pa.field("occurred_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("source_file", pa.string(), nullable=False),
        pa.field("source_hour", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("raw_event_json", pa.string(), nullable=False),
    ]
)

EVENTS_ICEBERG_SCHEMA = Schema(
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

EVENTS_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=11,
        field_id=1000,
        transform=HourTransform(),
        name="source_hour_hour",
    )
)
