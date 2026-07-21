from typing import Literal
from unittest.mock import MagicMock, Mock

import pytest
import requests

from mini_lakehouse.config import get_settings
from mini_lakehouse.observability import notifications
from mini_lakehouse.observability.notifications import RunNotification


def _notification(
    *,
    kind: Literal["flow", "task"] = "task",
    status: Literal["running", "succeeded", "failed"] = "failed",
) -> RunNotification:
    return RunNotification(
        kind=kind,
        status=status,
        definition_name="etl_ingest_github_archive_hour",
        run_name="helpful-otter",
        run_id="task-run-id",
        flow_run_id="flow-run-id",
        state_name="Failed",
        detail="archive endpoint returned 503",
    )


def test_slack_task_failure_is_posted_inside_its_flow_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAKEHOUSE_ENVIRONMENT", "ci")
    monkeypatch.setenv("LAKEHOUSE_NOTIFICATIONS__SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("LAKEHOUSE_NOTIFICATIONS__SLACK_CHANNEL_ID", "C0123456789")
    monkeypatch.setenv(
        "LAKEHOUSE_NOTIFICATIONS__PREFECT_UI_URL",
        "https://prefect.example.com",
    )
    get_settings.cache_clear()
    response = Mock(spec=requests.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True, "ts": "1710000000.123"}
    post = Mock(return_value=response)
    monkeypatch.setattr(notifications.requests, "post", post)

    try:
        delivery = notifications.dispatch_run_notification(
            _notification(),
            slack_thread_ts="1700000000.456",
        )
    finally:
        get_settings.cache_clear()

    assert delivery.slack_thread_ts == "1710000000.123"
    post.assert_called_once()
    assert post.call_args.args[0] == "https://slack.com/api/chat.postMessage"
    payload = post.call_args.kwargs["json"]
    assert payload["thread_ts"] == "1700000000.456"
    assert payload["reply_broadcast"] is False
    rendered = str(payload["attachments"])
    assert "etl_ingest_github_archive_hour" in rendered
    assert "archive endpoint returned 503" in rendered
    assert "https://prefect.example.com/runs/flow-run/flow-run-id" in rendered


def test_slack_terminal_flow_updates_the_parent_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAKEHOUSE_NOTIFICATIONS__SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("LAKEHOUSE_NOTIFICATIONS__SLACK_CHANNEL_ID", "C0123456789")
    get_settings.cache_clear()
    response = Mock(spec=requests.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True, "ts": "1700000000.456"}
    post = Mock(return_value=response)
    monkeypatch.setattr(notifications.requests, "post", post)

    try:
        notifications.dispatch_run_notification(
            _notification(kind="flow", status="succeeded"),
            slack_thread_ts="1700000000.456",
        )
    finally:
        get_settings.cache_clear()

    assert post.call_args.args[0] == "https://slack.com/api/chat.update"
    assert post.call_args.kwargs["json"]["ts"] == "1700000000.456"


def test_gmail_sends_html_task_failure_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LAKEHOUSE_NOTIFICATIONS__GMAIL_SENDER",
        "lakehouse-alerts@gmail.com",
    )
    monkeypatch.setenv("LAKEHOUSE_NOTIFICATIONS__GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv(
        "LAKEHOUSE_NOTIFICATIONS__GMAIL_RECIPIENTS",
        '["data-platform@example.com"]',
    )
    get_settings.cache_clear()
    smtp = MagicMock()
    smtp_context = MagicMock()
    smtp_context.__enter__.return_value = smtp
    smtp_factory = Mock(return_value=smtp_context)
    monkeypatch.setattr(notifications.smtplib, "SMTP", smtp_factory)

    try:
        notifications.dispatch_run_notification(_notification())
    finally:
        get_settings.cache_clear()

    smtp_factory.assert_called_once_with("smtp.gmail.com", 587, timeout=10.0)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("lakehouse-alerts@gmail.com", "app-password")
    message = smtp.send_message.call_args.args[0]
    assert message["To"] == "data-platform@example.com"
    assert "FAILED" in message["Subject"]
    assert message.get_body(preferencelist=("html",)) is not None
