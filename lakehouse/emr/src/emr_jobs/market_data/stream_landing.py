"""Publish verified SSI Stream batches into append-oriented landing tables."""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import LongType, StringType, StructField, StructType

from emr_jobs.common.contracts import spark_schema
from emr_jobs.common.iceberg import qualified_name
from emr_jobs.market_data.stream_manifest import StreamCapture
from lakehouse.contracts.sources import SourceContract

RAW_SCHEMA = StructType(
    [
        StructField("stream_session_id", StringType(), False),
        StructField("receive_sequence", LongType(), False),
        StructField("message_type", StringType(), False),
        StructField("subscription_context", StringType(), True),
        StructField("provider_topic", StringType(), True),
        StructField("symbol", StringType(), True),
        StructField("source_time_text", StringType(), True),
        StructField("received_at", StringType(), False),
        StructField("message_json", StringType(), False),
        StructField("message_sha256", StringType(), False),
        StructField("api_version", StringType(), False),
        StructField("sdk_version", StringType(), False),
    ]
)


def _messages_frame(spark: SparkSession, capture: StreamCapture) -> DataFrame | None:
    if not capture.batches:
        return None
    metadata = spark.createDataFrame(
        [
            (
                batch.uri.rsplit("/", 1)[-1],
                batch.batch_id,
                batch.object_key,
                batch.object_sha256,
                batch.first_receive_sequence,
                batch.last_receive_sequence,
                batch.message_count,
                batch.published_at,
            )
            for batch in capture.batches
        ],
        """source_file string, batch_id string, object_key string, object_sha256 string,
        expected_first_sequence bigint, expected_last_sequence bigint,
        expected_message_count bigint, published_at timestamp""",
    )
    messages = (
        spark.read.schema(RAW_SCHEMA)
        .json([batch.uri for batch in capture.batches])
        .withColumn("source_file", F.regexp_extract(F.input_file_name(), r"([^/]+)$", 1))
        .join(F.broadcast(metadata), "source_file")
        .select(
            "stream_session_id",
            "receive_sequence",
            "message_type",
            "subscription_context",
            "provider_topic",
            "symbol",
            "source_time_text",
            F.to_timestamp("received_at").alias("received_at"),
            "message_json",
            "message_sha256",
            "api_version",
            "sdk_version",
            "batch_id",
            "object_key",
            "object_sha256",
            "published_at",
            "expected_first_sequence",
            "expected_last_sequence",
            "expected_message_count",
        )
        .cache()
    )
    required_columns = (
        "stream_session_id",
        "receive_sequence",
        "message_type",
        "subscription_context",
        "received_at",
        "message_json",
        "message_sha256",
        "api_version",
        "sdk_version",
        "batch_id",
        "object_key",
        "object_sha256",
        "published_at",
    )
    payload_symbol = F.upper(F.get_json_object("message_json", "$.symbol"))
    payload_time = F.get_json_object("message_json", "$.trading_time")
    invalid = messages.filter(
        F.expr(" OR ".join(f"{column} IS NULL" for column in required_columns))
        | (F.trim("message_type") == F.lit(""))
        | (F.col("subscription_context") != F.lit("symbols"))
        | (F.col("stream_session_id") != F.lit(capture.stream_session_id))
        | (F.col("receive_sequence") < F.col("expected_first_sequence"))
        | (F.col("receive_sequence") > F.col("expected_last_sequence"))
        | (F.col("api_version") != F.lit(capture.api_version))
        | (F.col("sdk_version") != F.lit(capture.sdk_version))
        | (F.sha2("message_json", 256) != F.col("message_sha256"))
        | (~F.trim("message_json").startswith("{"))
        | (F.get_json_object("message_json", "$").isNull())
        | (F.col("received_at") < F.lit(capture.connected_at))
        | (F.col("received_at") > F.lit(capture.disconnected_at))
        | (F.col("received_at") > F.col("published_at"))
        | (F.col("symbol").isNotNull() & ~F.col("symbol").isin(*capture.symbols))
        | (
            F.coalesce(F.col("symbol"), F.lit("__NULL__"))
            != F.coalesce(payload_symbol, F.lit("__NULL__"))
        )
        | (
            F.coalesce(F.col("source_time_text"), F.lit("__NULL__"))
            != F.coalesce(payload_time, F.lit("__NULL__"))
        )
    ).count()
    duplicate_keys = (
        messages.groupBy("stream_session_id", "receive_sequence")
        .count()
        .filter(F.col("count") != 1)
        .count()
    )
    batch_count_mismatches = (
        messages.groupBy("batch_id", "expected_message_count")
        .agg(
            F.count("*").alias("actual_count"),
            F.min("receive_sequence").alias("actual_first_sequence"),
            F.max("receive_sequence").alias("actual_last_sequence"),
            F.min("expected_first_sequence").alias("expected_first_sequence"),
            F.max("expected_last_sequence").alias("expected_last_sequence"),
        )
        .filter(
            (F.col("actual_count") != F.col("expected_message_count"))
            | (F.col("actual_first_sequence") != F.col("expected_first_sequence"))
            | (F.col("actual_last_sequence") != F.col("expected_last_sequence"))
        )
        .count()
    )
    coverage = messages.agg(
        F.count("*").alias("actual_count"),
        F.min("receive_sequence").alias("first_sequence"),
        F.max("receive_sequence").alias("last_sequence"),
    ).first()
    sequence_mismatch = (
        coverage is None
        or coverage["actual_count"] != capture.message_count
        or coverage["first_sequence"] != capture.first_receive_sequence
        or coverage["last_sequence"] != capture.last_receive_sequence
    )
    if invalid or duplicate_keys or batch_count_mismatches or sequence_mismatch:
        messages.unpersist()
        raise RuntimeError(
            "SSI Stream landing quality failed: "
            f"invalid={invalid}, duplicate_keys={duplicate_keys}, "
            f"batch_counts={batch_count_mismatches}, sequence={int(sequence_mismatch)}"
        )
    result = messages.drop(
        "expected_first_sequence",
        "expected_last_sequence",
        "expected_message_count",
    ).cache()
    result.count()
    messages.unpersist()
    return result


def publish(
    spark: SparkSession,
    *,
    source: SourceContract,
    capture: StreamCapture,
) -> str:
    messages_table = qualified_name(source.table_identifier("messages"))
    sessions_table = qualified_name(source.table_identifier("sessions"))
    messages = _messages_frame(spark, capture)
    if messages is not None:
        messages.createOrReplaceTempView("ssi_stream_capture_messages")
        conflict = spark.sql(
            f"""
            SELECT 1
            FROM ssi_stream_capture_messages source
            JOIN {messages_table} target
              ON target.stream_session_id = source.stream_session_id
             AND target.receive_sequence = source.receive_sequence
            WHERE target.message_sha256 != source.message_sha256
               OR target.batch_id != source.batch_id
               OR target.object_sha256 != source.object_sha256
            LIMIT 1
            """
        ).count()
        if conflict:
            messages.unpersist()
            raise RuntimeError("Immutable SSI Stream landing message conflict")
        spark.sql(
            f"""
            MERGE INTO {messages_table} target
            USING ssi_stream_capture_messages source
            ON target.stream_session_id = source.stream_session_id
               AND target.receive_sequence = source.receive_sequence
            WHEN NOT MATCHED THEN INSERT *
            """
        )
        messages.unpersist()

    session = spark.createDataFrame(
        [
            {
                "stream_session_id": capture.stream_session_id,
                "connected_at": capture.connected_at,
                "disconnected_at": capture.disconnected_at,
                "disconnect_kind": capture.disconnect_kind,
                "sdk_version": capture.sdk_version,
                "message_count": capture.message_count,
                "first_receive_sequence": capture.first_receive_sequence,
                "last_receive_sequence": capture.last_receive_sequence,
                "manifest_key": capture.manifest_key,
                "manifest_sha256": capture.manifest_sha256,
                "published_at": capture.published_at,
            }
        ],
        spark_schema(source.table("sessions")),
    )
    session.createOrReplaceTempView("ssi_stream_capture_session")
    session_conflict = spark.sql(
        f"""
        SELECT 1
        FROM ssi_stream_capture_session source
        JOIN {sessions_table} target USING (stream_session_id)
        WHERE target.manifest_sha256 != source.manifest_sha256
        LIMIT 1
        """
    ).count()
    if session_conflict:
        raise RuntimeError("Immutable SSI Stream session conflict")
    spark.sql(
        f"""
        MERGE INTO {sessions_table} target
        USING ssi_stream_capture_session source
        ON target.stream_session_id = source.stream_session_id
        WHEN NOT MATCHED THEN INSERT *
        """
    )
    return messages_table
