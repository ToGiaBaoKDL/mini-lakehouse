from datetime import UTC, datetime
from types import SimpleNamespace

from airflow.sdk.execution_time import macros
from config.templates import (
    DAG_START_DATE,
    LOCAL_TIMEZONE,
    partition_key_or_previous_date,
    partition_key_or_run_date,
    runtime_value,
)
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment


def test_partition_date_prefers_scheduled_key() -> None:
    rendered = (
        SandboxedEnvironment()
        .from_string(partition_key_or_run_date())
        .render(
            dag_run=SimpleNamespace(
                partition_key="2026-08-01",
                run_after=datetime(2026, 8, 4, 18, 30, tzinfo=UTC),
            ),
            macros=macros,
        )
    )

    assert rendered == "2026-08-01"


def test_previous_partition_date_defaults_to_last_completed_utc_day() -> None:
    rendered = (
        SandboxedEnvironment()
        .from_string(partition_key_or_previous_date())
        .render(
            dag_run=SimpleNamespace(
                partition_key=None,
                run_after=datetime(2026, 8, 4, 18, 30, tzinfo=UTC),
            ),
            macros=macros,
        )
    )

    assert rendered == "2026-08-03"


def test_partition_date_defaults_to_local_manual_trigger_date() -> None:
    rendered = (
        SandboxedEnvironment()
        .from_string(partition_key_or_run_date())
        .render(
            dag_run=SimpleNamespace(
                partition_key=None,
                run_after=datetime(2026, 8, 4, 18, 30, tzinfo=UTC),
            ),
            macros=macros,
        )
    )

    assert rendered == "2026-08-05"


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
