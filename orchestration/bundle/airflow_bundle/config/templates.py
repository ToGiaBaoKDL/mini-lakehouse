"""Jinja values shared by thin Airflow DAG definitions."""

import json


def runtime_value(path: str) -> str:
    """Resolve one required SSM-backed Airflow Variable when a task is rendered."""
    return f"{{{{ var.value[{json.dumps(path)}] }}}}"


def previous_local_date(timezone: str = "Asia/Ho_Chi_Minh") -> str:
    """Resolve an optional source date for scheduled and manual DAG runs."""
    return (
        "{{ params.source_date or "
        "(((dag_run.logical_date or dag_run.run_after).astimezone("
        f"macros.dateutil.tz.gettz('{timezone}'))) "
        "- macros.timedelta(days=1)).strftime('%Y-%m-%d') }}"
    )
