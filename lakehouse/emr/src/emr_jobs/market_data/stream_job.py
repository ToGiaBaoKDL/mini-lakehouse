"""Orchestrate one terminal SSI Stream capture replay."""

from loguru import logger

from emr_jobs.common.contracts import load_contracts
from emr_jobs.common.iceberg import require_tables
from emr_jobs.common.spark import configure_logging, session
from emr_jobs.market_data.stream_curated import publish as publish_curated
from emr_jobs.market_data.stream_landing import publish as publish_landing
from emr_jobs.market_data.stream_manifest import load_capture


def run(*, capture_manifest_uri: str, contracts_uri: str) -> None:
    contracts = load_contracts(contracts_uri)
    source = contracts.source("ssi_fastconnect_stream")
    product = contracts.curated_product("market_data")
    capture = load_capture(capture_manifest_uri, source.raw_object_prefix)
    configure_logging("ssi_market_data_stream", capture.trade_date)
    required_identifiers = (
        *(source.table_identifier(table.key) for table in source.tables),
        *(
            product.table_identifier(key)
            for key in ("trade_ticks", "quote_snapshots", "quote_levels")
        ),
    )

    spark = session(f"ssi-market-data-stream-{capture.trade_date}")
    try:
        require_tables(spark, required_identifiers)
        landing_table = publish_landing(spark, source=source, capture=capture)
        publish_curated(
            spark,
            landing_table=landing_table,
            product=product,
            capture=capture,
        )
        logger.info(
            "Replayed SSI Stream session {} with {} messages",
            capture.stream_session_id,
            capture.message_count,
        )
    finally:
        spark.stop()
