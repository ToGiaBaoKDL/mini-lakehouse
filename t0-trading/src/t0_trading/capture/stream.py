"""Bounded immutable SSI Stream capture through the official SDK."""

from __future__ import annotations

import gzip
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from queue import Empty, Full, Queue
from threading import Event, Lock
from time import monotonic
from typing import Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from ssi_sdk import __version__ as SSI_SDK_VERSION
from ssi_sdk.enums import Timeframe

from t0_trading.capture.spool import CaptureSpool
from t0_trading.capture.store import CaptureStore, canonical_json, sha256
from t0_trading.evidence import public_value
from t0_trading.market.events import StreamEnvelope
from t0_trading.provider import SSI_API_VERSION

MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
SSI_STREAM_RAW_PREFIX = "stream/ssi_fastconnect_stream/raw"


class StreamCaptureError(RuntimeError):
    """A connected stream session ended without a complete healthy capture."""


@dataclass(frozen=True, slots=True)
class StreamCaptureOptions:
    symbols: tuple[str, ...] = ("VIC", "VHM")
    duration_seconds: float = 600
    heartbeat_seconds: float = 30
    stale_after_seconds: float = 90
    flush_seconds: float = 30
    batch_size: int = 500
    queue_size: int = 10_000

    def __post_init__(self) -> None:
        if (
            not self.symbols
            or len(self.symbols) != len(set(self.symbols))
            or any(not symbol or symbol != symbol.strip().upper() for symbol in self.symbols)
        ):
            raise ValueError("symbols must contain unique uppercase identifiers")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if self.heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive")
        if self.stale_after_seconds <= self.heartbeat_seconds:
            raise ValueError("stale_after_seconds must exceed heartbeat_seconds")
        if self.flush_seconds <= 0 or self.batch_size < 1 or self.queue_size < self.batch_size:
            raise ValueError("stream buffer limits are invalid")


def _field(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _text_field(value: object, *names: str) -> str | None:
    for name in names:
        item = _field(value, name)
        if isinstance(item, str) and item:
            return item
    return None


class _Receiver:
    def __init__(
        self,
        messages: Queue[StreamEnvelope],
        *,
        session_id: str,
        clock: Callable[[], datetime],
        timer: Callable[[], float],
    ) -> None:
        self._messages = messages
        self._session_id = session_id
        self._clock = clock
        self._timer = timer
        self._lock = Lock()
        self._next_sequence = 1
        self._error_type: str | None = None
        self.heartbeat_count = 0
        self.last_heartbeat_at: str | None = None
        self.last_business_message_at: str | None = None
        self.last_heartbeat_tick = timer()

    def record(self, message: Any) -> None:
        try:
            received_at = self._clock().astimezone(UTC)
            public = public_value(message)
            message_json = canonical_json(public).decode("utf-8")
            with self._lock:
                sequence = self._next_sequence
                self._messages.put_nowait(
                    StreamEnvelope(
                        stream_session_id=self._session_id,
                        receive_sequence=sequence,
                        received_at=received_at,
                        message_type=type(message).__name__,
                        symbol=_text_field(message, "symbol"),
                        source_time_text=_text_field(
                            message, "trading_time", "interval_time", "trading_date"
                        ),
                        message_json=message_json,
                        message_sha256=sha256(message_json.encode("utf-8")),
                    )
                )
                self._next_sequence += 1
                self.last_business_message_at = received_at.isoformat()
        except Full:
            with self._lock:
                self._error_type = "QueueFull"
        except Exception as error:
            with self._lock:
                self._error_type = type(error).__name__

    def heartbeat(self, _message: Any) -> None:
        observed_at = self._clock().astimezone(UTC).isoformat()
        observed_tick = self._timer()
        with self._lock:
            self.heartbeat_count += 1
            self.last_heartbeat_at = observed_at
            self.last_heartbeat_tick = observed_tick

    def mark_connected(self) -> None:
        with self._lock:
            self.last_heartbeat_tick = self._timer()

    def health(self) -> tuple[str | None, float, int]:
        with self._lock:
            return self._error_type, self.last_heartbeat_tick, self.heartbeat_count


def _row(message: StreamEnvelope) -> dict[str, object]:
    return {
        "stream_session_id": message.stream_session_id,
        "receive_sequence": message.receive_sequence,
        "message_type": message.message_type,
        "subscription_context": "symbols",
        "provider_topic": None,
        "symbol": message.symbol,
        "source_time_text": message.source_time_text,
        "received_at": message.received_at.isoformat(),
        "message_json": message.message_json,
        "message_sha256": message.message_sha256,
        "api_version": SSI_API_VERSION,
        "sdk_version": SSI_SDK_VERSION,
    }


def _publish_batch(
    store: CaptureStore,
    session_prefix: str,
    session_id: str,
    messages: list[StreamEnvelope],
    clock: Callable[[], datetime],
    spool: CaptureSpool | None,
) -> dict[str, object]:
    rows = [_row(message) for message in messages]
    body = gzip.compress(
        b"".join(canonical_json(row) + b"\n" for row in rows),
        compresslevel=6,
        mtime=0,
    )
    object_sha256 = sha256(body)
    first_sequence = messages[0].receive_sequence
    last_sequence = messages[-1].receive_sequence
    object_key = (
        f"{session_prefix}/batches/"
        f"{first_sequence:012d}-{last_sequence:012d}-{object_sha256}.json.gz"
    )
    if spool is None:
        _, stored_sha256 = store.put_capture(object_key, body)
        if stored_sha256 != object_sha256:
            raise RuntimeError("Capture store returned an unexpected stream object checksum")
    elif spool.stage_capture(object_key, body) != object_sha256:
        raise RuntimeError("Capture spool returned an unexpected stream object checksum")
    published_at = clock().astimezone(UTC).isoformat()
    batch_id = sha256(
        canonical_json(
            {
                "stream_session_id": session_id,
                "first_receive_sequence": first_sequence,
                "last_receive_sequence": last_sequence,
                "object_sha256": object_sha256,
            }
        )
    )
    return {
        "batch_id": batch_id,
        "first_receive_sequence": first_sequence,
        "last_receive_sequence": last_sequence,
        "message_count": len(messages),
        "object_key": object_key,
        "object_sha256": object_sha256,
        "published_at": published_at,
    }


def capture_stream(
    client: Any,
    store: CaptureStore,
    options: StreamCaptureOptions,
    *,
    stop: Event | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    timer: Callable[[], float] = monotonic,
    session_id: str | None = None,
    on_ready: Callable[[], None] | None = None,
    spool: CaptureSpool | None = None,
) -> str:
    """Capture one bounded SDK connection and return its terminal manifest URI."""
    stop = stop or Event()
    session_id = session_id or str(uuid4())
    if spool is not None:
        spool.drain(store)
    queue: Queue[StreamEnvelope] = Queue(maxsize=options.queue_size)
    receiver = _Receiver(queue, session_id=session_id, clock=clock, timer=timer)
    client.on_data = receiver.record
    client.on_heartbeat = receiver.heartbeat
    client.connect()

    connected_at = clock().astimezone(UTC)
    receiver.mark_connected()
    session_prefix = (
        f"{SSI_STREAM_RAW_PREFIX}/trade_date="
        f"{connected_at.astimezone(MARKET_TIMEZONE).date().isoformat()}/session={session_id}"
    )
    started_tick = timer()
    next_ping_tick = started_tick + options.heartbeat_seconds
    last_flush_tick = started_tick
    batches: list[dict[str, object]] = []
    buffered: list[StreamEnvelope] = []
    disconnect_kind = "completed"
    failure_type: str | None = None
    ready = False

    def drain() -> None:
        while True:
            try:
                message = queue.get_nowait()
            except Empty:
                return
            buffered.append(message)

    def flush(*, force: bool = False) -> None:
        nonlocal last_flush_tick
        while len(buffered) >= options.batch_size or (force and buffered):
            count = min(len(buffered), options.batch_size)
            batches.append(
                _publish_batch(
                    store,
                    session_prefix,
                    session_id,
                    buffered[:count],
                    clock,
                    spool,
                )
            )
            del buffered[:count]
        if spool is not None:
            spool.drain(store)
        last_flush_tick = timer()

    try:
        # The sync SDK prints subscription RequestMessage values; retain SDK ownership while
        # keeping protocol details out of service logs.
        with redirect_stdout(StringIO()):
            client.subscribe_symbol(list(options.symbols))
            client.subscribe_symbol_ohlcv(list(options.symbols), interval=Timeframe.MINUTE_1)
            client.ping()
            while True:
                client.wait(timeout=0.25)
                now = timer()
                drain()
                if len(buffered) >= options.batch_size or (
                    buffered and now - last_flush_tick >= options.flush_seconds
                ):
                    flush(force=now - last_flush_tick >= options.flush_seconds)
                receiver_error, last_heartbeat_tick, heartbeat_count = receiver.health()
                if receiver_error is not None:
                    disconnect_kind = "capture_error"
                    failure_type = receiver_error
                    break
                if not ready and heartbeat_count > 0:
                    if on_ready is not None:
                        on_ready()
                    ready = True
                if stop.is_set():
                    disconnect_kind = "shutdown"
                    break
                if now - started_tick >= options.duration_seconds:
                    break
                if now - last_heartbeat_tick > options.stale_after_seconds:
                    disconnect_kind = "stale"
                    failure_type = "HeartbeatTimeout"
                    break
                if now >= next_ping_tick:
                    client.ping()
                    while next_ping_tick <= now:
                        next_ping_tick += options.heartbeat_seconds
    except Exception as error:
        disconnect_kind = "capture_error"
        failure_type = type(error).__name__
    finally:
        try:
            client.disconnect()
        except Exception as error:
            disconnect_kind = "capture_error"
            failure_type = type(error).__name__

    disconnected_at = clock().astimezone(UTC)
    try:
        drain()
        flush(force=True)
    except Exception as error:
        disconnect_kind = "capture_error"
        failure_type = type(error).__name__

    message_count = sum(cast(int, batch["message_count"]) for batch in batches)
    manifest_key = f"{session_prefix}/manifest.json"
    manifest: dict[str, object] = {
        "schema_version": 1,
        "stream_session_id": session_id,
        "symbols": list(options.symbols),
        "connected_at": connected_at.isoformat(),
        "disconnected_at": disconnected_at.isoformat(),
        "disconnect_kind": disconnect_kind,
        "message_count": message_count,
        "first_receive_sequence": batches[0]["first_receive_sequence"] if batches else None,
        "last_receive_sequence": batches[-1]["last_receive_sequence"] if batches else None,
        "heartbeat_count": receiver.heartbeat_count,
        "last_heartbeat_at": receiver.last_heartbeat_at,
        "last_business_message_at": receiver.last_business_message_at,
        "batch_count": len(batches),
        "batches": batches,
        "api_version": SSI_API_VERSION,
        "sdk_version": SSI_SDK_VERSION,
        "error_type": failure_type,
        "published_at": clock().astimezone(UTC).isoformat(),
    }
    if spool is None:
        store.put_json(manifest_key, manifest)
    else:
        spool.stage_json(manifest_key, manifest)
        spool.drain(store)
    if disconnect_kind not in {"completed", "shutdown"}:
        raise StreamCaptureError(f"SSI stream capture ended as {disconnect_kind}")
    return store.uri(manifest_key)
