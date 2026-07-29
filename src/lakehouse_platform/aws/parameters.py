"""Cached reads for non-secret runtime references in Parameter Store."""

from functools import cache

import boto3


@cache
def _client():
    return boto3.client("ssm")


@cache
def get_parameter(name: str) -> str:
    if not name.startswith("/"):
        raise ValueError("Parameter Store names must be absolute paths")
    value = _client().get_parameter(Name=name)["Parameter"].get("Value")
    if not isinstance(value, str):
        raise RuntimeError(f"Parameter Store returned no string value for {name}")
    return value


def get_runtime_parameter(environment: str, key: str) -> str:
    normalized = key.strip("/")
    if not normalized:
        raise ValueError("Runtime parameter key cannot be empty")
    return get_parameter(f"/lakehouse/{environment}/{normalized}")
