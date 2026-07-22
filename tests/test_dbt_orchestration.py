import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call

import pytest


def _load_dbt_utils() -> ModuleType:
    path = Path("orchestration/utils/dbt.py")
    spec = importlib.util.spec_from_file_location("orchestration_dbt", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dbt_pipeline_stops_when_source_freshness_fails() -> None:
    runner = Mock()
    runner.invoke.return_value = SimpleNamespace(success=False)

    with pytest.raises(RuntimeError, match="source freshness"):
        _load_dbt_utils().run_dbt_pipeline(runner=runner)

    runner.invoke.assert_called_once_with(["source", "freshness", "--selector", "github_sources"])


def test_dbt_pipeline_serializes_only_iceberg_write_phase() -> None:
    runner = Mock()
    runner.invoke.side_effect = [
        SimpleNamespace(success=True),
        SimpleNamespace(success=True),
        SimpleNamespace(success=True),
        SimpleNamespace(success=True),
    ]

    _load_dbt_utils().run_dbt_pipeline(runner=runner)

    assert runner.invoke.call_args_list == [
        call(["source", "freshness", "--selector", "github_sources"]),
        call(["test", "--selector", "github_sources", "--indirect-selection", "cautious"]),
        call(["run", "--selector", "engineering_marts", "--threads", "1"]),
        call(["test", "--selector", "engineering_marts"]),
    ]
