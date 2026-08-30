"""Orchestrate one bounded SSI REST capture publication."""

from datetime import date

from loguru import logger

from emr_jobs.common.contracts import load_contracts
from emr_jobs.common.iceberg import require_tables
from emr_jobs.common.spark import configure_logging, session
from emr_jobs.market_data.curated import publish as publish_curated
from emr_jobs.market_data.landing import publish as publish_landing
from emr_jobs.market_data.manifest import load_capture, require_bounded_scope


def run(*, source_date: str, capture_manifest_uri: str, contracts_uri: str) -> None:
    date.fromisoformat(source_date)
    configure_logging("ssi_market_data", source_date)
    contracts = load_contracts(contracts_uri)
    source = contracts.source("ssi_fastconnect_rest")
    product = contracts.curated_product("market_data")
    capture = load_capture(capture_manifest_uri, source_date)
    require_bounded_scope(capture)
    required_identifiers = (
        *(source.table_identifier(table.key) for table in source.tables),
        *(
            product.table_identifier(key)
            for key in (
                "securities",
                "daily_security_summaries",
                "intraday_bars_1m",
                "index_snapshots",
            )
        ),
    )

    spark = session(f"ssi-market-data-{source_date}")
    try:
        require_tables(spark, required_identifiers)
        landing_table = publish_landing(spark, source=source, capture=capture)
        has_market_data = publish_curated(
            spark,
            landing_table=landing_table,
            product=product,
            capture=capture,
            source_date=source_date,
        )
        if has_market_data:
            logger.info("Published reconciled SSI market data for {}", source_date)
        else:
            logger.info("Published SSI reference data; no market facts existed for {}", source_date)
    finally:
        spark.stop()
