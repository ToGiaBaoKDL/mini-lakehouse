from unittest.mock import MagicMock

import pytest
from pytest import MonkeyPatch

from mini_lakehouse.presentation import data_loader


def test_frame_closes_request_scoped_cursor_and_connection(monkeypatch: MonkeyPatch) -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.description = (("value",),)
    cursor.fetchall.return_value = ((42,),)
    monkeypatch.setattr(data_loader, "create_trino_connection", lambda: connection)

    frame = data_loader.query_frame("SELECT ?", [42])

    cursor.execute.assert_called_once_with("SELECT ?", params=[42])
    cursor.close.assert_called_once_with()
    connection.close.assert_called_once_with()
    assert frame.to_dict(orient="records") == [{"value": 42}]


def test_frame_closes_connection_when_query_fails(monkeypatch: MonkeyPatch) -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.execute.side_effect = RuntimeError("query failed")
    monkeypatch.setattr(data_loader, "create_trino_connection", lambda: connection)

    with pytest.raises(RuntimeError, match="query failed"):
        data_loader.query_frame("SELECT broken")

    cursor.close.assert_called_once_with()
    connection.close.assert_called_once_with()
