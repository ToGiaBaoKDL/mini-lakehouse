from datetime import UTC, datetime
from types import SimpleNamespace

from airflow.sdk.execution_time import macros
from airflow_bundle.config.templates import previous_local_date
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
