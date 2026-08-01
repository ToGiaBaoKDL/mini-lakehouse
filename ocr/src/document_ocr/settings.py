"""Credentials used by remote OCR provider adapters."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class KaggleSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KAGGLE_",
        extra="ignore",
    )

    username: str = Field(min_length=1)
    api_token: SecretStr = Field(min_length=1)


class ModalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MODAL_",
        extra="ignore",
    )

    token_id: SecretStr = Field(min_length=1)
    token_secret: SecretStr = Field(min_length=1)
