"""Managed market-data credential boundary."""

from __future__ import annotations

from typing import Annotated, Literal

import boto3
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


class CredentialError(ValueError):
    """The managed SSI market-data credential is unavailable or invalid."""


class Credentials(BaseModel):
    """Strict v1 market-data secret, masked by default in logs and tracebacks."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[1] = 1
    client_id: Annotated[SecretStr, Field(min_length=1)]
    api_key: Annotated[SecretStr, Field(min_length=1)]
    api_secret: Annotated[SecretStr, Field(min_length=1)]

    @classmethod
    def from_json(cls, value: str) -> Credentials:
        try:
            return cls.model_validate_json(value)
        except ValidationError as error:
            raise CredentialError(
                "SSI credential does not match the market-data v1 contract."
            ) from error


def load_credentials(secret_id: str, region_name: str | None = None) -> Credentials:
    client = boto3.client("secretsmanager", region_name=region_name)
    response = client.get_secret_value(SecretId=secret_id)
    value = response.get("SecretString")
    if not isinstance(value, str):
        raise CredentialError("SSI credential must be stored as SecretString.")
    return Credentials.from_json(value)
