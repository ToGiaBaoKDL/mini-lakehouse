"""Declared SSI FastConnect REST capabilities used by capture and publication."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

REST_CAPABILITY_CONTRACT = "ssi-fastconnect-rest/v1"

InstrumentScope = Literal["symbols", "indices"]
RequestCardinality = Literal["per_identity", "single"]


@dataclass(frozen=True, slots=True)
class RestCapability:
    """One provider-supported endpoint and its bounded instrument scope."""

    endpoint: str
    scope: InstrumentScope
    cardinality: RequestCardinality
    label: str

    def request_count(self, *, symbols: int, indices: int) -> int:
        if self.cardinality == "single":
            return 1
        return symbols if self.scope == "symbols" else indices


REST_CAPABILITIES = (
    RestCapability(
        "get_securities_info",
        "symbols",
        "per_identity",
        "security catalog",
    ),
    RestCapability(
        "get_securities_summary_historical",
        "symbols",
        "per_identity",
        "completed-day stock summary",
    ),
    RestCapability(
        "get_ohlc_1day_historical",
        "symbols",
        "per_identity",
        "completed-day stock OHLC 1-day",
    ),
    RestCapability(
        "get_ohlc_1minute_historical",
        "symbols",
        "per_identity",
        "completed-day stock OHLC 1-minute",
    ),
    RestCapability(
        "get_master_data_historical",
        "symbols",
        "single",
        "completed-day stock master-data",
    ),
    RestCapability(
        "get_indexes",
        "indices",
        "single",
        "index catalog",
    ),
    RestCapability(
        "get_index_summary_historical",
        "indices",
        "per_identity",
        "completed-day index summary",
    ),
)


def expected_request_counts(*, symbols: int, indices: int) -> Counter[str]:
    """Return exact request cardinality for the declared provider capabilities."""
    return Counter(
        {
            capability.endpoint: capability.request_count(symbols=symbols, indices=indices)
            for capability in REST_CAPABILITIES
        }
    )


def has_exact_request_set(
    endpoints: tuple[str, ...],
    *,
    symbols: int,
    indices: int,
    allow_legacy_index_minute_probe: bool = False,
) -> bool:
    """Check a bounded request set, optionally accepting the retired index probe."""
    actual = Counter(endpoints)
    expected = expected_request_counts(symbols=symbols, indices=indices)
    if actual == expected:
        return True
    if not allow_legacy_index_minute_probe:
        return False
    legacy = expected.copy()
    legacy["get_ohlc_1minute_historical"] += indices
    if actual == legacy:
        return True
    # Captures written before index-catalog evidence was added remain replayable;
    # new captures must always satisfy the current contract above.
    del legacy["get_indexes"]
    return actual == legacy
