"""Materialize deterministic conditional execution outcomes for certified features."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from pyspark.sql import SparkSession
from t0_trading.capture.reader import StreamSessionReader
from t0_trading.configuration import OutcomeVersion, TradingVersion
from t0_trading.features import FeatureSnapshot
from t0_trading.outcomes import (
    OutcomeAuditReport,
    OutcomeLabel,
    build_outcome_audit,
    label_outcomes,
)

from emr_jobs.common.contracts import spark_schema
from emr_jobs.common.iceberg import qualified_name
from emr_jobs.t0_trading.iceberg import insert_missing, require_compatible
from emr_jobs.t0_trading.landing import envelopes
from lakehouse.contracts.curated import CuratedProductContract


def _rows(
    labels: Sequence[OutcomeLabel],
    *,
    processed_at: datetime,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "schema_version": label.schema_version,
            "outcome_version": label.outcome_version,
            "outcome_configuration_sha256": label.outcome_configuration_sha256,
            "feature_version": label.feature_version,
            "feature_configuration_sha256": label.feature_configuration_sha256,
            "feature_snapshot_sha256": label.feature_snapshot_sha256,
            "stream_session_id": label.stream_session_id,
            "symbol": label.symbol,
            "trade_date": label.trade_date,
            "decision_at": label.decision_at,
            "action": label.action,
            "horizon_seconds": label.horizon_seconds,
            "order_quantity": label.order_quantity,
            "entry_at": label.entry_at,
            "horizon_at": label.horizon_at,
            "entry_quote_received_at": label.entry_quote_received_at,
            "entry_receive_sequence": label.entry_receive_sequence,
            "entry_vwap": label.entry_vwap,
            "horizon_quote_received_at": label.horizon_quote_received_at,
            "horizon_receive_sequence": label.horizon_receive_sequence,
            "horizon_vwap": label.horizon_vwap,
            "gross_return_bps": label.gross_return_bps,
            "is_eligible": label.is_eligible,
            "outcome_reasons_json": json.dumps(
                label.reasons,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "processed_at": processed_at,
            "outcome_sha256": label.sha256,
        }
        for label in labels
    )


def publish(
    spark: SparkSession,
    *,
    landing_table: str,
    product: CuratedProductContract,
    capture: StreamSessionReader,
    configuration: TradingVersion,
    policy: OutcomeVersion,
    snapshots: Sequence[FeatureSnapshot],
) -> OutcomeAuditReport:
    """Quality-gate and idempotently publish one complete outcome matrix."""
    manifest = capture.manifest
    labels = label_outcomes(
        snapshots,
        envelopes(spark, landing_table=landing_table, capture=capture),
        configuration,
        policy,
    )
    audit = build_outcome_audit(
        snapshots,
        labels,
        configuration,
        policy,
        trade_date=capture.trade_date,
        manifest_uri=capture.uri,
        stream_session_id=manifest.stream_session_id,
        input_message_count=manifest.message_count,
    )
    contract = product.table("outcome_labels")
    frame = spark.createDataFrame(
        _rows(labels, processed_at=datetime.now(UTC)),
        spark_schema(contract),
    )
    view = "t0_outcome_label_candidates"
    target = qualified_name(product.table_identifier("outcome_labels"))
    frame.createOrReplaceTempView(view)
    require_compatible(
        spark,
        view=view,
        target=target,
        keys=contract.primary_key,
        fingerprint="outcome_sha256",
    )
    insert_missing(
        spark,
        view=view,
        target=target,
        keys=contract.primary_key,
    )
    return audit
