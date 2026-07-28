from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageEndpointSettings(BaseModel):
    external_url: str | None = "http://localhost:9000"
    internal_url: str | None = "http://object-store:9000"

    @field_validator("external_url", "internal_url")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        endpoint = value.rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Storage endpoint must be an HTTP(S) URL, got {value!r}")
        if parsed.params or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError(f"Storage endpoint cannot contain a path or query: {value!r}")
        return endpoint


class StorageSettings(BaseModel):
    backend: Literal["s3"] = "s3"
    network_scope: Literal["external", "internal"] = "external"
    endpoints: StorageEndpointSettings = Field(default_factory=StorageEndpointSettings)
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

    @property
    def endpoint_url(self) -> str | None:
        return (
            self.endpoints.external_url
            if self.network_scope == "external"
            else self.endpoints.internal_url
        )

    @field_validator("landing_uri", "curated_uri", "analytics_uri", mode="before")
    @classmethod
    def normalize_lifecycle_uri(cls, value: object) -> object:
        return value.rstrip("/") if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_root_uris(self) -> Self:
        uris = (self.landing_uri, self.curated_uri, self.analytics_uri)
        for uri in uris:
            parsed = urlparse(uri)
            if parsed.scheme != self.backend or not parsed.netloc:
                raise ValueError(f"Expected a {self.backend} storage URI, got {uri!r}")
            if parsed.params or parsed.query or parsed.fragment:
                raise ValueError(f"Storage URI cannot contain params, query, or fragment: {uri!r}")
            path_segments = parsed.path.removeprefix("/").split("/") if parsed.path else ()
            if any(segment in {"", ".", ".."} for segment in path_segments):
                raise ValueError(f"Storage URI path must be normalized: {uri!r}")
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


class PlatformAdminSettings(BaseModel):
    enabled: bool = False
    container_marker: Path = Path("/.dockerenv")
    guard_file: Path = Path("/run/secrets/platform_admin_guard")

    def require_capability(self) -> None:
        if not self.enabled:
            raise RuntimeError("Platform administration is disabled outside its admin container")
        if not self.container_marker.is_file():
            raise RuntimeError("Platform administration requires a container runtime marker")
        try:
            guard = self.guard_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise RuntimeError("Platform administration guard is not mounted") from error
        if guard != "platform-admin":
            raise RuntimeError("Platform administration guard is invalid")


class PolarisSettings(BaseModel):
    catalog_name: str = Field(default="prod", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    uri: str = "http://localhost:8181/api/catalog"
    management_uri: str = "http://localhost:8181/api/management/v1"
    realm: str = "POLARIS"
    credential: SecretStr = SecretStr("root:secretpassword")
    scope: str = "PRINCIPAL_ROLE:ALL"

    @property
    def oauth2_server_uri(self) -> str:
        return f"{self.uri.rstrip('/')}/v1/oauth/tokens"

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


class ArxivSettings(BaseModel):
    base_url: str = "https://oaipmh.arxiv.org/oai"
    metadata_prefix: Literal["arXiv"] = "arXiv"
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    max_pages_per_day: int = Field(default=1000, ge=1, le=10000)
    user_agent: str = "mini-lakehouse"


class KaggleSettings(BaseModel):
    username: str | None = None
    api_token: SecretStr | None = None

    @field_validator("username", mode="before")
    @classmethod
    def empty_username_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("api_token", mode="before")
    @classmethod
    def empty_token_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def configured(self) -> bool:
        return self.username is not None and self.api_token is not None

    def kernel_slug(self, kernel_name: str) -> str:
        if self.username is None:
            raise ValueError("Kaggle username is not configured")
        return f"{self.username}/{kernel_name}"

    def dataset_slug(self, dataset_name: str) -> str:
        if self.username is None:
            raise ValueError("Kaggle username is not configured")
        return f"{self.username}/{dataset_name}"

    def model_slug(self, model_name: str, *, framework: str, variation: str) -> str:
        if self.username is None:
            raise ValueError("Kaggle username is not configured")
        return f"{self.username}/{model_name}/{framework}/{variation}"


class ModalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MODAL_",
        extra="ignore",
    )

    token_id: SecretStr | None = None
    token_secret: SecretStr | None = None

    @field_validator("token_id", "token_secret", mode="before")
    @classmethod
    def empty_credential_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_credentials(self) -> Self:
        values = (self.token_id, self.token_secret)
        if any(value is None for value in values) and any(value is not None for value in values):
            raise ValueError("Modal token_id and token_secret must be configured together")
        return self

    @property
    def configured(self) -> bool:
        return self.token_id is not None and self.token_secret is not None


class NotificationSettings(BaseModel):
    prefect_ui_url: str = "http://localhost:4200"
    slack_bot_token: SecretStr | None = None
    slack_channel_id: str | None = None
    gmail_sender: str | None = None
    gmail_app_password: SecretStr | None = None
    gmail_recipients: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    @field_validator(
        "slack_bot_token",
        "slack_channel_id",
        "gmail_sender",
        "gmail_app_password",
        mode="before",
    )
    @classmethod
    def empty_delivery_value_is_unset(cls, value: object) -> object:
        return None if value == "" else value

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
        env_nested_max_split=2,
        secrets_dir=Path("/run/secrets") if Path("/run/secrets").is_dir() else None,
        extra="ignore",
    )

    environment: Literal["local", "ci", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    contracts_dir: Path = Path("contracts")
    storage: StorageSettings = Field(default_factory=StorageSettings)
    platform_admin: PlatformAdminSettings = Field(default_factory=PlatformAdminSettings)
    polaris: PolarisSettings = Field(default_factory=PolarisSettings)
    trino: TrinoSettings = Field(default_factory=TrinoSettings)
    dbt: DbtSettings = Field(default_factory=DbtSettings)
    github_archive: GithubArchiveSettings = Field(default_factory=GithubArchiveSettings)
    arxiv: ArxivSettings = Field(default_factory=ArxivSettings)
    kaggle: KaggleSettings = Field(default_factory=KaggleSettings)
    modal: ModalSettings = Field(default_factory=ModalSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)

    @model_validator(mode="after")
    def reject_local_credentials_in_production(self) -> Self:
        if self.environment != "production":
            return self
        storage_access_key = self.storage.secret_value(self.storage.access_key)
        storage_secret_key = self.storage.secret_value(self.storage.secret_key)
        if storage_access_key == "minioadmin" or storage_secret_key == "minioadmin":
            raise ValueError("Production cannot use the local object-store root credentials")
        local_endpoints = {"http://localhost:9000", "http://object-store:9000"}
        if local_endpoints.intersection(
            {
                self.storage.endpoints.external_url,
                self.storage.endpoints.internal_url,
            }
        ):
            raise ValueError("Production cannot use the local object-store endpoint")
        if self.polaris.credential.get_secret_value() == "root:secretpassword":
            raise ValueError("Production cannot use the local Polaris root credential")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
