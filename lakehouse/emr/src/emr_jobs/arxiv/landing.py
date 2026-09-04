"""Publish one complete ArXiv OAI landing day."""

from datetime import date, datetime

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from emr_jobs.common.contracts import spark_schema
from emr_jobs.common.iceberg import qualified_name
from lakehouse.contracts.sources import SourceContract


def publish(
    spark: SparkSession,
    *,
    source: SourceContract,
    source_day: date,
    records: list[dict[str, object]],
    manifest_key: str,
    manifest_sha256: str,
    page_count: int,
    published_at: datetime,
) -> tuple[str, bool]:
    records_contract = source.table("oai_records_raw")
    publication_contract = source.table("oai_publications")
    records_table = qualified_name(source.table_identifier("oai_records_raw"))
    publication_table = qualified_name(source.table_identifier("oai_publications"))
    day_filter = F.col("datestamp_date") == F.lit(source_day)
    current = (
        spark.table(publication_table)
        .filter(day_filter)
        .select("raw_manifest_sha256")
        .limit(1)
        .collect()
    )
    if current and current[0]["raw_manifest_sha256"] == manifest_sha256:
        return records_table, False

    spark.createDataFrame(records, spark_schema(records_contract)).writeTo(records_table).overwrite(
        day_filter
    )
    spark.createDataFrame(
        [
            {
                "datestamp_date": source_day,
                "raw_manifest_key": manifest_key,
                "raw_manifest_sha256": manifest_sha256,
                "page_count": page_count,
                "record_count": len(records),
                "published_at": published_at,
            }
        ],
        spark_schema(publication_contract),
    ).writeTo(publication_table).overwrite(day_filter)
    return records_table, True
