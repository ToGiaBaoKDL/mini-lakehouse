"""Airflow logging that keeps task INFO records out of container stdout."""

from copy import deepcopy
from typing import Any, cast

from airflow.config_templates.airflow_local_settings import DEFAULT_LOGGING_CONFIG

LOGGING_CONFIG = deepcopy(DEFAULT_LOGGING_CONFIG)
handlers = cast(dict[str, dict[str, Any]], LOGGING_CONFIG["handlers"])
handlers["console"]["level"] = "WARNING"
