"""Canonical timezone and Jinja values shared by thin DAG definitions."""

import json

import pendulum

LOCAL_TIMEZONE = "Asia/Ho_Chi_Minh"
DAG_START_DATE = pendulum.datetime(2026, 1, 1, tz=LOCAL_TIMEZONE)


def runtime_value(path: str) -> str:
    """Resolve one required SSM-backed Airflow Variable when a task is rendered."""
    return f"{{{{ var.value[{json.dumps(path)}] }}}}"


def previous_local_date(timezone: str = LOCAL_TIMEZONE) -> str:
    """Resolve an optional source date for scheduled and manual DAG runs."""
    return (
        "{{ params.source_date or "
        "((dag_run.run_after.astimezone("
        f"macros.dateutil.tz.gettz('{timezone}'))) "
        "- macros.timedelta(days=1)).strftime('%Y-%m-%d') }}"
    )
