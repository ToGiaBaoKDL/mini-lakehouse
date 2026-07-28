from collections.abc import Iterator

from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.typedef import Identifier

from mini_lakehouse.config.settings import Settings


def iceberg_catalog_properties(settings: Settings) -> dict[str, str]:
    storage = settings.storage
    properties = {
        "type": "rest",
        "uri": settings.polaris.uri,
        "warehouse": settings.polaris.catalog_name,
        "credential": (
            f"{settings.polaris.client_id}:{settings.polaris.client_secret.get_secret_value()}"
        ),
        "scope": settings.polaris.scope,
        "oauth2-server-uri": settings.polaris.oauth2_server_uri,
        "s3.region": storage.region,
        "s3.path-style-access": str(storage.path_style_access).lower(),
    }
    if storage.endpoint_url:
        properties["s3.endpoint"] = storage.endpoint_url
    access_key = storage.secret_value(storage.access_key)
    secret_key = storage.secret_value(storage.secret_key)
    if storage.iceberg_access_delegation == "vended-credentials":
        properties["header.X-Iceberg-Access-Delegation"] = "vended-credentials"
    elif access_key and secret_key:
        properties["s3.access-key-id"] = access_key
        properties["s3.secret-access-key"] = secret_key
        # PyIceberg otherwise requests vended credentials by default. Static
        # storage credentials and delegation are mutually exclusive modes; an
        # empty explicit header prevents that default for local and other
        # static-credential deployments.
        properties["header.X-Iceberg-Access-Delegation"] = ""
    return properties


def load_iceberg_catalog(settings: Settings) -> Catalog:
    return load_catalog(
        settings.polaris.catalog_name,
        **iceberg_catalog_properties(settings),
    )


def _walk_namespaces(catalog: Catalog) -> Iterator[Identifier]:
    pending = list(catalog.list_namespaces())
    seen: set[Identifier] = set()
    while pending:
        namespace = pending.pop()
        if namespace in seen:
            continue
        seen.add(namespace)
        yield namespace
        pending.extend(catalog.list_namespaces(namespace))


def discover_table_identifiers(catalog: Catalog) -> Iterator[Identifier]:
    for namespace in _walk_namespaces(catalog):
        yield from catalog.list_tables(namespace)
