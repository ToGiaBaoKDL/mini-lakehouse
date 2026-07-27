from pathlib import Path

import pytest
from pydantic import ValidationError

from mini_lakehouse.config.settings import (
    ModalSettings,
    NotificationSettings,
    PlatformAdminSettings,
    PolarisSettings,
    Settings,
    StorageEndpointSettings,
    StorageSettings,
)


def test_default_contract_uses_three_distinct_buckets() -> None:
    settings = Settings()

    assert settings.polaris.catalog_name == "prod"
    assert settings.polaris.oauth2_server_uri.endswith("/v1/oauth/tokens")
    assert settings.storage.landing_uri == "s3://landing"
    assert settings.storage.curated_uri == "s3://curated"
    assert settings.storage.analytics_uri == "s3://analytics"
    assert settings.storage.endpoint_url == "http://localhost:9000"


def test_storage_selects_an_endpoint_for_the_runtime_network() -> None:
    storage = StorageSettings(network_scope="internal")

    assert storage.endpoint_url == "http://object-store:9000"


def test_nested_storage_endpoints_load_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAKEHOUSE_STORAGE__NETWORK_SCOPE", "internal")
    monkeypatch.setenv(
        "LAKEHOUSE_STORAGE__ENDPOINTS__EXTERNAL_URL",
        "https://storage.example.com",
    )
    monkeypatch.setenv(
        "LAKEHOUSE_STORAGE__ENDPOINTS__INTERNAL_URL",
        "http://storage.internal:9000",
    )

    storage = Settings().storage

    assert storage.endpoint_url == "http://storage.internal:9000"
    assert storage.endpoints.external_url == "https://storage.example.com"


def test_native_s3_does_not_require_custom_endpoints() -> None:
    storage = StorageSettings(
        endpoints=StorageEndpointSettings(external_url=None, internal_url=None)
    )

    assert storage.endpoint_url is None


def test_storage_rejects_endpoint_paths() -> None:
    with pytest.raises(ValidationError, match="cannot contain a path"):
        StorageEndpointSettings(external_url="https://storage.example.com/s3")


def test_storage_accepts_isolated_prefixes_in_one_object_store_bucket() -> None:
    settings = StorageSettings(
        landing_uri="s3://shared/landing",
        curated_uri="s3://shared/curated",
        analytics_uri="s3://shared/analytics",
    )

    assert settings.curated_uri == "s3://shared/curated"


def test_storage_normalizes_trailing_slashes() -> None:
    settings = StorageSettings(landing_uri="s3://landing/")

    assert settings.landing_uri == "s3://landing"


def test_storage_rejects_non_normalized_prefixes() -> None:
    with pytest.raises(ValidationError, match="must be normalized"):
        StorageSettings(landing_uri="s3://shared/../landing")


def test_storage_rejects_an_uninstalled_backend() -> None:
    with pytest.raises(ValidationError, match="Input should be 's3'"):
        StorageSettings.model_validate({"backend": "gcs"})


def test_storage_rejects_static_and_vended_iceberg_credentials_together() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        StorageSettings(iceberg_access_delegation="vended-credentials")


def test_polaris_credential_requires_a_complete_pair() -> None:
    with pytest.raises(ValidationError, match="client_id:client_secret"):
        PolarisSettings.model_validate({"credential": "missing-secret"})


def test_production_rejects_local_root_credentials() -> None:
    with pytest.raises(ValidationError, match="Production cannot use"):
        Settings(environment="production")


def test_platform_reconciliation_requires_the_container_capability(tmp_path: Path) -> None:
    marker = tmp_path / "container"
    guard = tmp_path / "guard"
    marker.write_text("", encoding="utf-8")
    guard.write_text("platform-reconcile\n", encoding="utf-8")

    PlatformAdminSettings(
        enabled=True,
        container_marker=marker,
        guard_file=guard,
    ).require_reconciliation_capability()

    with pytest.raises(RuntimeError, match="disabled"):
        PlatformAdminSettings().require_reconciliation_capability()


def test_gmail_notification_channel_requires_complete_delivery_config() -> None:
    with pytest.raises(ValidationError, match="sender, app_password, and recipients"):
        NotificationSettings(gmail_sender="lakehouse-alerts@gmail.com")


def test_slack_credentials_must_be_configured_as_a_pair() -> None:
    with pytest.raises(ValidationError, match="bot_token and channel_id"):
        NotificationSettings(slack_channel_id="C0123456789")


def test_modal_credentials_must_be_configured_as_a_pair() -> None:
    with pytest.raises(ValidationError, match="token_id and token_secret"):
        ModalSettings.model_validate({"token_id": "ak-incomplete"})


def test_empty_notification_environment_values_are_disabled() -> None:
    settings = NotificationSettings.model_validate(
        {
            "slack_bot_token": "",
            "slack_channel_id": "",
            "gmail_sender": "",
            "gmail_app_password": "",
        }
    )

    assert settings.slack_bot_token is None
    assert settings.slack_channel_id is None
    assert settings.gmail_sender is None
    assert settings.gmail_app_password is None
