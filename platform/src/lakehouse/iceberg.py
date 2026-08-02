"""PyIceberg Glue catalog construction for the table control plane."""

from pyiceberg.catalog import Catalog, load_catalog

from lakehouse.catalog import CATALOG_NAME


def load_iceberg_catalog(
    *,
    region_name: str,
) -> Catalog:
    return load_catalog(
        CATALOG_NAME,
        type="glue",
        **{"client.region": region_name},
    )
