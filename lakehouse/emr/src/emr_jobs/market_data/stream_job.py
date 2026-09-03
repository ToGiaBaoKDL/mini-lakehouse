"""Orchestrate terminal SSI Stream capture replay for one trade date."""

from loguru import logger

from emr_jobs.common.contracts import load_contracts
from emr_jobs.common.iceberg import require_tables
from emr_jobs.common.spark import configure_logging, session
from emr_jobs.market_data.stream_curated import publish as publish_curated
from emr_jobs.market_data.stream_landing import publish as publish_landing
from emr_jobs.market_data.stream_manifest import capture_manifest_uris, load_capture


def run(*, source_date: str, landing_uri: str, contracts_uri: str) -> None:
    contracts = load_contracts(contracts_uri)
    source = contracts.source("ssi_fastconnect_stream")
    product = contracts.curated_product("market_data")
    configure_logging("ssi_market_data_stream", source_date)
    manifest_uris = capture_manifest_uris(landing_uri, source_date, source.raw_object_prefix)
    if not manifest_uris:
        logger.info("No terminal SSI Stream sessions found for {}", source_date)
        return
    captures = tuple(load_capture(uri, source.raw_object_prefix) for uri in manifest_uris)
    if any(capture.trade_date != source_date for capture in captures):
        raise RuntimeError("SSI Stream capture escaped the requested trade date")
    required_identifiers = (
        *(source.table_identifier(table.key) for table in source.tables),
        *(
            product.table_identifier(key)
            for key in ("trade_ticks", "quote_snapshots", "quote_levels")
        ),
    )

    spark = session(f"ssi-market-data-stream-{source_date}")
    try:
        require_tables(spark, required_identifiers)
        for capture in captures:
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
