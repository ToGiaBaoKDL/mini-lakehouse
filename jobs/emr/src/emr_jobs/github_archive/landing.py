"""Parse and validate GitHub Archive landing records."""

from datetime import datetime

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    LongType,
    StringType,
    StructField,
    StructType,
)

EVENT_SCHEMA = StructType(
    [
        StructField("id", StringType()),
        StructField("type", StringType()),
        StructField(
            "actor",
            StructType(
                [
                    StructField("id", LongType()),
                    StructField("login", StringType()),
                ]
            ),
        ),
        StructField(
            "repo",
            StructType(
                [
                    StructField("id", LongType()),
                    StructField("name", StringType()),
                ]
            ),
        ),
        StructField("public", BooleanType()),
        StructField("created_at", StringType()),
    ]
)


def build_frame(
    spark: SparkSession,
    captures: list[tuple[str, datetime]],
) -> DataFrame:
    paths = [path for path, _ in captures]
    source_files = spark.createDataFrame(
        [(path.rsplit("/", maxsplit=1)[-1], captured_at) for path, captured_at in captures],
        "source_file string, ingested_at timestamp",
    )
    raw = (
        spark.read.text(paths)
        .withColumn("_input_file", F.input_file_name())
        .withColumn("source_file", F.regexp_extract(F.col("_input_file"), r"([^/]+)$", 1))
        .withColumn("_event", F.from_json("value", EVENT_SCHEMA))
    )
    source_date = F.regexp_extract(F.col("source_file"), r"^(\d{4}-\d{2}-\d{2})-", 1)
    source_hour = F.regexp_extract(F.col("source_file"), r"-(\d{1,2})\.json\.gz$", 1)
    events = (
        raw.select(
            F.col("_event.id").alias("event_id"),
            F.col("_event.type").alias("event_type"),
            F.col("_event.actor.id").alias("actor_id"),
            F.col("_event.actor.login").alias("actor_login"),
            F.col("_event.repo.id").alias("repository_id"),
            F.col("_event.repo.name").alias("repository_name"),
            F.get_json_object("value", "$.payload").alias("payload_json"),
            F.col("_event.public").alias("is_public"),
            F.to_timestamp(F.col("_event.created_at")).alias("occurred_at"),
            F.col("source_file"),
            F.to_timestamp(
                F.concat(
                    source_date,
                    F.lit(" "),
                    F.lpad(source_hour, 2, "0"),
                    F.lit(":00:00"),
                )
            ).alias("source_hour"),
            F.col("value").alias("raw_event_json"),
        )
        .join(source_files, "source_file")
        .select(
            "event_id",
            "event_type",
            "actor_id",
            "actor_login",
            "repository_id",
            "repository_name",
            "payload_json",
            "is_public",
            "occurred_at",
            "source_file",
            "source_hour",
            "ingested_at",
            "raw_event_json",
        )
        .cache()
    )
    invalid = events.filter(
        F.col("event_id").isNull()
        | F.col("event_type").isNull()
        | F.col("payload_json").isNull()
        | F.col("is_public").isNull()
        | F.col("occurred_at").isNull()
        | F.col("source_hour").isNull()
    ).count()
    if invalid:
        events.unpersist()
        raise RuntimeError(f"GitHub Archive contains {invalid} invalid required records")
    duplicate = (
        events.groupBy("event_id")
        .count()
        .filter(F.col("count") > 1)
        .select("event_id", "count")
        .limit(1)
        .collect()
    )
    if duplicate:
        events.unpersist()
        raise RuntimeError(
            "GitHub Archive violates the event_id merge key: "
            f"{duplicate[0]['event_id']} occurs {duplicate[0]['count']} times"
        )
    return events
