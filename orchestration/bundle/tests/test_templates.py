import shlex
from datetime import UTC, datetime
from types import SimpleNamespace

from airflow.sdk.execution_time import macros
from airflow_bundle.config.templates import previous_local_date, runtime_value
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment


def _render_source_date(source_date: str | None) -> str:
    run_at = datetime(2026, 8, 5, 17, 30, tzinfo=UTC)
    return (
        SandboxedEnvironment()
        .from_string(previous_local_date())
        .render(
            dag_run=SimpleNamespace(logical_date=run_at, run_after=run_at),
            macros=macros,
            params={"source_date": source_date},
        )
    )


def test_previous_local_date_accepts_standard_datetime() -> None:
    assert _render_source_date(None) == "2026-08-05"


def test_explicit_source_date_takes_precedence() -> None:
    assert _render_source_date("2025-01-02") == "2025-01-02"


def test_required_runtime_value_is_shell_quote_safe() -> None:
    template = runtime_value("emr/code_uri")
    command = shlex.join(["--archives", f"{template}/python.tar.gz#environment"])
    rendered = (
        SandboxedEnvironment(undefined=StrictUndefined)
        .from_string(command)
        .render(var=SimpleNamespace(value={"emr/code_uri": "s3://artifacts/emr/jobs/release"}))
    )

    assert template == '{{ var.value["emr/code_uri"] }}'
    assert rendered == "--archives 's3://artifacts/emr/jobs/release/python.tar.gz#environment'"
