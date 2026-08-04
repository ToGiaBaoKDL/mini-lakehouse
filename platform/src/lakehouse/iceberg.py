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
    }
    if frozen.token:
        properties["client.session-token"] = frozen.token
    return load_catalog(
        CATALOG_NAME,
        type="glue",
        **properties,
    )
