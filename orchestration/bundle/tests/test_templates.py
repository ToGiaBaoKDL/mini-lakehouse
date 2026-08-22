from datetime import UTC, datetime
from types import SimpleNamespace

from airflow.sdk.execution_time import macros
from config.templates import (
    DAG_START_DATE,
    LOCAL_TIMEZONE,
    previous_local_date,
    runtime_value,
)
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment


def _render_source_date(source_date: str | None) -> str:
    run_at = datetime(2026, 8, 5, 17, 30, tzinfo=UTC)
    logical_date = datetime(2026, 8, 4, 17, 30, tzinfo=UTC)
    return (
        SandboxedEnvironment()
        .from_string(previous_local_date())
        .render(
            dag_run=SimpleNamespace(logical_date=logical_date, run_after=run_at),
            macros=macros,
            params={"source_date": source_date},
        )
    )


def test_previous_local_date_accepts_standard_datetime() -> None:
    assert _render_source_date(None) == "2026-08-05"


def test_dag_start_date_uses_the_canonical_local_timezone() -> None:
    assert DAG_START_DATE.timezone_name == LOCAL_TIMEZONE


def test_explicit_source_date_takes_precedence() -> None:
    assert _render_source_date("2025-01-02") == "2025-01-02"


def test_required_runtime_value_renders_exactly() -> None:
    template = runtime_value("emr/code_uri")
    rendered = (
        SandboxedEnvironment(undefined=StrictUndefined)
        .from_string(template)
        .render(var=SimpleNamespace(value={"emr/code_uri": "s3://artifacts/emr/jobs/release"}))
    )

    assert template == '{{ var.value["emr/code_uri"] }}'
    assert rendered == "s3://artifacts/emr/jobs/release"
