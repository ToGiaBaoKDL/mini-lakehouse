"""Publish the current deterministic market-day eligibility assessment."""

from datetime import UTC, datetime

from pyspark.sql import SparkSession
from t0_trading.market.reconciliation import MarketDayCertification

from emr_jobs.common.contracts import spark_schema
from emr_jobs.common.iceberg import qualified_name
from lakehouse.contracts.curated import CuratedProductContract


def publish(
    spark: SparkSession,
    *,
    product: CuratedProductContract,
    certification: MarketDayCertification,
) -> None:
    """Upsert one monotonic evidence assessment without mutating identical replays."""
    contract = product.table("market_day_certifications")
    target = qualified_name(product.table_identifier(contract.key))
    frame = spark.createDataFrame(
        [
            {
                **certification.model_dump(mode="python"),
                "evaluated_at": datetime.now(UTC),
            }
        ],
        spark_schema(contract),
    )
    view = "t0_market_day_certification_candidate"
    frame.createOrReplaceTempView(view)
    conflict = spark.sql(
        f"""
        SELECT 1
        FROM {view} source
        JOIN {target} target
          ON target.trade_date = source.trade_date
         AND target.configuration_sha256 = source.configuration_sha256
        WHERE source.manifest_count < target.manifest_count
           OR (source.manifest_count = target.manifest_count
               AND source.evidence_sha256 != target.evidence_sha256)
           OR (source.evidence_sha256 = target.evidence_sha256 AND NOT (
                source.configuration_version <=> target.configuration_version
            AND source.status <=> target.status
            AND source.failure_reason <=> target.failure_reason
            AND source.full_window_session_count <=> target.full_window_session_count
            AND source.eligible_session_count <=> target.eligible_session_count
            AND source.selected_stream_session_id <=> target.selected_stream_session_id
           ))
        LIMIT 1
        """
    ).count()
    if conflict:
        raise RuntimeError("T0 market-day certification conflicts with durable evidence")
    spark.sql(
        f"""
        MERGE INTO {target} target
        USING {view} source
        ON target.trade_date = source.trade_date
           AND target.configuration_sha256 = source.configuration_sha256
        WHEN MATCHED AND source.manifest_count > target.manifest_count THEN UPDATE SET
            configuration_version = source.configuration_version,
            status = source.status,
            failure_reason = source.failure_reason,
            manifest_count = source.manifest_count,
            full_window_session_count = source.full_window_session_count,
            eligible_session_count = source.eligible_session_count,
            selected_stream_session_id = source.selected_stream_session_id,
            evidence_sha256 = source.evidence_sha256,
            evaluated_at = source.evaluated_at
        WHEN NOT MATCHED THEN INSERT *
        """
    )
