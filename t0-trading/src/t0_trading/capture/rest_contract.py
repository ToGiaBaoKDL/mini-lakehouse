"""Declared SSI FastConnect REST capabilities used by capture and publication."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

REST_CAPABILITY_CONTRACT = "ssi-fastconnect-rest/v2"

InstrumentScope = Literal["symbols", "indices", "membership_indices"]
RequestCardinality = Literal["per_identity", "single"]


@dataclass(frozen=True, slots=True)
class RestCapability:
    """One provider-supported endpoint and its bounded instrument scope."""

    endpoint: str
    scope: InstrumentScope
    cardinality: RequestCardinality
    label: str

    def request_count(self, *, symbols: int, indices: int, membership_indices: int) -> int:
        if self.cardinality == "single":
            return 1
        return {
            "symbols": symbols,
            "indices": indices,
            "membership_indices": membership_indices,
        }[self.scope]


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
    RestCapability(
        "get_ohlc_1minute",
        "indices",
        "per_identity",
        "same-day index OHLC 1-minute",
    ),
    RestCapability(
        "get_securities_info_by_index",
        "membership_indices",
        "per_identity",
        "point-in-time index membership",
    ),
)


def expected_request_counts(*, symbols: int, indices: int, membership_indices: int) -> Counter[str]:
    """Return exact request cardinality for the declared provider capabilities."""
    return Counter(
        {
            capability.endpoint: capability.request_count(
                symbols=symbols,
                indices=indices,
                membership_indices=membership_indices,
            )
            for capability in REST_CAPABILITIES
        }
    )


def has_exact_request_set(
    endpoints: tuple[str, ...],
    *,
    symbols: int,
    indices: int,
    membership_indices: int,
) -> bool:
    """Check that a capture contains exactly the current bounded request set."""
    return Counter(endpoints) == expected_request_counts(
        symbols=symbols,
        indices=indices,
        membership_indices=membership_indices,
    )
