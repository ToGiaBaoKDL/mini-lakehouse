"""Separate notifier process; explicit opt-in is required before any external send."""

import asyncio
import logging
import signal
from datetime import timedelta
from pathlib import Path
from threading import Event
from typing import Annotated

import typer

from t0_trading.persistence import TransientDatabaseError


def notify_shadow(
    dsn_file: Annotated[Path, typer.Option(envvar="T0_DELIVERY_DSN_FILE")],
    telegram_file: Annotated[Path, typer.Option(envvar="T0_TELEGRAM_SECRET_FILE")],
    enabled: Annotated[bool, typer.Option(envvar="T0_NOTIFICATIONS_ENABLED")] = False,
    once: Annotated[bool, typer.Option()] = False,
    poll_seconds: Annotated[float, typer.Option(min=0.1, max=60)] = 1,
    timeout_seconds: Annotated[float, typer.Option(min=1, max=30)] = 5,
    lease_seconds: Annotated[float, typer.Option(min=2, max=120)] = 15,
    maximum_attempts: Annotated[int, typer.Option(min=1, max=10)] = 3,
) -> None:
    """Deliver persisted SHADOW selections to Telegram; never authorize paper or capital."""
    if not enabled:
        typer.echo("T0 shadow notifications disabled; no database or Telegram access")
        return
    if lease_seconds <= timeout_seconds + 3:
        raise typer.BadParameter("lease must exceed send timeout plus DB statement timeout")
    stop = Event()

    def shutdown(_signum: int, _frame: object) -> None:
        stop.set()

    handlers = {sig: signal.signal(sig, shutdown) for sig in (signal.SIGINT, signal.SIGTERM)}

    async def run() -> None:
        from telegram import Bot

        from t0_trading.notifications.runtime import delivery_repository
        from t0_trading.notifications.telegram import (
            TelegramCredentials,
            TelegramSender,
            dispatch_one,
        )

        # HTTP URLs contain bot credentials. Never enable verbose SDK/HTTP logging here.
        for name in ("telegram", "httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.CRITICAL + 1)
        credentials = TelegramCredentials.model_validate_json(telegram_file.read_text())
        async with Bot(credentials.bot_token.get_secret_value()) as bot:
            sender = TelegramSender(bot, credentials.chat_id, timeout_seconds=timeout_seconds)
            while not stop.is_set():
                try:
                    with delivery_repository(dsn_file) as repository:
                        while not stop.is_set():
                            result = await dispatch_one(
                                repository,
                                sender,
                                enabled=True,
                                timeout=timedelta(seconds=timeout_seconds),
                                lease=timedelta(seconds=lease_seconds),
                                maximum_attempts=maximum_attempts,
                            )
                            if result != "IDLE":
                                typer.echo(f"T0 shadow notification: {result}")
                            if once:
                                return
                            if result == "IDLE":
                                await asyncio.sleep(poll_seconds)
                except TransientDatabaseError:
                    if once:
                        raise
                    # Reconnect, never replay the external send. An orphan SENDING lease is
                    # recovered as UNKNOWN by the repository after its original lease expires.
                    typer.echo("T0 notifier database unavailable; reconnecting", err=True)
                    await asyncio.sleep(poll_seconds)

    try:
        asyncio.run(run())
    except Exception as error:
        typer.echo(f"T0 notifier stopped ({type(error).__name__}); no automatic resend", err=True)
        raise typer.Exit(code=1) from None
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
