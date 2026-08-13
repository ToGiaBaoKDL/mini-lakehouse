"""Keep task INFO records while service consoles start at WARNING."""

from copy import deepcopy
from typing import Any, cast

from airflow.config_templates.airflow_local_settings import DEFAULT_LOGGING_CONFIG

LOGGING_CONFIG = deepcopy(DEFAULT_LOGGING_CONFIG)
loggers = cast(dict[str, dict[str, Any]], LOGGING_CONFIG["loggers"])
loggers["airflow.task"]["level"] = "INFO"
