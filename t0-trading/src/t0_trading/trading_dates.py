"""Resolve completed Vietnamese trading dates from official SSI history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol


class MarketHistory(Protocol):
    def get_securities_summary_by_index_historical(
        self,
        index: str,
        from_date: str,
        to_date: str,
    ) -> Sequence[object]: ...


class TradingDateError(ValueError):
    """A requested or discoverable completed trading date is unavailable."""


def _record_date(record: object) -> date:
    value = record.get("trading_date") if isinstance(record, Mapping) else None
    if value is None:
        value = getattr(record, "trading_date", None)
    if not isinstance(value, str):
        raise TradingDateError("SSI trading history returned an invalid trading date.")
    try:
        return date.fromisoformat(value.replace("/", "-"))
    except ValueError as error:
        raise TradingDateError("SSI trading history returned an invalid trading date.") from error


def require_observed_trade_date(
    market: MarketHistory,
    *,
    trade_date: date,
    index: str = "VN30",
) -> date:
    """Require SSI history to contain the exact requested trading date."""
    formatted = trade_date.strftime("%Y/%m/%d")
    records = market.get_securities_summary_by_index_historical(
        index,
        formatted,
        formatted,
    )
    if trade_date not in {_record_date(record) for record in records}:
        raise TradingDateError("trade_date is not an SSI-observed trading day.")
    return trade_date
