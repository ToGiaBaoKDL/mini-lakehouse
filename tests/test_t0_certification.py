import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from t0_trading import certification, provider
from t0_trading.certification import CertificationOptions, run_certification
from t0_trading.credentials import CredentialError, Credentials
from t0_trading.evidence import observation, public_value


@dataclass
class TradeMessage:
    symbol: str
    price: int
    client_id: str


@dataclass
class QuoteMessage:
    symbol: str
    bid_prices: list[int]


@dataclass
class _IndexSummary:
    trading_date: str


class _Auth:
    def __init__(self, config: object) -> None:
        self.config = config

    def authenticate(self) -> object:
        return object()

    def __enter__(self) -> "_Auth":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _MarketData:
    def get_indexes(self) -> list[dict[str, str]]:
        return [{"index": "VN30"}]

    def get_master_data_historical(
        self, from_date: str, to_date: str
    ) -> list[dict[str, str | int]]:
        return [{"symbol": "VIC", "from_date": from_date, "to_date": to_date, "ceiling": 100}]

    def get_securities_info(self, symbol: str) -> dict[str, Any]:
        return {"symbol": symbol, "lot_size": 100}

    def get_securities_summary(self, _symbol: str) -> list[object]:
        return []

    def get_securities_summary_historical(
        self, _symbol: str, _from_date: str, _to_date: str
    ) -> list[object]:
        return []

    def get_securities_summary_by_index_historical(
        self, index: str, _from_date: str, _to_date: str
    ) -> list[dict[str, str]]:
        return [{"index": index}]

    def get_securities_summary_by_index(self, index: str) -> list[dict[str, str]]:
        return [{"index": index}]

    def get_ohlc_1minute(self, symbol: str) -> list[dict[str, str]]:
        return [{"symbol": symbol}]

    def get_ohlc_1day_historical(
        self, symbol: str, _from_date: str, _to_date: str, *, page: int, size: int
    ) -> list[dict[str, Any]]:
        return [{"symbol": symbol, "page": page, "size": size}]

    def get_ohlc_1minute_historical(
        self, symbol: str, _from_date: str, _to_date: str, *, page: int, size: int
    ) -> list[dict[str, Any]]:
        return [{"symbol": symbol, "page": page, "size": size}]

    def get_index_summary(self, _index: str) -> _IndexSummary:
        return _IndexSummary(trading_date="2026/08/26")

    def get_index_summary_historical(self, index: str, date: str) -> dict[str, str]:
        return {"index": index, "date": date}


class _Data:
    def __init__(self, _auth: _Auth) -> None:
        self.market_data = _MarketData()

    def __enter__(self) -> "_Data":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Streaming:
    def __init__(self) -> None:
        self.on_data: Any = None
        self.on_heartbeat: Any = None
        self.ping_count = 0

    def connect(self) -> None:
        return None

    def subscribe_symbol(self, _symbols: list[str]) -> None:
        return None

    def subscribe_symbol_ohlcv(self, _symbols: list[str], *, interval: object) -> None:
        assert interval == certification.Timeframe.MINUTE_1
        return None

    def subscribe_index(self, _indices: list[str]) -> None:
        return None

    def ping(self) -> None:
        self.ping_count += 1
        self.on_heartbeat({})

    def wait(self, *, timeout: float) -> None:
        assert timeout == 1
        assert self.ping_count == 1
        self.on_data(TradeMessage(symbol="VIC", price=100, client_id="must-redact"))
        self.on_data(QuoteMessage(symbol="VIC", bid_prices=[99]))


class _Stream:
    def __init__(self, _auth: _Auth) -> None:
        self.streaming = _Streaming()

    def __enter__(self) -> "_Stream":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_credentials_accept_only_market_data_contract() -> None:
    credentials = Credentials.from_json(
        json.dumps({"version": 1, "client_id": "id", "api_key": "key", "api_secret": "secret"})
    )
    assert credentials.client_id.get_secret_value() == "id"

    with pytest.raises(CredentialError, match="contract") as error:
        Credentials.from_json(
            json.dumps(
                {
                    "version": 1,
                    "client_id": "id",
                    "api_key": "key",
                    "api_secret": "secret",
                    "private_key": "not-allowed",
                }
            )
        )
    assert error.value.__cause__ is not None
    assert "not-allowed" not in str(error.value.__cause__)


def test_public_evidence_recursively_redacts_credentials() -> None:
    value = {
        "symbol": "VIC",
        "authorization": "Bearer secret",
        "nested": {"api_secret": "secret", "price": 100},
    }

    assert public_value(value) == {"symbol": "VIC", "nested": {"price": 100}}
    assert "secret" not in json.dumps(observation([value]))


def test_probe_measures_the_provider_call(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter((10.0, 10.25))
    monkeypatch.setattr(certification, "perf_counter", lambda: next(times))

    result = certification._probe(  # pyright: ignore[reportPrivateUsage]
        lambda: [{"symbol": "VIC"}], sample_limit=1
    )

    assert result["duration_ms"] == 250


def test_certification_requires_market_events_not_only_subscription_acknowledgements() -> None:
    result = certification._certification_result(  # pyright: ignore[reportPrivateUsage]
        {"indexes": {"status": "ok"}},
        [{"status": "ok", "heartbeats": 1, "messages": {"dict": {"count": 7}}}],
    )

    assert result == {
        "status": "partial",
        "rest_errors": 0,
        "stream_errors": 0,
        "missing_evidence": ["stream_model:TradeMessage", "stream_model:QuoteMessage"],
    }


def test_certification_uses_only_official_data_and_stream_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_values: list[dict[str, Any]] = []

    def config(**values: Any) -> object:
        config_values.append(values)
        return object()

    monkeypatch.setattr(provider, "Config", config)
    monkeypatch.setattr(provider, "Auth", _Auth)
    monkeypatch.setattr(certification, "Data", _Data)
    monkeypatch.setattr(certification, "Stream", _Stream)
    monkeypatch.setattr(certification, "SSI_SDK_VERSION", "3.2.0")

    report = run_certification(
        Credentials.from_json(
            json.dumps({"version": 1, "client_id": "id", "api_key": "key", "api_secret": "secret"})
        ),
        CertificationOptions(
            symbols=("VIC",),
            indices=("VN30",),
            stream_seconds=1,
            stream_cycles=2,
        ),
        observed_at=datetime(2026, 8, 27, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    )

    assert report["result"] == {
        "status": "passed",
        "rest_errors": 0,
        "stream_errors": 0,
        "missing_evidence": [],
    }
    assert len(report["stream"]) == 2
    assert report["stream"][0]["heartbeats"] == 1
    assert report["stream"][0]["messages"]["TradeMessage"]["count"] == 1
    assert report["stream"][0]["messages"]["TradeMessage"]["symbol_counts"] == {"VIC": 1}
    assert report["rest"]["master_data_historical"]["status"] == "ok"
    assert report["rest"]["master_data_historical"]["requested_date"] == "2026/08/26"
    assert report["rest"]["ohlc_1minute:VIC"]["status"] == "ok"
    assert report["rest"]["securities_summary_by_index:VN30"]["status"] == "ok"
    assert config_values == [
        {"client_id": "id", "api_key": "key", "api_secret": "secret", "log_level": "ERROR"}
    ]
    serialized = json.dumps(report)
    assert "must-redact" not in serialized
    assert "private_key" not in serialized
