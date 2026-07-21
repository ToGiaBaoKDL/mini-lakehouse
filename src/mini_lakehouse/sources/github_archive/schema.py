"""GitHub Archive schemas generated from the source-owned YAML contract."""

from mini_lakehouse.contracts import (
    arrow_schema,
    iceberg_schema,
    load_contracts,
    partition_spec,
)

_EVENTS_CONTRACT = load_contracts().source("github_archive").table("events_raw")

EVENTS_SCHEMA_CONTRACT = _EVENTS_CONTRACT.schema_contract
EVENTS_ARROW_SCHEMA = arrow_schema(_EVENTS_CONTRACT.columns)
EVENTS_ICEBERG_SCHEMA = iceberg_schema(_EVENTS_CONTRACT.columns)
EVENTS_PARTITION_SPEC = partition_spec(
    _EVENTS_CONTRACT.columns,
    _EVENTS_CONTRACT.partitioning,
)
