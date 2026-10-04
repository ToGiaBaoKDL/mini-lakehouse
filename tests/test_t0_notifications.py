"""No real Telegram calls. Delivery ambiguity must never become a blind resend."""

import asyncio
import os
from collections.abc import Generator
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import LiteralString, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from t0_trading.cli import app
from t0_trading.identity import sha256
from t0_trading.notifications.postgres import DeliveryClaim, ShadowDeliveryRepository
from t0_trading.notifications.runtime import delivery_repository
from t0_trading.notifications.telegram import TelegramSender, dispatch_one
from t0_trading.persistence import PostgresConnection, TransientDatabaseError
from telegram import Bot
from telegram.error import BadRequest, RetryAfter, TimedOut
from test_t0_realtime import realtime_selection
from typer.testing import CliRunner


def _claim() -> DeliveryClaim:
    selection = realtime_selection()
    return DeliveryClaim(
        selection.arbitration.sha256,
        uuid4(),
        selection.candidate.decision_at + timedelta(seconds=15),
        selection.canonical_bytes().decode(),
        sha256(selection.canonical_bytes()),
        1,
    )


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (None, "SENT"),
        (RetryAfter(2), "PENDING"),
        (BadRequest("secret"), "FAILED"),
        (TimedOut(), "UNKNOWN"),
        (TimeoutError("secret"), "UNKNOWN"),
    ],
)
def test_dispatch_uses_sdk_result_without_retrying_ambiguous_sends(
    failure: Exception | None, expected: str
) -> None:
    repository = Mock(spec=ShadowDeliveryRepository)
    claim = _claim()
    repository.claim.return_value = claim
    repository.may_send.return_value = True
    repository.finish.return_value = expected
    sender = Mock()
    sender.send = AsyncMock(return_value="42", side_effect=failure)
    result = asyncio.run(dispatch_one(repository, sender, enabled=True))
    assert result == expected
    sender.send.assert_awaited_once()
    assert "SHADOW" in sender.send.call_args.args[0]
    assert "CHƯA QUA PROMOTION" in sender.send.call_args.args[0]
    assert "secret" not in str(repository.finish.call_args)
    assert repository.finish.call_args.args == (claim, expected)
    if expected == "PENDING":
        assert repository.finish.call_args.kwargs["retry_after"] == timedelta(seconds=2)


def test_disabled_expired_and_corrupt_deliveries_never_send() -> None:
    repository = Mock(spec=ShadowDeliveryRepository)
    sender = Mock()
    sender.send = AsyncMock()
    assert asyncio.run(dispatch_one(repository, sender)) == "DISABLED"
    repository.claim.assert_not_called()
    repository.claim.return_value = _claim()
    repository.may_send.return_value = False
    assert asyncio.run(dispatch_one(repository, sender, enabled=True)) == "EXPIRED"
    claim = _claim()
    repository.claim.return_value = DeliveryClaim(
        claim.selection_id, claim.token, claim.expires_at, claim.payload_json, "0" * 64, 1
    )
    assert asyncio.run(dispatch_one(repository, sender, enabled=True)) == "FAILED"
    sender.send.assert_not_called()


def test_telegram_adapter_uses_plain_text_and_sdk_timeout_options() -> None:
    bot = Mock(spec=Bot)
    bot.send_message = AsyncMock(return_value=Mock(message_id=7))
    sender = TelegramSender(cast(Bot, bot), "1234", timeout_seconds=2)
    assert asyncio.run(sender.send("SHADOW")) == "7"
    assert bot.send_message.call_args.kwargs["parse_mode"] is None
    assert bot.send_message.call_args.kwargs["read_timeout"] == 2


def test_notification_cli_defaults_to_no_io() -> None:
    result = CliRunner().invoke(
        app, ["notify-shadow", "--dsn-file", "/missing/dsn", "--telegram-file", "/missing/token"]
    )
    assert result.exit_code == 0
    assert "disabled" in result.output


def test_lost_database_ack_after_send_does_not_repeat_the_send() -> None:
    repository = Mock(spec=ShadowDeliveryRepository)
    repository.claim.return_value = _claim()
    repository.may_send.return_value = True
    repository.finish.side_effect = RuntimeError("lost acknowledgement")
    sender = Mock()
    sender.send = AsyncMock(return_value="42")
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        asyncio.run(dispatch_one(repository, sender, enabled=True))
    sender.send.assert_awaited_once()


def test_send_window_accounts_for_database_roundtrip_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Mock(spec=PostgresConnection)
    connection.autocommit = True
    connection.execute.return_value.fetchone.return_value = (6,)
    clocks = iter((0.0, 2.0))
    monkeypatch.setattr("t0_trading.notifications.postgres.monotonic", lambda: next(clocks))
    repository = ShadowDeliveryRepository(connection)
    assert not repository.may_send(_claim(), timeout=timedelta(seconds=5))


def test_runtime_classifies_temporary_errors_without_retrying_invalid_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    dsn = tmp_path / "dsn"
    dsn.write_text("unused")
    connect = Mock(side_effect=psycopg.OperationalError("secret password"))
    monkeypatch.setattr(psycopg, "connect", connect)
    with pytest.raises(TransientDatabaseError) as failure, delivery_repository(dsn):
        pytest.fail("unreachable")
    assert "secret" not in str(failure.value)
    connect.side_effect = psycopg.errors.InvalidPassword("secret password")
    with pytest.raises(psycopg.errors.InvalidPassword), delivery_repository(dsn):
        pytest.fail("unreachable")


def test_notifier_reconnects_after_transient_database_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import t0_trading.notifications.cli as notification_cli

    stop = Event()
    monkeypatch.setattr(notification_cli, "Event", lambda: stop)
    repository = Mock(spec=ShadowDeliveryRepository)
    connect = Mock(
        side_effect=[TransientDatabaseError("OperationalError"), nullcontext(repository)]
    )
    monkeypatch.setattr("t0_trading.notifications.runtime.delivery_repository", connect)
    bot = Mock(spec=Bot)
    bot_context = AsyncMock()
    bot_context.__aenter__.return_value = bot
    monkeypatch.setattr("telegram.Bot", Mock(return_value=bot_context))
    calls: list[ShadowDeliveryRepository] = []

    async def dispatch(repository: ShadowDeliveryRepository, *_: object, **__: object) -> str:
        calls.append(repository)
        stop.set()
        return "SENT"

    monkeypatch.setattr("t0_trading.notifications.telegram.dispatch_one", dispatch)
    credentials = tmp_path / "telegram.json"
    credentials.write_text('{"bot_token":"test-only", "chat_id":"123"}')
    result = CliRunner().invoke(
        app,
        [
            "notify-shadow",
            "--dsn-file",
            str(tmp_path / "dsn"),
            "--telegram-file",
            str(credentials),
            "--enabled",
            "--poll-seconds",
            "0.1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert connect.call_count == 2
    assert calls == [repository]
    assert "reconnecting" in result.output


@pytest.fixture
def durable_repositories() -> Generator[
    tuple[ShadowDeliveryRepository, ShadowDeliveryRepository, PostgresConnection]
]:
    dsn = os.environ.get("T0_PAPER_TEST_DSN")
    if not dsn:
        pytest.skip("explicit disposable PostgreSQL DSN required")
    import psycopg
    from psycopg import sql

    schema = "delivery_test_" + uuid4().hex
    ddl = (
        Path("infra/runtime/postgres/bootstrap/t0_trading.sql")
        .read_text()
        .split("SET ROLE t0_trading;", 1)[1]
        .split("RESET ROLE;", 1)[0]
    )
    with (
        psycopg.connect(dsn, autocommit=True) as conn,
        psycopg.connect(dsn, autocommit=True) as peer,
    ):
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            for connection in (conn, peer):
                connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL(cast(LiteralString, ddl)))
            yield (
                ShadowDeliveryRepository(cast(PostgresConnection, conn)),
                ShadowDeliveryRepository(cast(PostgresConnection, peer)),
                cast(PostgresConnection, conn),
            )
        finally:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.integration
def test_durable_delivery_claim_recovery_and_expiry(
    durable_repositories: tuple[
        ShadowDeliveryRepository, ShadowDeliveryRepository, PostgresConnection
    ],
) -> None:
    repo, other, conn = durable_repositories
    at = datetime.now(UTC) - timedelta(seconds=1)
    selection = realtime_selection(at=at)
    key = repo.enqueue(selection, lifetime=timedelta(seconds=60))
    assert other.enqueue(selection, lifetime=timedelta(seconds=60)) == key
    assert conn.execute("SELECT count(*) FROM shadow_deliveries", ()).fetchone() == (1,)
    with pytest.raises(ValueError, match="conflicting"):
        other.enqueue(selection, lifetime=timedelta(seconds=61))
    with conn.transaction():
        claim = repo.claim(lease=timedelta(seconds=15))
        assert other.claim(lease=timedelta(seconds=15)) is None
    assert claim is not None
    assert claim.selection() == selection
    assert other.claim(lease=timedelta(seconds=15)) is None
    assert repo.may_send(claim, timeout=timedelta(seconds=2))
    repo.finish(claim, "PENDING", retry_after=timedelta(seconds=1))
    assert other.claim(lease=timedelta(seconds=15)) is None
    conn.execute("UPDATE shadow_deliveries SET available_at = clock_timestamp()", ())
    retry = other.claim(lease=timedelta(seconds=15))
    assert retry is not None and retry.attempts == 2
    with pytest.raises(RuntimeError, match="lease lost"):
        repo.finish(claim, "SENT", provider_message_id="stale-owner")
    conn.execute(
        "UPDATE shadow_deliveries SET lease_until = clock_timestamp() - interval '1 second'", ()
    )
    assert repo.claim(lease=timedelta(seconds=15)) is None
    assert conn.execute("SELECT status FROM shadow_deliveries", ()).fetchone() == ("UNKNOWN",)
    repo.enqueue(selection, lifetime=timedelta(seconds=60))
    assert other.claim(lease=timedelta(seconds=15)) is None
    old = realtime_selection("VHM", at=at - timedelta(minutes=5))
    repo.enqueue(old, lifetime=timedelta(seconds=15))
    assert repo.claim(lease=timedelta(seconds=15)) is None
    assert conn.execute(
        "SELECT status FROM shadow_deliveries WHERE symbol='VHM'", ()
    ).fetchone() == ("EXPIRED",)
    fresh = realtime_selection("VHM", at=datetime.now(UTC) - timedelta(milliseconds=1))
    repo.enqueue(fresh, lifetime=timedelta(seconds=60))
    fresh_claim = other.claim(lease=timedelta(seconds=15))
    assert fresh_claim is not None
    assert other.finish(fresh_claim, "SENT", provider_message_id="7") == "SENT"
    assert repo.claim(lease=timedelta(seconds=15)) is None
    retry_expiry = realtime_selection(at=datetime.now(UTC) - timedelta(milliseconds=1))
    repo.enqueue(retry_expiry, lifetime=timedelta(seconds=60))
    limited = other.claim(lease=timedelta(seconds=15))
    assert limited is not None
    assert other.finish(limited, "PENDING", retry_after=timedelta(minutes=2)) == "EXPIRED"


@pytest.mark.integration
def test_cooldown_blocks_other_messages_and_survives_repository_restart(
    durable_repositories: tuple[
        ShadowDeliveryRepository, ShadowDeliveryRepository, PostgresConnection
    ],
) -> None:
    repo, other, conn = durable_repositories
    at = datetime.now(UTC) - timedelta(seconds=1)
    repo.enqueue(realtime_selection(at=at), lifetime=timedelta(seconds=60))
    repo.enqueue(
        realtime_selection("VHM", at=at + timedelta(milliseconds=1)), lifetime=timedelta(seconds=60)
    )
    claim = repo.claim(lease=timedelta(seconds=15))
    assert claim is not None
    assert other.claim(lease=timedelta(seconds=15)) is None
    # Even the final attempt must protect other messages from the destination's RetryAfter.
    repo.finish(claim, "FAILED", error_code="RATE_LIMIT", retry_after=timedelta(seconds=30))
    restarted = ShadowDeliveryRepository(conn)
    assert restarted.claim(lease=timedelta(seconds=15)) is None
    assert other.claim(lease=timedelta(seconds=15)) is None
    conn.execute("UPDATE shadow_delivery_control SET cooldown_until = clock_timestamp()", ())
    next_claim = other.claim(lease=timedelta(seconds=15))
    assert next_claim is not None and next_claim.selection().candidate.symbol == "VHM"
    other.finish(next_claim, "SENT", provider_message_id="8")


@pytest.mark.integration
def test_disconnect_fences_claimed_pending_and_late_writes_but_allows_new_decisions(
    durable_repositories: tuple[
        ShadowDeliveryRepository, ShadowDeliveryRepository, PostgresConnection
    ],
) -> None:
    repo, other, conn = durable_repositories
    at = datetime.now(UTC) - timedelta(seconds=3)
    first = realtime_selection(at=at)
    repo.enqueue(first, lifetime=timedelta(seconds=60))
    repo.enqueue(realtime_selection("VHM", at=at), lifetime=timedelta(seconds=60))
    claim = other.claim(lease=timedelta(seconds=15))
    assert claim is not None
    cutoff = at + timedelta(seconds=1)
    repo.invalidate(cutoff)
    repo.invalidate(at)  # An older callback cannot move the watermark backwards.
    assert conn.execute(
        "SELECT invalidated_through FROM shadow_delivery_control", ()
    ).fetchone() == (cutoff,)
    assert not other.may_send(claim, timeout=timedelta(seconds=2))
    # A RetryAfter received after disconnect must not revive the claimed selection as PENDING.
    assert other.finish(claim, "PENDING", retry_after=timedelta(seconds=1)) == "EXPIRED"
    repo.enqueue(first, lifetime=timedelta(seconds=60))  # No terminal-state revival.
    repo.enqueue(realtime_selection(at=at - timedelta(seconds=1)), lifetime=timedelta(seconds=60))
    assert other.claim(lease=timedelta(seconds=15)) is None
    statuses = conn.execute("SELECT status FROM shadow_deliveries", ()).fetchall()
    assert statuses == [("EXPIRED",)] * 3
    fresh = realtime_selection(at=cutoff + timedelta(milliseconds=1))
    repo.enqueue(fresh, lifetime=timedelta(seconds=60))
    new_claim = other.claim(lease=timedelta(seconds=15))
    assert new_claim is not None and new_claim.selection() == fresh
    other.finish(new_claim, "SENT", provider_message_id="9")
