"""Bounded Iceberg maintenance for contract-owned landing and curated tables."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from lakehouse.catalog.layout import declared_tables
from loguru import logger

from lakehouse.catalog import CATALOG_NAME
from lakehouse.contracts import ManagedIcebergTableContract


@dataclass(frozen=True)
class MaintenancePolicy:
    optimize_days: int
    snapshot_days: int
    orphan_days: int
    retain_snapshots: int
    target_file_size_bytes: int


POLICIES = {
    "landing": MaintenancePolicy(
        optimize_days=3,
        snapshot_days=7,
        orphan_days=7,
        retain_snapshots=5,
        target_file_size_bytes=256 * 1024 * 1024,
    ),
    "curated": MaintenancePolicy(
        optimize_days=7,
        snapshot_days=14,
        orphan_days=14,
        retain_snapshots=10,
        target_file_size_bytes=512 * 1024 * 1024,
    ),
}


def _optimize_filter(
    table: ManagedIcebergTableContract,
    cutoff: date,
) -> str | None:
    if not table.partitioning:
        return None
    partition = table.partitioning[0]
    column = next(column for column in table.columns if column.name == partition.field)
    if column.data_type == "date":
        return f"{partition.field} >= DATE '{cutoff.isoformat()}'"
    if column.data_type == "timestamptz":
        return f"{partition.field} >= TIMESTAMP '{cutoff.isoformat()} 00:00:00'"
    return None


def _string_literal(value: str) -> str:
    """Render a Spark SQL string literal for Iceberg procedure arguments."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _timestamp_literal(value: datetime) -> str:
    timestamp = value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return f"TIMESTAMP {_string_literal(timestamp)}"


def maintenance_statements(
    *,
    table_name: str,
    table: ManagedIcebergTableContract,
    policy: MaintenancePolicy,
    as_of: datetime,
) -> tuple[str, ...]:
    statements = []
    optimize_filter = _optimize_filter(table, (as_of - timedelta(days=policy.optimize_days)).date())
    if optimize_filter:
        statements.append(
            f"""
            CALL {CATALOG_NAME}.system.rewrite_data_files(
                table => {_string_literal(table_name)},
                strategy => 'binpack',
                options => map(
                    'target-file-size-bytes', {_string_literal(str(policy.target_file_size_bytes))},
                    'max-file-group-size-bytes', {_string_literal(str(5 * 1024 * 1024 * 1024))},
                    'partial-progress.enabled', 'true'
                ),
                where => {_string_literal(optimize_filter)}
            )
            """
        )

    statements.append(
        f"""
        CALL {CATALOG_NAME}.system.expire_snapshots(
            table => {_string_literal(table_name)},
            older_than => {_timestamp_literal(as_of - timedelta(days=policy.snapshot_days))},
            retain_last => {policy.retain_snapshots},
            stream_results => true
        )
        """
    )
    statements.append(
        f"""
        CALL {CATALOG_NAME}.system.remove_orphan_files(
            table => {_string_literal(table_name)},
            older_than => {_timestamp_literal(as_of - timedelta(days=policy.orphan_days))},
            max_concurrent_deletes => 20
        )
        """
    )
    return tuple(statements)


def maintain_table(
    spark: Any,
    *,
    table_name: str,
    table: ManagedIcebergTableContract,
    policy: MaintenancePolicy,
    as_of: datetime,
) -> None:
    statements = maintenance_statements(
        table_name=table_name,
        table=table,
        policy=policy,
        as_of=as_of,
    )
    if len(statements) == 2:
        logger.info("Skipping data-file rewrite for unpartitioned table {}", table_name)
    for statement in statements:
        spark.sql(statement)


def run(*, contracts_uri: str) -> None:
    from emr_jobs.common.contracts import load_contracts
    from emr_jobs.common.iceberg import require_tables
    from emr_jobs.common.spark import configure_logging, session

    configure_logging("iceberg_maintenance", "all")
    contracts = load_contracts(contracts_uri)
    declared = tuple(declared_tables(contracts))
    spark = session("iceberg-maintenance")
    try:
        identifiers = tuple(identifier for _, identifier, _, _ in declared)
        require_tables(spark, identifiers)
        as_of = datetime.now(UTC)
        for tier, identifier, _, table in declared:
            table_name = ".".join(identifier.iceberg)
            logger.info("Maintaining {} table {}", tier, table_name)
            maintain_table(
                spark,
                table_name=table_name,
                table=table,
                policy=POLICIES[tier],
                as_of=as_of,
            )
    finally:
        spark.stop()
