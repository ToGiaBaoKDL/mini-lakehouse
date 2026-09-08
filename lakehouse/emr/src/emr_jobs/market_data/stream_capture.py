"""Thin S3 adapter for the canonical T0 stream-capture reader."""

from datetime import date
from typing import Any

from botocore.exceptions import ClientError
from t0_trading.capture import SSI_STREAM_RAW_PREFIX
from t0_trading.capture.reader import StreamSessionReader, stream_manifest_uris

from emr_jobs.common.s3 import split_uri


def discover_captures(
    client: Any,
    *,
    landing_uri: str,
    trade_date: date,
    raw_object_prefix: str,
) -> tuple[str, ...]:
    """Discover terminal manifests after checking the cross-layer storage boundary."""
    if raw_object_prefix != SSI_STREAM_RAW_PREFIX:
        raise RuntimeError("SSI Stream capture and catalog raw prefixes disagree")
    return stream_manifest_uris(client, landing_uri, trade_date)


def load_capture(client: Any, uri: str) -> StreamSessionReader:
    """Load the shared manifest model and cheaply verify every referenced S3 object."""
    reader = StreamSessionReader.from_uri(client, uri)
    for batch in reader.manifest.batches:
        bucket, key = split_uri(reader.batch_uri(batch))
        try:
            metadata = client.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                raise
            metadata = None
        if (
            metadata is None
            or metadata.get("Metadata", {}).get("sha256") != batch.object_sha256
            or not isinstance(metadata.get("ContentLength"), int)
            or metadata["ContentLength"] < 1
        ):
            raise RuntimeError("SSI Stream batch is missing or has checksum drift")
    return reader
