"""Best-effort Slack and Gmail delivery for operational run notifications."""

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from typing import Any, Literal, cast

import requests

from mini_lakehouse.config import get_settings

logger = logging.getLogger(__name__)

NotificationStatus = Literal["running", "succeeded", "failed"]


@dataclass(frozen=True, slots=True)
class RunNotification:
    kind: Literal["flow", "task"]
    status: NotificationStatus
    definition_name: str
    run_name: str
    run_id: str
    flow_run_id: str
    state_name: str
    detail: str


@dataclass(frozen=True, slots=True)
class NotificationDelivery:
    slack_thread_ts: str | None = None


def _flow_run_url(notification: RunNotification) -> str:
    ui_url = get_settings().notifications.prefect_ui_url.rstrip("/")
    return f"{ui_url}/runs/flow-run/{notification.flow_run_id}"


def _status_presentation(status: NotificationStatus) -> tuple[str, str, str]:
    return {
        "running": ("RUNNING", ":hourglass_flowing_sand:", "#ECB22E"),
        "succeeded": ("SUCCEEDED", ":white_check_mark:", "#2EB67D"),
        "failed": ("FAILED", ":x:", "#E01E5A"),
    }[status]


def _slack_blocks(notification: RunNotification) -> list[dict[str, Any]]:
    label, icon, _ = _status_presentation(notification.status)
    object_label = "Flow" if notification.kind == "flow" else "Task"
    detail = escape(notification.detail[:1500])
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{icon} {object_label} {label}: {notification.definition_name}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Environment*\n{get_settings().environment}"},
                {"type": "mrkdwn", "text": f"*State*\n{notification.state_name}"},
                {"type": "mrkdwn", "text": f"*Run*\n{notification.run_name}"},
                {"type": "mrkdwn", "text": f"*Run ID*\n`{notification.run_id}`"},
            ],
        },
    ]
    if notification.detail:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Detail*\n```{detail}```"},
            }
        )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open Prefect run"},
                    "url": _flow_run_url(notification),
                    "style": "primary" if notification.status != "failed" else "danger",
                }
            ],
        }
    )
    return blocks


def _slack_api(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings().notifications
    assert settings.slack_bot_token is not None
    response = requests.post(
        f"https://slack.com/api/{method}",
        headers={
            "Authorization": f"Bearer {settings.slack_bot_token.get_secret_value()}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=settings.timeout_seconds,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or result.get("ok") is not True:
        error = result.get("error", "invalid_response") if isinstance(result, dict) else result
        raise RuntimeError(f"Slack API {method} failed: {error}")
    return cast(dict[str, Any], result)


def _send_slack(
    notification: RunNotification,
    thread_ts: str | None,
) -> str | None:
    settings = get_settings().notifications
    if settings.slack_bot_token is None:
        return thread_ts
    assert settings.slack_channel_id is not None
    label, _, color = _status_presentation(notification.status)
    payload: dict[str, Any] = {
        "channel": settings.slack_channel_id,
        "text": f"{notification.kind.title()} {label}: {notification.definition_name}",
        "attachments": [{"color": color, "blocks": _slack_blocks(notification)}],
    }
    if notification.kind == "flow" and notification.status != "running" and thread_ts:
        payload["ts"] = thread_ts
        _slack_api("chat.update", payload)
        return thread_ts
    if notification.kind == "task" and thread_ts:
        payload["thread_ts"] = thread_ts
        payload["reply_broadcast"] = False
    result = _slack_api("chat.postMessage", payload)
    timestamp = result.get("ts")
    return timestamp if isinstance(timestamp, str) else thread_ts


def _gmail_content(notification: RunNotification) -> tuple[str, str, str]:
    label, _, color = _status_presentation(notification.status)
    object_label = notification.kind.title()
    subject = (
        f"[{label}][{get_settings().environment}] Prefect {object_label}: "
        f"{notification.definition_name}"
    )
    text = "\n".join(
        (
            subject,
            f"Run: {notification.run_name}",
            f"State: {notification.state_name}",
            f"Run ID: {notification.run_id}",
            f"Detail: {notification.detail}",
            f"Prefect: {_flow_run_url(notification)}",
        )
    )
    html = f"""
    <html>
      <head>
        <style>
          body {{ font-family: Arial, sans-serif; background: #f6f7f9; padding: 24px; }}
          .card {{ max-width: 680px; margin: auto; background: #fff; border-radius: 12px; }}
          .header {{ background: {color}; color: #fff; padding: 20px 24px; }}
          .content {{ padding: 24px; }}
          .detail {{ margin: 20px 0; padding: 14px; background: #f3f4f6; }}
          .button {{ background: #2563eb; color: #fff; padding: 11px 18px; }}
          table {{ width: 100%; }}
        </style>
      </head>
      <body>
        <div class="card">
          <div class="header"><h2>{escape(object_label)} {escape(label)}</h2></div>
          <div class="content">
            <h3>{escape(notification.definition_name)}</h3>
            <table role="presentation">
              <tr><td><b>Environment</b></td><td>{escape(get_settings().environment)}</td></tr>
              <tr><td><b>Run</b></td><td>{escape(notification.run_name)}</td></tr>
              <tr><td><b>State</b></td><td>{escape(notification.state_name)}</td></tr>
              <tr><td><b>Run ID</b></td><td>{escape(notification.run_id)}</td></tr>
            </table>
            <div class="detail">{escape(notification.detail)}</div>
            <a class="button" href="{escape(_flow_run_url(notification))}">
              Open Prefect run
            </a>
          </div>
        </div>
      </body>
    </html>
    """
    return subject, text, html


def _send_gmail(notification: RunNotification) -> None:
    settings = get_settings().notifications
    # A running event creates the Slack parent thread; email is reserved for actionable terminal
    # flow states and task failures to avoid inbox noise.
    if settings.gmail_sender is None or notification.status == "running":
        return
    assert settings.gmail_app_password is not None
    subject, text, html = _gmail_content(notification)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.gmail_sender
    message["To"] = ", ".join(settings.gmail_recipients)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=settings.timeout_seconds) as smtp:
        smtp.starttls()
        smtp.login(settings.gmail_sender, settings.gmail_app_password.get_secret_value())
        smtp.send_message(message)


def dispatch_run_notification(
    notification: RunNotification,
    *,
    slack_thread_ts: str | None = None,
) -> NotificationDelivery:
    """Deliver configured channels independently without masking the pipeline state."""
    resulting_thread_ts = slack_thread_ts
    try:
        resulting_thread_ts = _send_slack(notification, slack_thread_ts)
    except Exception:
        logger.exception("Could not deliver Prefect notification to Slack")
    try:
        _send_gmail(notification)
    except Exception:
        logger.exception("Could not deliver Prefect notification through Gmail")
    return NotificationDelivery(slack_thread_ts=resulting_thread_ts)
