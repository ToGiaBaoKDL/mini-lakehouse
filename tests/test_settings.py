import pytest
from pydantic import ValidationError

from mini_lakehouse.config.settings import (
    NotificationSettings,
    PolarisSettings,
    Settings,
    StorageSettings,
)


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


def test_storage_rejects_a_prefix_instead_of_a_bucket_root() -> None:
    with pytest.raises(ValidationError, match="bucket root"):
        StorageSettings(landing_uri="s3://landing/shared-prefix")


def test_storage_rejects_an_uninstalled_backend() -> None:
    with pytest.raises(ValidationError, match="Input should be 's3'"):
        StorageSettings.model_validate({"backend": "gcs"})


def test_polaris_credential_requires_a_complete_pair() -> None:
    with pytest.raises(ValidationError, match="client_id:client_secret"):
        PolarisSettings.model_validate({"credential": "missing-secret"})


def test_production_rejects_local_root_credentials() -> None:
    with pytest.raises(ValidationError, match="Production cannot use"):
        Settings(environment="production")


def test_gmail_notification_channel_requires_complete_delivery_config() -> None:
    with pytest.raises(ValidationError, match="sender, app_password, and recipients"):
        NotificationSettings(gmail_sender="lakehouse-alerts@gmail.com")


def test_slack_credentials_must_be_configured_as_a_pair() -> None:
    with pytest.raises(ValidationError, match="bot_token and channel_id"):
        NotificationSettings(slack_channel_id="C0123456789")
