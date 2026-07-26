import os

import pytest

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.storage.iceberg import load_iceberg_catalog


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LAKEHOUSE_INTEGRATION") != "1",
    reason="set RUN_LAKEHOUSE_INTEGRATION=1 with the Compose stack running",
)
def test_expected_namespaces_are_readable() -> None:
    settings = get_settings()
    contracts = load_contracts(settings.contracts_dir)

    with load_iceberg_catalog(settings) as catalog:
        for namespace in contracts.managed_namespaces():
            assert catalog.namespace_exists(namespace.path), ".".join(namespace.path)
