"""Materialize deterministic T0 feature snapshots from validated landing messages."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from pyspark.sql import SparkSession
from t0_trading.capture.reader import StreamSessionReader
from t0_trading.configuration import TradingVersion
from t0_trading.features import (
    FeatureAuditReport,
    FeatureSnapshot,
    build_feature_audit,
    replay_features,
)

from emr_jobs.common.contracts import spark_schema
from emr_jobs.common.iceberg import qualified_name
from emr_jobs.t0_trading.iceberg import insert_missing, require_compatible
from emr_jobs.t0_trading.landing import envelopes
from lakehouse.contracts.curated import CuratedProductContract


def _materialization_rows(
    snapshots: Sequence[FeatureSnapshot],
    *,
    manifest_sha256: str,
    processed_at: datetime,
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    snapshot_rows: list[dict[str, object]] = []
    window_rows: list[dict[str, object]] = []
    for snapshot in snapshots:
        if snapshot.stream_session_id is None or snapshot.last_receive_sequence is None:
            raise RuntimeError("Persisted feature snapshot is missing stream position lineage")
        fingerprint = snapshot.sha256
        snapshot_rows.append(
            {
                "schema_version": snapshot.schema_version,
                "feature_version": snapshot.feature_version,
                "configuration_version": snapshot.configuration_version,
                "configuration_sha256": snapshot.configuration_sha256,
                "symbol": snapshot.symbol,
                "trade_date": snapshot.trade_date,
                "decision_at": snapshot.decision_at,
                "market_session": snapshot.market_session.value,
                "stream_session_id": snapshot.stream_session_id,
                "last_receive_sequence": snapshot.last_receive_sequence,
                "trade_age_seconds": snapshot.trade_age_seconds,
                "quote_age_seconds": snapshot.quote_age_seconds,
                "mid_price": snapshot.mid_price,
                "microprice": snapshot.microprice,
                "microprice_deviation_bps": snapshot.microprice_deviation_bps,
                "spread": snapshot.spread,
                "spread_bps": snapshot.spread_bps,
                "bid_depth": snapshot.bid_depth,
                "ask_depth": snapshot.ask_depth,
                "level_one_imbalance": snapshot.level_one_imbalance,
                "depth_imbalance": snapshot.depth_imbalance,
                "is_eligible": snapshot.is_eligible,
                "eligibility_reasons_json": json.dumps(
                    snapshot.reasons,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "available_at": snapshot.decision_at,
                "processed_at": processed_at,
                "manifest_sha256": manifest_sha256,
                "snapshot_sha256": fingerprint,
            }
        )
        window_rows.extend(
            {
                "feature_version": snapshot.feature_version,
                "configuration_sha256": snapshot.configuration_sha256,
                "symbol": snapshot.symbol,
                "trade_date": snapshot.trade_date,
                "decision_at": snapshot.decision_at,
                "window_seconds": window.window_seconds,
                "trade_count": window.trade_count,
                "quote_change_count": window.quote_change_count,
                "trade_volume": window.trade_volume,
                "signed_trade_volume": window.signed_trade_volume,
                "trade_volume_per_second": window.trade_volume_per_second,
                "trade_volume_imbalance": window.trade_volume_imbalance,
                "level_one_order_flow_imbalance": window.level_one_order_flow_imbalance,
                "price_return_bps": window.price_return_bps,
                "realized_volatility_bps": window.realized_volatility_bps,
                "vwap": window.vwap,
                "last_price_to_vwap_bps": window.last_price_to_vwap_bps,
                "snapshot_sha256": fingerprint,
            }
            for window in snapshot.windows
        )
    return tuple(snapshot_rows), tuple(window_rows)


def publish(
    spark: SparkSession,
    *,
    landing_table: str,
    product: CuratedProductContract,
    capture: StreamSessionReader,
    configuration: TradingVersion,
) -> tuple[tuple[FeatureSnapshot, ...], FeatureAuditReport]:
    """Replay once, quality-gate the complete clock, then idempotently publish it."""
    manifest = capture.manifest
    trade_date = capture.trade_date
    snapshots = replay_features(
        envelopes(spark, landing_table=landing_table, capture=capture),
        configuration,
        trade_date=trade_date,
    )
    audit = build_feature_audit(
        snapshots,
        configuration,
        trade_date=trade_date,
        manifest_uri=capture.uri,
        stream_session_id=manifest.stream_session_id,
        input_message_count=manifest.message_count,
    )
    processed_at = datetime.now(UTC)
    snapshots_contract = product.table("feature_snapshots")
    windows_contract = product.table("feature_windows")
    snapshot_rows, window_rows = _materialization_rows(
        snapshots,
        manifest_sha256=capture.manifest_sha256,
        processed_at=processed_at,
    )
    snapshot_frame = spark.createDataFrame(
        snapshot_rows,
        spark_schema(snapshots_contract),
    )
    window_frame = spark.createDataFrame(
        window_rows,
        spark_schema(windows_contract),
    )
    snapshot_view = "t0_feature_snapshot_candidates"
    window_view = "t0_feature_window_candidates"
    snapshot_target = qualified_name(product.table_identifier("feature_snapshots"))
    window_target = qualified_name(product.table_identifier("feature_windows"))
    snapshot_frame.createOrReplaceTempView(snapshot_view)
    window_frame.createOrReplaceTempView(window_view)

    # Preflight both immutable targets before the first cross-table mutation.
    require_compatible(
        spark,
        view=snapshot_view,
        target=snapshot_target,
        keys=snapshots_contract.primary_key,
        fingerprint="snapshot_sha256",
    )
    require_compatible(
        spark,
        view=window_view,
        target=window_target,
        keys=windows_contract.primary_key,
        fingerprint="snapshot_sha256",
    )
    # Publish children first. The parent table is the cross-table publication boundary:
    # a failed child merge may leave harmless orphans, but never a visible snapshot
    # without all of its window facts. A retry completes both immutable MERGEs.
    insert_missing(
        spark,
        view=window_view,
        target=window_target,
        keys=windows_contract.primary_key,
    )
    insert_missing(
        spark,
        view=snapshot_view,
        target=snapshot_target,
        keys=snapshots_contract.primary_key,
    )
    return snapshots, audit
