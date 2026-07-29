"""Centralized, testable Streamlit session-state transitions."""

from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Protocol


class SessionKey(StrEnum):
    SEARCH = "document_inspector.search"
    STATE_FILTER = "document_inspector.state_filter"
    RESULT_LIMIT = "document_inspector.result_limit"
    DOCUMENT_ID = "document_inspector.document_id"
    RUN_KEY = "document_inspector.run_key"
    PAGE_NUMBER = "document_inspector.page_number"


DEFAULTS: dict[SessionKey, object] = {
    SessionKey.SEARCH: "",
    SessionKey.STATE_FILTER: "all",
    SessionKey.RESULT_LIMIT: 50,
    SessionKey.DOCUMENT_ID: "",
    SessionKey.RUN_KEY: "",
    SessionKey.PAGE_NUMBER: 1,
}


class SessionState(Protocol):
    def __contains__(self, key: object, /) -> bool: ...

    def __getitem__(self, key: str, /) -> Any: ...

    def __setitem__(self, key: str, value: Any, /) -> None: ...


def initialize(state: SessionState) -> None:
    for key, value in DEFAULTS.items():
        if key not in state:
            state[key] = value


def reset_run(state: SessionState) -> None:
    state[SessionKey.RUN_KEY] = ""
    state[SessionKey.PAGE_NUMBER] = 1


def reset_page(state: SessionState) -> None:
    state[SessionKey.PAGE_NUMBER] = 1


def _get(state: SessionState, key: SessionKey, default: object) -> Any:
    try:
        return state[key]
    except KeyError:
        return default


def reconcile_document(
    state: SessionState,
    available_ids: Sequence[str],
) -> str | None:
    if not available_ids:
        state[SessionKey.DOCUMENT_ID] = ""
        reset_run(state)
        return None
    current = str(_get(state, SessionKey.DOCUMENT_ID, ""))
    if current not in available_ids:
        current = available_ids[0]
        state[SessionKey.DOCUMENT_ID] = current
        reset_run(state)
    return current


def reconcile_run(
    state: SessionState,
    available_keys: Sequence[str],
) -> str | None:
    if not available_keys:
        state[SessionKey.RUN_KEY] = ""
        reset_page(state)
        return None
    current = str(_get(state, SessionKey.RUN_KEY, ""))
    if current not in available_keys:
        current = available_keys[0]
        state[SessionKey.RUN_KEY] = current
        reset_page(state)
    return current


def clamp_page(state: SessionState, page_count: int) -> int:
    if page_count < 1:
        raise ValueError("page_count must be positive")
    current = int(_get(state, SessionKey.PAGE_NUMBER, 1))
    current = min(max(current, 1), page_count)
    state[SessionKey.PAGE_NUMBER] = current
    return current
