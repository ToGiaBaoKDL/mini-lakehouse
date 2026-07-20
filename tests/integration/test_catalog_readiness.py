import os

import pytest

from mini_lakehouse.config import get_settings
from mini_lakehouse.storage.iceberg import load_prod_catalog


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LAKEHOUSE_INTEGRATION") != "1",
    reason="set RUN_LAKEHOUSE_INTEGRATION=1 with the Compose stack running",
)
def test_expected_namespaces_are_readable() -> None:
    catalog = load_prod_catalog(get_settings())

    assert ("landing", "api", "github_archive") in catalog.list_namespaces(("landing", "api"))
    assert ("curated", "github") in catalog.list_namespaces(("curated",))
    assert ("curated", "github", "internal") in catalog.list_namespaces(("curated", "github"))
    assert ("analytics", "engineering") in catalog.list_namespaces(("analytics",))
