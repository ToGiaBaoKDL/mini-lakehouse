import logging
from collections.abc import Mapping
from time import sleep

from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError

from mini_lakehouse.config import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.platform.runtime import namespace_storage_uri, validate_runtime_contract
from mini_lakehouse.storage.iceberg import load_iceberg_catalog

logger = logging.getLogger(__name__)


def namespace_contract(
    settings: Settings,
    contracts: PlatformContracts | None = None,
) -> Mapping[tuple[str, ...], dict[str, str]]:
    registry = contracts or load_contracts(settings.contracts_dir)
    validate_runtime_contract(settings, registry)
    return {
        namespace.path: namespace.iceberg_properties(
            namespace_storage_uri(settings, namespace.path)
        )
        for namespace in registry.catalog.namespaces
    }


def ensure_namespaces(
    catalog: Catalog,
    settings: Settings,
    contracts: PlatformContracts,
) -> None:
    for namespace, properties in namespace_contract(settings, contracts).items():
        try:
            catalog.create_namespace(namespace, properties)
        except NamespaceAlreadyExistsError:
            current = catalog.load_namespace_properties(namespace)
            updates = {key: value for key, value in properties.items() if current.get(key) != value}
            if updates:
                catalog.update_namespace_properties(namespace, updates=updates)
        logger.info("Namespace %s matches its contract", ".".join(namespace))


def load_catalog_with_retry(settings: Settings) -> Catalog:
    last_error: Exception | None = None
    for _ in range(12):
        try:
            catalog = load_iceberg_catalog(settings)
            catalog.list_namespaces()
            return catalog
        except Exception as error:
            last_error = error
            sleep(2)
    raise RuntimeError("Polaris catalog did not become readable") from last_error
