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
            buckets.append(parsed.netloc)
        if len(set(buckets)) != len(buckets):
            raise ValueError("landing, curated, and analytics must use distinct buckets")
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


class TrinoSettings(BaseModel):
    host: str = "localhost"
    port: int = Field(default=8080, ge=1, le=65535)
    user: str = "lakehouse"
    catalog: str = Field(default="prod", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    http_scheme: Literal["http", "https"] = "http"


class DbtSettings(BaseModel):
    project_dir: Path = Path("dbt_project")
    profiles_dir: Path = Path("dbt_project")


class GithubArchiveSettings(BaseModel):
    base_url: str = "https://data.gharchive.org"
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    max_parse_error_ratio: float = Field(default=0.001, ge=0, le=1)
    user_agent: str = "mini-lakehouse/0.2"


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
    storage: StorageSettings = Field(default_factory=StorageSettings)
    polaris: PolarisSettings = Field(default_factory=PolarisSettings)
    trino: TrinoSettings = Field(default_factory=TrinoSettings)
    dbt: DbtSettings = Field(default_factory=DbtSettings)
    github_archive: GithubArchiveSettings = Field(default_factory=GithubArchiveSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
