"""Publish SDK-normalized constituent revisions without inventing finality or availability."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from t0_trading.capture.reader import StreamDayReader, StreamSessionReader
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.events import StreamEnvelope
from t0_trading.market.intervals import provider_bar

from lakehouse.contracts.curated import CuratedProductContract

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

_ROW_ADAPTER = TypeAdapter(dict[str, object])


def bar_rows(
    source: Iterable[StreamEnvelope],
    capture: StreamSessionReader,
    *,
    timezone: ZoneInfo,
    processed_at: datetime,
) -> Iterator[dict[str, object]]:
    snapshot = capture.manifest.breadth_membership
    if snapshot is None:
        return
    symbols = set(snapshot.symbols)
    for envelope in source:
        if envelope.symbol not in symbols:
            continue
        bar = provider_bar(envelope, timezone)
        if bar is None:
            continue
        if (
            bar.start.astimezone(timezone).date() != capture.trade_date
            or envelope.stream_session_id != capture.manifest.stream_session_id
        ):
            raise ValueError("constituent bar does not match capture lineage")
        values: dict[str, object] = {
            "stream_session_id": envelope.stream_session_id,
            "receive_sequence": envelope.receive_sequence,
            "symbol": bar.symbol,
            "trade_date": capture.trade_date,
            "bar_start": bar.start.astimezone(UTC),
            "observed_at": bar.observed_at.astimezone(UTC),
            "received_at": envelope.received_at,
            "source_kind": "ssi_stream_interval",
            "open_price": bar.open_price,
            "high_price": bar.high_price,
            "low_price": bar.low_price,
            "close_price": bar.close_price,
            "volume": bar.volume,
            "membership_sha256": snapshot.snapshot_sha256,
            "message_sha256": envelope.message_sha256,
            "manifest_uri": capture.uri,
            "manifest_sha256": capture.manifest_sha256,
        }
        yield {
            **values,
            "source_record_sha256": sha256(
                canonical_json(_ROW_ADAPTER.dump_python(values, mode="json"))
            ),
            "processed_at": processed_at,
        }


def publish(
    spark: SparkSession,
    *,
    landing_table: str,
    product: CuratedProductContract,
    capture: StreamSessionReader,
    timezone: str,
) -> None:
    from emr_jobs.common.contracts import spark_schema
    from emr_jobs.common.iceberg import qualified_name
    from emr_jobs.t0_trading.iceberg import insert_missing, require_compatible
    from emr_jobs.t0_trading.landing import envelopes

    snapshot = capture.manifest.breadth_membership
    if snapshot is None:
        return
    zone = ZoneInfo(timezone)
    if snapshot.captured_at.astimezone(zone).date() != capture.trade_date:
        raise ValueError("membership snapshot must belong to the capture date")
    processed_at = datetime.now(UTC)
    membership = [
        {
            "snapshot_sha256": snapshot.snapshot_sha256,
            "trade_date": capture.trade_date,
            "captured_at": snapshot.captured_at,
            "source_kind": "ssi_rest_index_membership",
            "memberships_json": canonical_json(
                [item.model_dump(mode="json") for item in snapshot.memberships]
            ).decode(),
            "processed_at": processed_at,
        }
    ]
    rows = list(
        bar_rows(
            envelopes(spark, landing_table=landing_table, capture=StreamDayReader((capture,)))
            if capture.manifest.message_count
            else (),
            capture,
            timezone=zone,
            processed_at=processed_at,
        )
    )
    for key, facts, fingerprint in (
        ("index_membership_snapshots", membership, "memberships_json"),
        ("constituent_bars_1m", rows, "source_record_sha256"),
    ):
        if not facts:
            continue
        table = product.table(key)
        view = f"ssi_{key}_rows"
        target = qualified_name(product.table_identifier(key))
        spark.createDataFrame(facts, schema=spark_schema(table)).createOrReplaceTempView(view)
        require_compatible(
            spark, view=view, target=target, keys=table.primary_key, fingerprint=fingerprint
        )
        insert_missing(spark, view=view, target=target, keys=table.primary_key)
