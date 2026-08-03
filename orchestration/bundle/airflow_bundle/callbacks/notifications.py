"""Airflow-provider notifications shared by every production DAG."""

from collections.abc import Callable

from airflow.providers.slack.notifications.slack import send_slack_notification
from airflow.providers.smtp.notifications.smtp import send_smtp_notification
from airflow.sdk.definitions.context import Context

ALERT_EMAIL = "{{ var.value.get('notifications/alert_email') }}"
SLACK_CHANNEL = "{{ var.value.get('notifications/slack_channel') }}"


def dag_failure_callbacks() -> list[Callable[[Context], None]]:
    return [
        send_slack_notification(
            slack_conn_id="slack_api_default",
            channel=SLACK_CHANNEL,
            username="Lakehouse Airflow",
            text=(
                ":red_circle: *DAG failed*\n"
                "*DAG:* `{{ dag.dag_id }}`\n"
                "*Run:* `{{ run_id }}`\n"
                "*When:* `{{ ts }}`"
            ),
        ),
        send_smtp_notification(
            smtp_conn_id="smtp_default",
            to=ALERT_EMAIL,
            subject="[Lakehouse] DAG {{ dag.dag_id }} failed",
            html_content=(
                "<h3>DAG failed</h3>"
                "<p><b>DAG:</b> {{ dag.dag_id }}<br>"
                "<b>Run:</b> {{ run_id }}<br>"
                "<b>When:</b> {{ ts }}</p>"
            ),
        ),
    ]


def dag_success_callbacks() -> list[Callable[[Context], None]]:
    return [
        send_slack_notification(
            slack_conn_id="slack_api_default",
            channel=SLACK_CHANNEL,
            username="Lakehouse Airflow",
            text=(
                ":large_green_circle: *DAG succeeded*\n"
                "*DAG:* `{{ dag.dag_id }}`\n"
                "*Run:* `{{ run_id }}`"
            ),
        ),
        send_smtp_notification(
            smtp_conn_id="smtp_default",
            to=ALERT_EMAIL,
            subject="[Lakehouse] DAG {{ dag.dag_id }} succeeded",
            html_content=(
                "<h3>DAG succeeded</h3>"
                "<p><b>DAG:</b> {{ dag.dag_id }}<br><b>Run:</b> {{ run_id }}</p>"
            ),
        ),
    ]


def task_failure_callbacks() -> list[Callable[[Context], None]]:
    return [
        send_slack_notification(
            slack_conn_id="slack_api_default",
            channel=SLACK_CHANNEL,
            username="Lakehouse Airflow",
            text=(
                ":warning: *Task failed*\n"
                "*DAG:* `{{ dag.dag_id }}`\n"
                "*Task:* `{{ ti.task_id }}`\n"
                "*Try:* `{{ ti.try_number }}`\n"
                "<{{ ti.log_url }}|Open task log>"
            ),
        ),
        send_smtp_notification(
            smtp_conn_id="smtp_default",
            to=ALERT_EMAIL,
            subject="[Lakehouse] Task {{ ti.task_id }} failed",
            html_content=(
                "<h3>Task failed</h3>"
                "<p><b>DAG:</b> {{ dag.dag_id }}<br>"
                "<b>Task:</b> {{ ti.task_id }}<br>"
                "<b>Try:</b> {{ ti.try_number }}<br>"
                '<a href="{{ ti.log_url }}">Open task log</a></p>'
            ),
        ),
    ]
