"""Read one validated SSI Stream session from its landing table."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from t0_trading.capture.reader import StreamDayReader
from t0_trading.market import StreamEnvelope


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError("SSI Stream landing timestamp is invalid")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def envelopes(
    spark: SparkSession,
    *,
    landing_table: str,
    capture: StreamDayReader,
) -> Iterator[StreamEnvelope]:
    """Yield selected segments in deterministic receipt order."""
    if capture.message_count < 1:
        raise RuntimeError("T0 materialization requires a non-empty stream capture")
    rows = (
        spark.table(landing_table)
        .filter(F.col("stream_session_id").isin(*capture.stream_session_ids))
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
        .orderBy("received_at", "stream_session_id", "receive_sequence")
        .toLocalIterator(prefetchPartitions=True)
    )
    expected_sequences = dict.fromkeys(capture.stream_session_ids, 1)
    for row in rows:
        expected_sequence = expected_sequences[row.stream_session_id]
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
        expected_sequences[row.stream_session_id] = expected_sequence + 1
    if any(
        expected_sequences[session_id] - 1 != message_count
        for session_id, message_count in capture.session_message_counts.items()
    ):
        raise RuntimeError("SSI Stream landing count does not match its terminal manifest")
