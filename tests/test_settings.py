import pytest
from pydantic import ValidationError

from mini_lakehouse.config.settings import Settings, StorageSettings


def test_default_contract_uses_three_distinct_buckets() -> None:
    settings = Settings()

    assert settings.polaris.catalog_name == "prod"
    assert settings.polaris.oauth2_server_uri.endswith("/v1/oauth/tokens")
    assert settings.storage.landing_uri == "s3://landing"
    assert settings.storage.curated_uri == "s3://curated"
    assert settings.storage.analytics_uri == "s3://analytics"


def test_storage_rejects_reused_bucket() -> None:
    with pytest.raises(ValidationError, match="distinct buckets"):
        StorageSettings(
            landing_uri="s3://shared",
            curated_uri="s3://shared",
            analytics_uri="s3://analytics",
        )


def test_storage_rejects_an_uninstalled_backend() -> None:
    with pytest.raises(ValidationError, match="Input should be 's3'"):
        StorageSettings.model_validate({"backend": "gcs"})
