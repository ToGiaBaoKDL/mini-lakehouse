"""Orchestrate one ArXiv OAI source-to-curated job."""

from datetime import date

from loguru import logger

from emr_jobs.arxiv.curated import publish as publish_curated
from emr_jobs.arxiv.landing import publish as publish_landing
from emr_jobs.arxiv.manifest import load_capture
from emr_jobs.arxiv.oai import parse_records
from emr_jobs.common.contracts import load_contracts
from emr_jobs.common.iceberg import require_tables
from emr_jobs.common.spark import configure_logging, session


def run(
    *,
    source_date: str,
    capture_manifest_uri: str,
    contracts_uri: str,
) -> None:
    source_day = date.fromisoformat(source_date)
    configure_logging("arxiv_metadata", source_date)

    contracts = load_contracts(contracts_uri)
    source = contracts.source("arxiv")
    product = contracts.curated_product("arxiv")
    required_identifiers = (
        *(source.table_identifier(table.key) for table in source.tables),
        *(product.table_identifier(key) for key in ("papers", "paper_authors", "paper_categories")),
    )

    capture = load_capture(
        capture_manifest_uri,
        expected_source_date=source_day,
        raw_object_prefix=source.raw_object_prefix,
    )
    records = parse_records(
        capture.pages,
        source_day=source_day,
        page_objects=capture.page_objects,
        ingested_at=capture.published_at,
    )
    spark = session(f"arxiv-metadata-{source_date}")
    try:
        require_tables(spark, required_identifiers)
        records_table, changed = publish_landing(
            spark,
            source=source,
            source_day=source_day,
            records=records,
            manifest_key=capture.manifest_key,
            manifest_sha256=capture.manifest_sha256,
            page_count=len(capture.pages),
            published_at=capture.published_at,
        )
        if changed:
            logger.info(
                "Published {} OAI records from {} pages",
                len(records),
                len(capture.pages),
            )
        else:
            logger.info("Landing publication already matches the OAI manifest")
        publish_curated(
            spark,
            source_table=records_table,
            product=product,
            source_date=source_date,
        )
        logger.info("Published curated ArXiv metadata tables")
    finally:
        spark.stop()
