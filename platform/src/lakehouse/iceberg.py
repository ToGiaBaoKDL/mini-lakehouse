"""PyIceberg Glue catalog construction for the table control plane."""

from pyiceberg.catalog import Catalog, load_catalog


def load_iceberg_catalog(
    name: str,
    *,
    region_name: str,
    profile_name: str | None = None,
) -> Catalog:
    properties = {
        "type": "glue",
        "client.region": region_name,
    }
    if profile_name:
        properties["client.profile-name"] = profile_name
    return load_catalog(name, **properties)
