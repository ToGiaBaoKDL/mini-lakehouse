from mini_lakehouse.config import Settings
from mini_lakehouse.storage.iceberg import iceberg_catalog_properties


def test_static_storage_credentials_disable_rest_credential_vending() -> None:
    properties = iceberg_catalog_properties(Settings())

    assert properties["header.X-Iceberg-Access-Delegation"] == ""


def test_credential_vending_remains_available_without_static_keys() -> None:
    settings = Settings()
    settings.storage.access_key = None
    settings.storage.secret_key = None
    settings.storage.iceberg_access_delegation = "vended-credentials"

    properties = iceberg_catalog_properties(settings)

    assert properties["header.X-Iceberg-Access-Delegation"] == "vended-credentials"
    assert "s3.access-key-id" not in properties
    assert "s3.secret-access-key" not in properties
