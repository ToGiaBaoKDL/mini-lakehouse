"""Publish verified SSI REST capture objects into append-oriented landing tables."""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import LongType, StringType, StructField, StructType

from emr_jobs.common.contracts import spark_schema
from emr_jobs.common.iceberg import qualified_name
from emr_jobs.market_data.manifest import CaptureRun
from lakehouse.contracts.sources import SourceContract

RAW_SCHEMA = StructType(
    [
        StructField("request_id", StringType(), False),
        StructField("endpoint", StringType(), False),
        StructField("request_parameters_sha256", StringType(), False),
        StructField("page", LongType(), False),
        StructField("record_index", LongType(), False),
        StructField("requested_at", StringType(), False),
        StructField("received_at", StringType(), False),
        StructField("record_type", StringType(), False),
        StructField("symbol", StringType(), True),
        StructField("source_time_text", StringType(), True),
        StructField("record_json", StringType(), False),
        StructField("record_sha256", StringType(), False),
        StructField("api_version", StringType(), False),
        StructField("sdk_version", StringType(), False),
    ]
)

RECORD_TYPES = {
    "get_securities_info": "SecuritiesInfo",
    "get_securities_summary_historical": "SecuritiesSummary",
    "get_ohlc_1day_historical": "OHLCData",
    "get_ohlc_1minute_historical": "OHLCData",
    "get_master_data_historical": "MasterData",
    "get_index_summary_historical": "MarketIndexSummary",
}


def _records_frame(spark: SparkSession, capture: CaptureRun) -> DataFrame | None:
    if not capture.objects:
        return None
    metadata = spark.createDataFrame(
        [
            (
                item.uri.rsplit("/", 1)[-1],
                item.object_key,
                item.object_sha256,
                item.page,
                item.record_count,
                item.requested_at,
                item.received_at,
                item.published_at,
            )
            for item in capture.objects
        ],
        """source_file string, object_key string, object_sha256 string,
        expected_page bigint, expected_record_count bigint,
        expected_requested_at timestamp, expected_received_at timestamp,
        published_at timestamp""",
    )
    requests = spark.createDataFrame(
        [
            (
                item.request_id,
                item.endpoint,
                item.request_parameters_sha256,
                item.record_count,
                RECORD_TYPES[item.endpoint],
            )
            for item in capture.requests
        ],
        """request_id string, expected_endpoint string,
        expected_parameters_sha256 string, expected_request_record_count bigint,
        expected_record_type string""",
    )
    records = (
        spark.read.schema(RAW_SCHEMA)
        .json([item.uri for item in capture.objects])
        .withColumn("source_file", F.regexp_extract(F.input_file_name(), r"([^/]+)$", 1))
        .join(F.broadcast(metadata), "source_file")
        .join(F.broadcast(requests), "request_id")
        .select(
            "request_id",
            "endpoint",
            "request_parameters_sha256",
            "page",
            "record_index",
            F.to_timestamp("requested_at").alias("requested_at"),
            F.to_timestamp("received_at").alias("received_at"),
            "record_type",
            "symbol",
            "source_time_text",
            "record_json",
            "record_sha256",
            "api_version",
            "sdk_version",
            "object_key",
            "object_sha256",
            "expected_page",
            "expected_record_count",
            "expected_endpoint",
            "expected_parameters_sha256",
            "expected_request_record_count",
            "expected_record_type",
            "expected_requested_at",
            "expected_received_at",
            "published_at",
        )
        .cache()
    )
    invalid = records.filter(
        F.col("request_id").isNull()
        | F.col("endpoint").isNull()
        | F.col("request_parameters_sha256").isNull()
        | F.col("page").isNull()
        | F.col("record_index").isNull()
        | F.col("requested_at").isNull()
        | F.col("received_at").isNull()
        | F.col("record_type").isNull()
        | F.col("record_json").isNull()
        | F.col("record_sha256").isNull()
        | F.col("api_version").isNull()
        | F.col("sdk_version").isNull()
        | F.col("object_key").isNull()
        | F.col("object_sha256").isNull()
        | F.col("published_at").isNull()
        | (F.col("page") < 1)
        | (F.col("record_index") < 0)
        | (F.col("page") != F.col("expected_page"))
        | (F.col("endpoint") != F.col("expected_endpoint"))
        | (F.col("request_parameters_sha256") != F.col("expected_parameters_sha256"))
        | (F.col("record_type") != F.col("expected_record_type"))
        | (F.col("api_version") != F.lit(capture.api_version))
        | (F.col("sdk_version") != F.lit(capture.sdk_version))
        | (F.sha2("record_json", 256) != F.col("record_sha256"))
        | (F.col("requested_at") != F.col("expected_requested_at"))
        | (F.col("received_at") != F.col("expected_received_at"))
        | (F.col("requested_at") > F.col("received_at"))
        | (F.col("received_at") > F.col("published_at"))
    ).count()
    duplicates = (
        records.groupBy("request_id", "page", "record_index")
        .count()
        .filter(F.col("count") != 1)
        .count()
    )
    object_count_mismatches = (
        records.groupBy("object_key", "expected_record_count")
        .agg(
            F.count("*").alias("actual_count"),
            F.min("record_index").alias("first_index"),
            F.max("record_index").alias("last_index"),
        )
        .filter(
            (F.col("actual_count") != F.col("expected_record_count"))
            | (F.col("first_index") != 0)
            | (F.col("last_index") != F.col("expected_record_count") - 1)
        )
        .count()
    )
    request_count_mismatches = (
        requests.join(
            records.groupBy("request_id").agg(F.count("*").alias("actual_count")),
            "request_id",
            "left",
        )
        .filter(
            F.coalesce(F.col("actual_count"), F.lit(0)) != F.col("expected_request_record_count")
        )
        .count()
    )
    if invalid or duplicates or object_count_mismatches or request_count_mismatches:
        records.unpersist()
        raise RuntimeError(
            "SSI landing quality failed: "
            f"invalid={invalid}, duplicate_keys={duplicates}, "
            f"object_counts={object_count_mismatches}, "
            f"request_counts={request_count_mismatches}"
        )
    result = records.drop(
        "expected_page",
        "expected_record_count",
        "expected_endpoint",
        "expected_parameters_sha256",
        "expected_request_record_count",
        "expected_record_type",
        "expected_requested_at",
        "expected_received_at",
    ).cache()
    result.count()
    records.unpersist()
    return result


def publish(
    spark: SparkSession,
    *,
    source: SourceContract,
    capture: CaptureRun,
) -> str:
    publications_contract = source.table("request_publications")
    records_table = qualified_name(source.table_identifier("records"))
    publications_table = qualified_name(source.table_identifier("request_publications"))
    records = _records_frame(spark, capture)
    if records is not None:
        records.createOrReplaceTempView("ssi_rest_capture_records")
        conflict = spark.sql(
            f"""
            SELECT 1
            FROM ssi_rest_capture_records source
            JOIN {records_table} target
              ON target.request_id = source.request_id
             AND target.page = source.page
             AND target.record_index = source.record_index
            WHERE target.record_sha256 != source.record_sha256
               OR target.object_sha256 != source.object_sha256
            LIMIT 1
            """
        ).count()
        if conflict:
            records.unpersist()
            raise RuntimeError("Immutable SSI landing record conflict")
        spark.sql(
            f"""
            MERGE INTO {records_table} target
            USING ssi_rest_capture_records source
            ON target.request_id = source.request_id
               AND target.page = source.page
               AND target.record_index = source.record_index
            WHEN NOT MATCHED THEN INSERT *
            """
        )
        records.unpersist()

    publications = spark.createDataFrame(
        [publication.__dict__ for publication in capture.requests],
        spark_schema(publications_contract),
    )
    publications.createOrReplaceTempView("ssi_rest_capture_publications")
    publication_conflict = spark.sql(
        f"""
        SELECT 1
        FROM ssi_rest_capture_publications source
        JOIN {publications_table} target
          ON target.request_id = source.request_id
        WHERE target.manifest_sha256 != source.manifest_sha256
        LIMIT 1
        """
    ).count()
    if publication_conflict:
        raise RuntimeError("Immutable SSI request publication conflict")
    spark.sql(
        f"""
        MERGE INTO {publications_table} target
        USING ssi_rest_capture_publications source
        ON target.request_id = source.request_id
        WHEN NOT MATCHED THEN INSERT *
        """
    )
    return records_table
