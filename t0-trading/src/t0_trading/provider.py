"""Single composition boundary for official SSI SDK clients."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from ssi_sdk import Auth, Config, Stream
from ssi_sdk.enums import StreamingChannel, StreamingMethod
from ssi_sdk.models import RequestMessage

from t0_trading.credentials import Credentials

SSI_API_VERSION = "v3"


class MarketStream:
    """Narrow adapter for the pinned SDK, including its missing market-status helper."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)

    @property
    def on_data(self) -> Any:
        return self._service.on_data

    @on_data.setter
    def on_data(self, callback: Any) -> None:
        self._service.on_data = callback

    @property
    def on_heartbeat(self) -> Any:
        return self._service.on_heartbeat

    @on_heartbeat.setter
    def on_heartbeat(self, callback: Any) -> None:
        self._service.on_heartbeat = callback

    def subscribe_market_status(self, markets: list[str]) -> None:
        subscribe = getattr(self._service, "_subscribe", None)
        if not callable(subscribe):
            raise RuntimeError("Pinned SSI SDK no longer exposes its subscription boundary")
        subscribe(
            RequestMessage(
                method=StreamingMethod.SUBSCRIBE,
                channel=StreamingChannel.DATA,
                topics=[f"market.{market}" for market in markets],
            )
        )


@contextmanager
def authenticated(credentials: Credentials) -> Generator[Any, None, None]:
    """Yield one authenticated market-data SDK context without trading privileges."""
    config = Config(
        client_id=credentials.client_id.get_secret_value(),
        api_key=credentials.api_key.get_secret_value(),
        api_secret=credentials.api_secret.get_secret_value(),
        log_level="ERROR",
    )
    with Auth(config) as auth:
        auth.authenticate()  # pyright: ignore[reportCallIssue] - SDK dynamic public delegate.
        yield auth


@contextmanager
def market_stream(credentials: Credentials) -> Generator[Any, None, None]:
    """Yield a fresh official streaming service for one transport segment."""
    with authenticated(credentials) as auth, Stream(auth) as stream:
        yield MarketStream(stream.streaming)
