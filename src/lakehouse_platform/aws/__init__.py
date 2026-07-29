"""AWS runtime access through the standard SDK credential chain."""

from lakehouse_platform.aws.parameters import get_parameter, get_runtime_parameter

__all__ = ["get_parameter", "get_runtime_parameter"]
