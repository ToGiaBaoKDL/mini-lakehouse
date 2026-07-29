"""Credentials used by remote OCR provider adapters."""

from typing import Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseSettings):
    @field_validator("*", mode="before")
    @classmethod
    def empty_value_is_unset(cls, value: object) -> object:
        return None if value == "" else value


class KaggleSettings(ProviderSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="KAGGLE_",
        extra="ignore",
    )

    username: str | None = None
    api_token: SecretStr | None = None

    @model_validator(mode="after")
    def validate_credentials(self) -> Self:
        if (self.username is None) != (self.api_token is None):
            raise ValueError("Kaggle username and api_token must be configured together")
        return self

    @property
    def configured(self) -> bool:
        return self.username is not None


class ModalSettings(ProviderSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MODAL_",
        extra="ignore",
    )

    token_id: SecretStr | None = None
    token_secret: SecretStr | None = None

    @model_validator(mode="after")
    def validate_credentials(self) -> Self:
        if (self.token_id is None) != (self.token_secret is None):
            raise ValueError("Modal token_id and token_secret must be configured together")
        return self

    @property
    def configured(self) -> bool:
        return self.token_id is not None
