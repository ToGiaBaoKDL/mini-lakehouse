"""Source-owned Iceberg repository and idempotent partition commit."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.expressions import EqualTo, Reference
from pyiceberg.expressions.literals import literal
from pyiceberg.table import Table

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import (
    PlatformContracts,
    iceberg_schema,
    load_contracts,
    partition_spec,
)
from mini_lakehouse.storage.iceberg import load_iceberg_catalog


@dataclass(frozen=True, slots=True)
class GithubArchiveWrite:
    row_count: int
    snapshot_id: int | None
    was_written: bool


class GithubArchiveRepository:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog | None = None,
        contracts: PlatformContracts | None = None,
    ) -> None:
        self._settings = settings
        self._owned_catalog = catalog is None
        self._catalog = catalog or load_iceberg_catalog(settings)
        source = (contracts or load_contracts(settings.contracts_dir)).source("github_archive")
        self._table_contract = source.table("events_raw")
        self._identifier = source.table_identifier("events_raw")
        self._schema = iceberg_schema(self._table_contract.columns)
        self._partition_spec = partition_spec(
            self._table_contract.columns,
            self._table_contract.partitioning,
        )
        if self._table_contract.write_mode != "checkpoint_overwrite":
            raise ValueError("GitHub Archive requires checkpoint_overwrite for idempotent commits")
        contract_partitioning = tuple(
            (partition.field, partition.transform)
            for partition in self._table_contract.partitioning
        )
        if contract_partitioning != (("source_hour", "hour"),):
            raise ValueError("GitHub Archive requires hour(source_hour) partitioning")
        if self._table_contract.partitioning[0].name != "archive_hour":
            raise ValueError("GitHub Archive partition must be named archive_hour")

    def __enter__(self) -> "GithubArchiveRepository":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._owned_catalog:
            self._catalog.__exit__(exc_type, exc_val, exc_tb)

    def ensure_table(self) -> Table:
        if self._catalog.table_exists(self._identifier.iceberg):
            table = self._catalog.load_table(self._identifier.iceberg)
            self._validate_partition_spec(table)
            return table
        table = self._catalog.create_table(
            identifier=self._identifier.iceberg,
            schema=self._schema,
            location=(
                f"{self._settings.storage.landing_uri.rstrip('/')}"
                f"/{self._table_contract.location_prefix}"
            ),
            partition_spec=self._partition_spec,
            properties={
                "format-version": "2",
                "write.format.default": "parquet",
                "write.parquet.compression-codec": "zstd",
            },
        )
        self._validate_partition_spec(table)
        return table

    def _validate_partition_spec(self, table: Table) -> None:
        current = table.spec()
        if current != self._partition_spec:
            raise RuntimeError(
                "GitHub Archive partition spec drifted from its source contract; "
                f"expected {self._partition_spec}, found {current}"
            )

    def hour_state(self, source_hour: datetime) -> GithubArchiveWrite | None:
        if not self._catalog.table_exists(self._identifier.iceberg):
            return None
        table = self._catalog.load_table(self._identifier.iceberg)
        files = tuple(
            table.scan(
                row_filter=EqualTo(
                    term=Reference(name="source_hour"),
                    value=literal(source_hour),
                )
            ).plan_files()
        )
        if not files:
            return None
        snapshot = table.current_snapshot()
        return GithubArchiveWrite(
            row_count=sum(task.file.record_count for task in files),
            snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
            was_written=False,
        )

    def write_hour(self, events: pa.Table, source_hour: datetime) -> GithubArchiveWrite:
        if events.num_rows == 0:
            raise ValueError("Cannot write an empty GitHub Archive hour")
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
        table.overwrite(
            events,
            overwrite_filter=EqualTo(
                term=Reference(name="source_hour"),
                value=literal(source_hour),
            ),
            snapshot_properties={
                "data-tier": "landing",
                "source-hour": source_hour.isoformat(),
                "source-row-count": str(events.num_rows),
                "source-system": "github_archive",
            },
        )
        snapshot = table.refresh().current_snapshot()
        return GithubArchiveWrite(
            row_count=events.num_rows,
            snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
            was_written=True,
        )

    def properties(self) -> Mapping[str, Any]:
        return self.ensure_table().properties
