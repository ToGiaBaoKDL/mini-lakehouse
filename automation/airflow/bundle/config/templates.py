"""Canonical timezone and Jinja values shared by thin DAG definitions."""

import json

import pendulum

LOCAL_TIMEZONE = "Asia/Ho_Chi_Minh"
DAG_START_DATE = pendulum.datetime(2026, 1, 1, tz=LOCAL_TIMEZONE)


def runtime_value(path: str) -> str:
    """Resolve one required SSM-backed Airflow Variable when a task is rendered."""
    return f"{{{{ var.value[{json.dumps(path)}] }}}}"


def partition_key_or_previous_date(timezone: str = "UTC") -> str:
    """Use a scheduled partition key or the last completed day for manual runs."""
    return (
        "{{ dag_run.partition_key or ((dag_run.run_after.astimezone("
        f"macros.dateutil.tz.gettz('{timezone}'))) - "
        "macros.timedelta(days=1)).strftime('%Y-%m-%d') }}"
    )


def partition_key_or_run_date(timezone: str = LOCAL_TIMEZONE) -> str:
    """Use a scheduled partition key or the local trigger date for manual runs."""
    return (
        "{{ dag_run.partition_key or dag_run.run_after.astimezone("
        f"macros.dateutil.tz.gettz('{timezone}')).strftime('%Y-%m-%d') }}}}"
    )
