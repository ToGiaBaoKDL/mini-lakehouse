"""Read-only SSI Data REST and Stream DATA contract certification."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import StringIO
from threading import Lock
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from ssi_sdk import Data, Stream
from ssi_sdk import __version__ as SSI_SDK_VERSION
from ssi_sdk.enums import Timeframe

from t0_trading.credentials import Credentials
from t0_trading.evidence import fingerprint, observation, public_value
from t0_trading.provider import authenticated

MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True, slots=True)
class CertificationOptions:
    symbols: tuple[str, ...] = ("VIC", "VHM")
    indices: tuple[str, ...] = ("VNINDEX", "VN30")
    history_days: int = 10
    page_size: int = 5
    stream_seconds: float = 15
    stream_cycles: int = 1
    sample_limit: int = 2

    def __post_init__(self) -> None:
        if not self.symbols or not self.indices:
            raise ValueError("Certification requires at least one symbol and index.")
        if self.history_days < 1 or self.page_size < 1:
            raise ValueError("History days and page size must be positive.")
        if self.stream_seconds < 0 or self.stream_cycles < 0 or self.sample_limit < 1:
            raise ValueError(
                "Streaming values cannot be negative and sample limit must be positive."
            )


def _probe(call: Callable[[], Any], sample_limit: int) -> dict[str, Any]:
    started_at = perf_counter()
    try:
        result = call()
        return {
            "status": "ok",
            "duration_ms": round((perf_counter() - started_at) * 1000),
            **observation(result, sample_limit=sample_limit),
        }
    except Exception as error:  # SDK boundary: never serialize provider exception messages/state.
        return {
            "status": "error",
            "duration_ms": round((perf_counter() - started_at) * 1000),
            "error_type": type(error).__name__,
        }


def _probe_pages(call: Callable[[int], Any], sample_limit: int) -> dict[str, Any]:
    started_at = perf_counter()
    try:
        first = call(1)
        second = call(2)
        first_records = first if isinstance(first, list) else ([] if first is None else [first])
        second_records = (
            second if isinstance(second, list) else ([] if second is None else [second])
        )
        overlap = {fingerprint(item) for item in first_records} & {
            fingerprint(item) for item in second_records
        }
        return {
            "status": "ok",
            "duration_ms": round((perf_counter() - started_at) * 1000),
            "pages": {
                "1": observation(first_records, sample_limit=sample_limit),
                "2": observation(second_records, sample_limit=sample_limit),
            },
            "overlap_records": len(overlap),
        }
    except Exception as error:  # SDK boundary: never serialize provider exception messages/state.
        return {
            "status": "error",
            "duration_ms": round((perf_counter() - started_at) * 1000),
            "error_type": type(error).__name__,
        }


class _StreamEvidence:
    def __init__(self, sample_limit: int) -> None:
        self._sample_limit = sample_limit
        self._lock = Lock()
        self._counts: Counter[str] = Counter()
        self._fingerprints: dict[str, Counter[str]] = defaultdict(Counter)
        self._samples: dict[str, list[Any]] = defaultdict(list)
        self._symbols: dict[str, Counter[str]] = defaultdict(Counter)
        self._event_types: dict[str, Counter[str]] = defaultdict(Counter)
        self._last_source_time: dict[tuple[str, str], str] = {}
        self._timestamp_regressions: Counter[str] = Counter()
        self._heartbeats = 0

    def record(self, message: Any) -> None:
        name = type(message).__name__
        public = public_value(message)
        with self._lock:
            self._counts[name] += 1
            self._fingerprints[name][fingerprint(public)] += 1
            if len(self._samples[name]) < self._sample_limit:
                self._samples[name].append(public)
            if isinstance(public, dict):
                symbol = public.get("symbol")
                event_type = public.get("type")
                source_time = public.get("trading_time")
                if isinstance(symbol, str):
                    self._symbols[name][symbol] += 1
                if isinstance(event_type, str):
                    self._event_types[name][event_type] += 1
                if isinstance(symbol, str) and isinstance(source_time, str):
                    key = (name, symbol)
                    previous = self._last_source_time.get(key)
                    if previous is not None and source_time < previous:
                        self._timestamp_regressions[name] += 1
                    self._last_source_time[key] = source_time

    def heartbeat(self, _message: Any) -> None:
        with self._lock:
            self._heartbeats += 1

    def report(self) -> dict[str, Any]:
        with self._lock:
            messages = {
                name: {
                    "count": count,
                    "duplicate_messages": sum(
                        occurrences - 1 for occurrences in self._fingerprints[name].values()
                    ),
                    "timestamp_regressions": self._timestamp_regressions[name],
                    "symbol_counts": dict(sorted(self._symbols[name].items())),
                    "event_type_counts": dict(sorted(self._event_types[name].items())),
                    "observation": observation(
                        self._samples[name], sample_limit=self._sample_limit
                    ),
                }
                for name, count in sorted(self._counts.items())
            }
            return {"heartbeats": self._heartbeats, "messages": messages}


def _stream_cycle(auth: Any, options: CertificationOptions, cycle: int) -> dict[str, Any]:
    evidence = _StreamEvidence(options.sample_limit)
    started_at = perf_counter()
    try:
        with Stream(auth) as stream:
            client = stream.streaming
            client.on_data = evidence.record
            client.on_heartbeat = evidence.heartbeat
            client.connect()
            # ssi-sdk 3.2.0 prints RequestMessage from its synchronous subscribe helper.
            # Keep the SDK-owned subscriptions while preventing protocol noise in CLI output.
            with redirect_stdout(StringIO()):
                client.subscribe_symbol(list(options.symbols))
                client.subscribe_symbol_ohlcv(list(options.symbols), interval=Timeframe.MINUTE_1)
                client.subscribe_index(list(options.indices))
                client.ping()
            client.wait(timeout=options.stream_seconds)
        return {
            "cycle": cycle,
            "status": "ok",
            "duration_ms": round((perf_counter() - started_at) * 1000),
            **evidence.report(),
        }
    except Exception as error:  # SDK boundary: never serialize provider exception messages/state.
        return {
            "cycle": cycle,
            "status": "error",
            "duration_ms": round((perf_counter() - started_at) * 1000),
            "error_type": type(error).__name__,
            **evidence.report(),
        }


def _certification_result(rest: dict[str, Any], stream: list[dict[str, Any]]) -> dict[str, Any]:
    rest_errors = sum(item["status"] == "error" for item in rest.values())
    stream_errors = sum(item["status"] == "error" for item in stream)
    observed_stream_models = {model for cycle in stream for model in cycle.get("messages", {})}
    missing_evidence = [
        f"stream_model:{model}"
        for model in ("TradeMessage", "QuoteMessage")
        if model not in observed_stream_models
    ]
    if not stream or any(cycle.get("heartbeats", 0) < 1 for cycle in stream):
        missing_evidence.append("stream_heartbeat")
    return {
        "status": (
            "passed"
            if rest_errors == 0 and stream_errors == 0 and not missing_evidence
            else "partial"
        ),
        "rest_errors": rest_errors,
        "stream_errors": stream_errors,
        "missing_evidence": missing_evidence,
    }


def run_certification(
    credentials: Credentials,
    options: CertificationOptions,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    now = observed_at.astimezone(MARKET_TIMEZONE) if observed_at else datetime.now(MARKET_TIMEZONE)
    start = now - timedelta(days=options.history_days)
    from_date = start.strftime("%Y/%m/%d 00:00:00")
    to_date = now.strftime("%Y/%m/%d 23:59:59")
    from_day = start.strftime("%Y/%m/%d")
    to_day = now.strftime("%Y/%m/%d")
    report: dict[str, Any] = {
        "certification": "ssi-fastconnect-v3",
        "generated_at": now.isoformat(),
        "market_timezone": str(MARKET_TIMEZONE),
        "sdk": {"package": "ssi-sdk", "version": SSI_SDK_VERSION},
        "scope": {
            "symbols": list(options.symbols),
            "indices": list(options.indices),
            "history": {"from": from_date, "to": to_date},
            "page_size": options.page_size,
            "stream_seconds_per_cycle": options.stream_seconds,
            "stream_cycles": options.stream_cycles,
            "trading_client_initialized": False,
        },
        "rest": {},
        "stream": [],
    }

    with authenticated(credentials) as auth:
        report["authentication"] = {"status": "ok", "otp_supplied": False}
        with Data(auth) as data:
            market = data.market_data
            report["rest"]["indexes"] = _probe(market.get_indexes, options.sample_limit)
            for symbol in options.symbols:
                report["rest"][f"securities_info:{symbol}"] = _probe(
                    lambda symbol=symbol: market.get_securities_info(symbol), options.sample_limit
                )
                report["rest"][f"securities_summary:{symbol}"] = _probe(
                    lambda symbol=symbol: market.get_securities_summary(symbol),
                    options.sample_limit,
                )
                report["rest"][f"securities_summary_historical:{symbol}"] = _probe(
                    lambda symbol=symbol: market.get_securities_summary_historical(
                        symbol, from_day, to_day
                    ),
                    options.sample_limit,
                )
                report["rest"][f"ohlc_1day_historical:{symbol}"] = _probe_pages(
                    lambda page, symbol=symbol: market.get_ohlc_1day_historical(
                        symbol, from_date, to_date, page=page, size=options.page_size
                    ),
                    options.sample_limit,
                )
                report["rest"][f"ohlc_1minute_historical:{symbol}"] = _probe_pages(
                    lambda page, symbol=symbol: market.get_ohlc_1minute_historical(
                        symbol, from_date, to_date, page=page, size=options.page_size
                    ),
                    options.sample_limit,
                )
                report["rest"][f"ohlc_1minute:{symbol}"] = _probe(
                    lambda symbol=symbol: market.get_ohlc_1minute(symbol),
                    options.sample_limit,
                )
            completed_trading_dates: set[str] = set()
            for index in options.indices:
                report["rest"][f"securities_summary_by_index:{index}"] = _probe(
                    lambda index=index: market.get_securities_summary_by_index(index),
                    options.sample_limit,
                )
                report["rest"][f"securities_summary_by_index_historical:{index}"] = _probe(
                    lambda index=index: market.get_securities_summary_by_index_historical(
                        index, from_day, to_day
                    ),
                    options.sample_limit,
                )
                report["rest"][f"ohlc_1day_historical:{index}"] = _probe_pages(
                    lambda page, index=index: market.get_ohlc_1day_historical(
                        index, from_date, to_date, page=page, size=options.page_size
                    ),
                    options.sample_limit,
                )
                report["rest"][f"ohlc_1minute_historical:{index}"] = _probe_pages(
                    lambda page, index=index: market.get_ohlc_1minute_historical(
                        index, from_date, to_date, page=page, size=options.page_size
                    ),
                    options.sample_limit,
                )
                started_at = perf_counter()
                try:
                    current_summary = market.get_index_summary(index)
                    report["rest"][f"index_summary:{index}"] = {
                        "status": "ok",
                        "duration_ms": round((perf_counter() - started_at) * 1000),
                        **observation(current_summary, sample_limit=options.sample_limit),
                    }
                except Exception as error:  # SDK boundary: never serialize provider state.
                    current_summary = None
                    report["rest"][f"index_summary:{index}"] = {
                        "status": "error",
                        "duration_ms": round((perf_counter() - started_at) * 1000),
                        "error_type": type(error).__name__,
                    }
                historical_date = getattr(current_summary, "trading_date", None)
                if isinstance(historical_date, str) and historical_date:
                    completed_trading_dates.add(historical_date)
                    historical = _probe(
                        lambda index=index, historical_date=historical_date: (
                            market.get_index_summary_historical(index, historical_date)
                        ),
                        options.sample_limit,
                    )
                    historical["requested_date"] = historical_date
                else:
                    historical = {
                        "status": "skipped",
                        "reason": "current_summary_has_no_completed_trading_date",
                    }
                report["rest"][f"index_summary_historical:{index}"] = historical
            if completed_trading_dates:
                master_date = max(completed_trading_dates)
                master_data = _probe(
                    lambda: market.get_master_data_historical(master_date, master_date),
                    options.sample_limit,
                )
                master_data["requested_date"] = master_date
            else:
                master_data = {
                    "status": "skipped",
                    "reason": "index_summary_has_no_completed_trading_date",
                }
            report["rest"]["master_data_historical"] = master_data

        report["stream"] = [
            _stream_cycle(auth, options, cycle) for cycle in range(1, options.stream_cycles + 1)
        ]

    report["result"] = _certification_result(report["rest"], report["stream"])
    return report
