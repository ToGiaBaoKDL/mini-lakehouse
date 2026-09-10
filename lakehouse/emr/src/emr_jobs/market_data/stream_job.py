"""Orchestrate terminal SSI Stream capture replay for one trade date."""

from datetime import date

from loguru import logger
from t0_trading.configuration import parse_configuration
from t0_trading.market.reconciliation import certify_market_day

from emr_jobs.common.contracts import load_contracts
from emr_jobs.common.iceberg import qualified_name, require_tables
from emr_jobs.common.s3 import client, read_bytes
from emr_jobs.common.spark import configure_logging, session
from emr_jobs.market_data.stream_capture import discover_captures, load_capture
from emr_jobs.market_data.stream_curated import publish as publish_curated
from emr_jobs.market_data.stream_landing import publish as publish_landing
from emr_jobs.t0_trading.certifications import publish as publish_certification
from emr_jobs.t0_trading.features import publish as publish_features
from emr_jobs.t0_trading.outcomes import publish as publish_outcomes


def run(
    *,
    source_date: str,
    landing_uri: str,
    contracts_uri: str,
    trading_config_uri: str,
) -> None:
    contracts = load_contracts(contracts_uri)
    source = contracts.source("ssi_fastconnect_stream")
    market_data = contracts.curated_product("market_data")
    t0_trading = contracts.curated_product("t0_trading")
    trade_date = date.fromisoformat(source_date)
    trading = parse_configuration(read_bytes(trading_config_uri).decode())
    configuration = trading.resolve(trade_date)
    outcome_policy = trading.resolve_outcomes(trade_date)
    configure_logging("ssi_market_data_stream", source_date)
    s3 = client()
    manifest_uris = discover_captures(
        s3,
        landing_uri=landing_uri,
        trade_date=trade_date,
        raw_object_prefix=source.raw_object_prefix,
    )
    if not manifest_uris:
        logger.info("No terminal SSI Stream sessions found for {}", source_date)
        return
    captures = tuple(load_capture(s3, uri) for uri in manifest_uris)
    if any(capture.trade_date != trade_date for capture in captures):
        raise RuntimeError("SSI Stream capture escaped the requested trade date")
    certification, _ = certify_market_day(
        captures,
        configuration,
        trade_date=trade_date,
    )
    required_identifiers = (
        *(source.table_identifier(table.key) for table in source.tables),
        *(
            market_data.table_identifier(key)
            for key in ("trade_ticks", "quote_snapshots", "quote_levels")
        ),
        *(t0_trading.table_identifier(table.key) for table in t0_trading.tables),
    )

    spark = session(f"ssi-market-data-stream-{source_date}")
    try:
        require_tables(spark, required_identifiers)
        for capture in captures:
            landing_table = publish_landing(spark, source=source, capture=capture)
            publish_curated(
                spark,
                landing_table=landing_table,
                product=market_data,
                capture=capture,
            )
            logger.info(
                "Replayed SSI Stream session {} with {} messages",
                capture.manifest.stream_session_id,
                capture.manifest.message_count,
            )
        publish_certification(
            spark,
            product=t0_trading,
            certification=certification,
        )
        if certification.status != "passed":
            logger.warning(
                "Withheld deterministic features for {}: {}",
                source_date,
                certification.failure_reason,
            )
            return
        feature_capture = next(
            capture
            for capture in captures
            if capture.manifest.stream_session_id == certification.selected_stream_session_id
        )
        landing_table = qualified_name(source.table_identifier("messages"))
        snapshots, feature_audit = publish_features(
            spark,
            landing_table=landing_table,
            product=t0_trading,
            capture=feature_capture,
            configuration=configuration,
        )
        logger.info(
            "Published {} deterministic feature snapshots for SSI Stream session {}",
            feature_audit.snapshot_count,
            feature_capture.manifest.stream_session_id,
        )
        outcome_audit = publish_outcomes(
            spark,
            landing_table=landing_table,
            product=t0_trading,
            capture=feature_capture,
            configuration=configuration,
            policy=outcome_policy,
            snapshots=snapshots,
        )
        logger.info(
            "Published {} deterministic outcome labels for SSI Stream session {}",
            outcome_audit.label_count,
            feature_capture.manifest.stream_session_id,
        )
    finally:
        spark.stop()
