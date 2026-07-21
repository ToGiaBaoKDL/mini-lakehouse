"""Prefect hooks with one Slack thread per flow run."""

import logging
from typing import Any

from prefect.client.schemas.objects import FlowRun, State, TaskRun
from prefect.flows import Flow
from prefect.tasks import Task
from prefect.variables import Variable

from mini_lakehouse.config import get_settings
from mini_lakehouse.observability import RunNotification, dispatch_run_notification

logger = logging.getLogger(__name__)


def _state_detail(state: State[Any]) -> str:
    detail = state.message or "No state detail was provided by Prefect"
    return detail[:2000]


def _thread_variable_name(flow_run_id: str) -> str:
    return f"slack-thread-{flow_run_id.replace('-', '')}"


def _load_thread(flow_run_id: str) -> str | None:
    if get_settings().notifications.slack_bot_token is None:
        return None
    try:
        value = Variable.get(_thread_variable_name(flow_run_id))
        return value if isinstance(value, str) else None
    except Exception:
        logger.exception("Could not load the Slack thread reference from Prefect")
        return None


def _store_thread(flow_run_id: str, thread_ts: str) -> None:
    try:
        Variable.set(
            _thread_variable_name(flow_run_id),
            thread_ts,
            tags=["mini-lakehouse", "slack-thread"],
            overwrite=True,
        )
    except Exception:
        logger.exception("Could not persist the Slack thread reference in Prefect")


def _clear_thread(flow_run_id: str) -> None:
    if get_settings().notifications.slack_bot_token is None:
        return
    try:
        Variable.unset(_thread_variable_name(flow_run_id))
    except Exception:
        logger.exception("Could not clear the Slack thread reference from Prefect")


def notify_flow_running(
    flow: Flow[..., Any],
    flow_run: FlowRun,
    state: State[Any],
) -> None:
    flow_run_id = str(flow_run.id)
    delivery = dispatch_run_notification(
        RunNotification(
            kind="flow",
            status="running",
            definition_name=flow.name,
            run_name=flow_run.name,
            run_id=flow_run_id,
            flow_run_id=flow_run_id,
            state_name=state.name or str(state.type),
            detail=_state_detail(state),
        )
    )
    if delivery.slack_thread_ts is not None:
        _store_thread(flow_run_id, delivery.slack_thread_ts)


def _notify_terminal_flow(
    flow: Flow[..., Any],
    flow_run: FlowRun,
    state: State[Any],
    *,
    succeeded: bool,
) -> None:
    flow_run_id = str(flow_run.id)
    dispatch_run_notification(
        RunNotification(
            kind="flow",
            status="succeeded" if succeeded else "failed",
            definition_name=flow.name,
            run_name=flow_run.name,
            run_id=flow_run_id,
            flow_run_id=flow_run_id,
            state_name=state.name or str(state.type),
            detail=_state_detail(state),
        ),
        slack_thread_ts=_load_thread(flow_run_id),
    )
    _clear_thread(flow_run_id)


def notify_flow_success(
    flow: Flow[..., Any],
    flow_run: FlowRun,
    state: State[Any],
) -> None:
    _notify_terminal_flow(flow, flow_run, state, succeeded=True)


def notify_flow_failure(
    flow: Flow[..., Any],
    flow_run: FlowRun,
    state: State[Any],
) -> None:
    _notify_terminal_flow(flow, flow_run, state, succeeded=False)


def notify_task_failure(
    task: Task[..., Any],
    task_run: TaskRun,
    state: State[Any],
) -> None:
    flow_run_id = str(task_run.flow_run_id)
    dispatch_run_notification(
        RunNotification(
            kind="task",
            status="failed",
            definition_name=task.name,
            run_name=task_run.name,
            run_id=str(task_run.id),
            flow_run_id=flow_run_id,
            state_name=state.name or str(state.type),
            detail=_state_detail(state),
        ),
        slack_thread_ts=_load_thread(flow_run_id),
    )
