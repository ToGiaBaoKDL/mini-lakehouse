"""Orchestrate one GitHub Archive source-to-curated job."""

from datetime import date

from loguru import logger

from lakehouse_jobs.common.contracts import load_contracts, spark_identifier
from lakehouse_jobs.common.s3 import split_uri
from lakehouse_jobs.common.spark import configure_logging, require_tables, session
from lakehouse_jobs.github_archive.curated import publish
from lakehouse_jobs.github_archive.extract import capture_day
from lakehouse_jobs.github_archive.landing import build_frame


def run(
    *,
    source_date: str,
    landing_uri: str,
    contracts_uri: str,
    catalog_name: str,
    capture_workers: int,
) -> None:
    date.fromisoformat(source_date)
    split_uri(landing_uri)
    split_uri(contracts_uri)
    configure_logging("github_archive", source_date)

    contracts = load_contracts(contracts_uri)
    source = contracts.source("github_archive")
    product = contracts.curated_product("github")
    landing_table = spark_identifier(catalog_name, source.table_identifier("events_raw"))
    curated_tables = tuple(
        spark_identifier(catalog_name, product.table_identifier(table.key))
        for table in product.tables
    )

    spark = session(f"github-archive-{source_date}")
    try:
        require_tables(spark, (landing_table, *curated_tables))
        captures = capture_day(
            source_date=source_date,
            landing_uri=landing_uri,
            raw_object_prefix=source.raw_object_prefix,
            workers=capture_workers,
        )
        logger.info("Captured all {} GitHub Archive hours", len(captures))
        landing = build_frame(spark, captures)
        logger.info("Validated {} landing events", landing.count())
        landing.writeTo(landing_table).overwritePartitions()
        publish(
            spark,
            catalog_name=catalog_name,
            landing_table=landing_table,
            product=product,
            source_date=source_date,
        )
        logger.info("Published landing and curated GitHub tables")
    finally:
        spark.stop()
