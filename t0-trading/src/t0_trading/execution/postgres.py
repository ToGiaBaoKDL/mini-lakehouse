"""PostgreSQL CAS checkpoint plus append-only operations in the same transaction.

The service owns the driver/connection lifecycle. No credentials, migrations, or database access
are added to the raw-capture process. Compatible with a psycopg connection's transaction API.
"""

from __future__ import annotations

from datetime import date

from t0_trading.execution.runtime import PaperSession
from t0_trading.persistence import PostgresConnection


class PaperConflict(RuntimeError):
    """Reload and reconsider the command; stale resources must never be retried blindly."""


class PostgresPaperRepository:
    def __init__(
        self, connection: PostgresConnection, *, trade_date: date, account_sha256: str
    ) -> None:
        if not connection.autocommit:
            raise ValueError(
                "paper repository requires autocommit; it owns each explicit transaction"
            )
        self._connection = connection
        self._key = (trade_date, account_sha256)

    def _validate_key(self, session: PaperSession) -> None:
        if (session.ledger.trade_date, session.account.sha256) != self._key:
            raise ValueError("paper repository session key mismatch")

    def initialize(self, session: PaperSession) -> None:
        self._validate_key(session)
        PaperSession.model_validate(session.model_dump())
        if session.revision or session.operations or session.markets:
            raise ValueError("paper repository initialization requires an empty revision zero")
        with self._connection.transaction():
            self._connection.execute(
                "INSERT INTO paper_sessions "
                "(trade_date, account_sha256, revision, state_sha256, state_json) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (trade_date, account_sha256) DO NOTHING",
                (*self._key, session.revision, session.sha256, session.model_dump_json()),
            )
            existing = self.load()
            if existing.account != session.account or existing.costs != session.costs:
                raise PaperConflict("paper session initialization conflicts with existing policy")

    def load(self) -> PaperSession:
        row = self._connection.execute(
            "SELECT state_sha256, state_json FROM paper_sessions "
            "WHERE trade_date = %s AND account_sha256 = %s",
            self._key,
        ).fetchone()
        if row is None:
            raise LookupError("paper session has not been initialized")
        if not isinstance(row[1], str):
            raise ValueError("paper checkpoint must be canonical JSON text")
        session = PaperSession.model_validate_json(row[1])
        self._validate_key(session)
        if session.sha256 != row[0]:
            raise ValueError("paper checkpoint checksum mismatch")
        rows = self._connection.execute(
            "SELECT sequence, operation_sha256, operation_json FROM paper_operations "
            "WHERE trade_date = %s AND account_sha256 = %s AND sequence <= %s ORDER BY sequence",
            (*self._key, len(session.operations)),
        ).fetchall()
        if len(rows) != len(session.operations):
            raise ValueError("paper operation log is incomplete")
        for sequence, (record, operation) in enumerate(
            zip(rows, session.operations, strict=True), start=1
        ):
            if (
                record[0] != sequence
                or record[1] != operation.sha256
                or not isinstance(record[2], str)
                or type(operation).model_validate_json(record[2]) != operation
            ):
                raise ValueError("paper checkpoint and operation log disagree")
        return session

    def commit(self, previous: PaperSession, current: PaperSession) -> None:
        self._validate_key(previous)
        self._validate_key(current)
        PaperSession.model_validate(current.model_dump())
        if (
            current.revision != previous.revision + 1
            or current.account != previous.account
            or current.costs != previous.costs
            or current.operations[: len(previous.operations)] != previous.operations
        ):
            raise ValueError("paper commit must preserve its immutable operation prefix")
        with self._connection.transaction():
            updated = self._connection.execute(
                "UPDATE paper_sessions SET revision = %s, state_sha256 = %s, state_json = %s "
                "WHERE trade_date = %s AND account_sha256 = %s "
                "AND revision = %s AND state_sha256 = %s",
                (
                    current.revision,
                    current.sha256,
                    current.model_dump_json(),
                    *self._key,
                    previous.revision,
                    previous.sha256,
                ),
            )
            if updated.rowcount != 1:
                # A lost commit response is safe to retry with exactly the same transition.
                if self.load() == current:
                    return
                raise PaperConflict("paper checkpoint compare-and-swap conflict")
            for sequence, operation in enumerate(
                current.operations[len(previous.operations) :], start=len(previous.operations) + 1
            ):
                self._connection.execute(
                    "INSERT INTO paper_operations "
                    "(trade_date, account_sha256, sequence, operation_sha256, operation_json) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (*self._key, sequence, operation.sha256, operation.model_dump_json()),
                )
