"""Small process settings; AWS resource references live in Parameter Store."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONTRACTS_DIR = Path("lakehouse/contracts")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LAKEHOUSE_",
        extra="ignore",
        populate_by_name=True,
    )

    environment: Literal["dev", "ci", "production"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    contracts_dir: Path = DEFAULT_CONTRACTS_DIR
    aws_region: str = Field(
        default="ap-southeast-1",
        validation_alias=AliasChoices("AWS_REGION", "LAKEHOUSE_AWS_REGION"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
