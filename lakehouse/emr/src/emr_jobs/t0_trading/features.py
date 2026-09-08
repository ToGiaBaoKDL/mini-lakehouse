"""Materialize deterministic T0 feature snapshots from validated landing messages."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from t0_trading.capture.reader import StreamSessionReader
from t0_trading.configuration import TradingVersion
from t0_trading.features import (
    FeatureAuditReport,
    FeatureSnapshot,
    build_feature_audit,
    replay_features,
)
from t0_trading.market import StreamEnvelope

from emr_jobs.common.contracts import spark_schema
from emr_jobs.common.iceberg import qualified_name
from lakehouse.contracts.curated import CuratedProductContract


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError("SSI Stream landing timestamp is invalid")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _envelopes(
    spark: SparkSession,
    *,
    landing_table: str,
    capture: StreamSessionReader,
) -> Iterator[StreamEnvelope]:
    manifest = capture.manifest
    if manifest.message_count < 1:
        raise RuntimeError("Feature materialization requires a non-empty stream capture")
    rows = (
        spark.table(landing_table)
        .filter(F.col("stream_session_id") == manifest.stream_session_id)
        .select(
            "stream_session_id",
            "receive_sequence",
            "message_type",
            "symbol",
            "source_time_text",
            "received_at",
            "message_json",
            "message_sha256",
        )
        .orderBy("receive_sequence")
        .toLocalIterator(prefetchPartitions=True)
    )
    expected_sequence = 1
    for row in rows:
        if row.receive_sequence != expected_sequence:
            raise RuntimeError("SSI Stream landing sequence is not contiguous")
        yield StreamEnvelope(
            stream_session_id=row.stream_session_id,
            receive_sequence=row.receive_sequence,
            message_type=row.message_type,
            symbol=row.symbol,
            source_time_text=row.source_time_text,
            received_at=_utc(row.received_at),
            message_json=row.message_json,
            message_sha256=row.message_sha256,
        )
        expected_sequence += 1
    if expected_sequence - 1 != manifest.message_count:
        raise RuntimeError("SSI Stream landing count does not match its terminal manifest")


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


def _join(keys: Sequence[str]) -> str:
    return " AND ".join(f"target.{key} = source.{key}" for key in keys)


def _require_compatible(
    spark: SparkSession,
    *,
    view: str,
    target: str,
    keys: Sequence[str],
) -> None:
    conflict = spark.sql(
        f"""
        SELECT 1
        FROM {view} source
        JOIN {target} target ON {_join(keys)}
        WHERE target.snapshot_sha256 != source.snapshot_sha256
        LIMIT 1
        """
    ).count()
    if conflict:
        raise RuntimeError(f"Immutable T0 feature conflict in {target}")


def _insert_missing(
    spark: SparkSession,
    *,
    view: str,
    target: str,
    keys: Sequence[str],
) -> None:
    spark.sql(
        f"""
        MERGE INTO {target} target
        USING {view} source
        ON {_join(keys)}
        WHEN NOT MATCHED THEN INSERT *
        """
    )


def publish(
    spark: SparkSession,
    *,
    landing_table: str,
    product: CuratedProductContract,
    capture: StreamSessionReader,
    configuration: TradingVersion,
) -> FeatureAuditReport:
    """Replay once, quality-gate the complete clock, then idempotently publish it."""
    manifest = capture.manifest
    trade_date = capture.trade_date
    snapshots = replay_features(
        _envelopes(spark, landing_table=landing_table, capture=capture),
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
    _require_compatible(
        spark,
        view=snapshot_view,
        target=snapshot_target,
        keys=snapshots_contract.primary_key,
    )
    _require_compatible(
        spark,
        view=window_view,
        target=window_target,
        keys=windows_contract.primary_key,
    )
    # Publish children first. The parent table is the cross-table publication boundary:
    # a failed child merge may leave harmless orphans, but never a visible snapshot
    # without all of its window facts. A retry completes both immutable MERGEs.
    _insert_missing(
        spark,
        view=window_view,
        target=window_target,
        keys=windows_contract.primary_key,
    )
    _insert_missing(
        spark,
        view=snapshot_view,
        target=snapshot_target,
        keys=snapshots_contract.primary_key,
    )
    return audit
