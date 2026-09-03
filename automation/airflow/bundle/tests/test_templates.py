from datetime import UTC, datetime
from types import SimpleNamespace

from airflow.sdk.execution_time import macros
from config.templates import (
    DAG_START_DATE,
    LOCAL_TIMEZONE,
    data_interval_start_date,
    runtime_value,
)
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment


def _render_source_date() -> str:
    interval_start = datetime(2026, 8, 4, 0, 30, tzinfo=UTC)
    return (
        SandboxedEnvironment()
        .from_string(data_interval_start_date())
        .render(
            data_interval_start=interval_start,
            macros=macros,
        )
    )


def test_data_interval_start_date_accepts_standard_datetime() -> None:
    assert _render_source_date() == "2026-08-04"


def test_dag_start_date_uses_the_canonical_local_timezone() -> None:
    assert DAG_START_DATE.timezone_name == LOCAL_TIMEZONE


def test_required_runtime_value_renders_exactly() -> None:
    template = runtime_value("emr/code_uri")
    rendered = (
        SandboxedEnvironment(undefined=StrictUndefined)
        .from_string(template)
        .render(var=SimpleNamespace(value={"emr/code_uri": "s3://artifacts/emr/jobs/release"}))
    )

    assert template == '{{ var.value["emr/code_uri"] }}'
    assert rendered == "s3://artifacts/emr/jobs/release"
