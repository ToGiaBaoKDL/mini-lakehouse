"""Single composition boundary for official SSI SDK clients."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from ssi_sdk import Auth, Config

from t0_trading.credentials import Credentials

SSI_API_VERSION = "v3"


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
