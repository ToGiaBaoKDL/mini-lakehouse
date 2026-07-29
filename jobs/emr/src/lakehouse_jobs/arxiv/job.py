"""Orchestrate one ArXiv OAI source-to-curated job."""

from datetime import UTC, date, datetime

from loguru import logger

from lakehouse_jobs.arxiv.curated import publish as publish_curated
from lakehouse_jobs.arxiv.landing import archive_pages
from lakehouse_jobs.arxiv.landing import publish as publish_landing
from lakehouse_jobs.arxiv.oai import harvest, parse_records
from lakehouse_jobs.common.contracts import load_contracts, spark_identifier
from lakehouse_jobs.common.s3 import split_uri
from lakehouse_jobs.common.spark import configure_logging, require_tables, session


def run(
    *,
    source_date: str,
    landing_uri: str,
    contracts_uri: str,
    catalog_name: str,
    max_pages: int,
) -> None:
    source_day = date.fromisoformat(source_date)
    split_uri(landing_uri)
    split_uri(contracts_uri)
    configure_logging("arxiv_metadata", source_date)

    contracts = load_contracts(contracts_uri)
    source = contracts.source("arxiv")
    product = contracts.curated_product("arxiv")
    landing_tables = tuple(
        spark_identifier(catalog_name, source.table_identifier(table.key))
        for table in source.tables
    )
    curated_tables = tuple(
        spark_identifier(catalog_name, product.table_identifier(key))
        for key in ("papers", "paper_authors", "paper_categories")
    )

    spark = session(f"arxiv-metadata-{source_date}")
    try:
        require_tables(spark, (*landing_tables, *curated_tables))
        pages = harvest(source_date, max_pages)
        page_objects, manifest_key, manifest_sha256 = archive_pages(
            pages,
            source_date=source_date,
            landing_uri=landing_uri,
            raw_object_prefix=source.raw_object_prefix,
        )
        published_at = datetime.now(UTC)
        records = parse_records(
            pages,
            source_day=source_day,
            page_objects=page_objects,
            ingested_at=published_at,
        )
        records_table, changed = publish_landing(
            spark,
            catalog_name=catalog_name,
            source=source,
            source_day=source_day,
            records=records,
            manifest_key=manifest_key,
            manifest_sha256=manifest_sha256,
            page_count=len(pages),
            published_at=published_at,
        )
        if changed:
            logger.info("Published {} OAI records from {} pages", len(records), len(pages))
        else:
            logger.info("Landing publication already matches the OAI manifest")
        publish_curated(
            spark,
            catalog_name=catalog_name,
            source_table=records_table,
            product=product,
            source_date=source_date,
        )
        logger.info("Published curated ArXiv metadata tables")
    finally:
        spark.stop()
