"""Archive raw OAI pages and publish one complete landing day."""

import gzip
import hashlib
import json
from datetime import date, datetime

from lakehouse.contracts.sources import SourceContract
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from emr_jobs.common.contracts import spark_schema
from emr_jobs.common.iceberg import qualified_name
from emr_jobs.common.s3 import join_key, put_if_changed, split_uri


def archive_pages(
    pages: list[bytes],
    *,
    source_date: str,
    landing_uri: str,
    raw_object_prefix: str,
) -> tuple[list[tuple[str, str]], str, str]:
    bucket, root = split_uri(landing_uri)
    prefix = join_key(root, raw_object_prefix, f"datestamp={source_date}")
    page_objects: list[tuple[str, str]] = []
    manifest_pages: list[dict[str, object]] = []
    for index, page in enumerate(pages, start=1):
        body = gzip.compress(page, compresslevel=6, mtime=0)
        sha256 = hashlib.sha256(body).hexdigest()
        key = f"{prefix}/page-{index:06d}.xml.gz"
        put_if_changed(
            bucket=bucket,
            key=key,
            body=body,
            sha256=sha256,
            content_type="application/xml",
            content_encoding="gzip",
        )
        page_objects.append((key, sha256))
        manifest_pages.append({"key": key, "sha256": sha256, "size_bytes": len(body)})

    manifest = json.dumps(
        {"schema_version": 1, "source_date": source_date, "pages": manifest_pages},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest_sha256 = hashlib.sha256(manifest).hexdigest()
    manifest_key = f"{prefix}/manifest.json"
    put_if_changed(
        bucket=bucket,
        key=manifest_key,
        body=manifest,
        sha256=manifest_sha256,
        content_type="application/json",
    )
    return page_objects, manifest_key, manifest_sha256


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
