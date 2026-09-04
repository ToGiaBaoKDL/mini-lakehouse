"""Orchestrate one GitHub Archive source-to-curated job."""

from datetime import date, datetime, time, timedelta

from loguru import logger
from pyspark.sql import functions as F

from emr_jobs.common.contracts import load_contracts
from emr_jobs.common.iceberg import qualified_name, require_tables
from emr_jobs.common.spark import configure_logging, session
from emr_jobs.github_archive.curated import publish
from emr_jobs.github_archive.landing import build_frame
from emr_jobs.github_archive.manifest import load_capture


def run(
    *,
    source_date: str,
    capture_manifest_uri: str,
    contracts_uri: str,
) -> None:
    source_day = date.fromisoformat(source_date)
    configure_logging("github_archive", source_date)

    contracts = load_contracts(contracts_uri)
    source = contracts.source("github_archive")
    product = contracts.curated_product("github")
    landing_identifier = source.table_identifier("events_raw")
    required_identifiers = (
        landing_identifier,
        *(product.table_identifier(table.key) for table in product.tables),
    )
    landing_table = qualified_name(landing_identifier)

    captures = load_capture(
        capture_manifest_uri,
        expected_source_date=source_day,
        raw_object_prefix=source.raw_object_prefix,
    )
    spark = session(f"github-archive-{source_date}")
    try:
        require_tables(spark, required_identifiers)
        landing = build_frame(spark, captures)
        logger.info("Validated the GitHub Archive landing partition")
        day_start = datetime.combine(source_day, time.min)
        day_end = day_start + timedelta(days=1)
        try:
            landing.writeTo(landing_table).overwrite(
                (F.col("source_hour") >= F.lit(day_start)) & (F.col("source_hour") < F.lit(day_end))
            )
        finally:
            landing.unpersist()
        publish(
            spark,
            landing_table=landing_table,
            product=product,
            source_date=source_date,
        )
        logger.info("Published landing and curated GitHub tables")
    finally:
        spark.stop()
