"""Process-boundary configuration and connection lifecycle for durable shadow delivery."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from t0_trading.arbitration.selection import RealtimeSelection
from t0_trading.notifications.postgres import ShadowDeliveryRepository
from t0_trading.persistence import PostgresConnection, TransientDatabaseError


@contextmanager
def delivery_repository(dsn_file: Path) -> Generator[ShadowDeliveryRepository]:
    import psycopg

    # Connect only on the owning consumer thread/process, with bounded DB operations.
    try:
        with psycopg.connect(
            dsn_file.read_text().strip(),
            autocommit=True,
            connect_timeout=3,
            options="-c statement_timeout=3000 -c lock_timeout=1000",
        ) as connection:
            yield ShadowDeliveryRepository(cast(PostgresConnection, connection))
    except psycopg.Error as error:
        if (
            isinstance(error, psycopg.InterfaceError)
            or (isinstance(error, psycopg.OperationalError) and error.sqlstate is None)
            or (error.sqlstate is not None and error.sqlstate.startswith("08"))
            or error.sqlstate in {"40001", "40P01", "55P03", "57014", "57P01", "57P02", "57P03"}
        ):
            raise TransientDatabaseError(type(error).__name__) from None
        raise


class DurableShadowSink:
    def __init__(self, dsn_file: Path, *, lifetime: timedelta) -> None:
        self._dsn_file = dsn_file
        self._lifetime = lifetime

    def __call__(self, selection: RealtimeSelection) -> None:
        with delivery_repository(self._dsn_file) as repository:
            repository.enqueue(selection, lifetime=self._lifetime)

    def invalidate(self, at: datetime) -> None:
        with delivery_repository(self._dsn_file) as repository:
            repository.invalidate(at)
