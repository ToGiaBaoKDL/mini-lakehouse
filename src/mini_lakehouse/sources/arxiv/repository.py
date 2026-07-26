"""ArXiv landing tables and day-checkpoint publication."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import TracebackType

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.expressions import EqualTo, Reference
from pyiceberg.expressions.literals import literal
from pyiceberg.table import Table

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import (
    PlatformContracts,
    arrow_schema,
    iceberg_schema,
    load_contracts,
    partition_spec,
)
from mini_lakehouse.contracts.sources import SourceTableContract
from mini_lakehouse.platform.runtime import source_table_storage_uri
from mini_lakehouse.storage.iceberg import load_iceberg_catalog, validate_table_location


@dataclass(frozen=True, slots=True)
class ArxivLandingWrite:
    records_snapshot_id: int | None
    checkpoint_snapshot_id: int | None
    was_written: bool


@dataclass(frozen=True, slots=True)
class ArxivLandingDayState:
    raw_object_key: str
    raw_object_sha256: str
    page_count: int
    record_count: int
    schema_version: str
    records_snapshot_id: int | None
    checkpoint_snapshot_id: int | None


class ArxivLandingRepository:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog | None = None,
        contracts: PlatformContracts | None = None,
    ) -> None:
        self._settings = settings
        self._owned_catalog = catalog is None
        self._catalog = catalog or load_iceberg_catalog(settings)
        self._source = (contracts or load_contracts(settings.contracts_dir)).source("arxiv")

    def __enter__(self) -> "ArxivLandingRepository":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._owned_catalog:
            self._catalog.__exit__(exc_type, exc_val, exc_tb)

    def _ensure_table(self, key: str) -> Table:
        contract = self._source.table(key)
        identifier = self._source.table_identifier(key)
        expected_location = source_table_storage_uri(self._settings, self._source, key)
        expected_spec = partition_spec(contract.columns, contract.partitioning)
        if self._catalog.table_exists(identifier.iceberg):
            table = self._catalog.load_table(identifier.iceberg)
        else:
            table = self._catalog.create_table(
                identifier=identifier.iceberg,
                schema=iceberg_schema(contract.columns),
                location=expected_location,
                partition_spec=expected_spec,
                properties={
                    "format-version": "2",
                    "write.format.default": "parquet",
                    "write.parquet.compression-codec": "zstd",
                },
            )
        validate_table_location(table, expected_location, owner="ArXiv landing")
        if table.spec() != expected_spec:
            raise RuntimeError(
                f"ArXiv landing table {contract.name!r} partition spec drifted; "
                f"expected {expected_spec}, found {table.spec()}"
            )
        return table

    @staticmethod
    def _snapshot_id(table: Table) -> int | None:
        snapshot = table.refresh().current_snapshot()
        return snapshot.snapshot_id if snapshot is not None else None

    @staticmethod
    def _day_filter(day: date) -> EqualTo:
        return EqualTo(term=Reference(name="datestamp_date"), value=literal(day))

    def day_state(self, datestamp_date: date) -> ArxivLandingDayState | None:
        records_identifier = self._source.table_identifier("oai_records_raw")
        checkpoint_identifier = self._source.table_identifier("oai_checkpoints")
        records_exists = self._catalog.table_exists(records_identifier.iceberg)
        checkpoints_exist = self._catalog.table_exists(checkpoint_identifier.iceberg)
        if not records_exists and not checkpoints_exist:
            return None
        if records_exists != checkpoints_exist:
            raise RuntimeError("ArXiv landing records/checkpoint tables must exist together")

        records_table = self._ensure_table("oai_records_raw")
        checkpoint_table = self._ensure_table("oai_checkpoints")
        rows = checkpoint_table.scan(
            row_filter=self._day_filter(datestamp_date),
            selected_fields=(
                "raw_object_key",
                "raw_object_sha256",
                "page_count",
                "record_count",
                "schema_version",
            ),
        ).to_arrow()
        if rows.num_rows == 0:
            return None
        if rows.num_rows != 1:
            raise RuntimeError(f"ArXiv landing day {datestamp_date} has duplicate checkpoints")
        row = rows.to_pylist()[0]
        records = records_table.scan(
            row_filter=self._day_filter(datestamp_date),
            selected_fields=("raw_object_sha256",),
        ).to_arrow()
        expected_record_count = int(row["record_count"])
        observed_hashes = {
            str(value)
            for value in records.column("raw_object_sha256").to_pylist()
            if value is not None
        }
        if records.num_rows != expected_record_count or (
            expected_record_count > 0 and observed_hashes != {str(row["raw_object_sha256"])}
        ):
            return None
        return ArxivLandingDayState(
            raw_object_key=str(row["raw_object_key"]),
            raw_object_sha256=str(row["raw_object_sha256"]),
            page_count=int(row["page_count"]),
            record_count=expected_record_count,
            schema_version=str(row["schema_version"]),
            records_snapshot_id=self._snapshot_id(records_table),
            checkpoint_snapshot_id=self._snapshot_id(checkpoint_table),
        )

    def publish_day(
        self,
        records: pa.Table,
        *,
        datestamp_date: date,
        raw_object_key: str,
        raw_object_sha256: str,
        page_count: int,
    ) -> ArxivLandingWrite:
        expected_schema = arrow_schema(self._source.table("oai_records_raw").columns)
        if records.schema != expected_schema:
            raise ValueError("ArXiv landing Arrow schema drifted from its source contract")
        observed_days = records.column("datestamp_date").unique().to_pylist()
        if observed_days not in ([], [datestamp_date]):
            raise ValueError(
                f"Every ArXiv landing row must match {datestamp_date}; found {observed_days!r}"
            )

        records_table = self._ensure_table("oai_records_raw")
        records_table.overwrite(records, overwrite_filter=self._day_filter(datestamp_date))
        records_snapshot_id = self._snapshot_id(records_table)

        checkpoint_contract = self._source.table("oai_checkpoints")
        checkpoint = pa.Table.from_pylist(
            [
                {
                    "datestamp_date": datestamp_date,
                    "raw_object_key": raw_object_key,
                    "raw_object_sha256": raw_object_sha256,
                    "page_count": page_count,
                    "record_count": records.num_rows,
                    "schema_version": self._source.table_schema_contract("oai_records_raw"),
                    "published_at": datetime.now(UTC),
                }
            ],
            schema=arrow_schema(checkpoint_contract.columns),
        )
        checkpoint_table = self._ensure_table("oai_checkpoints")
        checkpoint_table.overwrite(
            checkpoint,
            overwrite_filter=self._day_filter(datestamp_date),
        )
        checkpoint_snapshot_id = self._snapshot_id(checkpoint_table)
        return ArxivLandingWrite(
            records_snapshot_id=records_snapshot_id,
            checkpoint_snapshot_id=checkpoint_snapshot_id,
            was_written=True,
        )

    def table_contract(self, key: str) -> SourceTableContract:
        return self._source.table(key)
