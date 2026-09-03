"""PyIceberg Glue catalog construction for the table control plane."""

import boto3
from pyiceberg.catalog import Catalog, load_catalog

from lakehouse.catalog import CATALOG_NAME


def load_iceberg_catalog(
    *,
    region_name: str,
) -> Catalog:
    credentials = boto3.Session(region_name=region_name).get_credentials()
    if credentials is None:
        raise RuntimeError("AWS credentials are unavailable")
    frozen = credentials.get_frozen_credentials()
    properties = {
        "client.region": region_name,
        "client.access-key-id": frozen.access_key,
        "client.secret-access-key": frozen.secret_key,
        # Keep control-plane access resilient and avoid per-bucket DNS lookups.
        "s3.connect-timeout": "30",
        "s3.endpoint": f"https://s3.{region_name}.amazonaws.com",
        "s3.force-virtual-addressing": "false",
        "s3.request-timeout": "60",
    }
    if frozen.token:
        properties["client.session-token"] = frozen.token
    return load_catalog(
        CATALOG_NAME,
        type="glue",
        **properties,
    )
