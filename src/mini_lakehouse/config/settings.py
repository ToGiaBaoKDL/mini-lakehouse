from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageSettings(BaseModel):
    backend: Literal["s3"] = "s3"
    endpoint_url: str | None = "http://localhost:9000"
    access_key: SecretStr | None = SecretStr("minioadmin")
    secret_key: SecretStr | None = SecretStr("minioadmin")
    region: str = "us-east-1"
    path_style_access: bool = True
    iceberg_access_delegation: Literal["none", "vended-credentials"] = "none"
    sts_unavailable: bool = True
    kms_unavailable: bool = True
    landing_uri: str = "s3://landing"
    curated_uri: str = "s3://curated"
    analytics_uri: str = "s3://analytics"

    @model_validator(mode="after")
    def validate_root_uris(self) -> Self:
        expected_scheme = self.backend
        uris = (self.landing_uri, self.curated_uri, self.analytics_uri)
        buckets: list[str] = []
        for uri in uris:
            parsed = urlparse(uri)
            if parsed.scheme != expected_scheme or not parsed.netloc:
                raise ValueError(f"Expected a {expected_scheme} bucket URI, got {uri!r}")
            if parsed.path not in ("", "/"):
                raise ValueError(f"Storage lifecycle URI must be a bucket root, got {uri!r}")
            buckets.append(parsed.netloc)
        if len(set(buckets)) != len(buckets):
            raise ValueError("landing, curated, and analytics must use distinct buckets")
        credentials = (self.access_key, self.secret_key)
        if any(value is None for value in credentials) and any(
            value is not None for value in credentials
        ):
            raise ValueError("Storage access_key and secret_key must be configured together")
        if self.iceberg_access_delegation == "vended-credentials" and all(
            value is not None for value in credentials
        ):
            raise ValueError(
                "Static storage credentials and Iceberg credential vending are mutually exclusive"
            )
        return self

    def secret_value(self, value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value is not None else None


class PolarisSettings(BaseModel):
    catalog_name: str = Field(default="prod", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    uri: str = "http://localhost:8181/api/catalog"
    management_uri: str = "http://localhost:8181/api/management/v1"
    oauth2_server_uri: str = "http://localhost:8181/api/catalog/v1/oauth/tokens"
    realm: str = "POLARIS"
    credential: SecretStr = SecretStr("root:secretpassword")
    scope: str = "PRINCIPAL_ROLE:ALL"

    @model_validator(mode="after")
    def validate_credential(self) -> Self:
        client_id, separator, client_secret = self.credential.get_secret_value().partition(":")
        if not separator or not client_id or not client_secret:
            raise ValueError("Polaris credential must use a non-empty client_id:client_secret pair")
        return self


class TrinoSettings(BaseModel):
    host: str = "localhost"
    port: int = Field(default=8080, ge=1, le=65535)
    user: str = "lakehouse"
    catalog: str = Field(default="prod", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    http_scheme: Literal["http", "https"] = "http"


class DbtSettings(BaseModel):
    project_dir: Path = Path("dbt/analytics")
    profiles_dir: Path = Path("dbt/analytics")


class GithubArchiveSettings(BaseModel):
    base_url: str = "https://data.gharchive.org"
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    max_parse_error_ratio: float = Field(default=0.001, ge=0, le=1)
    user_agent: str = "mini-lakehouse"


class NotificationSettings(BaseModel):
    prefect_ui_url: str = "http://localhost:4200"
    slack_bot_token: SecretStr | None = None
    slack_channel_id: str | None = None
    gmail_sender: str | None = None
    gmail_app_password: SecretStr | None = None
    gmail_recipients: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    @model_validator(mode="after")
    def validate_channels(self) -> Self:
        slack_values = (self.slack_bot_token, self.slack_channel_id)
        if any(value is not None for value in slack_values) and any(
            value is None for value in slack_values
        ):
            raise ValueError("Slack notifications require bot_token and channel_id")
        gmail_values = (
            self.gmail_sender,
            self.gmail_app_password,
            self.gmail_recipients or None,
        )
        if any(value is not None for value in gmail_values) and any(
            value is None for value in gmail_values
        ):
            raise ValueError("Gmail notifications require sender, app_password, and recipients")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LAKEHOUSE_",
        env_nested_delimiter="__",
        env_nested_max_split=1,
        secrets_dir=Path("/run/secrets") if Path("/run/secrets").is_dir() else None,
        extra="ignore",
    )

    environment: Literal["local", "ci", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    contracts_dir: Path = Path("contracts")
    storage: StorageSettings = Field(default_factory=StorageSettings)
    polaris: PolarisSettings = Field(default_factory=PolarisSettings)
    trino: TrinoSettings = Field(default_factory=TrinoSettings)
    dbt: DbtSettings = Field(default_factory=DbtSettings)
    github_archive: GithubArchiveSettings = Field(default_factory=GithubArchiveSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)

    @model_validator(mode="after")
    def reject_local_credentials_in_production(self) -> Self:
        if self.environment != "production":
            return self
        storage_access_key = self.storage.secret_value(self.storage.access_key)
        storage_secret_key = self.storage.secret_value(self.storage.secret_key)
        if storage_access_key == "minioadmin" or storage_secret_key == "minioadmin":
            raise ValueError("Production cannot use the local MinIO root credentials")
        if self.storage.endpoint_url in {
            "http://localhost:9000",
            "http://object-store:9000",
        }:
            raise ValueError("Production cannot use the local object-store endpoint")
        if self.polaris.credential.get_secret_value() == "root:secretpassword":
            raise ValueError("Production cannot use the local Polaris root credential")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
