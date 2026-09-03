"""Normalize verified SSI Stream messages into replay-safe market facts."""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from emr_jobs.common.iceberg import qualified_name
from emr_jobs.market_data.stream_manifest import StreamCapture
from lakehouse.contracts.curated import CuratedProductContract


def _capture_view(
    spark: SparkSession,
    *,
    landing_table: str,
    capture: StreamCapture,
) -> None:
    sessions = spark.createDataFrame([(capture.stream_session_id,)], "stream_session_id string")
    sessions.createOrReplaceTempView("ssi_stream_session_ids")
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW ssi_stream_messages AS
        SELECT messages.*
        FROM {landing_table} messages
        JOIN ssi_stream_session_ids sessions USING (stream_session_id)
        """
    )


def _trade_view(spark: SparkSession, capture: StreamCapture) -> None:
    spark.sql(
        """
        CREATE OR REPLACE TEMP VIEW ssi_stream_trade_candidates AS
        SELECT
            stream_session_id,
            receive_sequence,
            upper(get_json_object(message_json, '$.symbol')) AS symbol,
            to_date(get_json_object(message_json, '$.trading_time'),
                'yyyy/MM/dd HH:mm:ss') AS trade_date,
            to_utc_timestamp(
                to_timestamp(get_json_object(message_json, '$.trading_time'),
                    'yyyy/MM/dd HH:mm:ss'),
                'Asia/Ho_Chi_Minh'
            ) AS event_time,
            received_at,
            received_at AS available_at,
            current_timestamp() AS processed_at,
            try_cast(get_json_object(message_json, '$.price') AS decimal(18, 0)) AS price,
            try_cast(get_json_object(message_json, '$.quantity') AS bigint) AS quantity,
            CASE upper(get_json_object(message_json, '$.side'))
                WHEN 'B' THEN 'BUY'
                WHEN 'S' THEN 'SELL'
            END AS aggressor_side,
            try_cast(get_json_object(message_json, '$.total_volume') AS bigint)
                AS cumulative_volume,
            CAST(NULL AS decimal(24, 0)) AS cumulative_value,
            message_sha256,
            lower(get_json_object(message_json, '$.type')) AS provider_type
        FROM ssi_stream_messages
        WHERE message_type = 'TradeMessage'
        """
    )
    invalid = spark.sql(
        f"""
        SELECT 1 FROM ssi_stream_trade_candidates
        WHERE symbol IS NULL OR trade_date IS NULL OR trade_date != DATE '{capture.trade_date}'
           OR event_time IS NULL OR received_at IS NULL OR received_at < event_time
           OR price IS NULL OR price <= 0 OR quantity IS NULL OR quantity <= 0
           OR aggressor_side IS NULL OR cumulative_volume IS NULL
           OR cumulative_volume < quantity
           OR provider_type IS NULL OR provider_type != 'trade' OR message_sha256 IS NULL
        LIMIT 1
        """
    ).count()
    if invalid:
        raise RuntimeError("SSI Stream trade normalization produced invalid values")
    spark.sql(
        """
        CREATE OR REPLACE TEMP VIEW ssi_stream_trade_rows AS
        SELECT stream_session_id, receive_sequence, symbol, trade_date, event_time,
            received_at, available_at, processed_at, price, quantity, aggressor_side,
            cumulative_volume, cumulative_value, message_sha256
        FROM ssi_stream_trade_candidates
        """
    )


def _quote_views(spark: SparkSession, capture: StreamCapture) -> DataFrame:
    quotes = spark.sql(
        """
        SELECT
            stream_session_id,
            receive_sequence,
            upper(get_json_object(message_json, '$.symbol')) AS symbol,
            to_date(get_json_object(message_json, '$.trading_time'),
                'yyyy/MM/dd HH:mm:ss') AS trade_date,
            to_utc_timestamp(
                to_timestamp(get_json_object(message_json, '$.trading_time'),
                    'yyyy/MM/dd HH:mm:ss'),
                'Asia/Ho_Chi_Minh'
            ) AS event_time,
            received_at,
            received_at AS available_at,
            current_timestamp() AS processed_at,
            from_json(get_json_object(message_json, '$.bid_prices'),
                'array<decimal(18,0)>') AS bid_prices,
            from_json(get_json_object(message_json, '$.bid_volumes'),
                'array<bigint>') AS bid_volumes,
            from_json(get_json_object(message_json, '$.ask_prices'),
                'array<decimal(18,0)>') AS ask_prices,
            from_json(get_json_object(message_json, '$.ask_volumes'),
                'array<bigint>') AS ask_volumes,
            message_sha256,
            lower(get_json_object(message_json, '$.type')) AS provider_type
        FROM ssi_stream_messages
        WHERE message_type = 'QuoteMessage'
        """
    ).cache()
    quotes.createOrReplaceTempView("ssi_stream_quote_values")
    invalid = spark.sql(
        f"""
        SELECT 1 FROM ssi_stream_quote_values
        WHERE symbol IS NULL OR trade_date IS NULL OR trade_date != DATE '{capture.trade_date}'
           OR event_time IS NULL OR received_at IS NULL OR received_at < event_time
           OR provider_type IS NULL OR provider_type != 'quote' OR message_sha256 IS NULL
           OR bid_prices IS NULL OR bid_volumes IS NULL
           OR ask_prices IS NULL OR ask_volumes IS NULL
           OR size(bid_prices) != 10 OR size(bid_volumes) != 10
           OR size(ask_prices) != 10 OR size(ask_volumes) != 10
           OR exists(bid_prices, value -> value IS NULL)
           OR exists(bid_volumes, value -> value IS NULL)
           OR exists(ask_prices, value -> value IS NULL)
           OR exists(ask_volumes, value -> value IS NULL)
           OR exists(bid_prices, value -> value < 0)
           OR exists(bid_volumes, value -> value < 0)
           OR exists(ask_prices, value -> value < 0)
           OR exists(ask_volumes, value -> value < 0)
           OR array_contains(zip_with(bid_prices, bid_volumes,
                (price, quantity) -> (price = 0) != (quantity = 0)), true)
           OR array_contains(zip_with(ask_prices, ask_volumes,
                (price, quantity) -> (price = 0) != (quantity = 0)), true)
           OR exists(slice(bid_prices, 4, 7), value -> value != 0)
           OR exists(slice(bid_volumes, 4, 7), value -> value != 0)
           OR exists(slice(ask_prices, 4, 7), value -> value != 0)
           OR exists(slice(ask_volumes, 4, 7), value -> value != 0)
        LIMIT 1
        """
    ).count()
    if invalid:
        quotes.unpersist()
        raise RuntimeError("SSI Stream quote normalization produced invalid Top-3 values")

    quotes.select(
        "stream_session_id",
        "receive_sequence",
        "symbol",
        "trade_date",
        "event_time",
        "received_at",
        "available_at",
        "processed_at",
        F.lit("FULL_TOP_3").alias("update_kind"),
        F.lit(True).alias("is_complete"),
        "message_sha256",
    ).createOrReplaceTempView("ssi_stream_quote_candidates")

    identity = (
        "stream_session_id",
        "receive_sequence",
        "symbol",
        "trade_date",
        "available_at",
    )
    bid_levels = (
        quotes.select(
            *identity,
            F.posexplode(F.arrays_zip("bid_prices", "bid_volumes")).alias("level_offset", "quoted"),
        )
        .select(
            *identity,
            F.lit("BID").alias("side"),
            (F.col("level_offset") + 1).cast("long").alias("level"),
            F.col("quoted.bid_prices").alias("price"),
            F.col("quoted.bid_volumes").alias("quantity"),
        )
        .filter((F.col("price") > 0) & (F.col("quantity") > 0))
    )
    ask_levels = (
        quotes.select(
            *identity,
            F.posexplode(F.arrays_zip("ask_prices", "ask_volumes")).alias("level_offset", "quoted"),
        )
        .select(
            *identity,
            F.lit("ASK").alias("side"),
            (F.col("level_offset") + 1).cast("long").alias("level"),
            F.col("quoted.ask_prices").alias("price"),
            F.col("quoted.ask_volumes").alias("quantity"),
        )
        .filter((F.col("price") > 0) & (F.col("quantity") > 0))
    )
    bid_levels.unionByName(ask_levels).select(
        "stream_session_id",
        "receive_sequence",
        "side",
        "level",
        "symbol",
        "trade_date",
        "price",
        "quantity",
        F.lit(None).cast("long").alias("order_count"),
        "available_at",
    ).createOrReplaceTempView("ssi_stream_quote_level_candidates")
    invalid_order = spark.sql(
        """
        SELECT 1 FROM (
            SELECT side, price,
                lag(price) OVER (
                    PARTITION BY stream_session_id, receive_sequence, side ORDER BY level
                ) AS previous_price
            FROM ssi_stream_quote_level_candidates
        ) levels
        WHERE (side = 'BID' AND price >= previous_price)
           OR (side = 'ASK' AND price <= previous_price)
        LIMIT 1
        """
    ).count()
    if invalid_order:
        quotes.unpersist()
        raise RuntimeError("SSI Stream quote levels are not ordered best-to-worst")
    return quotes


def _merge_ticks(spark: SparkSession, target: str) -> None:
    conflict = spark.sql(
        f"""
        SELECT 1
        FROM ssi_stream_trade_rows source
        JOIN {target} target USING (stream_session_id, receive_sequence)
        WHERE target.message_sha256 != source.message_sha256
        LIMIT 1
        """
    ).count()
    if conflict:
        raise RuntimeError("Immutable SSI Stream trade-tick conflict")
    spark.sql(
        f"""
        MERGE INTO {target} target
        USING ssi_stream_trade_rows source
        ON target.stream_session_id = source.stream_session_id
           AND target.receive_sequence = source.receive_sequence
        WHEN NOT MATCHED THEN INSERT *
        """
    )


def _merge_quotes(spark: SparkSession, snapshots: str, levels: str) -> None:
    snapshot_conflict = spark.sql(
        f"""
        SELECT 1
        FROM ssi_stream_quote_candidates source
        JOIN {snapshots} target USING (stream_session_id, receive_sequence)
        WHERE target.message_sha256 != source.message_sha256
        LIMIT 1
        """
    ).count()
    if snapshot_conflict:
        raise RuntimeError("Immutable SSI Stream quote-snapshot conflict")
    spark.sql(
        f"""
        MERGE INTO {snapshots} target
        USING ssi_stream_quote_candidates source
        ON target.stream_session_id = source.stream_session_id
           AND target.receive_sequence = source.receive_sequence
        WHEN NOT MATCHED THEN INSERT *
        """
    )

    level_conflict = spark.sql(
        f"""
        SELECT 1
        FROM ssi_stream_quote_level_candidates source
        JOIN {levels} target
          USING (stream_session_id, receive_sequence, side, level)
        WHERE target.price != source.price OR target.quantity != source.quantity
        LIMIT 1
        """
    ).count()
    if level_conflict:
        raise RuntimeError("Immutable SSI Stream quote-level conflict")
    spark.sql(
        f"""
        MERGE INTO {levels} target
        USING ssi_stream_quote_level_candidates source
        ON target.stream_session_id = source.stream_session_id
           AND target.receive_sequence = source.receive_sequence
           AND target.side = source.side
           AND target.level = source.level
        WHEN NOT MATCHED THEN INSERT *
        """
    )


def publish(
    spark: SparkSession,
    *,
    landing_table: str,
    product: CuratedProductContract,
    capture: StreamCapture,
) -> None:
    _capture_view(spark, landing_table=landing_table, capture=capture)
    _trade_view(spark, capture)
    quotes = _quote_views(spark, capture)
    try:
        _merge_ticks(spark, qualified_name(product.table_identifier("trade_ticks")))
        _merge_quotes(
            spark,
            qualified_name(product.table_identifier("quote_snapshots")),
            qualified_name(product.table_identifier("quote_levels")),
        )
    finally:
        quotes.unpersist()
