"""Telegram shadow delivery through the SDK, with conservative uncertain-send handling."""

import asyncio
from datetime import timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from telegram import Bot
from telegram.error import BadRequest, ChatMigrated, Forbidden, InvalidToken, RetryAfter

from t0_trading.arbitration.selection import RealtimeSelection
from t0_trading.notifications.postgres import DeliveryClaim, ShadowDeliveryRepository


class TelegramCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    bot_token: SecretStr
    chat_id: str = Field(min_length=1)


def shadow_message(selection: RealtimeSelection, claim: DeliveryClaim) -> str:
    candidate = selection.candidate
    local = candidate.decision_at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    feature = next(item for item in selection.features if item.symbol == candidate.symbol)
    return (
        "SHADOW — CHƯA QUA PROMOTION — KHÔNG PHẢI LỆNH GIAO DỊCH\n"
        f"{candidate.symbol} | {candidate.strategy}\n"
        f"Quan sát: {local.isoformat()}\n"
        f"Giá mid tham chiếu tại quan sát: {feature.mid_price} VND (không phải giá khớp)\n"
        f"Strength: {candidate.strength} | Regime: {candidate.market_regime}\n"
        f"Nhóm: {', '.join(group.name + '=' + group.status for group in candidate.groups)}\n"
        "Chưa xác nhận halt từng mã; không cấp quyền dùng vốn thật.\n"
        f"Hết hạn: {claim.expires_at.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).isoformat()}\n"
        f"Selection: {claim.selection_id}\n"
        f"Context: {selection.context.sha256}"
    )


class Sender(Protocol):
    async def send(self, text: str) -> str: ...


class TelegramSender:
    """Bot lifecycle belongs to the notifier process, never capture or a DB transaction."""

    def __init__(self, bot: Bot, chat_id: str, *, timeout_seconds: float) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._timeout = timeout_seconds

    async def send(self, text: str) -> str:
        message = await self._bot.send_message(
            chat_id=self._chat_id,
            text=text,
            parse_mode=None,
            protect_content=True,
            read_timeout=self._timeout,
            write_timeout=self._timeout,
            connect_timeout=self._timeout,
            pool_timeout=self._timeout,
        )
        return str(message.message_id)


async def dispatch_one(
    repository: ShadowDeliveryRepository,
    sender: Sender,
    *,
    enabled: bool = False,
    timeout: timedelta = timedelta(seconds=5),
    lease: timedelta = timedelta(seconds=15),
    maximum_attempts: int = 3,
) -> str:
    if not enabled:
        return "DISABLED"
    if timeout <= timedelta(0) or lease <= timeout or maximum_attempts < 1:
        raise ValueError("invalid notifier timeout, lease or attempt budget")
    claim = repository.claim(lease=lease)
    if claim is None:
        return "IDLE"
    try:
        selection = claim.selection()
        text = shadow_message(selection, claim)
    except ValueError:
        repository.finish(claim, "FAILED", error_code="INVALID_PAYLOAD")
        return "FAILED"
    if not repository.may_send(claim, timeout=timeout):
        repository.finish(claim, "EXPIRED", error_code="INELIGIBLE_SEND_WINDOW")
        return "EXPIRED"
    if claim.attempts > maximum_attempts:
        repository.finish(claim, "FAILED", error_code="ATTEMPT_LIMIT")
        return "FAILED"
    try:
        message_id = await asyncio.wait_for(sender.send(text), timeout.total_seconds())
    except RetryAfter as error:
        delay = error.retry_after
        if not isinstance(delay, timedelta):
            delay = timedelta(seconds=delay)
        delay = max(delay, timedelta(seconds=1))
        status = "FAILED" if claim.attempts >= maximum_attempts else "PENDING"
        return repository.finish(claim, status, error_code="RATE_LIMIT", retry_after=delay)
    except (BadRequest, Forbidden, InvalidToken, ChatMigrated) as error:
        repository.finish(claim, "FAILED", error_code=type(error).__name__)
        return "FAILED"
    except Exception as error:
        # Telegram has no caller-supplied idempotency key for sendMessage. Network errors and
        # timeouts may follow a successful send; never retry these as if nothing happened.
        repository.finish(claim, "UNKNOWN", error_code=type(error).__name__)
        return "UNKNOWN"
    repository.finish(claim, "SENT", provider_message_id=message_id)
    return "SENT"
